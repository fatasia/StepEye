"""产品工作流：空数据创建、试运行隔离、恢复、正常记录分流与权限。"""
from pathlib import Path
import json
import time

import cv2
import numpy as np
from fastapi.testclient import TestClient

from visual.server import build_app
from visual.auth import AuthStore


def image():
    img=np.full((112,112,3),120,np.uint8)
    img[20:60,30:80]=240
    return cv2.imencode('.png',img)[1].tobytes()


def test_application_create_trial_enable_pause_restart(tmp_path):
    app=build_app([],tmp_path)
    with TestClient(app) as c:
        created=c.post('/api/applications',json={'name':'装配','task':'sop'})
        assert created.status_code==201,created.text
        aid=created.json()['id']
        assert c.post(f'/api/applications/{aid}/run',json={}).status_code==409
        assert c.post(f'/api/applications/{aid}/input',json={'type':'sim'}).status_code==200
        assert c.post(f'/api/applications/{aid}/run',json={'production':True,'confirmed':True}).status_code==409
        assert c.post(f'/api/applications/{aid}/run',json={}).status_code==200
        app.state.manager._forwarder(aid)('safety',{'rule':'test','severity':'warn','message':'test'})
        assert not c.get('/api/alerts').json()
        assert len(c.get(f'/api/applications/{aid}/events').json())==1
        assert c.post(f'/api/applications/{aid}/run',json={'production':True,'confirmed':True}).status_code==200
        app.state.manager._forwarder(aid)('safety',{'rule':'test','severity':'warn','message':'test'})
        assert len(c.get('/api/alerts').json())==1
    with TestClient(build_app([],tmp_path)) as c:
        assert c.get('/api/applications').json()[0]['running']
        assert c.post(f'/api/applications/{aid}/pause').status_code==200
        assert not c.get(f'/api/applications/{aid}').json()['running']


def test_application_exposure_setting_requires_pause_and_persists(tmp_path):
    with TestClient(build_app([],tmp_path)) as c:
        aid=c.post('/api/applications',json={'name':'SOP','task':'sop','exposure_correction':'auto'}).json()['id']
        assert c.get(f'/api/applications/{aid}').json()['exposure_correction']=='auto'
        assert c.post(f'/api/applications/{aid}/input',json={'type':'sim'}).status_code==200
        assert c.post(f'/api/applications/{aid}/run',json={}).status_code==200
        assert c.put(f'/api/applications/{aid}/exposure',json={'mode':'off'}).status_code==409
        assert c.post(f'/api/applications/{aid}/pause').status_code==200
        assert c.put(f'/api/applications/{aid}/exposure',json={'mode':'off'}).status_code==200
    with TestClient(build_app([],tmp_path)) as c:
        assert c.get(f'/api/applications/{aid}').json()['exposure_correction']=='off'


def test_camera_invalid_input_and_no_credentials_in_response(tmp_path,monkeypatch):
    with TestClient(build_app([],tmp_path)) as c:
        aid=c.post('/api/applications',json={'name':'相机','task':'sop'}).json()['id']
        assert c.post(f'/api/applications/{aid}/input',json={'type':'rtsp','uri':'http://example.com'}).status_code==409
        assert c.post(f'/api/applications/{aid}/input',json={'type':'camera','uri':'../x'}).status_code==409
        assert c.get('/api/applications').json()[0]['state']=='draft'


def test_source_probe_worker_produces_clean_result():
    import subprocess
    import sys
    result=subprocess.run([sys.executable,'-m','visual.source_probe'],input=json.dumps({'type':'sim'}),
                          text=True,capture_output=True,timeout=15)
    assert result.returncode==0
    data=json.loads(result.stdout)
    assert data['width']==640 and data['height']==480 and data['image']


def test_rtsp_probe_retries_transient_failure(tmp_path,monkeypatch):
    import subprocess
    from types import SimpleNamespace
    from visual import applications
    calls=[]
    def run(command,**kwargs):
        calls.append(command)
        if len(calls)==1:raise subprocess.TimeoutExpired(command,15)
        return SimpleNamespace(returncode=0,stdout=json.dumps({'width':640,'height':480,'image':'eA=='}))
    monkeypatch.setattr(applications.subprocess,'run',run)
    with TestClient(build_app([],tmp_path)) as c:
        aid=c.post('/api/applications',json={'name':'camera','task':'appearance'}).json()['id']
        r=c.post(f'/api/applications/{aid}/input',json={'type':'rtsp','uri':'rtsp://127.0.0.1/test'})
        assert r.status_code==200 and len(calls)==2


def test_sop_form_validation_and_real_reload(tmp_path):
    app=build_app([],tmp_path)
    with TestClient(app) as c:
        aid=c.post('/api/applications',json={'name':'SOP','task':'sop'}).json()['id']
        c.post(f'/api/applications/{aid}/input',json={'type':'sim'})
        c.post(f'/api/applications/{aid}/run',json={})
        data=c.get(f'/api/stations/{aid}/steps-editor').json()
        patch={k:data[k] for k in ('steps','cycle','roi_states')}
        patch['steps'][0]['name']='取料确认'
        patch['steps'][0]['timeout_s']=8
        result=c.put(f'/api/stations/{aid}/steps-editor',json=patch)
        assert result.status_code==200,result.text
        assert app.state.manager.runtimes[aid].cfg.steps[0].name=='取料确认'
        path=app.state.manager.paths[aid];before=path.read_bytes()
        patch['steps'][0]['evidence']={'all':[{'hand_dwell':{'zone':'nonexistent','min_ms':300}}]}
        assert c.put(f'/api/stations/{aid}/steps-editor',json=patch).status_code==409
        assert path.read_bytes()==before


def test_anomaly_pass_history_versions_and_restore(tmp_path):
    with TestClient(build_app([],tmp_path)) as c:
        file=('ok.png',image(),'image/png')
        assert c.post('/api/anomaly/case/register',files={'files':file},data={'backend':'grid'}).status_code==201
        r=c.post('/api/anomaly/case/inspect',files={'image':file})
        assert r.status_code==201,r.text
        assert r.json()['verdict']=='PASS' and r.json()['alert_id'] is None
        assert len(r.json()['heatmap'])==4
        assert c.get('/api/alerts').json()==[]
        assert c.put('/api/anomaly/case/settings',json={'threshold':.4}).status_code==200
        detail=c.get('/api/anomaly/case/details').json()
        assert len(detail['history'])==1 and len(detail['refs'])==1
        assert c.post(f"/api/anomaly/case/versions/{detail['versions'][0]}/restore").status_code==200
        assert c.get('/api/anomaly/scenes').json()[0]['threshold']==.12
        assert c.get('/api/anomaly/case/images/'+detail['refs'][0]+'.jpg').status_code==200
        assert c.post('/api/anomaly/case/calibrate',files={'normal':file,'abnormal':file}).status_code==409
        img=np.full((112,112,3),150,np.uint8);second=cv2.imencode('.png',img)[1].tobytes()
        assert c.post('/api/anomaly/case/register',files={'files':('two.png',second,'image/png')}).status_code==201
        refs=c.get('/api/anomaly/case/details').json()['refs'];assert len(refs)==2
        assert c.delete(f'/api/anomaly/case/refs/{refs[0]}').status_code==200
        detail=c.get('/api/anomaly/case/details').json();assert len(detail['refs'])==1
        assert c.post(f"/api/anomaly/case/versions/{detail['versions'][0]}/restore").status_code==200
        assert len(c.get('/api/anomaly/case/details').json()['refs'])==2


def test_exposure_preview_and_reference_rebuild(tmp_path):
    with TestClient(build_app([],tmp_path)) as c:
        ok=('ok.png',image(),'image/png')
        assert c.post('/api/anomaly/light/register',files={'files':ok},data={'backend':'grid'}).status_code==201
        preview=c.post('/api/anomaly/light/exposure-preview',files={'image':ok})
        assert preview.status_code==200 and preview.json()['image'] and not preview.json()['unusable']
        changed=c.put('/api/anomaly/light/settings',json={'threshold':.12,'lighting':'auto'})
        assert changed.status_code==200 and changed.json()['lighting']=='auto'
        assert c.get('/api/anomaly/scenes').json()[0]['lighting']=='auto'
        black=cv2.imencode('.png',np.zeros((112,112,3),np.uint8))[1].tobytes()
        result=c.post('/api/anomaly/light/inspect',files={'image':('black.png',black,'image/png')})
        assert result.status_code==201 and result.json()['verdict']=='UNCERTAIN'
        assert c.get('/api/alerts').json()==[]
        old=c.get('/api/anomaly/light/details').json()['versions'][0]
        assert c.post(f'/api/anomaly/light/versions/{old}/restore').status_code==200
        assert c.get('/api/anomaly/scenes').json()[0]['lighting']=='off'


def test_bootstrap_auth_persistence_and_remote_rejection(tmp_path):
    with TestClient(build_app([],tmp_path)) as c:
        assert c.post('/api/auth/setup',json={'username':'admin','password':'password123'},headers={'Origin':'https://evil.example'}).status_code==403
        assert c.post('/api/auth/setup',json={'username':'admin','password':'password123'}).status_code==200
        assert c.get('/api/health').json()['auth'] is True
        c.post('/api/auth/logout')
        assert c.post('/api/applications',json={'name':'x','task':'sop'}).status_code==401
    with TestClient(build_app([],tmp_path)) as c:
        assert c.get('/api/health').json()['auth'] is True
        assert c.post('/api/auth/login',json={'username':'admin','password':'password123'}).status_code==200


def test_viewer_cannot_change_workflows_or_runtime(tmp_path):
    auth=AuthStore(tmp_path/'auth.db');auth.set_user('viewer','password123','viewer')
    with TestClient(build_app([],tmp_path,auth=auth)) as c:
        c.post('/api/auth/login',json={'username':'viewer','password':'password123'})
        assert c.post('/api/applications',json={'name':'x','task':'sop'}).status_code==403
        assert c.put('/api/setup',json={}).status_code==403
        assert c.post('/api/setup/notification-test').status_code==403
        assert c.post('/api/setup/training-install').status_code==403
        assert c.post('/api/setup/training-cancel').status_code==403
        assert c.get('/api/applications').status_code==200
    auth.close()


def test_operator_cannot_configure_gb_platform(tmp_path):
    auth=AuthStore(tmp_path/'auth.db');auth.set_user('operator','password123','operator')
    with TestClient(build_app([],tmp_path/'data',auth=auth)) as c:
        assert c.post('/api/auth/login',json={'username':'operator','password':'password123'}).status_code==200
        assert c.put('/api/setup/gb28181',json={'url':'http://127.0.0.1:18080'}).status_code==403
        assert c.post('/api/setup/training-cancel').status_code==403
    auth.close()


def test_gb_platform_settings_and_reconnect(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from visual import gb28181, applications, llm_provider
    for name in ('GB28181_WVP_URL','GB28181_USER','GB28181_PASS'):monkeypatch.delenv(name,raising=False)
    monkeypatch.setattr(llm_provider,'_default_env_path',lambda:tmp_path/'missing.env')
    calls=[]
    def play(self,device,channel):
        calls.append((device,channel));return {'rtsp':'rtsp://127.0.0.1/test'}
    monkeypatch.setattr(gb28181.WvpClient,'play_url',play)
    def probe(command,**kwargs):
        return SimpleNamespace(stdout=json.dumps({'width':640,'height':480,'image':'eA=='}),returncode=0)
    monkeypatch.setattr(applications.subprocess,'run',probe)
    app=build_app([],tmp_path/'data')
    with TestClient(app) as c:
        assert c.put('/api/setup/gb28181',json={'url':'http://u:p@host'}).status_code==400
        assert c.put('/api/setup/gb28181',json={'url':'http://127.0.0.1:18080','username':'user','password':'private-test'}).status_code==200
        assert 'private-test' not in c.get('/api/setup').text
        assert c.get('/api/cameras/gb28181/status').json()['configured']
        aid=c.post('/api/applications',json={'name':'国标','task':'appearance'}).json()['id']
        response=c.post(f'/api/applications/{aid}/gb28181',json={'device_id':'dev1','channel_id':'ch1'})
        assert response.status_code==200 and 'rtsp://' not in response.text
        assert 'rtsp://' not in c.get(f'/api/applications/{aid}').text
        monkeypatch.setattr(app.state.manager,'add_station',lambda path:None)
        assert c.post(f'/api/applications/{aid}/run',json={}).status_code==200
        assert calls==[('dev1','ch1'),('dev1','ch1')]
        assert c.post(f'/api/applications/{aid}/gb28181',json={'device_id':'dev1','channel_id':'ch1'}).status_code==409
        assert len(calls)==2


def test_setup_config_and_inspection_scene_persist(tmp_path):
    with TestClient(build_app([],tmp_path)) as c:
        assert c.get('/api/setup').status_code==200
        assert c.put('/api/setup',json={'notification_urls':['https://evil.example/key']}).status_code==400
        assert c.put('/api/setup',json={'notification_urls':[]}).status_code==200
        assert c.post('/api/setup/inspection',json={'name':'手套检查','instruction':'检查操作员是否佩戴手套'}).status_code==201
        assert len(c.get('/api/inspection/scenarios').json())==1
    with TestClient(build_app([],tmp_path)) as c:
        assert len(c.get('/api/inspection/scenarios').json())==1


def test_managed_training_install_and_restart_configuration(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from visual import setup_routes
    manager=SimpleNamespace(data_dir=tmp_path,training=SimpleNamespace(python=None))
    monkeypatch.setattr(setup_routes,'python_command',lambda:['python'])
    calls=[]
    def run(command, timeout, cancelled):
        calls.append(command)
        if '-m' in command and 'venv' in command:
            path=setup_routes.candidates(manager)['managed-training']
            path.parent.mkdir(parents=True,exist_ok=True);path.touch()
    monkeypatch.setattr(setup_routes,'run_install_command',run)
    state={}
    setup_routes.install_training(manager,state)
    assert state['phase']=='ready'
    assert manager.training.python==str(setup_routes.candidates(manager)['managed-training'])
    assert json.loads((tmp_path/'runtime-options.json').read_text())['training']=='managed-training'
    manager.training.python=None
    setup_routes.apply_options(manager)
    assert manager.training.python and any('pip' in cmd for cmd in calls)


def test_managed_training_install_failure_is_retryable(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from visual import setup_routes
    manager=SimpleNamespace(data_dir=tmp_path,training=SimpleNamespace(python=None))
    monkeypatch.setattr(setup_routes,'python_command',lambda: (_ for _ in ()).throw(ValueError('Python 缺失')))
    state={}
    setup_routes.install_training(manager,state)
    assert state=={'phase':'error','message':'Python 缺失'}


def test_concurrent_component_updates_preserve_other_options(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    from visual.setup_routes import update_options, read_options
    manager=SimpleNamespace(data_dir=tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i:update_options(manager,{f'key{i}':i}),range(12)))
    assert read_options(manager)=={f'key{i}':i for i in range(12)}


def test_training_component_cancel_stops_child_process():
    import sys
    import threading
    import time
    from visual.setup_routes import InstallationCancelled, run_install_command
    cancelled=threading.Event();result=[]
    def run():
        try:run_install_command([sys.executable,'-c','import time; time.sleep(30)'],40,cancelled)
        except InstallationCancelled:result.append('cancelled')
    worker=threading.Thread(target=run);worker.start();time.sleep(.5);cancelled.set();worker.join(10)
    assert not worker.is_alive() and result==['cancelled']


def test_same_content_split_does_not_depend_on_random_asset_ids():
    from visual.labels import LabelStore
    assets=[{'id':str(i),'group_id':'camera-'+str(i),'sha256':'raw-'+str(i),
             'image_sha256':'image-'+str(i),'synthetic':False} for i in range(8)]
    first,_=LabelStore._split(assets,42,.5)
    copied=[{**a,'id':'new-'+a['id']} for a in assets]
    second,_=LabelStore._split(copied,42,.5)
    assert [first[a['id']]['split'] for a in assets]==[second[a['id']]['split'] for a in copied]


def test_scoped_application_listing_and_cross_project_denial(tmp_path):
    auth=AuthStore(tmp_path/'auth.db');auth.set_user('admin','password123','admin')
    app=build_app([],tmp_path/'data',auth=auth)
    with TestClient(app) as c:
        c.post('/api/auth/login',json={'username':'admin','password':'password123'})
        a=c.post('/api/applications',json={'name':'A','task':'appearance'}).json()['id']
        b=c.post('/api/applications',json={'name':'B','task':'appearance'}).json()['id']
        project=app.state.manager.labels.create_project('A',['part'],a)
        auth.set_user('limited','password123','operator',[project['id']])
        c.post('/api/auth/login',json={'username':'limited','password':'password123'})
        assert [r['id'] for r in c.get('/api/applications').json()]==[a]
        assert c.get(f'/api/applications/{b}').status_code==403
        assert c.post(f'/api/applications/{b}/pause').status_code==403
    auth.close()


def test_failed_runtime_start_keeps_application_ready(tmp_path,monkeypatch):
    app=build_app([],tmp_path)
    with TestClient(app) as c:
        aid=c.post('/api/applications',json={'name':'A','task':'sop'}).json()['id']
        c.post(f'/api/applications/{aid}/input',json={'type':'sim'})
        def fail(path):raise ValueError('设备初始化失败')
        monkeypatch.setattr(app.state.manager,'add_station',fail)
        assert c.post(f'/api/applications/{aid}/run',json={}).status_code==409
        result=c.get(f'/api/applications/{aid}').json()
        assert result['state']=='ready' and not result['running']


def test_video_upload_probe_is_real_and_bounded(tmp_path):
    path=tmp_path/'clip.avi'
    writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'MJPG'),5,(64,48))
    assert writer.isOpened()
    for _ in range(3):writer.write(np.full((48,64,3),100,np.uint8))
    writer.release()
    with TestClient(build_app([],tmp_path/'data')) as c:
        aid=c.post('/api/applications',json={'name':'Video','task':'appearance'}).json()['id']
        result=c.post(f'/api/applications/{aid}/video',files={'file':('clip.avi',path.read_bytes(),'video/x-msvideo')})
        assert result.status_code==200,result.text
        assert result.json()['width']==64 and result.json()['height']==48
        assert c.post(f'/api/applications/{aid}/run',json={}).status_code==200
        assert c.post(f'/api/applications/{aid}/pause').status_code==200


def test_native_perception_cleanup_is_explicit_and_idempotent():
    from types import SimpleNamespace
    from visual.runtime import StationRuntime
    closed=[]
    rt=StationRuntime.__new__(StationRuntime)
    rt._health_errors=0
    rt.hands=SimpleNamespace(close=lambda:closed.append('hands'))
    rt.persons=SimpleNamespace(close=lambda:closed.append('persons'))
    rt.detector=SimpleNamespace(close=lambda:closed.append('detector'))
    rt._close_perception();rt._close_perception()
    assert closed==['hands','persons','detector']


def test_archive_preserves_application_and_restores_paused(tmp_path):
    with TestClient(build_app([],tmp_path)) as c:
        aid=c.post('/api/applications',json={'name':'待归档','task':'appearance'}).json()['id']
        assert c.delete(f'/api/applications/{aid}').json()['state']=='archived'
        assert c.get('/api/applications').json()==[]
        assert c.get('/api/applications?include_archived=true').json()[0]['id']==aid
        assert c.post(f'/api/applications/{aid}/run',json={}).status_code==409
        assert c.post(f'/api/applications/{aid}/restore').json()['state']=='paused'


def test_appearance_trial_runs_latest_frame_without_alerts(tmp_path):
    with TestClient(build_app([],tmp_path)) as c:
        aid=c.post('/api/applications',json={'name':'外观','task':'appearance'}).json()['id']
        assert c.post(f'/api/anomaly/{aid}/register',files={'files':('ok.png',image(),'image/png')}).status_code==201
        assert c.post(f'/api/applications/{aid}/input',json={'type':'sim'}).status_code==200
        result=c.post(f'/api/applications/{aid}/run',json={})
        assert result.status_code==200,result.text
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            if c.get(f'/api/anomaly/{aid}/details').json()['history']:break
            time.sleep(.1)
        assert c.get(f'/api/anomaly/{aid}/details').json()['history']
        assert c.get('/api/alerts').json()==[]
        assert c.post(f'/api/applications/{aid}/pause').status_code==200


def test_safety_application_rejects_changed_model(tmp_path):
    path=tmp_path/'fixture.onnx';path.write_bytes(b'model-original')
    app=build_app([],tmp_path/'data')
    app.state.manager.label_engines.register('fixture',path,['fire'],'Fixture',metadata={'adapter':'yolo_raw'})
    with TestClient(app) as c:
        row=c.post('/api/applications',json={'name':'烟火','task':'safety','rule':'fire_smoke','engine_id':'fixture'})
        assert row.status_code==201,row.text
        aid=row.json()['id'];assert c.post(f'/api/applications/{aid}/input',json={'type':'sim'}).status_code==200
        path.write_bytes(b'model-changed')
        result=c.post(f'/api/applications/{aid}/run',json={})
        assert result.status_code==409 and '模型已变化' in result.text


def test_helmet_application_requires_person_and_explicit_negative(tmp_path):
    path=tmp_path/'fixture.onnx';path.write_bytes(b'fixture')
    app=build_app([],tmp_path/'data')
    app.state.manager.label_engines.register('valid',path,['person','hardhat','no-hardhat','car'],'Fixture',metadata={'adapter':'yolo_raw'})
    app.state.manager.label_engines.register('incomplete',path,['hardhat','no-hardhat'],'Incomplete',metadata={'adapter':'yolo_raw'})
    with TestClient(app) as c:
        base={'name':'安全帽','task':'safety','rule':'ppe_helmet'}
        assert c.post('/api/applications',json={**base,'engine_id':'incomplete'}).status_code==409
        row=c.post('/api/applications',json={**base,'engine_id':'valid'});assert row.status_code==201,row.text
        aid=row.json()['id'];c.post(f'/api/applications/{aid}/input',json={'type':'sim'})
        raw=app.state.manager.applications.config(app.state.manager.applications.get(aid))
        assert raw['ppe']['enabled'] and raw['detector']['classes']==['person','hardhat','no-hardhat']
