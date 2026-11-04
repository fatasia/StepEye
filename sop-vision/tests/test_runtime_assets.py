"""Runtime staging contains explicit inference assets, not local developer files."""
import json
import pytest
from deploy import build_runtime as build


def test_runtime_asset_plan_and_hashes(tmp_path, monkeypatch):
    root = tmp_path / 'app'
    for name, contents in {
        'web/index.html': b'page', 'web/.env': b'not-for-distribution',
        'visual/module.py': b'pass', 'visual/__pycache__/module.pyc': b'cache',
        'models/core.onnx': b'model', 'models/.venv-export/secret.txt': b'dev',
        'deploy/monibuca/config.yml': b'api: 8080',
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
    assert names == {'web/index.html','visual/module.py','models/core.onnx',
                     'deploy/monibuca/config.yml','deploy/monibuca/monibuca_windows_amd64.exe'}
    stage = tmp_path / 'stage'
    build.stage_assets(plan, stage)
    assert build.verify_assets(plan, stage)['mismatches'] == []
    (stage / 'models/core.onnx').write_bytes(b'changed')
    with pytest.raises(ValueError, match='mismatch'):
        build.verify_assets(plan, stage)


def test_staging_rejects_destination_escape(tmp_path):
    with pytest.raises(ValueError, match='escape'):
        build.stage_assets({'assets':[{'target':'../outside'}]}, tmp_path / 'stage')
