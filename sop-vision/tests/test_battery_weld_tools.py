import hashlib,importlib.util,io,zipfile
from pathlib import Path
from PIL import Image


def load():
    path=Path(__file__).parents[1]/'tools'/'battery_weld_audit.py'
    spec=importlib.util.spec_from_file_location('battery_audit',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def test_battery_audit_duplicate_split_and_dimensions(tmp_path):
    module=load();archive=tmp_path/'data.zip';shared=None
    with zipfile.ZipFile(archive,'w') as z:
        for i,split in enumerate(['train','val','test']):
            for j,label in enumerate(['OK','KO']):
                image=Image.new('L',(30+j*10,10),i*20+j+1)
                data=io.BytesIO();image.save(data,format='PNG');raw=data.getvalue()
                z.writestr(f'{split}/{label}/image.png',raw)
                if split=='train' and label=='OK':shared=raw
        z.writestr('test/OK/duplicate.png',shared)
    module.EXPECTED_SIZE=archive.stat().st_size;module.EXPECTED_MD5=hashlib.md5(archive.read_bytes()).hexdigest()
    result=module.audit(archive,tmp_path/'audit')
    assert result['crc_and_decode_verified']==7 and result['cross_split_duplicate_groups']==1
    assert result['retained_counts']['test/OK']==1
    assert result['width_only_holdout']['test']['accuracy']==1
    assert result['size_statistics']['train/KO']['width']['median']==40


def test_battery_audit_rejects_partial(tmp_path):
    import pytest
    module=load();path=tmp_path/'partial';path.write_bytes(b'partial')
    with pytest.raises(ValueError,match='incomplete'):module.audit(path,tmp_path/'audit')

def test_curl_partial_resume_checks_range_and_keeps_tls(tmp_path,monkeypatch):
    import time,types
    path=Path(__file__).parents[1]/'tools'/'battery_weld_download.py'
    spec=importlib.util.spec_from_file_location('battery_download',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    target=tmp_path/'chunk.bin';target.with_suffix('.part').write_bytes(b'abc')
    def fake(cmd,**kwargs):
        assert '--tlsv1.2' in cmd and '--tls-max' in cmd and '-k' not in cmd and '--insecure' not in cmd
        assert cmd[cmd.index('--header')+1]=='Range: bytes=13-15'
        Path(cmd[cmd.index('--dump-header')+1]).write_text(f'HTTP/1.1 206 Partial Content\nContent-Range: bytes 13-15/{m.SIZE}\n')
        Path(cmd[cmd.index('--output')+1]).write_bytes(b'def')
        return types.SimpleNamespace(returncode=0)
    monkeypatch.setattr(m.subprocess,'run',fake)
    m.curl_range(10,15,target,time.monotonic()+20)
    assert target.read_bytes()==b'abcdef'


def test_train_guard_and_exception_cleanup_in_isolated_env(tmp_path):
    import subprocess,sys,pytest
    root=Path(__file__).resolve().parents[2]
    interpreter=root/'.v0/train-venv/Scripts/python.exe'
    if not interpreter.exists():pytest.skip('optional isolated torch environment absent')
    code='''
import importlib.util,types
from pathlib import Path
spec=importlib.util.spec_from_file_location('train',r"SCRIPT")
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
a=types.SimpleNamespace(epochs=1,batch=2,workers=0,max_train=0,max_eval=2,out=r"OUT")
try:m.train(a);raise AssertionError('unbounded max_train accepted')
except ValueError:pass
a.max_train=2
Path(a.out).mkdir()
try:m.train(a);raise AssertionError('existing output accepted')
except FileExistsError:pass
a.out+='-new'
closed=[]
def fail(args,datasets):
 datasets['train']=types.SimpleNamespace(close=lambda:closed.append(True))
 raise RuntimeError('synthetic failure')
m._train_impl=fail
try:m.train(a)
except RuntimeError:pass
assert closed==[True]
'''.replace('SCRIPT',str(root/'sop-vision/tools/battery_weld_train.py')).replace('OUT',str(tmp_path/'run'))
    subprocess.run([str(interpreter),'-c',code],check=True,timeout=30,capture_output=True)
