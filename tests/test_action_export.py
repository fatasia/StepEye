import importlib.util
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('action_export',Path(__file__).parents[1]/'tools/action_export.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def config():
    return {'architectures':['VideoMAEForVideoClassification'],'num_frames':16,'num_channels':3,
            'model_type':'videomae','id2label':{str(i):f'class-{i}' for i in range(400)}}


def test_classification_map_has_fixed_order():
    assert module.class_map(config())[10]=='class-10'


@pytest.mark.parametrize('key,value',[('architectures',['VideoMAEModel']),('num_frames',8),('id2label',{'0':'fake'})])
def test_reject_features_or_wrong_contract(key,value):
    c=config();c[key]=value
    with pytest.raises(ValueError):module.class_map(c)


def test_duplicate_classes_rejected():
    c=config();c['id2label']['1']=c['id2label']['0']
    with pytest.raises(ValueError):module.class_map(c)


def test_streamed_hash(tmp_path):
    import hashlib
    p=tmp_path/'weight';p.write_bytes(b'abc')
    assert module.sha256(p)==hashlib.sha256(b'abc').hexdigest()
