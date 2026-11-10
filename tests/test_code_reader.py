import io
import json
from pathlib import Path
import subprocess
import sys
import pytest
from PIL import Image
from visual.reader_routes import ReadingService,router
from visual.labels import LabelError


def picture():
    b=io.BytesIO();Image.new('RGB',(100,100),'white').save(b,format='PNG');b.seek(0);return b


def test_roi_mapping_and_temp_cleanup(monkeypatch):
    s=ReadingService();seen=[]
    def worker(spec):
        seen.append(Path(spec['image_path']))
        with Image.open(seen[-1]) as im:assert im.size==(50,50)
        return {'items':[{'text':'abc','format':'QR','confidence':None,'quad':[[0,0],[50,0],[50,50],[0,50]]}]}
    monkeypatch.setattr(s,'_worker',worker)
    result=s.read(picture(),roi=[.25,.25,.75,.75])
    assert result['items'][0]['quad_norm']==[[.25,.25],[.75,.25],[.75,.75],[.25,.75]]
    assert not seen[0].exists()


@pytest.mark.parametrize('roi', [[0,0,0,1],[0,0,2,1],[0,0,float('nan'),1],'bad',[0,0,True,1]])
def test_bad_roi(roi):
    with pytest.raises(LabelError):ReadingService().read(picture(),roi=roi)


def test_reject_file_and_busy():
    s=ReadingService()
    with pytest.raises(LabelError):s.read(io.BytesIO(b'<script>'))
    with pytest.raises(LabelError):s.read(picture(),'http://localhost/')
    s._slot.acquire()
    try:
        with pytest.raises(LabelError) as e:s.read(picture())
        assert e.value.status==429
    finally:s._slot.release()


def test_bad_worker_result(monkeypatch):
    s=ReadingService();monkeypatch.setattr(s,'_worker',lambda _: {'items':[{'text':'x','format':'t','quad':[[float('nan'),0]]*4}]})
    with pytest.raises(LabelError):s.read(picture())
    assert s._slot.acquire(False);s._slot.release()


def test_timeout_kills_owned_process(monkeypatch):
    from visual import reader_routes
    class Process:
        dead=False
        def communicate(self,*a,**k):
            if not self.dead:raise subprocess.TimeoutExpired('worker',.01)
        def poll(self):return 1 if self.dead else None
        def kill(self):self.dead=True
    p=Process();monkeypatch.setattr(reader_routes.subprocess,'Popen',lambda *a,**k:p)
    with pytest.raises(LabelError):ReadingService()._worker({})
    assert p.dead


def test_route_multipart(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    s=ReadingService();monkeypatch.setattr(s,'_worker',lambda _: {'items':[]})
    app=FastAPI();app.include_router(router(s));client=TestClient(app)
    assert client.post('/api/readers/read',files={'file':('x.png',picture().read(),'image/png')},data={'kind':'barcode'}).json()['items']==[]


def test_actual_qr_optional_environment():
    qrcode=pytest.importorskip('qrcode')
    python=Path(__file__).resolve().parents[1]/'.v0/reader-venv/Scripts/python.exe'
    if not python.exists():pytest.skip('optional isolated reader environment absent')
    image=qrcode.make('STEPEYE-Q2-TEST');b=io.BytesIO();image.save(b,format='PNG');b.seek(0)
    assert ReadingService(str(python)).read(b)['items'][0]['text']=='STEPEYE-Q2-TEST'


def test_image_size_and_multiframe_rejected():
    b=io.BytesIO();Image.new('RGB',(4097,1)).save(b,format='PNG');b.seek(0)
    with pytest.raises(LabelError) as e:ReadingService().read(b)
    assert e.value.status==413
    b=io.BytesIO();Image.new('RGB',(10,10)).save(b,format='GIF',save_all=True,append_images=[Image.new('RGB',(10,10),'white')]);b.seek(0)
    with pytest.raises(LabelError):ReadingService().read(b)


def test_reader_env_priority(tmp_path,monkeypatch):
    env=tmp_path/'.env';env.write_text('STEPEYE_READER_PYTHON=file-python\n',encoding='utf-8')
    monkeypatch.delenv('STEPEYE_READER_PYTHON',raising=False)
    assert ReadingService(env_file=env).python=='file-python'
    monkeypatch.setenv('STEPEYE_READER_PYTHON','env-python')
    assert ReadingService(env_file=env).python=='env-python'
    assert ReadingService('explicit-python',env_file=env).python=='explicit-python'
