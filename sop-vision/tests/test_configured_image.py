import base64
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from PIL import Image
from fastapi import FastAPI
from fastapi.testclient import TestClient
from visual.ai_services import AIServiceStore
from visual.configured_image import configured_batch, image_request
from visual.samplegen_routes import router


def picture():
    buffer=io.BytesIO();Image.new('RGB',(64,48),'teal').save(buffer,format='PNG');return buffer.getvalue()


@pytest.fixture
def endpoint():
    state={'requests':[], 'response':{'data':[{'b64_json':base64.b64encode(picture()).decode()}]}}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            state['requests'].append({'path':self.path,'body':json.loads(self.rfile.read(int(self.headers['Content-Length']))),
                                      'authorization':self.headers.get('Authorization')})
            raw=json.dumps(state['response']).encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    yield f'http://127.0.0.1:{server.server_port}/v1',state
    server.shutdown();thread.join();server.server_close()


def setup(tmp_path,url):
    store=AIServiceStore(tmp_path/'ai.json')
    store.update('image-service',dict(name='local-image',base_url=url,model='test-image',api_key='TEST-SECRET'))
    store.bind(dict(chat=None,vlm=None,image='image-service'))
    app=FastAPI();app.include_router(router(SimpleNamespace(ai_services=store,data_dir=tmp_path)))
    return store,TestClient(app)


def test_bound_actual_protocol_success_and_provenance(tmp_path,endpoint):
    url,state=endpoint;store,client=setup(tmp_path,url)
    assert store.catalog()['image_supported']
    assert 'TEST-SECRET' not in json.dumps(store.catalog())
    result=client.post('/api/samples/generate',files={'ref':('ref.png',picture())},
                       data={'label':'NG','engine':'configured','n':1,'description':'a scratched part'})
    assert result.status_code==200,result.text
    data=result.json();assert data['ok'] and data['generated']==1
    row=data['rows'][0];assert row['engine']=='configured' and row['synthetic'] and row['review_required']
    assert row['label_origin']=='user_intent'
    with Image.open(tmp_path/'samplegen/batch'/row['file']) as image:assert image.format=='JPEG'
    request=state['requests'][0];assert request['path']=='/v1/images/generations'
    assert request['body']==dict(model='test-image',prompt='a scratched part',n=1,size='1024x1024',response_format='b64_json')
    assert request['authorization']=='Bearer TEST-SECRET'
    assert 'TEST-SECRET' not in result.text
    assert not list((tmp_path/'samplegen').glob('ref-*'))
    assert (tmp_path/'samplegen'/row['source']).read_bytes()==picture()
    assert row['group_id']=='sample-reference:'+row['source_sha256']


@pytest.mark.parametrize('bad',[{'data':[{'url':'http://127.0.0.1/private'}]}, {'data':[{'b64_json':'@@@'}]},
                                {'data':[{'b64_json':base64.b64encode(b'not image TEST-SECRET').decode()}]}])
def test_remote_failure_is_not_success_and_slot_releases(tmp_path,endpoint,bad):
    url,state=endpoint;store,client=setup(tmp_path,url);state['response']=bad
    for _ in range(2):
        r=client.post('/api/samples/generate',files={'ref':('ref.png',picture())},data={'label':'OK','engine':'configured','n':1})
        assert r.status_code==200 and r.json()['generated']==0 and not r.json()['ok']
        assert r.json()['rows'][0]['file'] is None and 'TEST-SECRET' not in r.text
    assert len(state['requests'])==2
    assert not list((tmp_path/'samplegen/batch').glob('*.jpg'))


def test_response_cap_and_timeout_fail_closed(tmp_path,endpoint,monkeypatch):
    import visual.configured_image as module
    url,state=endpoint;store,_=setup(tmp_path,url)
    monkeypatch.setattr(module,'MAX_RESPONSE',64)
    rows=configured_batch(tmp_path/'ref.png','NG','describe',1,tmp_path/'out',store.image_config())
    assert rows[0]['file'] is None
    def timeout(*args):raise TimeoutError('TEST-SECRET')
    rows=configured_batch(tmp_path/'ref.png','NG','describe',1,tmp_path/'out',store.image_config(),requester=timeout)
    assert rows[0]['file'] is None and 'TEST-SECRET' not in json.dumps(rows)


def test_single_slot_and_invalid_input(tmp_path,endpoint,monkeypatch):
    import visual.samplegen_routes as module
    url,_=endpoint;_,client=setup(tmp_path,url)
    entered=threading.Event();release=threading.Event();original=module.generate_batch
    def slow(*args,**kw):
        entered.set();release.wait(3);return original(*args,**kw)
    monkeypatch.setattr(module,'generate_batch',slow)
    responses=[]
    def post():responses.append(client.post('/api/samples/generate',files={'ref':('ref.png',picture())},data={'label':'OK','n':1}))
    thread=threading.Thread(target=post);thread.start();assert entered.wait(2)
    try:
        r=client.post('/api/samples/generate',files={'ref':('ref.png',picture())},data={'label':'OK','n':1})
        assert r.status_code==429
    finally:release.set();thread.join()
    assert responses[0].status_code==200
    assert client.post('/api/samples/generate',files={'ref':('bad.png',b'bad')},data={'label':'OK','n':1}).status_code==400
    post();assert responses[-1].status_code==200


def test_same_reference_kept_and_stably_grouped(tmp_path,endpoint):
    url,_=endpoint;_,client=setup(tmp_path,url)
    rows=[]
    for _ in range(2):
        r=client.post('/api/samples/generate',files={'ref':('ref.png',picture())},data={'label':'OK','engine':'augment','n':1})
        assert r.status_code==200
        rows.append(r.json()['rows'][0])
    assert rows[0]['source_sha256']==rows[1]['source_sha256']
    assert rows[0]['group_id']==rows[1]['group_id']
    assert rows[0]['file']!=rows[1]['file']
    assert len(list((tmp_path/'samplegen/references').iterdir()))==1
    assert (tmp_path/'samplegen'/rows[0]['source']).read_bytes()==picture()


def test_generated_pixel_limit():
    from visual.configured_image import normalize_image
    buffer=io.BytesIO();Image.new('RGB',(2100,2000)).save(buffer,format='PNG')
    with pytest.raises(ValueError):normalize_image(buffer.getvalue())
