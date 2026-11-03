import json
import urllib.error
import pytest
from visual.ai_services import AIServiceStore,AIServiceError,ServiceTransport,NoRedirect,validate_url
from visual.llm_provider import ProviderConfig


def body(**kw):return dict(name='Local',base_url='http://127.0.0.1:12345/v1',model='test',api_key='SECRET-DO-NOT-EXPOSE')|kw


def test_persistence_and_secret_preservation(tmp_path):
    p=tmp_path/'services.json';s=AIServiceStore(p)
    response=s.update('service-123',body())
    assert 'SECRET' not in json.dumps(response) and response['api_key_set']
    no_key=body(name='renamed');no_key.pop('api_key');s.update('service-123',no_key)
    loaded=AIServiceStore(p)
    loaded.bind(dict(chat='service-123',vlm='service-123',image=None))
    assert loaded.provider_config('vlm',ProviderConfig()).api_key=='SECRET-DO-NOT-EXPOSE'
    assert loaded.catalog()['bindings']['vlm']=='service-123'
    assert 'SECRET' not in json.dumps(loaded.catalog())


@pytest.mark.parametrize('url',['file:///tmp/a','https://user:pass@example.com','https://example.com?key=secret','http://example.com','https://example.com/#secret','https://bad\nhost'])
def test_url_validation(url):
    with pytest.raises(AIServiceError):validate_url(url)


def test_bind_validation_and_fallback(tmp_path):
    s=AIServiceStore(tmp_path/'ai.json');fallback=ProviderConfig()
    assert s.resolve_provider('vlm',fallback).cfg is fallback
    with pytest.raises(AIServiceError):s.bind(dict(chat='missing',vlm=None,image=None))
    with pytest.raises(AIServiceError):s.bind(dict(chat=None,vlm=None,image='s'))


def test_actual_test_shape_and_redaction(tmp_path,monkeypatch):
    from visual import ai_services
    s=AIServiceStore(tmp_path/'ai.json');s.update('s',body())
    seen=[]
    def post(self,url,headers,payload):
        seen.append(payload)
        return {'choices':[{'message':{'content':'OK'},'finish_reason':'stop'}]}
    monkeypatch.setattr(ServiceTransport,'post',post)
    assert s.test('s')['ok']
    assert s.catalog()['services'][0]['checks']['chat']=='passed'
    monkeypatch.setattr(ai_services.secrets,'choice',lambda _: 'blue')
    def vision(self,url,headers,payload):
        assert payload['messages'][0]['content'][0]['image_url']['url'].startswith('data:image/jpeg;base64,')
        return {'choices':[{'message':{'content':'blue'}}]}
    monkeypatch.setattr(ServiceTransport,'post',vision)
    assert s.test('s','vlm')['ok']
    def broken(*a,**k):raise RuntimeError('SECRET-DO-NOT-EXPOSE')
    monkeypatch.setattr(ServiceTransport,'post',broken)
    result=s.test('s')
    assert not result['ok'] and 'SECRET' not in json.dumps(result)
    s._testing.acquire()
    try:
        with pytest.raises(AIServiceError) as e:s.test('s')
        assert e.value.status==429
    finally:s._testing.release()


def test_redirect_never_followed():
    assert NoRedirect().redirect_request(None,None,302,'',{},'https://elsewhere') is None


def test_api_omitted_key_and_error_no_secrets(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from visual.ai_routes import router
    app=FastAPI();app.include_router(router(AIServiceStore(tmp_path/'ai.json')));client=TestClient(app)
    assert client.put('/api/ai/services/s',json=body()).status_code==200
    result=client.get('/api/ai/services')
    assert 'SECRET' not in result.text
    result=client.put('/api/ai/services/s',json=body(base_url='https://SECRET@example.com'))
    assert result.status_code==400 and 'SECRET' not in result.text


def test_real_loopback_redirect_and_json():
    import threading
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    hits=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            hits.append(self.path)
            if self.path=='/redirect':
                self.send_response(302);self.send_header('Location','/capture');self.end_headers()
            else:
                self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
                self.wfile.write(b'{"ok":true}')
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        base=f'http://127.0.0.1:{server.server_port}'
        assert ServiceTransport(2).post(base+'/ok',{}, {})=={'ok':True}
        with pytest.raises(AIServiceError):ServiceTransport(2).post(base+'/redirect',{'Authorization':'Bearer SECRET'}, {})
        assert '/capture' not in hits
    finally:server.shutdown();server.server_close();thread.join(timeout=3)


def test_failed_secure_save_rolls_back(tmp_path,monkeypatch):
    s=AIServiceStore(tmp_path/'ai.json');s.update('s',body())
    def fail():raise AIServiceError('failed',500)
    monkeypatch.setattr(s,'_save',fail)
    with pytest.raises(AIServiceError):s.update('s',body(name='changed'))
    assert s.catalog()['services'][0]['name']=='Local'


def test_dynamic_proxy_preserves_injected_fallback_and_routes_purposes(tmp_path,monkeypatch):
    from visual.ai_services import DynamicAIProvider
    from visual.llm_provider import VLMProvider
    class FakeTransport:
        def post(self,url,headers,payload):return {'choices':[{'message':{'content':'{"verdict":"PASS","confidence":1,"reason":"ok"}'}}]}
    fallback=VLMProvider(ProviderConfig(),FakeTransport())
    store=AIServiceStore(tmp_path/'ai.json');proxy=DynamicAIProvider(store,fallback)
    assert proxy.snapshot() is fallback
    assert proxy.snapshot().transport is fallback.transport
    assert proxy.inspect(['image'],'test')['verdict']=='PASS'
    store.update('chat',body(model='chat-model'));store.update('vision',body(model='vision-model'))
    store.bind(dict(chat='chat',vlm='vision',image=None));seen=[]
    def post(self,url,headers,payload):
        seen.append(payload['model'])
        return {'choices':[{'message':{'content':'OK'}}]}
    monkeypatch.setattr(ServiceTransport,'post',post)
    proxy.chat([{'role':'user','content':'hello'}])
    proxy.chat(proxy.image_message(['image'],'see image'))
    assert seen==['chat-model','vision-model']


def test_provider_snapshot_not_changed_by_rebind(tmp_path):
    from visual.ai_services import DynamicAIProvider
    from visual.llm_provider import VLMProvider
    s=AIServiceStore(tmp_path/'ai.json');s.update('a',body(model='a'));s.update('b',body(model='b'))
    s.bind(dict(chat=None,vlm='a',image=None))
    proxy=DynamicAIProvider(s,VLMProvider(ProviderConfig()))
    first=proxy.snapshot()
    s.bind(dict(chat=None,vlm='b',image=None))
    assert first.cfg.model=='a' and proxy.snapshot().cfg.model=='b'


def test_inspection_multiframe_snapshots_once(tmp_path):
    from visual.inspection import InspectionService,Scenario
    (tmp_path/'a.jpg').write_bytes(b'image-a');(tmp_path/'b.jpg').write_bytes(b'image-b')
    class Stable:
        count=0
        def inspect(self,*args):
            self.count+=1
            return {'ok':True,'verdict':'PASS','confidence':.9,'reason':'ok'}
    stable=Stable()
    class Proxy:
        count=0
        def snapshot(self,purpose):
            self.count+=1
            assert purpose=='vlm'
            return stable
    service=InspectionService.__new__(InspectionService);service.provider=Proxy()
    result=service._judge(Scenario(id='x',name='test',enabled=True,prompt='test'),tmp_path,['a.jpg','b.jpg'])
    assert service.provider.count==1 and stable.count==2
    assert result[0]=='PASS'
