"""Runtime staging contains explicit inference assets, not local developer files."""
import json
import pytest
from deploy import build_runtime as build


def test_runtime_asset_plan_and_hashes(tmp_path, monkeypatch):
    root = tmp_path / 'app'
    for name, contents in {
        'web/index.html': b'page', 'web/.env': b'not-for-distribution',
        'visual/module.py': b'pass', 'visual/documents_LICENSE.txt': b'MIT attribution', 'visual/__pycache__/module.pyc': b'cache',
        'models/core.onnx': b'model', 'models/.venv-export/secret.txt': b'dev',
        'deploy/monibuca/config.yml': b'api: 8080',
        'requirements-training.txt': b'torch==1',
    }.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(contents)
    media = tmp_path / 'media.exe'
    media.write_bytes(b'native-media')
    policy = json.loads(build.POLICY.read_text(encoding='utf8'))
    policy.update(source_trees=['web','visual'],core_models=['models/core.onnx'],
                  catalogs=[],extra_models=[],mechanism_bundles=[])
    file = tmp_path / 'policy.json'
    file.write_text(json.dumps(policy))
    monkeypatch.setattr(build, 'POLICY', file)
    plan = build.asset_plan(root, monibuca=media)
    names = {row['target'] for row in plan['assets']}
    assert names == {'web/index.html','visual/module.py','visual/documents_LICENSE.txt','models/core.onnx',
                     'deploy/monibuca/config.yml','deploy/monibuca/monibuca_windows_amd64.exe',
                     'deploy/requirements-training.txt'}
    stage = tmp_path / 'stage'
    build.stage_assets(plan, stage)
    assert build.verify_assets(plan, stage)['mismatches'] == []
    (stage / 'models/core.onnx').write_bytes(b'changed')
    with pytest.raises(ValueError, match='mismatch'):
        build.verify_assets(plan, stage)


def test_staging_rejects_destination_escape(tmp_path):
    with pytest.raises(ValueError, match='escape'):
        build.stage_assets({'assets':[{'target':'../outside'}]}, tmp_path / 'stage')


def test_packaging_keeps_new_runnable_model_catalogs():
    policy = json.loads(build.POLICY.read_text(encoding='utf8'))
    assert {'glue-models.json', 'pin-models.json'} <= set(policy['optional_quality_catalogs'])
    assert 'safety-training-candidates.json' not in policy['catalogs']


def test_hidden_module_filter_keeps_runtime_not_library_tests():
    import ast
    tree = ast.parse((build.ROOT/'deploy/runtime_build.spec').read_text(encoding='utf8'))
    definition = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'runtime_module')
    namespace = {}
    exec(compile(ast.Module(body=[definition], type_ignores=[]), '<runtime-filter>', 'exec'), namespace)
    keep = namespace['runtime_module']
    assert keep('mediapipe.tasks.python.vision.hand_landmarker')
    assert keep('visual.training_worker')
    assert not keep('mediapipe.tasks.python.test.vision.hand_landmarker_test')
    assert not keep('mediapipe.tasks.python.benchmark.model_benchmark')
    spec=(build.ROOT/'deploy/runtime_build.spec').read_text(encoding='utf8')
    assert "collect_data_files('apprise', include_py_files=True)" in spec


def test_optional_quality_weights_require_explicit_inclusion(tmp_path, monkeypatch):
    import hashlib
    root = tmp_path/'app'
    folder = root/'models/public'; folder.mkdir(parents=True)
    (root/'deploy/monibuca').mkdir(parents=True)
    (root/'deploy/monibuca/config.yml').write_text('api: 8080')
    (root/'requirements-training.txt').write_text('torch==1')
    media = tmp_path/'media.exe'; media.write_bytes(b'media')
    weight = folder/'pin.onnx'; weight.write_bytes(b'pin-model')
    catalog = {'models':[{'model_path':'models/public/pin.onnx','sha256':hashlib.sha256(weight.read_bytes()).hexdigest()}]}
    (folder/'pin-models.json').write_text(json.dumps(catalog))
    policy = json.loads(build.POLICY.read_text(encoding='utf8'))
    policy.update(source_trees=[],core_models=[],catalogs=[],optional_quality_catalogs=['pin-models.json'],extra_models=[],mechanism_bundles=[])
    path = tmp_path/'policy.json'; path.write_text(json.dumps(policy))
    monkeypatch.setattr(build,'POLICY',path)
    minimal = build.asset_plan(root,monibuca=media)
    assert 'models/public/pin-models.json' in {r['target'] for r in minimal['assets']}
    assert 'models/public/pin.onnx' not in {r['target'] for r in minimal['assets']}
    assert {'target':'models/public/pin.onnx','reason':'optional_quality_extension'} in minimal['omitted_optional_assets']
    expanded = build.asset_plan(root,monibuca=media,include_quality_extensions=True)
    assert 'models/public/pin.onnx' in {r['target'] for r in expanded['assets']}
    assert expanded['quality_extensions_included'] is True
