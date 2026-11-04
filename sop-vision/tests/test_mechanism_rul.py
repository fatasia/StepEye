import json
from pathlib import Path
import numpy as np
from visual.mechanism_rul import RULBody,RULService,CHANNELS
import pytest


def body():return dict(dataset='NASA-CMAPSS-FD001',unit='cycle',channels=CHANNELS,windows=[dict(unit_id='a',end_cycle=30,values=[[0.]*24 for _ in range(30)])])


def test_contract():
    RULBody.model_validate(body())
    for key,value in [('channels',list(reversed(CHANNELS))),('dataset','motor'),('unit','day')]:
        b=body();b[key]=value
        with pytest.raises(ValueError):RULBody.model_validate(b)
    b=body();b['windows'][0]['values'][0]=[0.]*23
    with pytest.raises(ValueError):RULBody.model_validate(b)


def test_missing_and_closed(tmp_path):
    service=RULService(tmp_path)
    assert not service.capabilities()['available']
    with pytest.raises(RuntimeError):service.analyze(body())
    assert service.close()
    with pytest.raises(RuntimeError):service.analyze(body())


def test_real_local_asset_worker():
    root=Path(__file__).resolve().parents[1]
    fixture=root.parent/'.v0/mechanism-rul/real-request.json'
    if not fixture.is_file():pytest.skip('Optional local public-data fixture')
    service=RULService(root);result=service.analyze(json.loads(fixture.read_text()))
    assert result['status']=='evidence_only' and not result['signature']['verified']
    assert 0<=result['results'][0]['rul_cycles']<=125
    assert service.close()


def test_busy_timeout_and_close(tmp_path,monkeypatch):
    import subprocess
    from visual.mechanism_rul import RULError
    service=RULService(tmp_path)
    service.lock.acquire()
    with pytest.raises(RULError) as e:service.analyze(body())
    assert e.value.status==429 and service.close() is False
    service.lock.release()
    service=RULService(tmp_path)
    monkeypatch.setattr('visual.mechanism_rul.pinned_bundle',lambda root:root)
    def timeout(*a,**kw):raise subprocess.TimeoutExpired('worker',30)
    monkeypatch.setattr('visual.mechanism_rul.subprocess.run',timeout)
    with pytest.raises(RULError) as e:service.analyze(body())
    assert e.value.status==504 and not service.lock.locked()


def test_private_request_file_cleanup(tmp_path,monkeypatch):
    import subprocess
    from types import SimpleNamespace
    service=RULService(tmp_path);seen=[]
    monkeypatch.setattr('visual.mechanism_rul.pinned_bundle',lambda root:root)
    def run(command,**kwargs):
        assert kwargs['stdin']==subprocess.DEVNULL and 'input' not in kwargs and kwargs['timeout']==30
        assert command[-2]=='--request'
        request=Path(command[-1]);seen.append(request)
        assert json.loads(request.read_text())['body']['dataset']=='NASA-CMAPSS-FD001'
        return SimpleNamespace(returncode=0,stdout='{"status":"evidence_only"}')
    monkeypatch.setattr('visual.mechanism_rul.subprocess.run',run)
    assert service.analyze(body())['status']=='evidence_only'
    assert not seen[0].exists()
