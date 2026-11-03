"""One cold and one warm native Qwen-VL request; always stops its own server."""
from __future__ import annotations
import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

from qwen_download import digest


def gpu():
    try:
        r=subprocess.run(['nvidia-smi','--query-gpu=name,memory.used,memory.total','--format=csv,noheader,nounits'],
            capture_output=True,text=True,timeout=3,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        name,used,total=r.stdout.strip().splitlines()[0].rsplit(',',2)
        return {'name':name.strip(),'used_mib':int(used),'total_mib':int(total)}
    except (OSError,ValueError,IndexError,subprocess.TimeoutExpired): return None


def run(root:Path,image:Path,out:Path,port:int=19089) -> dict:
    if out.exists(): raise ValueError('Choose a new output file; evidence is never overwritten')
    manifest=json.loads((Path(__file__).resolve().parents[1]/'deploy/qwen-local-manifest.json').read_text(encoding='utf-8'))
    files={}
    for item in manifest['files']:
        if item['role'] in ('model','mmproj'):
            path=(root/item['name']).resolve()
            if not path.is_file() or path.stat().st_size!=item['bytes'] or digest(path)!=item['sha256']:
                raise ValueError('Model/projector is missing or unverified; run qwen_download.py first')
            files[item['role']]=path
    servers=list((root/'runtime').rglob('llama-server.exe' if os.name=='nt' else 'llama-server'))
    if len(servers)!=1: raise ValueError('Expected one verified llama-server in runtime/')
    server=servers[0].resolve()
    if not image.is_file() or not 0<image.stat().st_size<=20*1024*1024: raise ValueError('Image must be1..20MB')
    with socket.socket() as sock: sock.bind(('127.0.0.1',port))
    token=secrets.token_urlsafe(32)
    env={k:v for k,v in os.environ.items() if not k.startswith('LLAMA_ARG_')}
    env['LLAMA_API_KEY']=token
    command=[str(server),'-m',str(files['model']),'--mmproj',str(files['mmproj']),
        '--host','127.0.0.1','--port',str(port),'--alias','stepeye-local','--ctx-size','4096',
        '--n-gpu-layers','99','--threads','4','--parallel','1']
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def request(path,body=None,timeout=5):
        data=json.dumps(body).encode() if body is not None else None
        req=urllib.request.Request(f'http://127.0.0.1:{port}'+path,data=data,
             headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        with opener.open(req,timeout=timeout) as response: return json.loads(response.read(2*1024*1024))
    baseline=gpu(); samples=[]; stopped=threading.Event()
    def monitor():
        while not stopped.is_set():
            item=gpu()
            if item: samples.append(item['used_mib'])
            stopped.wait(.5)
    report={'schema_version':1,'model_revision':manifest['model_revision'],'llama_tag':manifest['llama_tag'],
        'image_sha256':digest(image),'quantization':'Q4_K_M','projector':'F16','gpu_baseline':baseline,
        'requests':[],'status':'starting','scope':'one image, no accuracy or quantization equivalence claim'}
    process=None; monitor_thread=None; start=time.monotonic()
    log_path=out.with_suffix('.server.log')
    try:
        with log_path.open('xb') as log:
            process=subprocess.Popen(command,cwd=server.parent,env=env,stdout=log,stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            monitor_thread=threading.Thread(target=monitor,daemon=True);monitor_thread.start()
            deadline=start+180
            while time.monotonic()<deadline:
                if process.poll() is not None: raise RuntimeError('Native server exited during startup')
                try:
                    if request('/health').get('status')=='ok': break
                except (urllib.error.URLError,TimeoutError): pass
                time.sleep(.5)
            else: raise TimeoutError('Model startup exceeded180seconds')
            report['startup_seconds']=round(time.monotonic()-start,3)
            suffix=image.suffix.lower(); mime='image/png' if suffix=='.png' else 'image/jpeg'
            url=f'data:{mime};base64,'+base64.b64encode(image.read_bytes()).decode()
            body={'model':'stepeye-local','temperature':.1,'max_tokens':256,'stream':False,'cache_prompt':False,
                'messages':[{'role':'user','content':[{'type':'text','text':'简短描述图中可见的安全帽和人员。看不清时明确说明，不推测不可见部分。'},
                 {'type':'image_url','image_url':{'url':url}}]}]}
            for phase in ('cold','warm'):
                begun=time.monotonic(); response=request('/v1/chat/completions',body,60)
                report['requests'].append({'phase':phase,'request_seconds':round(time.monotonic()-begun,3),'response':response})
                if phase=='cold': report['cold_start_to_response_seconds']=round(time.monotonic()-start,3)
            report['status']='completed'
    except Exception as exc:
        report['status']='failed'; report['error']=type(exc).__name__+': '+str(exc)[:200]
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill();process.wait(timeout=5)
        stopped.set()
        if monitor_thread is not None: monitor_thread.join(timeout=5)
        report['server_stopped']=process is None or process.poll() is not None
        report['gpu_peak_total_used_mib']=max(samples) if samples else None
        report['gpu_after_stop']=gpu()
        with out.open('x',encoding='utf-8') as evidence: json.dump(report,evidence,ensure_ascii=False,indent=2)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[2]/'.v0/qwen-local')
    p.add_argument('--image',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--port',type=int,default=19089)
    a=p.parse_args()
    if not 1024<=a.port<=65535:p.error('port must be1024..65535')
    report=run(a.root,a.image,a.out,a.port)
    print(json.dumps({k:v for k,v in report.items() if k!='requests'},ensure_ascii=True))
    return 0 if report['status']=='completed' else 2

if __name__=='__main__': raise SystemExit(main())
