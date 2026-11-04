"""Frozen worker dispatch contract without loading models or launching workers."""
import importlib.util
from pathlib import Path
import sys
import pytest


@pytest.fixture
def entry():
    path=Path(__file__).resolve().parents[1]/'deploy/runtime_entry.py'
    spec=importlib.util.spec_from_file_location('runtime_entry_under_test',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('name',[
    'visual.action_predict','visual.label_predict','visual.classification_predict',
    'visual.gauge_worker','visual.holdout_worker','visual.mechanism_valve_worker',
    'visual.mechanism_rul_worker','visual.notifications','visual.code_reader',
    'visual.training_worker','visual.documents_parse'])
def test_allowlisted_worker_arguments_and_restore(entry,monkeypatch,name):
    original=sys.argv;calls=[]
    def run(module,**kwargs):calls.append((module,kwargs,list(sys.argv)))
    monkeypatch.setattr(entry.runpy,'run_module',run)
    assert entry.dispatch(['-m',name,'--request','with spaces.json'])==0
    assert calls==[(name,{'run_name':'__main__','alter_sys':True},[name,'--request','with spaces.json'])]
    assert sys.argv is original


@pytest.mark.parametrize('args',[['-m'],['-m','os'],['-m','visual.server'],['-m','visual.fake_worker']])
def test_unknown_module_rejected_without_execution(entry,monkeypatch,args):
    def fail(*args,**kwargs):pytest.fail('Unknown module executed')
    monkeypatch.setattr(entry.runpy,'run_module',fail)
    with pytest.raises(ValueError,match='Unsupported'):entry.dispatch(args)


def test_legacy_documents_path_is_mapped_not_executed(entry,monkeypatch,tmp_path):
    calls=[]
    monkeypatch.setattr(entry.runpy,'run_module',lambda name,**kwargs:calls.append((name,list(sys.argv))))
    missing=tmp_path/'not-real-assets'/'visual'/'documents_parse.py'
    assert not missing.exists()
    assert entry.dispatch([str(missing),'input.txt'])==0
    assert calls==[('visual.documents_parse',['visual.documents_parse','input.txt'])]


@pytest.mark.parametrize('error',[SystemExit(1),RuntimeError('worker failed')])
def test_argv_restored_on_worker_exit(entry,monkeypatch,error):
    original=sys.argv
    def run(*a,**kw):raise error
    monkeypatch.setattr(entry.runpy,'run_module',run)
    with pytest.raises(type(error)):entry.dispatch(['-m','visual.gauge_worker'])
    assert sys.argv is original


def test_default_argv_and_cli_dispatch(entry,monkeypatch):
    import visual.cli
    calls=[]
    monkeypatch.setattr(visual.cli,'main',lambda args:calls.append(args) or 7)
    monkeypatch.setattr(sys,'argv',['runtime.exe','--help'])
    assert entry.dispatch()==7 and calls==[['--help']]
