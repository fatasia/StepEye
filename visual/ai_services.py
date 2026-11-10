"""管理员 AI 服务配置；密钥仅保存到当前服务账户受限文件，响应不回传。"""
from __future__ import annotations
import base64
import csv
import io
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
import threading
import time
import urllib.request
from urllib.parse import urlsplit
from .llm_provider import ProviderConfig, Transport, VLMProvider


class AIServiceError(Exception):
    def __init__(self,message,status=400):super().__init__(message);self.status=status


def validate_url(value):
    if not isinstance(value,str) or len(value)>2048 or any(ord(c)<33 for c in value):
        raise AIServiceError('服务地址无效')
    try:
        parts=urlsplit(value);port=parts.port
    except ValueError:raise AIServiceError('服务地址无效') from None
    if (not parts.hostname or parts.username is not None or parts.password is not None
            or parts.query or parts.fragment or parts.scheme not in ('https','http')):
        raise AIServiceError('服务地址须为无凭据、查询或片段的 HTTP(S) 地址')
    if parts.scheme=='http' and parts.hostname not in ('localhost','127.0.0.1','::1'):
        raise AIServiceError('非本机服务必须使用 HTTPS')
    return value.rstrip('/')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


class ServiceTransport(Transport):
    """不跟随重定向，不使用系统代理，响应最多 1MB，不暴露远端错误正文。"""
    _slots=threading.BoundedSemaphore(2)

    def post(self,url,headers,payload):
        if not self._slots.acquire(False):raise AIServiceError('AI服务请求繁忙',429)
        deadline=time.monotonic()+self.timeout_s
        try:
            req=urllib.request.Request(url,data=json.dumps(payload).encode(),
                headers={**headers,'Content-Type':'application/json'})
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
            with opener.open(req,timeout=self.timeout_s) as response:
                raw=bytearray()
                while True:
                    remaining=deadline-time.monotonic()
                    if remaining<=0:raise TimeoutError()
                    sock=getattr(getattr(getattr(response,'fp',None),'raw',None),'_sock',None)
                    if sock is not None:sock.settimeout(remaining)
                    chunk=response.read1(min(65536,1024*1024+1-len(raw)))
                    if not chunk:break
                    raw.extend(chunk)
                    if len(raw)>1024*1024:raise ValueError()
            result=json.loads(raw)
            if not isinstance(result,dict):raise ValueError()
            return result
        except Exception:
            raise AIServiceError('AI服务请求失败，请检查连接、凭据与模型配置',502) from None
        finally:self._slots.release()


def _restrict(path):
    os.chmod(path,0o600)
    if os.name=='nt':
        options={'capture_output':True,'timeout':5,'creationflags':getattr(subprocess,'CREATE_NO_WINDOW',0)}
        row=subprocess.run(['whoami','/user','/fo','csv','/nh'],check=True,**options).stdout.decode('utf-8',errors='replace')
        sid=list(csv.reader(io.StringIO(row.strip())))[0][-1]
        if not re.fullmatch(r'S-1-\d+(?:-\d+)+',sid):raise OSError('account lookup failed')
        subprocess.run(['icacls',str(path),'/inheritance:r','/grant:r',f'*{sid}:(F)'],check=True,**options)


class AIServiceStore:
    def __init__(self,path):
        self.path=Path(path)
        self._lock=threading.RLock()
        self._testing=threading.Lock()
        self._data={'services':{},'bindings':dict(chat=None,vlm=None,image=None)}
        if self.path.exists():
            if self.path.stat().st_size>512*1024:raise AIServiceError('AI服务配置过大',500)
            try:
                _restrict(self.path)
                data=json.loads(self.path.read_text(encoding='utf-8'))
                if not isinstance(data.get('services'),dict) or not isinstance(data.get('bindings'),dict):raise ValueError()
                self._data=data
            except Exception:raise AIServiceError('AI服务配置无法读取',500) from None

    def _save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        fd,name=tempfile.mkstemp(prefix='.ai-',dir=self.path.parent)
        os.close(fd);temporary=Path(name)
        try:
            _restrict(temporary) # 在写入任何凭据之前收紧权限。
            temporary.write_text(json.dumps(self._data,ensure_ascii=False),encoding='utf-8')
            os.replace(temporary,self.path)
        except Exception:raise AIServiceError('AI服务配置无法安全保存',500) from None
        finally:temporary.unlink(missing_ok=True)

    def catalog(self):
        with self._lock:
            rows=[{k:v for k,v in item.items() if k!='api_key'}|{'api_key_set':bool(item.get('api_key'))}
                  for item in self._data['services'].values()]
            return {'services':rows,'bindings':dict(self._data['bindings']),'image_supported':True}

    def update(self,sid,body):
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',sid):raise AIServiceError('服务ID无效')
        if not isinstance(body,dict) or set(body)-{'name','base_url','model','api_key'}:raise AIServiceError('服务字段无效')
        for key,limit in [('name',100),('model',200)]:
            val=body.get(key)
            if not isinstance(val,str) or not val.strip() or len(val)>limit or any(ord(c)<32 for c in val):
                raise AIServiceError('名称或模型无效')
        url=validate_url(body.get('base_url'))
        key=body.get('api_key')
        if 'api_key' in body and (not isinstance(key,str) or len(key)>8192 or any(ord(c)<32 for c in key)):
            raise AIServiceError('API Key格式无效')
        with self._lock:
            rows=self._data['services']
            if sid not in rows and len(rows)>=32:raise AIServiceError('最多配置32个服务')
            old=rows.get(sid,{})
            # 未提供保留；显式空字符串清除。修改模型/地址/密钥后测试状态失效。
            rows[sid]={'id':sid,'name':body['name'].strip(),'base_url':url,'model':body['model'].strip(),
                       'api_key':key if 'api_key' in body else old.get('api_key',''),
                       'checks':dict(chat='unverified',vlm='unverified',image='unverified')}
            try:self._save()
            except Exception:
                if old:rows[sid]=old
                else:rows.pop(sid,None)
                raise
            return next(r for r in self.catalog()['services'] if r['id']==sid)

    def bind(self,body):
        if not isinstance(body,dict) or set(body)!={'chat','vlm','image'}:raise AIServiceError('须提供chat、vlm、image绑定')
        with self._lock:
            for value in body.values():
                if value is not None and (not isinstance(value,str) or value not in self._data['services']):
                    raise AIServiceError('绑定服务不存在',404)
            old=self._data['bindings'];self._data['bindings']=dict(body)
            try:self._save()
            except Exception:self._data['bindings']=old;raise
            return dict(body)

    def image_config(self):
        """Internal-only credentials snapshot; never return this object from an API."""
        with self._lock:
            sid = self._data['bindings'].get('image')
            if sid is None:
                raise AIServiceError('尚未绑定图片生成服务')
            return self._config(self._data['services'][sid])

    def provider_config(self,purpose,fallback):
        if purpose not in ('chat','vlm'):raise AIServiceError('该用途暂不支持服务绑定')
        with self._lock:
            sid=self._data['bindings'].get(purpose)
            if sid is None:return fallback
            return self._config(self._data['services'][sid])

    @staticmethod
    def _config(item):
        return ProviderConfig(base_url=validate_url(item['base_url']),model=item['model'],api_key=item['api_key'],
                              timeout_s=10,max_retries=0,max_tokens=256)

    def resolve_provider(self,purpose,fallback):
        fallback_cfg=fallback.cfg if isinstance(fallback,VLMProvider) else fallback
        cfg=self.provider_config(purpose,fallback_cfg)
        if cfg is fallback_cfg:
            return fallback if isinstance(fallback,VLMProvider) else VLMProvider(cfg)
        return VLMProvider(cfg,ServiceTransport(cfg.timeout_s))

    def test(self,sid,purpose='chat'):
        if purpose not in ('chat','vlm'):raise AIServiceError('仅支持chat或vlm实际请求测试')
        if not self._testing.acquire(False):raise AIServiceError('已有服务测试正在运行',429)
        start=time.monotonic()
        try:
            with self._lock:
                item=dict(self._data['services'].get(sid,{}))
            if not item:raise AIServiceError('服务不存在',404)
            provider=VLMProvider(self._config(item),ServiceTransport(10))
            expected='OK';messages=[{'role':'user','content':'Reply with exactly OK.'}]
            if purpose=='vlm':
                from PIL import Image
                color=secrets.choice(['red','green','blue']);buffer=io.BytesIO()
                Image.new('RGB',(64,64),color).save(buffer,format='JPEG')
                messages=provider.image_message([base64.b64encode(buffer.getvalue()).decode()],
                    'Name the dominant color of the image. Reply with exactly one word: red, green, or blue.')
                expected=color
            try:ok=provider.chat(messages).strip().strip('.').casefold()==expected.casefold()
            except Exception:ok=False
            status='passed' if ok else 'failed'
            with self._lock:
                current=self._data['services'].get(sid)
                if current==item:
                    previous=current['checks'][purpose];current['checks'][purpose]=status
                    try:self._save()
                    except Exception:current['checks'][purpose]=previous;raise
            return {'ok':ok,'purpose':purpose,'status':status,'elapsed_ms':round((time.monotonic()-start)*1000,2)}
        finally:self._testing.release()


class DynamicAIProvider:
    """绑定变更只影响下一次调用；未绑定时复用原 Provider 及其 transport。"""
    def __init__(self,store,fallback: VLMProvider):
        self.store,self.fallback=store,fallback

    def snapshot(self,purpose='vlm'):
        return self.store.resolve_provider(purpose,self.fallback)

    @property
    def ready(self):
        return bool(self.store.catalog()['bindings'].get('vlm') or self.fallback.cfg.configured)

    @property
    def cfg(self):
        return self.snapshot('vlm').cfg

    image_message=staticmethod(VLMProvider.image_message)

    def inspect(self,images_b64,prompt):
        provider=self.snapshot('vlm')
        return provider.inspect(images_b64,prompt)

    def chat(self,messages):
        has_image=any(isinstance(message.get('content'),list) and
                      any(isinstance(part,dict) and part.get('type')=='image_url'
                          for part in message['content']) for message in messages)
        provider=self.snapshot('vlm' if has_image else 'chat')
        return provider.chat(messages)
