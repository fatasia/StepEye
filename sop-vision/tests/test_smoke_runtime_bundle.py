"""Test smoke protocol plumbing; actual packaged SDK is checked by the bundle run."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
import pytest

@pytest.fixture
def smoke():
    spec=importlib.util.spec_from_file_location('bundle_smoke',Path(__file__).resolve().parents[1]/'tools/smoke_runtime_bundle.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def test_mcp_probe_readonly_handshake_and_owned_exit(smoke,tmp_path,monkeypatch):
    code="""import json,sys
names=json.loads(sys.argv[1])
for line in sys.stdin:
 m=json.loads(line)
 if m['method']=='initialize':r={'protocolVersion':'2024-11-05'}
 elif m['method']=='tools/list':r={'tools':[{'name':n,'annotations':{'readOnlyHint':True}} for n in names]}
 else:continue
 print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':r}),flush=True)
"""
    original=subprocess.Popen;owned=[]
    def launch(command,**kwargs):
        assert command[1:]==['-m','visual.mcp_server','--transport','stdio']
        assert kwargs['env']['STEPEYE_MCP_TOKEN'].startswith('se_mcp_')
        p=original([sys.executable,'-c',code,json.dumps(sorted(smoke.MCP_TOOLS))],**kwargs);owned.append(p);return p
    monkeypatch.setattr(smoke.subprocess,'Popen',launch)
    result=smoke.mcp_probe('fake.exe',tmp_path,{},timeout=2)
    assert len(result['tools'])==6 and result['business_requests']==0
    assert owned[0].poll() is not None


def test_mcp_timeout_stops_owned_process(smoke,tmp_path,monkeypatch):
    original=subprocess.Popen;owned=[]
    def launch(command,**kwargs):
        p=original([sys.executable,'-c','import time;time.sleep(60)'],**kwargs);owned.append(p);return p
    monkeypatch.setattr(smoke.subprocess,'Popen',launch)
    with pytest.raises(RuntimeError,match='timeout'):smoke.mcp_probe('fake.exe',tmp_path,{},timeout=.1)
    assert owned[0].poll() is not None
