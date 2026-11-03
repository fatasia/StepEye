"""Manifest assets are evaluation-only and pinned through every prediction."""
import hashlib
import json
import pytest
from visual.label_jobs import LabelEngines
from visual.labels import LabelError
from visual.public_models import register_public_models


def manifest(tmp_path, **changes):
    folder = tmp_path / 'models/public'
    folder.mkdir(parents=True)
    path = folder / 'sample.onnx'
    path.write_bytes(b'test asset; not executed')
    entry = dict(id='public-test', name='test', model_path='models/public/sample.onnx',
                 classes=['object'], sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                 adapter_compatible=True, license_status='not_declared', source={'revision': 'fixed'})
    entry.update(changes)
    (folder / 'safety-models.json').write_text(json.dumps({'schema_version': 1, 'models': [entry]}))
    return path


def test_public_asset_pinned_and_evaluation_only(tmp_path):
    path = manifest(tmp_path)
    engines = LabelEngines()
    assert register_public_models(engines, tmp_path) == 1
    item = engines.snapshot('public-test')
    assert item['usage'] == 'evaluation_only'
    assert item['commercial_review_required']
    path.write_bytes(b'replaced')
    with pytest.raises(LabelError, match='指纹'):
        engines.snapshot('public-test')


@pytest.mark.parametrize('changes', [dict(sha256='wrong'), dict(adapter_compatible=False),
                                      dict(model_path='../outside.onnx')])
def test_invalid_public_asset_is_not_registered(tmp_path, changes):
    manifest(tmp_path, **changes)
    engines = LabelEngines()
    assert register_public_models(engines, tmp_path) == 0
    assert engines.list() == []


def test_specialized_detector_catalog_and_no_classifier(tmp_path):
    """The extra manifest reuses the same pinned, evaluation-only detector catalog."""
    from visual.model_routes import ModelTrials
    path = manifest(tmp_path)
    folder=path.parent
    (folder/'safety-models.json').unlink()
    base=dict(name='specialized',model_path='models/public/sample.onnx',
              sha256=hashlib.sha256(path.read_bytes()).hexdigest(),adapter='yolo_raw',
              adapter_compatible=True,license_status='AGPL-3.0; data pending',
              source={'revision':'fixed'},task='detect',native_input=[1,3,640,640])
    entries=[base|dict(id='ppe',classes=['gloves','goggles','safety shoes'],native_output=[1,7,8400]),
             base|dict(id='weld',classes=['Bad Weld','Good Weld','Defect'],native_output=[1,7,8400]),
             base|dict(id='roi-classifier',task='classify',classes=['Golden','SolderInsufficient'],native_output=[1,2]),
             base|dict(id='misdeclared-classifier',classes=['Golden','SolderInsufficient'],native_output=[1,2])]
    (folder/'specialized-detection-models.json').write_text(json.dumps({'schema_version':1,'models':entries}))
    engines=LabelEngines()
    assert register_public_models(engines,tmp_path)==2
    catalog=ModelTrials(engines).catalog()
    assert {r['id'] for r in catalog}=={'ppe','weld'}
    assert all(r['task']=='detect' and r['usage']=='evaluation_only' and r['commercial_review_required'] for r in catalog)
    assert all(r['license_status']=='AGPL-3.0; data pending' and 'model_path' not in r for r in catalog)
    path.write_bytes(b'changed')
    with pytest.raises(LabelError,match='指纹'): engines.snapshot('weld')


def test_candidate_audit_files_are_not_automatically_loaded(tmp_path):
    path=manifest(tmp_path)
    original=json.loads((path.parent/'safety-models.json').read_text())
    (path.parent/'safety-models.json').unlink()
    for name in ['ppe-candidates.json','joint-candidates.json','specialized-candidates.json']:
        (path.parent/name).write_text(json.dumps(original))
    engines=LabelEngines()
    assert register_public_models(engines,tmp_path)==0
    assert engines.list()==[]
