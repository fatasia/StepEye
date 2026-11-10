"""隔离环境助手的路径/命令边界；不在测试中安装框架。"""
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from tools import train_setup


def test_current_main_environment_is_never_modified():
    with pytest.raises(ValueError,match="主Python"): train_setup.environment(Path(sys.prefix))


def test_existing_non_venv_directory_is_not_repurposed(tmp_path):
    root=tmp_path/"ordinary";root.mkdir()
    with pytest.raises(ValueError,match="不是虚拟环境"): train_setup.environment(root)


def test_borrowing_writes_only_new_env_and_pip_targets_its_python(tmp_path,monkeypatch):
    root=tmp_path/"training";root.mkdir();(root/"pyvenv.cfg").write_text("fixture")
    borrowed=tmp_path/"host";borrowed.mkdir();(borrowed/"torch").mkdir()
    calls=[]
    monkeypatch.setattr(train_setup.subprocess,"run",lambda args,**kwargs:calls.append((args,kwargs)))
    python=train_setup.environment(root,borrow_site=borrowed)
    assert calls[0][0][0]==str(python)
    assert "--ignore-installed" in calls[0][0] and "numpy==2.5.4" in calls[0][0]
    assert calls[0][1]["env"]["PYTHONDONTWRITEBYTECODE"]=="1"
    assert not list(borrowed.rglob("*.pth"))
    assert len(list(root.rglob("stepeye-readonly-host.pth")))==1


def test_inspection_records_explicit_provenance_and_rejects_failed_import(tmp_path,monkeypatch):
    data={"python":"fixture","prefix":"isolated","versions":{"torch":"fixture"},"gpu_available":False}
    monkeypatch.setattr(train_setup.subprocess,"run",lambda *a,**k:SimpleNamespace(returncode=0,stdout=json.dumps(data),stderr=""))
    output=tmp_path/"report.json"
    assert train_setup.inspect(Path("isolated-python"),output)==data
    assert json.loads(output.read_text())==data
    monkeypatch.setattr(train_setup.subprocess,"run",lambda *a,**k:SimpleNamespace(returncode=1,stdout="",stderr="private diagnostic"))
    with pytest.raises(RuntimeError,match="隔离检查"): train_setup.inspect(Path("isolated-python"),output)
