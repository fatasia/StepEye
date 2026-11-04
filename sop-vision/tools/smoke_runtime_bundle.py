"""Smoke-test an independent onedir runtime using only child processes owned here."""
import argparse
import json
import os
from pathlib import Path
import socket
import sys
import secrets
import http.cookiejar
import urllib.error
import subprocess
import tempfile
import time
import urllib.request


def command(exe,args,cwd,env,timeout=30):
    p=subprocess.run([str(exe),*args],cwd=cwd,env=env,stdin=subprocess.DEVNULL,
                     capture_output=True,timeout=timeout)
    return p.returncode,p.stdout.decode('utf-8',errors='replace')


def verify(exe,assets,*,rul_fixture=None,gauge_fixture=None,timeout=45):
    exe=Path(exe).resolve();assets=Path(assets).resolve()
    if not exe.is_file():raise ValueError('Executable missing')
    if not (assets/'web/index.html').is_file():raise ValueError('Asset root lacks web/index.html')
    if (assets/'.env').exists():raise ValueError('Bundle contains .env; remove deployment credentials before distribution')
    results={'exe':str(exe),'assets':str(assets),'checks':{}}
    env={k:v for k,v in os.environ.items() if not k.startswith(('STEPEYE_','ZHIPU_','VISUAL_'))}
    env['VISUAL_AUTH']='0';env['PYTHONUTF8']='1'
    with tempfile.TemporaryDirectory(prefix='stepeye-bundle-smoke-') as temp:
        root=Path(temp)
        rc,out=command(exe,['--help'],root,env)
        if rc or 'serve' not in out:raise RuntimeError('Executable --help failed')
        results['checks']['help']='passed'
        doc=root/'probe.txt';doc.write_text('runtime smoke document',encoding='utf8')
        rc,out=command(exe,['-m','visual.documents_parse',str(doc)],root,env)
        if rc or json.loads(out).get('text')!='runtime smoke document':raise RuntimeError('Document module dispatch failed')
        results['checks']['documents_parse']='passed'
        # The legacy absolute .py launcher must be routed too, not treated as CLI args.
        rc,out=command(exe,[str(assets/'visual/documents_parse.py'),str(doc)],root,env)
        if rc or json.loads(out).get('text')!='runtime smoke document':raise RuntimeError('Document script dispatch failed')
        results['checks']['documents_script_dispatch']='passed'
        bad=root/'invalid.json';bad.write_text('{}')
        rc,out=command(exe,['-m','visual.gauge_worker','--request',str(bad)],root,env)
        if rc!=1 or json.loads(out).get('error')!='gauge_pose_failed':raise RuntimeError('Gauge worker dispatch failed')
        results['checks']['gauge_dispatch']='expected_structured_rejection'
        if rul_fixture:
            request=root/'rul.json';body=json.loads(Path(rul_fixture).read_text(encoding='utf8'))
            request.write_text(json.dumps({'root':str(assets),'body':body}),encoding='utf8')
            rc,out=command(exe,['-m','visual.mechanism_rul_worker','--request',str(request)],root,env)
            if rc or json.loads(out).get('kind')!='cmapss_rul':raise RuntimeError('RUL fixture inference failed')
            results['checks']['rul_fixture']='passed'
        if gauge_fixture:
            request=root/'gauge.json'
            request.write_text(json.dumps({'root':str(assets),'image_path':str(Path(gauge_fixture).resolve())}),encoding='utf8')
            rc,out=command(exe,['-m','visual.gauge_worker','--request',str(request)],root,env)
            if rc or 'detections' not in json.loads(out):raise RuntimeError('Gauge fixture inference failed')
            results['checks']['gauge_fixture']='passed'
        # Create only an ephemeral admin with the project's real password implementation.
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
        from visual.auth import AuthStore
        password=secrets.token_urlsafe(24)
        auth=AuthStore(root/'data/auth.db')
        try:auth.set_user('smoke-admin',password,'admin')
        finally:auth.close()
        # A model-free simulation station; never open cameras or connect external services.
        config={'station':'bundle-smoke-owned','source':{'type':'sim','fps':2},
                'zones':[{'id':'z','polygon':[[0,0],[1,0],[1,1],[0,1]]}],
                'hands':{'enabled':False},'persons':{'enabled':False},'detector':{'enabled':False},
                'safety':{'enabled':False,'rules':[{'id':'disabled','kind':'person_absent','zone':'z'}]}}
        path=root/'station.json';path.write_text(json.dumps(config),encoding='utf8')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        with (root/'server.log').open('wb') as log:
            child=subprocess.Popen([str(exe),'serve',str(path),'--host','127.0.0.1','--port',str(port),
                                    '--data',str(root/'data'),'--auth'],cwd=root,env=env,stdin=subprocess.DEVNULL,
                                   stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:
                deadline=time.monotonic()+timeout
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
                while True:
                    if child.poll() is not None:raise RuntimeError('Owned server exited before health check')
                    try:
                        with opener.open(f'http://127.0.0.1:{port}/api/health',timeout=1) as response:health=json.load(response)
                        if health.get('ok') is True and health.get('stations')==1:break
                    except (OSError,ValueError):pass
                    if time.monotonic()>deadline:raise RuntimeError('Owned server health timeout')
                    time.sleep(.2)
                try:
                    opener.open(f'http://127.0.0.1:{port}/api/stations',timeout=3)
                    raise RuntimeError('Auth gate allowed anonymous API access')
                except urllib.error.HTTPError as exc:
                    if exc.code!=401:raise
                request=urllib.request.Request(f'http://127.0.0.1:{port}/api/auth/login',
                    data=json.dumps({'username':'smoke-admin','password':password}).encode(),
                    headers={'Content-Type':'application/json'})
                with opener.open(request,timeout=3) as response:
                    if not json.load(response).get('ok'):raise RuntimeError('Runtime login failed')
                with opener.open(f'http://127.0.0.1:{port}/api/stations',timeout=3) as response:
                    if b'bundle-smoke-owned' not in response.read():raise RuntimeError('Owned station missing after login')
                results['checks']['auth_login']='passed'
                with opener.open(f'http://127.0.0.1:{port}/',timeout=3) as response:
                    if b'<html' not in response.read(2*1024*1024).lower():raise RuntimeError('Web assets not served')
                results['checks']['owned_health_web']='passed'
            finally:
                if child.poll() is None:
                    child.terminate()
                    try:child.wait(timeout=10)
                    except subprocess.TimeoutExpired:child.kill();child.wait(timeout=10)
                results['owned_process_stopped']=child.poll() is not None
    return results


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe',required=True);parser.add_argument('--assets',required=True)
    parser.add_argument('--gauge-fixture');parser.add_argument('--rul-fixture');parser.add_argument('--output')
    args=parser.parse_args()
    try:
        report=verify(args.exe,args.assets,rul_fixture=args.rul_fixture,gauge_fixture=args.gauge_fixture);code=0
    except Exception as exc:report={'status':'failed','error':str(exc)};code=1
    if args.output:Path(args.output).write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf8')
    print(json.dumps(report,ensure_ascii=True));raise SystemExit(code)
