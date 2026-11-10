import io
import stat
import zipfile
from PIL import Image
import pytest
from visual.label_import import import_dataset
from visual.labels import LabelError, LabelStore

def png():
    b=io.BytesIO(); Image.new('RGB',(32,20),'red').save(b,format='PNG'); return b.getvalue()
def pack(files):
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as z:
        for name,content in files.items(): z.writestr(name,content)
    return b.getvalue()
@pytest.fixture
def store(tmp_path):
    s=LabelStore(tmp_path/'labels'); yield s; s.close()
def test_mapping_and_review_states(store):
    p=store.create_project('test',['b','a'])
    raw=pack({'classes.txt':'a\nb','images/train/one.png':png(),'labels/train/one.txt':'0 .5 .5 .5 .5','images/val/two.png':png(),'labels/val/two.txt':'','images/three.png':png()})
    r=import_dataset(store,p['id'],raw,'tester')
    assert r['imported']==3 and r['failed']==0
    rows={a['name']:a for a in r['items']}
    assert rows['one.png']['state']=='draft' and rows['one.png']['targets'][0]['label_id']==1
    assert rows['two.png']['state']=='draft' and rows['two.png']['targets']==[]
    assert rows['three.png']['state']=='unlabeled'
    assert len({a['group_id'] for a in r['items']})==1
    assert store.quality(p['id'])['confirmed_total']==0

def test_yaml_root_ignores_external_paths(store):
    p=store.create_project('test',['a'])
    r=import_dataset(store,p['id'],pack({'data/data.yaml':'names: {0: a}\npath: /secrets\ntrain: https://unused','data/images/test/x.png':png(),'data/labels/test/x.txt':'0 .5 .5 1 1'}),'tester')
    assert r['imported']==1

@pytest.mark.parametrize('bad',['../x','/x','a\\x','C:/x','a/../x','a//x'])
def test_unsafe_paths_no_writes(store,bad):
    p=store.create_project('test',['a'])
    raw=pack({'classes.txt':'a','images/x.png':png(),bad:'x'})
    if bad == 'a\\x': raw=raw.replace(b'a/x',b'a\\x')
    with pytest.raises(LabelError): import_dataset(store,p['id'],raw,'tester')
    assert store.list_assets(p['id'])['total']==0

def test_symlink_and_case_collision(store):
    p=store.create_project('test',['a']); b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as z:
        info=zipfile.ZipInfo('link'); info.external_attr=(stat.S_IFLNK|0o777)<<16; z.writestr(info,'/outside')
    with pytest.raises(LabelError): import_dataset(store,p['id'],b.getvalue(),'tester')
    with pytest.raises(LabelError): import_dataset(store,p['id'],pack({'classes.txt':'a','images/A.png':png(),'images/a.png':png()}),'tester')

def test_bad_label_does_not_become_background(store):
    p=store.create_project('test',['a'])
    r=import_dataset(store,p['id'],pack({'classes.txt':'a','images/bad.png':png(),'labels/bad.txt':'0 nan .5 .1 .1','images/good.png':png()}),'tester')
    assert r['imported']==1 and r['failed']==1 and store.list_assets(p['id'])['total']==1

@pytest.mark.parametrize('metadata',[{'classes.txt':'unknown'},{'data.yaml':'!!python/object:os.system {}'},{'data.yaml':'names: {1: a}'},{'classes.txt':'a','data.yaml':'names: [b]'}])
def test_invalid_classes(store,metadata):
    p=store.create_project('test',['a'])
    with pytest.raises(LabelError): import_dataset(store,p['id'],pack({**metadata,'images/x.png':png()}),'tester')

def test_limits_and_exif(store):
    p=store.create_project('test',['a'])
    with pytest.raises(LabelError): import_dataset(store,p['id'],pack({'classes.txt':'a',**{f'images/{i}.png':png() for i in range(201)}}),'tester')
    b=io.BytesIO(); im=Image.new('RGB',(32,20)); exif=im.getexif(); exif[274]=6; im.save(b,format='JPEG',exif=exif)
    r=import_dataset(store,p['id'],pack({'classes.txt':'a','images/x.jpg':b.getvalue(),'labels/x.txt':'0 .5 .5 .5 .5'}),'tester')
    assert r['failed']==1 and r['imported']==0

def test_rest_import_and_viewer_denied(tmp_path):
    from fastapi.testclient import TestClient
    from visual.auth import AuthStore
    from visual.server import build_app
    auth=AuthStore(tmp_path/'auth.db')
    auth.set_user('admin','test-password','admin'); auth.set_user('viewer','test-password','viewer')
    app=build_app([],tmp_path/'data',auth=auth)
    try:
        with TestClient(app) as client:
            client.post('/api/auth/login',json={'username':'admin','password':'test-password'})
            p=client.post('/api/label-projects',json={'name':'import','classes':['a']}).json()
            path=f"/api/label-projects/{p['id']}/import-dataset"
            files={'file':('dataset.zip',pack({'classes.txt':'a','images/a.png':png(),'labels/a.txt':'0 .5 .5 .5 .5'}),'application/zip')}
            response=client.post(path,files=files)
            assert response.status_code==201, response.text
            assert response.json()['items'][0]['state']=='draft'
            client.post('/api/auth/login',json={'username':'viewer','password':'test-password'})
            assert client.post(path,files=files).status_code==403
    finally:
        auth.close()

def test_synthetic_roundtrip_cannot_become_validation(store):
    p=store.create_project('test',['a'])
    raw=pack({'classes.txt':'a','images/a.png':png(),'manifest.json':'{"assets":[{"synthetic":true}]}'})
    r=import_dataset(store,p['id'],raw,'tester')
    assert r['items'][0]['synthetic'] is True
    r2=import_dataset(store,p['id'],pack({'classes.txt':'a','images/b.png':png()}),'tester',synthetic=True)
    assert r2['items'][0]['synthetic'] is True
