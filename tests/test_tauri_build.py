import json
from pathlib import Path

import pytest

from deploy import build_tauri
from deploy.runtime_provenance import write_provenance


def test_stage_uses_new_runtime_without_touching_desktop(tmp_path, monkeypatch):
    root=tmp_path/'source'
    shell=root/'desktop/src-tauri'
    (shell/'runtime').mkdir(parents=True)
    (shell/'runtime/old').write_text('old')
    (shell/'target').mkdir()
    (shell/'target/cache').write_text('cache')
    (shell/'tauri.conf.json').write_text('{"version":"0.9.0"}')
    (root/'desktop/ui').mkdir()
    (root/'desktop/ui/index.html').write_text('ui')
    (root/'deploy').mkdir()
    (root/'deploy/runtime-assets.json').write_text(json.dumps({'forbidden_parts':['__pycache__'],'forbidden_suffixes':['.pyc']}))
    for name in ('build_runtime.py','runtime_provenance.py','runtime_build.spec','runtime_entry.py','requirements-runtime.txt'):
        (root/'deploy'/name).write_text(name)
    (root/'requirements-training.txt').write_text('training')
    (root/'web').mkdir();(root/'web/index.html').write_text('web')
    runtime=tmp_path/'built-runtime';(runtime/'_internal').mkdir(parents=True)
    (runtime/'stepeye-runtime.exe').write_bytes(b'new')
    (runtime/'_internal/web').mkdir();(runtime/'_internal/web/index.html').write_text('web')
    write_provenance(root,runtime)
    monkeypatch.setattr(build_tauri,'ROOT',root)
    output=tmp_path/'output'
    build_tauri.stage(runtime,output,'0.10.0')
    assert (output/'src-tauri/runtime/stepeye-runtime.exe').read_bytes()==b'new'
    assert not (output/'src-tauri/target').exists()
    assert (shell/'runtime/old').read_text()=='old'
    assert json.loads((output/'src-tauri/tauri.conf.json').read_text())['version']=='0.10.0'
    with pytest.raises(ValueError): build_tauri.stage(runtime,output,'0.10.0')
    with pytest.raises(ValueError): build_tauri.stage(runtime,root/'build','0.10.0')
    (root/'web/index.html').write_text('changed')
    with pytest.raises(ValueError,match='源码不一致'):build_tauri.stage(runtime,tmp_path/'stale','0.10.0')
    (root/'web/index.html').write_text('web')
    (runtime/'_internal/web/index.html').write_text('tampered')
    with pytest.raises(ValueError,match='资源与源码不一致'):build_tauri.stage(runtime,tmp_path/'modified','0.10.0')
    (runtime/'_internal/web/index.html').write_text('web')
    (runtime/'stepeye-runtime.exe').write_bytes(b'changed')
    with pytest.raises(ValueError,match='运行程序与构建指纹不一致'):build_tauri.stage(runtime,tmp_path/'binary','0.10.0')
