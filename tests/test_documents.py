import io
import zipfile
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from visual.documents import DocumentStore,DocumentError
from visual.documents_parse import extract
from visual.documents_routes import router


@pytest.fixture
def store(tmp_path):
    instance=DocumentStore(tmp_path/'docs')
    yield instance
    instance.close()


def upload(store,text='锁定能源后检查设备。安全操作手册。',doc=None):
    return store.upload('../../manual.md',text.encode(),{'title':'安全手册','station':'s1','device':'pump','product':'A'},'operator','op',doc)


def test_versions_publication_and_retrieval(store):
    first=upload(store)
    assert store.list('viewer')==[] and store.search('安全操作','viewer')==[]
    assert store.search('安全操作','operator')[0]['version_id']==first['id']
    with pytest.raises(DocumentError):store.set_status(first['id'],'published','operator')
    store.set_status(first['id'],'published','admin')
    assert store.search('安全操作','viewer')[0]['paragraph']==1
    second=upload(store,'新的安全操作规程。',first['document_id'])
    assert second['version']==2
    assert store.list('viewer')[0]['id']==first['id']
    store.set_status(second['id'],'published','admin')
    assert len(store.list('viewer'))==1 and store.list('viewer')[0]['id']==second['id']
    assert store.get_version(first['id'],'admin')['status']=='archived'
    assert store.original(first['id'],'admin')[1].name==first['id']+'.md'
    with pytest.raises(DocumentError):store.original(first['id'],'viewer')
    store.set_status(second['id'],'archived','admin')
    assert not store.search('安全操作','viewer')


def test_original_hash_and_persistence(store):
    record=upload(store)
    reopened=DocumentStore(store.root)
    assert reopened.get_version(record['id'],'admin')['sha256']==record['sha256']
    _,path=reopened.original(record['id'],'admin')
    path.write_bytes(b'tampered')
    with pytest.raises(DocumentError,match='校验'):reopened.original(record['id'],'admin')
    reopened.close()


@pytest.mark.parametrize('role',['viewer','unknown',None])
def test_upload_denied(store,role):
    with pytest.raises(DocumentError):store.upload('a.txt',b'hello',{'title':'t'},role,'x')


def test_docx_actual_xml_and_bomb_rejected():
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as archive:
        archive.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>真实段落</w:t></w:r></w:p></w:body></w:document>')
    assert extract(out.getvalue(),'.docx')=='真实段落'
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('word/document.xml','a'*100000)
    with pytest.raises(ValueError,match='压缩'):extract(out.getvalue(),'.docx')


@pytest.mark.parametrize('data,suffix',[(b'\xff','.txt'),(b'bad','.pdf'),(b'hello','.exe'),(b'','.md')])
def test_reject_invalid_parser_inputs(data,suffix):
    with pytest.raises(Exception):extract(data,suffix)


def test_http_permissions_and_source_metadata(store):
    app=FastAPI()
    @app.middleware('http')
    async def user(request,call_next):
        request.state.user={'role':request.headers.get('test-role','viewer'),'username':'test'}
        return await call_next(request)
    app.include_router(router(store))
    client=TestClient(app)
    file={'file':('manual.txt','设备安全操作手册'.encode(),'text/plain')}
    assert client.post('/api/documents',files=file,data={'title':'manual'}).status_code==403
    created=client.post('/api/documents',files=file,data={'title':'manual','device':'pump'},headers={'test-role':'operator'})
    assert created.status_code==201,created.text
    vid=created.json()['id']
    assert client.get(f'/api/documents/versions/{vid}').status_code==404
    url=f'/api/documents/versions/{vid}/status'
    assert client.put(url,json={'status':'published'},headers={'test-role':'operator'}).status_code==403
    assert client.put(url,json={'status':'published'},headers={'test-role':'admin'}).status_code==200
    result=client.get('/api/documents/search?q=安全操作').json()['items'][0]
    assert result['version_id']==vid and result['line_start']==1
    assert client.get(f'/api/documents/versions/{vid}/original').headers['content-type']=='application/octet-stream'
    assert client.get('/api/documents',headers={'test-role':'unknown'}).status_code==403
