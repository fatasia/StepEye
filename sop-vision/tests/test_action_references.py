import hashlib
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from visual.action_references import ActionReferenceStore
from visual.action_reference_routes import router
from visual.timeline import TimelineStore,probe_video


@pytest.fixture
def refs(tmp_path):
    videos=tmp_path/'timelines';videos.mkdir();video=videos/'source.avi'
    writer=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'MJPG'),10.,(80,60))
    assert writer.isOpened()
    for i in range(30):
        frame=np.full((60,80,3),(i*7,100,200),np.uint8)
        cv2.putText(frame,str(i),(5,30),cv2.FONT_HERSHEY_SIMPLEX,.4,(255,255,255),1)
        writer.write(frame)
    writer.release()
    duration,fps=probe_video(video)
    timelines=TimelineStore(tmp_path/'timelines.db')
    timeline=timelines.create('test video','source.avi',duration,fps)
    timelines.update(timeline['id'],intervals=[{'step':'S1','start':.2,'end':2.8}])
    store=ActionReferenceStore(tmp_path/'refs',videos,tmp_path/'timelines.db')
    yield store,timelines,timeline,video
    store.close();timelines.close()


def create(refs,**kwargs):
    store,_,timeline,_=refs
    values=dict(tid=timeline['id'],interval_index=0,station_id='station',step_id='S1',step_name='Fit part',key_time_s=1.5,rules_hash='abc',actor='op')
    values.update(kwargs)
    return store.create(**values)


def test_real_three_frame_extraction_hashes_and_immutable_versions(refs):
    store,timelines,timeline,video=refs
    first=create(refs)
    assert first['video_sha256']==hashlib.sha256(video.read_bytes()).hexdigest()
    assert first['version']==1 and not first['applied_to_production']
    assert set(first['frames'])=={'start','key','end'}
    decoded=[]
    for role in ('start','key','end'):
        frame=cv2.imread(str(store.frame(first['id'],role)))
        assert frame.shape==(60,80,3)
        decoded.append(first['frames'][role]['decoder_frame_index'])
    assert decoded[0]<decoded[1]<decoded[2]
    timelines.update(timeline['id'],intervals=[{'step':'S1','start':.5,'end':3.}])
    second=create(refs)
    assert second['version']==2 and second['frames']['end']['seek_time_s']==pytest.approx(2.9)
    assert store.get(first['id'])['interval']['start']==.2
    assert len(store.list(station_id='station'))==2
    store.frame(first['id'],'key').write_bytes(b'tampered')
    with pytest.raises(ValueError,match='校验'):store.frame(first['id'],'key')


@pytest.mark.parametrize('kwargs',[{'key_time_s':.2},{'key_time_s':3.},{'key_time_s':float('nan')},{'interval_index':5}])
def test_invalid_interval_rejected_before_artifacts(refs,kwargs):
    with pytest.raises(ValueError):create(refs,**kwargs)
    assert not refs[0].list()


def test_metadata_survives_reopen(refs):
    store,_,_,_=refs
    record=create(refs)
    reopened=ActionReferenceStore(store.root,store.timeline_root,store.timeline_db)
    try:assert reopened.get(record['id'])==record
    finally:reopened.close()


def test_api_step_binding_permissions_and_frame_serving(refs):
    store,_,timeline,_=refs
    rt=SimpleNamespace(program=SimpleNamespace(steps=[SimpleNamespace(id='S1',name='Fit part')],rules_hash='cfg-v1'))
    manager=SimpleNamespace(runtimes={'station':rt})
    app=FastAPI()
    @app.middleware('http')
    async def user(request,call_next):
        request.state.user={'role':request.headers.get('test-role','operator'),'username':'test'}
        return await call_next(request)
    app.include_router(router(store,manager));client=TestClient(app)
    payload={'interval_index':0,'station_id':'station','step_id':'S1','key_time_s':1.5}
    url=f"/api/timelines/{timeline['id']}/action-references"
    assert client.post(url,json=payload,headers={'test-role':'viewer'}).status_code==403
    assert client.post(url,json={**payload,'step_id':'missing'}).status_code==422
    result=client.post(url,json=payload)
    assert result.status_code==201,result.text
    record=result.json()
    assert record['rules_hash']=='cfg-v1'
    assert client.get('/api/stations/station/action-references').json()['items'][0]['id']==record['id']
    image=client.get(f"/api/action-references/{record['id']}/frames/key")
    assert image.status_code==200 and image.headers['content-type']=='image/png'


def test_extract_api_returns_real_filenames_and_persists_for_gallery(refs, tmp_path):
    from visual.server import build_app
    _, _, _, video = refs
    app = build_app([], tmp_path/'api-data')
    with TestClient(app) as client:
        created = client.post('/api/timelines', data={'name':'gallery'},
                              files={'video':('gallery.avi',video.read_bytes(),'video/x-msvideo')})
        assert created.status_code == 201, created.text
        tid = created.json()['id']
        result = client.post(f'/api/timelines/{tid}/intervals',json={
            'intervals':[{'step':'装配/特殊步骤','start':.2,'end':2.8}], 'extract':True,'per_interval':3})
        assert result.status_code == 200, result.text
        data = result.json()
        assert data['check']['ok'] and data['extracted'] == 3
        assert data['frames_url_prefix'] == f'/api/timelines/{tid}/frames'
        assert len(data['frames']) == 3
        for frame in data['frames']:
            assert frame['url'] == data['frames_url_prefix']+'/'+frame['file']
            assert frame['label'] == '装配/特殊步骤' and frame['engine'] == 'timeline'
            assert frame['params']['interval'] == [.2,2.8]
            response = client.get(frame['url'])
            assert response.status_code == 200
            assert cv2.imdecode(np.frombuffer(response.content,np.uint8),cv2.IMREAD_COLOR) is not None
        assert client.get(f'/api/timelines/{tid}').json()['frames'] == data['frames']
        assert client.get('/api/timelines').json()[0]['frames'] == data['frames']
        manifest = tmp_path/'api-data'/'timelines'/tid/'frames'/'samples.manifest.jsonl'
        assert len(manifest.read_text(encoding='utf-8').splitlines()) == 3
        assert 'crypto.subtle' not in (__import__('pathlib').Path(__file__).parents[1]/'web/js/pages/timeline.js').read_text(encoding='utf-8')


def test_legacy_timeline_schema_migrates_frames_without_losing_intervals(tmp_path):
    import sqlite3
    path=tmp_path/'old.db'
    db=sqlite3.connect(path)
    db.execute('CREATE TABLE timelines (id TEXT PRIMARY KEY,name TEXT,status TEXT,video TEXT,duration_s REAL,fps REAL,intervals TEXT,created TEXT,updated TEXT)')
    db.execute('INSERT INTO timelines VALUES(?,?,?,?,?,?,?,?,?)',('old','legacy','draft','v.avi',3.,10.,'[]','now','now'))
    db.commit();db.close()
    store=TimelineStore(path)
    try:
        assert store.get('old')['name']=='legacy'
        assert store.get('old')['frames']==[]
    finally:store.close()
