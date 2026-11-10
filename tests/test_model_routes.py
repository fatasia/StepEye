import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from PIL import Image
import pytest
from fastapi.testclient import TestClient
from visual.auth import AuthStore
from visual.label_jobs import LabelEngines
from visual.labels import LabelError
from visual.model_routes import ModelTrials
from visual.server import build_app

def image():
    b=io.BytesIO(); Image.new('RGB',(64,64),'gray').save(b,'PNG'); b.seek(0); return b

@pytest.fixture
def models(tmp_path):
    onnx=pytest.importorskip('onnx')
    import numpy as np
    pred=np.zeros((1,5,10),dtype=np.float32); pred[0,:,0]=[320,320,320,320,.9]
    node=onnx.helper.make_node('Constant',[],['pred'],value=onnx.numpy_helper.from_array(pred,'prediction'))
    graph=onnx.helper.make_graph([node],'fixture',[onnx.helper.make_tensor_value_info('images',onnx.TensorProto.FLOAT,[1,3,640,640])],[onnx.helper.make_tensor_value_info('pred',onnx.TensorProto.FLOAT,[1,5,10])])
    model=onnx.helper.make_model(graph,opset_imports=[onnx.helper.make_opsetid('',13)]); model.ir_version=8
    path=tmp_path/'model.onnx'; onnx.save(model,path)
    engines=LabelEngines(); engines.register('fixture',path,['part'],'Test model')
    engines.register('missing',tmp_path/'missing.onnx',['part'],'Not installed')
    return engines

def test_real_native_trial_no_persistence(models):
    trial=ModelTrials(models); before=set(Path(tempfile.gettempdir()).glob('stepeye-model-trial-*'))
    r=trial.trial('fixture',image())
    assert r['width']==64 and r['height']==64 and r['provider']=='CPUExecutionProvider'
    assert r['targets'][0]['xyxy_norm']==pytest.approx([.25,.25,.75,.75])
    assert r['targets'][0]['label_id']==0 and r['validation']=='unverified'
    assert r['elapsed_ms']>0 and len(r['model_sha256'])==64
    assert set(Path(tempfile.gettempdir()).glob('stepeye-model-trial-*'))==before
    assert trial._slot.acquire(False); trial._slot.release()

def test_catalog_and_unavailable(models):
    trial=ModelTrials(models)
    assert 'model_path' not in json.dumps(trial.catalog())
    assert all(r['validation']=='unverified' for r in trial.catalog())
    for model,status in [('unknown',404),('missing',409)]:
        with pytest.raises(LabelError) as e: trial.trial(model,image())
        assert e.value.status==status

def test_busy_and_bad_upload_release(models):
    trial=ModelTrials(models); trial._slot.acquire()
    with pytest.raises(LabelError) as e: trial.trial('fixture',image())
    assert e.value.status==429; trial._slot.release()
    for raw in [b'',b'not an image',b'x'*(20*1024*1024+1)]:
        with pytest.raises(LabelError): trial.trial('fixture',io.BytesIO(raw))
        assert trial._slot.acquire(False); trial._slot.release()
    for threshold in [float('nan'),0,1]:
        with pytest.raises(LabelError): trial.trial('fixture',image(),threshold)

def test_timeout_kills_real_child(models,monkeypatch):
    original=subprocess.Popen; processes=[]
    def hung(args,**kwargs):
        p=original([sys.executable,'-c','import time; time.sleep(60)'],**kwargs); processes.append(p); return p
    monkeypatch.setattr('visual.model_routes.subprocess.Popen',hung)
    trial=ModelTrials(models,timeout_s=.1)
    with pytest.raises(LabelError) as e: trial.trial('fixture',image())
    assert e.value.status==504 and processes[0].poll() is not None
    assert trial._slot.acquire(False); trial._slot.release()

def test_routes_roles_creation_and_mcp_denied(tmp_path,models):
    auth=AuthStore(tmp_path/'auth.db')
    for role in ['admin','operator','viewer']: auth.set_user(role,'test-password',role)
    app=build_app([],tmp_path/'data',auth=auth)
    for key,model in models._models.items(): app.state.manager.label_engines.register(key,Path(model['model_path']),model['classes'],model['name'])
    try:
        with TestClient(app) as c:
            def login(role): c.post('/api/auth/login',json={'username':role,'password':'test-password'})
            login('admin')
            token=c.post('/api/mcp/tokens',json={'name':'fixture','project_ids':[],'scopes':['read']}).json()['token']
            assert c.get('/api/models',headers={'Authorization':'Bearer '+token}).status_code==403
            login('viewer')
            assert c.get('/api/models').status_code==200
            assert c.post('/api/models/fixture/project',json={}).status_code==403
            assert c.post('/api/models/fixture/trial',files={'file':('a.png',image().getvalue())}).status_code==403
            login('operator')
            p=c.post('/api/models/missing/project',json={'name':'import first'})
            assert p.status_code==201 and p.json()['classes']==['part']
            assert c.post('/api/models/fixture/project',json={'model_path':'anything'}).status_code==422
            r=c.post('/api/models/fixture/trial',files={'file':('a.png',image().getvalue())})
            assert r.status_code==200,r.text
            assert r.json()['targets'][0]['label_id']==0
            assert app.state.manager.labels.list_assets(p.json()['id'])['total']==0
    finally: auth.close()

def test_invalid_worker_result_rejected_and_slot_released(models,monkeypatch):
    original=subprocess.Popen
    def invalid(args,**kwargs):
        return original([sys.executable,'-c','print(\'{"targets":[{"label_id":99,"xyxy_norm":[0,0,1,1]}]}\')'],**kwargs)
    monkeypatch.setattr('visual.model_routes.subprocess.Popen',invalid)
    trial=ModelTrials(models)
    with pytest.raises(LabelError) as e: trial.trial('fixture',image())
    assert e.value.status==422
    assert trial._slot.acquire(False); trial._slot.release()

def test_model_change_rejected(models,monkeypatch):
    original=subprocess.Popen
    def changed(args,**kwargs):
        p=original(args,**kwargs)
        # mutate while the subprocess is running; any invalid model or hash failure rejects it.
        Path(models._models['fixture']['model_path']).write_bytes(b'changed')
        return p
    monkeypatch.setattr('visual.model_routes.subprocess.Popen',changed)
    with pytest.raises(LabelError) as e: ModelTrials(models).trial('fixture',image())
    assert e.value.status in (409,422)
