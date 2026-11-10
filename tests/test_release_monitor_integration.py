"""Real app/ledger/reload + tiny ONNX; failure health is explicitly simulated, not camera evidence."""
import hashlib,json,time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from visual.auth import AuthStore
from visual.server import build_app
from visual.runtime import StationRuntime
from test_detector_runtime_adapter import graph

@pytest.fixture
def integrated(tmp_path,monkeypatch):
    # Keep deterministic health: no capture loop. Real runtime construction and manager.reload remain.
    monkeypatch.setattr(StationRuntime,'run',lambda self:None)
    auth=AuthStore(tmp_path/'auth.sqlite')
    for role in ['viewer','operator','admin']:auth.set_user(role,'integration-password',role)
    config=tmp_path/'station.json'
    config.write_text(json.dumps({'station':'monitor-test','source':{'type':'sim'},
        'zones':[{'id':'z','polygon':[[0,0],[1,0],[1,1],[0,1]]}],
        'hands':{'enabled':False},'persons':{'enabled':False},'detector':{'enabled':False},
        'safety':{'enabled':False,'rules':[{'id':'off','kind':'person_absent','zone':'z'}]}}))
    app=build_app([config],tmp_path/'data',auth=auth);manager=app.state.manager
    clock=[time.time()+100];manager.release_monitor.clock=lambda:clock[0]
    with TestClient(app) as client:
        def login(role):assert client.post('/api/auth/login',json={'username':role,'password':'integration-password'}).status_code==200
        def candidate(name):
            directory=tmp_path/name;directory.mkdir();path=graph(directory)
            import onnx
            model=onnx.load(path);model.doc_string=name;onnx.save(model,path)
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            manager.label_engines.register(name,path,['part','other'],name)
            report={'created_at':time.time(),'id':'HE-'+name,'kind':'detection_validation','publication':'not_requested','engine_id':name,
                    'model_sha256':digest,'adapter':'yolo_e2e','evaluation':{'classes':{'part':{},'other':{}}}}
            manager.holdout_evaluations.create(report)
            result=client.post('/api/model-releases',json={'engine_id':name,'model_sha256':digest,'evaluation_id':report['id'],
                'adapter':'yolo_e2e','classes':['part','other']});assert result.status_code==201,result.text
            rid=result.json()['id'];assert client.post(f'/api/model-releases/{rid}/approve').status_code==200
            return rid,path
        def activate(rid):
            response=client.post(f'/api/model-releases/{rid}/activate',json={'scene':'monitor-test'})
            assert response.status_code==200,response.text
            assert response.json()['applied_to_runtime'];return response
        login('admin');old,oldpath=candidate('old');activate(old);new,newpath=candidate('new');activate(new)
        yield client,manager,clock,login,candidate,old,new,oldpath,newpath
    auth.close()


def arm(client,rid,policy=None):
    return client.put('/api/model-release-monitor/monitor-test',json={'release_id':rid,**({'policy':policy} if policy else {})})


def fail_window(manager,clock):
    baseline=clock[0]
    rt=manager.runtimes['monitor-test']
    for n in range(6):
        clock[0]=baseline+n
        rt._health_running=False;rt._health_errors=n;rt._health_last_frame=None
        manager.monitor_releases()


def test_permission_defaults_and_real_reload(integrated):
    client,m,clock,login,_,old,new,oldpath,_=integrated
    for role in ['viewer','operator']:
        login(role);assert arm(client,new).status_code==403
    login('admin');assert arm(client,new).json()['status']=='disabled'
    fail_window(m,clock);assert m.model_releases.active_for('monitor-test')['id']==new
    policy={'enabled':True,'window_s':5,'min_samples':3,'max_frame_age_s':1,'max_error_delta':2}
    assert arm(client,new,policy).status_code==200
    fail_window(m,clock)
    state=client.get('/api/model-release-monitor/monitor-test').json()
    assert state['status']=='rolled_back' and state['runtime_applied']
    assert m.model_releases.active_for('monitor-test')['id']==old
    rt=m.runtimes['monitor-test'];assert rt.release_id==old
    assert Path(rt.cfg.detector.model).resolve()==oldpath.resolve()
    # The restored model is a runnable constant graph, not only a config string.
    import numpy as np
    from visual.detector_registry import create_backend
    backend=create_backend(rt.cfg.detector.model,rt.cfg.detector.class_names,.25,adapter=rt.cfg.detector.adapter)
    try:assert len(backend.detect(np.zeros((640,640,3),np.uint8)))==2
    finally:backend.close()


def test_corrupt_candidate_cannot_change_active_ledger(integrated):
    client,m,clock,login,candidate,old,new,_,_=integrated
    rid,path=candidate('corrupt');path.write_bytes(b'corrupt')
    response=client.post(f'/api/model-releases/{rid}/activate',json={'scene':'monitor-test'})
    assert response.status_code==400
    assert m.model_releases.active_for('monitor-test')['id']==new
    assert m.model_releases.get(rid)['status']=='approved'


def test_corrupt_fallback_refuses_manual_and_auto_rollback(integrated):
    client,m,clock,login,_,old,new,oldpath,_=integrated
    oldpath.write_bytes(b'tampered')
    response=client.post(f'/api/model-releases/{new}/rollback')
    assert response.status_code==400
    arm(client,new,{'enabled':True,'window_s':5,'min_samples':3,'max_frame_age_s':1,'max_error_delta':2})
    fail_window(m,clock)
    assert m.release_monitor.get('monitor-test')['status']=='blocked'
    assert m.model_releases.active_for('monitor-test')['id']==new
    assert m.runtimes['monitor-test'].release_id==new



def test_busy_reload_pending_then_actual_retry(integrated,monkeypatch):
    from fastapi import HTTPException
    client,m,clock,login,_,old,new,_,_=integrated
    real_reload=m.reload;attempts=[]
    def busy_once(scene):
        attempts.append(scene)
        if len(attempts)==1:raise HTTPException(409,'simulated cycle busy')
        return real_reload(scene)
    monkeypatch.setattr(m,'reload',busy_once)
    arm(client,new,{'enabled':True,'window_s':5,'min_samples':3,'max_frame_age_s':1,'max_error_delta':2})
    fail_window(m,clock)
    assert m.release_monitor.get('monitor-test')['status']=='pending_runtime_reload'
    revision=m.model_releases.get(new)['revision']
    assert m.runtimes['monitor-test'].release_id==new
    clock[0]+=1;m.monitor_releases()
    assert m.release_monitor.get('monitor-test')['status']=='rolled_back'
    assert m.runtimes['monitor-test'].release_id==old
    assert m.model_releases.get(new)['revision']==revision
