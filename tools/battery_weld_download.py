"""Bounded, resumable original-host range download for OPENZDM 14025630."""
from pathlib import Path
import concurrent.futures,hashlib,json,time,subprocess,re,os
SIZE=1153183844
MD5='f187dac063cf656bb627327fa9771d16'
URL='https://zenodo.org/api/records/14025630/files/Weld%20Aesthetical%20Defects.zip/content'


def curl_range(start,end,target,deadline):
    temp=target.with_suffix('.part');have=temp.stat().st_size if temp.exists() else 0
    expected=end-start+1
    if have>expected:raise ValueError('oversize partial range')
    if have==expected:temp.replace(target);return
    first=start+have;headers=target.with_suffix('.headers');segment=target.with_suffix('.segment')
    budget=max(1,min(120,int(deadline-time.monotonic())))
    cmd=['curl.exe' if os.name=='nt' else 'curl','--http1.1','--tlsv1.2','--tls-max','1.2',
         '--location','--fail','--silent','--show-error','--connect-timeout','10',
         '--max-time',str(budget),'--speed-limit','1024','--speed-time','30','--retry','0',
         '--header',f'Range: bytes={first}-{end}','--dump-header',str(headers),
         '--output',str(segment),URL]
    result=subprocess.run(cmd,capture_output=True,timeout=budget+5,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    text=headers.read_text(errors='replace') if headers.exists() else ''
    ranges=re.findall(r'^content-range:\s*(bytes [^\r\n]+)',text,re.I|re.M)
    codes=re.findall(r'^HTTP/\S+\s+(\d+)',text,re.M)
    if not codes or codes[-1]!='206' or not ranges or ranges[-1]!=f'bytes {first}-{end}/{SIZE}':
        raise ValueError('curl range rejected')
    length=segment.stat().st_size if segment.exists() else 0
    if not 0<length<=end-first+1:raise ValueError('curl range length invalid')
    # Verified 206 prefixes remain resumable on a bounded timeout; never mark incomplete as done.
    with temp.open('ab') as output,segment.open('rb') as source:
        for block in iter(lambda:source.read(262144),b''):output.write(block)
    segment.unlink()
    if temp.stat().st_size!=expected:raise TimeoutError('partial curl range retained')
    if result.returncode:raise ValueError('curl nonzero exit')
    temp.replace(target)


def download(root,seconds=480,transport="requests"):
    # curl is a standalone transport; importing this tool must not require requests.
    request_errors=()
    if transport=="requests":
        import requests
        request_errors=(requests.RequestException,)
    elif transport!="curl":
        raise ValueError('unknown download transport')
    root=Path(root);root.mkdir(parents=True,exist_ok=True);parts=root/'full-parts';parts.mkdir(exist_ok=True)
    prefix=root/'ev-weld-v1.zip.part';prefix_size=prefix.stat().st_size if prefix.exists() else 0
    plan=root/'download-plan.json'
    if plan.exists():
        base=json.loads(plan.read_text())['prefix_size']
        if prefix_size!=base:raise ValueError('prefix changed since range plan')
    else:
        base=prefix_size;plan.write_text(json.dumps({'url':URL,'total':SIZE,'prefix_size':base,'chunk':4*1024*1024}),encoding='utf8')
    deadline=time.monotonic()+seconds;errors=[];completed=0
    jobs=[(start,min(SIZE-1,start+4*1024*1024-1)) for start in range(base,SIZE,4*1024*1024)]
    def fetch(job):
        start,end=job;target=parts/f'{start}-{end}.bin'
        if target.exists() and target.stat().st_size==end-start+1:return True
        if time.monotonic()>deadline:return False
        try:
            if transport=="curl":
                curl_range(start,end,target,deadline)
                return True
            with requests.get(URL,headers={'Range':f'bytes={start}-{end}'},stream=True,timeout=(10,25)) as r:
                if r.status_code!=206 or r.headers.get('Content-Range')!=f'bytes {start}-{end}/{SIZE}':raise ValueError('range rejected')
                temp=target.with_suffix('.part');total=0
                with temp.open('wb') as f:
                    for chunk in r.iter_content(262144):
                        if time.monotonic()>deadline:raise TimeoutError('budget exceeded')
                        total+=len(chunk)
                        if total>end-start+1:raise ValueError('oversize response')
                        f.write(chunk)
                if total!=end-start+1:raise ValueError('short range')
                temp.replace(target)
            return True
        except (TimeoutError,ValueError,OSError,subprocess.TimeoutExpired)+request_errors as exc:
            errors.append({'range':job,'error':type(exc).__name__});return False
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for ok in pool.map(fetch,jobs):
            completed+=bool(ok)
            if completed and completed%25==0:print('completed chunks',completed,'/',len(jobs),flush=True)
    report={'url':URL,'prefix_bytes':base,'complete_chunks':completed,'total_chunks':len(jobs),'errors':errors,'complete':False}
    if completed==len(jobs):
        target=root/'ev-weld-v1.zip';digest=hashlib.md5()
        with target.open('wb') as output:
            for piece in ([prefix] if base else [])+[parts/f'{start}-{end}.bin' for start,end in jobs]:
                with piece.open('rb') as f:
                    for chunk in iter(lambda:f.read(1024*1024),b''):output.write(chunk);digest.update(chunk)
        report['actual_md5']=digest.hexdigest();report['complete']=target.stat().st_size==SIZE and digest.hexdigest()==MD5
    (root/'download-status.json').write_text(json.dumps(report,indent=2),encoding='utf8');print(json.dumps(report),flush=True)


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('directory');p.add_argument('--seconds',type=int,default=480);p.add_argument('--transport',choices=['requests','curl'],default='requests');a=p.parse_args()
    if not 30<=a.seconds<=1200:raise ValueError('budget must be 30..1200 seconds')
    download(a.directory,a.seconds,a.transport)
