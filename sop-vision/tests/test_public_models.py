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
