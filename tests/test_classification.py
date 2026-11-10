"""Classifier image contracts, short-lived native workers and real authenticated app routing."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image
import pytest
from fastapi.testclient import TestClient

from visual.auth import AuthStore
from visual.classification import ClassifierRegistry, ClassifierTrials, register_public_classifiers
from visual.classification_predict import prepare
from visual.labels import LabelError
from visual.server import build_app


def image():
    stream=io.BytesIO();Image.new('RGB',(20,30),(128,64,32)).save(stream,'PNG');stream.seek(0);return stream


def model(tmp_path,shape=None,output_shape=None,logits=None,dtype=None):
    onnx=pytest.importorskip('onnx');pytest.importorskip('onnxruntime')
    logits=np.array([[1000.,1002.]] if logits is None else logits,dtype=np.float32)
    node=onnx.helper.make_node('Constant',[],['logits'],value=onnx.numpy_helper.from_array(logits,'output'))
    graph=onnx.helper.make_graph([node],'classification-fixture',
        [onnx.helper.make_tensor_value_info('images',dtype or onnx.TensorProto.FLOAT,shape or [1,1,224,224])],
        [onnx.helper.make_tensor_value_info('logits',onnx.TensorProto.FLOAT,output_shape or list(logits.shape))])
    graph_model=onnx.helper.make_model(graph,opset_imports=[onnx.helper.make_opsetid('',13)]);graph_model.ir_version=8
    path=tmp_path/'classifier.onnx';onnx.save(graph_model,path);return path


def registry(path,preprocessing='gray224',classes=None):
    result=ClassifierRegistry();result.register('classifier-fixture',path,classes or ['OK','KO'],'Fixture classifier',
        preprocessing=preprocessing,metadata={'expected_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'license_status':'test fixture only','source':{'revision':'fixture'}})
    return result


def test_preprocessing_explicit_gray_and_imagenet_contracts():
    source=Image.new('L',(400,100),128)
    gray=prepare(source,'gray224')
    assert gray.shape==(1,1,224,224) and gray.dtype==np.float32
    assert np.allclose(gray,128/255) and gray.flags.c_contiguous
    rgb=prepare(source,'imagenet_center224')
    assert rgb.shape==(1,3,224,224) and rgb.dtype==np.float32
    assert rgb[0,:,10,10]==pytest.approx([(128/255-.485)/.229,(128/255-.456)/.224,(128/255-.406)/.225])
    with pytest.raises(ValueError):prepare(source,'auto')


@pytest.mark.parametrize('size',[(20000,1),(1,20000)])
def test_extreme_aspect_ratio_rejected_before_imagenet_resize(size,monkeypatch):
    source=Image.new('RGB',size)
    monkeypatch.setattr(Image.Image,'resize',lambda *a,**k:pytest.fail('must reject before large resize'))
    with pytest.raises(ValueError,match='pixel budget'):prepare(source,'imagenet_center224')


def test_real_gray_worker_softmax_no_boxes_and_clean_exit(tmp_path):
    trials=ClassifierTrials(registry(model(tmp_path)))
    before=set(Path(tempfile.gettempdir()).glob('stepeye-classify-trial-*'))
    result=trials.trial('classifier-fixture',image())
    assert result['task']=='classify' and result['predicted_label']=='KO'
    assert result['confidence']==pytest.approx(.8807970779)
    assert result['probabilities']==[{'label':'OK','score':pytest.approx(.119202922)}, {'label':'KO','score':pytest.approx(.880797078)}]
    assert result['width']==20 and result['height']==30 and result['elapsed_ms']>0
    assert not {'targets','boxes','verdict'} & result.keys()
    assert result['validation']=='unverified' and result['provider']=='CPUExecutionProvider'
    assert set(Path(tempfile.gettempdir()).glob('stepeye-classify-trial-*'))==before
    assert trials._slot.acquire(False);trials._slot.release()


@pytest.mark.parametrize('options,classes',[
    ({'shape':[1,3,224,224]},None), ({'shape':[1,1,256,224]},None),
    ({'shape':['batch',1,224,224]},None), ({'output_shape':[1,2,1],'logits':[[[0.],[1.]]]},None),
    ({},['A','B','C']), ({'logits':[[float('nan'),0.]]},None), ({'logits':[[float('inf'),0.]]},None),
])
def test_wrong_shape_classcount_or_invalid_logits_rejected(tmp_path,options,classes):
    trials=ClassifierTrials(registry(model(tmp_path,**options),classes=classes))
    with pytest.raises(LabelError) as error:trials.trial('classifier-fixture',image())
    assert error.value.status==422
    assert trials._slot.acquire(False);trials._slot.release()


def test_imagenet_dynamic_batch_only_is_supported(tmp_path):
    path=model(tmp_path,shape=['batch',3,224,224],output_shape=['batch',2])
    result=ClassifierTrials(registry(path,'imagenet_center224')).trial('classifier-fixture',image())
    assert result['predicted_label']=='KO' and result['task']=='classify'


def test_timeout_kills_real_worker_and_releases_slot(tmp_path,monkeypatch):
    trials=ClassifierTrials(registry(model(tmp_path)),timeout_s=.1);original=subprocess.Popen;children=[]
    def hung(args,**kwargs):
        child=original([sys.executable,'-c','import time;time.sleep(60)'],**kwargs);children.append(child);return child
    monkeypatch.setattr('visual.classification.subprocess.Popen',hung)
    with pytest.raises(LabelError) as error:trials.trial('classifier-fixture',image())
    assert error.value.status==504 and children[0].poll() is not None
    assert trials._slot.acquire(False);trials._slot.release()


@pytest.mark.parametrize('probabilities',[
    [{'label':'KO','score':.5},{'label':'OK','score':.5}],
    [{'label':'OK','score':True},{'label':'KO','score':False}],
    [{'label':'OK','score':.9},{'label':'KO','score':.9}],
    [{'label':'OK','score':.5}],
])
def test_bad_child_response_rejected(tmp_path,monkeypatch,probabilities):
    trials=ClassifierTrials(registry(model(tmp_path)));original=subprocess.Popen
    def bad(args,**kwargs):
        payload=json.dumps({'probabilities':probabilities})
        return original([sys.executable,'-c',f'print({payload!r})'],**kwargs)
    monkeypatch.setattr('visual.classification.subprocess.Popen',bad)
    with pytest.raises(LabelError) as error:trials.trial('classifier-fixture',image())
    assert error.value.status==422


def test_asset_change_busy_upload_and_registry_validation(tmp_path):
    path=model(tmp_path);models=registry(path);trials=ClassifierTrials(models)
    assert 'model_path' not in json.dumps(trials.catalog())
    assert trials.catalog()[0]['task']=='classify' and trials.catalog()[0]['usage']=='evaluation_only'
    trials._slot.acquire()
    with pytest.raises(LabelError) as error:trials.trial('classifier-fixture',image())
    assert error.value.status==429;trials._slot.release()
    with pytest.raises(LabelError):trials.trial('classifier-fixture',io.BytesIO(b'not an image'))
    path.write_bytes(b'changed')
    with pytest.raises(LabelError,match='指纹'):trials.trial('classifier-fixture',image())
    with pytest.raises(LabelError):models.register('bad',path,['one'],'bad',preprocessing='gray224')
    with pytest.raises(LabelError):models.register('bad',path,['A','B'],'bad',preprocessing='auto')


def test_public_manifest_hash_path_preprocessing_and_task(tmp_path):
    public=tmp_path/'models/public';public.mkdir(parents=True);path=public/'example.onnx';path.write_bytes(b'not executed')
    entry=dict(id='public-classifier',name='Wetfloor',task='classify',model_path='models/public/example.onnx',
        bytes=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),classes=['dry','wet'],
        preprocessing='imagenet_center224',license_status='test only',source={'revision':'fixed'})
    manifest=public/'classification-models.json'
    for change,expected in [({},1),({'task':'detect'},0),({'preprocessing':'auto'},0),({'sha256':'wrong'},0),({'model_path':'../outside.onnx'},0)]:
        manifest.write_text(json.dumps({'schema_version':1,'models':[entry|change]}))
        result=ClassifierRegistry();assert register_public_classifiers(result,tmp_path)==expected


def test_real_app_auth_merged_catalog_and_project_rejected(tmp_path):
    path=model(tmp_path);auth=AuthStore(tmp_path/'auth.db')
    for role in ('operator','viewer'):auth.set_user(role,'test-password',role)
    app=build_app([],tmp_path/'data',auth=auth)
    app.state.manager.classifiers.register('classifier-fixture',path,['OK','KO'],'Battery fixture',preprocessing='gray224')
    try:
        with TestClient(app) as client:
            assert client.get('/api/models').status_code==401
            client.post('/api/auth/login',json={'username':'viewer','password':'test-password'})
            rows=client.get('/api/models').json();entry=next(r for r in rows if r['id']=='classifier-fixture')
            assert entry['task']=='classify' and entry['preprocessing']=='gray224'
            assert 'classifier-fixture' not in [r['id'] for r in app.state.manager.label_engines.list()]
            assert client.post('/api/models/classifier-fixture/trial',files={'file':('image.png',image().getvalue())}).status_code==403
            client.post('/api/auth/login',json={'username':'operator','password':'test-password'})
            result=client.post('/api/models/classifier-fixture/trial',files={'file':('image.png',image().getvalue())})
            assert result.status_code==200 and result.json()['task']=='classify' and 'targets' not in result.json()
            denied=client.post('/api/models/classifier-fixture/project',json={})
            assert denied.status_code==409 and app.state.manager.labels.list_projects()==[]
    finally:auth.close()
