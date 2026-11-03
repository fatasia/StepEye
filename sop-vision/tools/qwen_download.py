"""Download pinned Qwen/llama assets with curl resume, bounded time and SHA256 checks."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import zipfile


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()


def fetch(item,root,timeout):
    if Path(item['name']).name != item['name']:
        return {'name':item['name'],'status':'invalid_filename'}
    path=root/item['name']; part=path.with_name(path.name+'.part')
    if path.exists():
        if path.stat().st_size==item['bytes'] and digest(path)==item['sha256']:
            return {'name':item['name'],'status':'verified','bytes':item['bytes']}
        return {'name':item['name'],'status':'existing_file_mismatch','bytes':path.stat().st_size}
    curl=shutil.which('curl.exe') or shutil.which('curl')
    if not curl: return {'name':item['name'],'status':'download_failed','error':'curl_not_found'}
    try:
        result=subprocess.run([curl,'--silent','--show-error','--location','--fail','--connect-timeout','15',
             '--max-time',str(timeout),'--continue-at','-','--output',str(part),item['url']],capture_output=True,
             timeout=timeout+20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    except (subprocess.TimeoutExpired,OSError) as exc:
        return {'name':item['name'],'status':'download_failed','error':type(exc).__name__,
                'bytes':part.stat().st_size if part.exists() else 0,'expected_bytes':item['bytes']}
    actual=part.stat().st_size if part.exists() else 0
    if result.returncode or actual!=item['bytes']:
        return {'name':item['name'],'status':'partial' if actual else 'download_failed','bytes':actual,
                'expected_bytes':item['bytes'],'curl_exit':result.returncode}
    if digest(part)!=item['sha256']:
        return {'name':item['name'],'status':'hash_mismatch','bytes':actual}
    part.replace(path)
    return {'name':item['name'],'status':'verified','bytes':actual}


def extract_runtime(root,items):
    out=(root/'runtime').resolve(); out.mkdir(exist_ok=True)
    for item in items:
        if item['role']!='runtime': continue
        path=root/item['name']
        if not path.is_file() or digest(path)!=item['sha256']: raise ValueError('Runtime must be verified before extraction')
        if path.suffix == '.zip':
            with zipfile.ZipFile(path) as z:
                for entry in z.infolist():
                    target=(out/entry.filename).resolve()
                    if not target.is_relative_to(out) or stat.S_ISLNK(entry.external_attr>>16):
                        raise ValueError('Unsafe runtime archive member')
                z.extractall(out)
        else:
            with tarfile.open(path) as archive:
                for member in archive.getmembers():
                    if not (out/member.name).resolve().is_relative_to(out) or not (member.isfile() or member.isdir()):
                        raise ValueError('Unsafe runtime archive member')
                archive.extractall(out,filter='data')
    return str(out)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[2]/'.v0/qwen-local')
    p.add_argument('--manifest',type=Path,default=Path(__file__).resolve().parents[1]/'deploy/qwen-local-manifest.json')
    p.add_argument('--timeout',type=int,default=300); p.add_argument('--extract',action='store_true')
    p.add_argument('--workers',type=int,choices=[1,2],default=2)
    args=p.parse_args()
    if not 15<=args.timeout<=3600: p.error('timeout must be15..3600 seconds per asset')
    args.root.mkdir(parents=True,exist_ok=True)
    manifest=json.loads(args.manifest.read_text(encoding='utf-8'))
    items=[i for i in manifest['files'] if i['platform'] in ('all','windows' if sys.platform=='win32' else 'linux')]
    def safe_fetch(item):
        try: return fetch(item,args.root,args.timeout)
        except Exception as exc:
            return {'name':item.get('name','unknown'),'status':'download_failed','error':type(exc).__name__}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results=list(pool.map(safe_fetch,items))
    report={'model_revision':manifest['model_revision'],'llama_tag':manifest['llama_tag'],'files':results,'extraction_status':'not_attempted'}
    if args.extract and all(r['status']=='verified' for r in results):
        try:
            report['runtime']=extract_runtime(args.root,items)
            report['extraction_status']='complete'
        except (OSError,ValueError,zipfile.BadZipFile,tarfile.TarError) as exc:
            report['extraction_status']='failed'
            report['extraction_error']=type(exc).__name__
    (args.root/'download-status.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))
    return 0 if all(r['status']=='verified' for r in results) and report.get('extraction_status')!='failed' else 2

if __name__=='__main__': raise SystemExit(main())
