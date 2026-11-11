"""隔离 ONVIF 依赖的进程环境，避免相机库修改主服务环境变量。"""
import contextlib
import json
import sys


def main():
    from .onvif import device_rtsp_and_geo, wsdiscovery_probe
    spec=json.load(sys.stdin)
    if spec.get('action')=='discover':
        rows=wsdiscovery_probe(spec['timeout_s'])
        return {'ok':True,'devices':[{'xaddr':str(r.get('xaddr',''))[:2048],
                 'scopes':[str(s)[:200] for s in r.get('scopes',[])[:10]]} for r in rows[:128]]}
    if spec.get('action')=='adopt':
        return {'ok':True,**device_rtsp_and_geo(spec['host'],spec['port'],spec['user'],spec['password'])}
    raise ValueError('无效的相机操作')


if __name__=='__main__':
    try:
        with contextlib.redirect_stdout(sys.stderr):result=main()
    except Exception:
        result={'ok':False,'error':'ONVIF 操作失败，请检查设备、网络和账号'}
    print(json.dumps(result,ensure_ascii=True))
