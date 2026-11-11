"""组件配置及管理员主动发起的独立训练环境安装。"""
import importlib.util
import json
import shutil
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid
from urllib.parse import urlsplit, parse_qs

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from .auth import AuthError, AuthStore
from .recipes import _atomic_write

_OPTIONS_LOCK=threading.RLock()


class RuntimeBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    training: str = ''
    notification_urls: list[str] | None = Field(default=None,max_length=8)


class InspectionBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1,max_length=80)
    instruction: str = Field(min_length=3,max_length=1500)


class WvpBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    url: str = Field(default='',max_length=2048)
    username: str = Field(default='',max_length=200)
    password: str = Field(default='',max_length=500)


def candidates(manager):
    root=Path(__file__).resolve().parents[1]
    executable='Scripts/python.exe' if sys.platform=='win32' else 'bin/python'
    return {**{name:root/'.v0'/name/executable for name in ('train-venv','notify-venv')},
            'managed-training':manager.data_dir/'components'/'training'/executable}


def training_requirements():
    root=Path(__file__).resolve().parents[1]
    path=root/'requirements-training.txt'
    return path if path.is_file() else root/'deploy'/'requirements-training.txt'


def python_command():
    choices=[]
    if not getattr(sys,'frozen',False): choices.append([sys.executable])
    if sys.platform=='win32' and shutil.which('py'):
        choices.extend([[shutil.which('py'),f'-3.{version}'] for version in (13,12,11,10)])
    for name in ('python3','python'):
        if shutil.which(name): choices.append([shutil.which(name)])
    for command in choices:
        try:
            result=subprocess.run(command+['-c','import sys; assert (3,10) <= sys.version_info[:2] < (3,14)'],
                                  capture_output=True,timeout=10)
            if result.returncode==0: return command
        except (OSError,subprocess.TimeoutExpired): pass
    raise ValueError('请先安装 Python 3.10 至 3.13，并勾选加入 PATH')


class InstallationCancelled(Exception):
    pass


def run_install_command(command, timeout, cancelled):
    import os
    import signal
    options={'stdin':subprocess.DEVNULL,'stdout':subprocess.DEVNULL,'stderr':subprocess.DEVNULL}
    if sys.platform=='win32':options['creationflags']=getattr(subprocess,'CREATE_NO_WINDOW',0)
    else:options['start_new_session']=True
    if cancelled.is_set():raise InstallationCancelled()
    process=subprocess.Popen(command,**options)
    started=time.monotonic()
    try:
        while process.poll() is None:
            if cancelled.wait(.25):raise InstallationCancelled()
            if time.monotonic()-started>timeout:raise subprocess.TimeoutExpired(command,timeout)
        if process.returncode:raise subprocess.CalledProcessError(process.returncode,command)
    finally:
        if process.poll() is None:
            if sys.platform=='win32':
                try:subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                except (OSError,subprocess.SubprocessError):pass
            else:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
            if process.poll() is None:process.kill()
            process.wait()


def install_training(manager, state, cancelled=None):
    cancelled=cancelled or threading.Event()
    try:
        command=python_command()
        requirements=training_requirements()
        if not requirements.is_file(): raise ValueError('训练依赖清单缺失')
        python=candidates(manager)['managed-training']
        directory=python.parent.parent
        directory.parent.mkdir(parents=True,exist_ok=True)
        state.update(phase='creating',message='正在创建独立环境')
        run_install_command(command+['-m','venv',str(directory)],180,cancelled)
        state.update(phase='installing',message='正在下载并安装训练组件，可能需要较长时间')
        run_install_command([str(python),'-m','pip','install','--disable-pip-version-check','-r',str(requirements)],2700,cancelled)
        state.update(phase='checking',message='正在检查训练组件')
        run_install_command([str(python),'-c','import torch,ultralytics,onnx'],60,cancelled)
        if cancelled.is_set():raise InstallationCancelled()
        update_options(manager,{'training':'managed-training'})
        manager.training.python=str(python)
        state.update(phase='ready',message='训练环境已安装并启用')
    except InstallationCancelled:
        state.update(phase='cancelled',message='安装已取消，可稍后重新安装')
    except ValueError as exc:
        state.update(phase='error',message=str(exc))
    except subprocess.TimeoutExpired:
        state.update(phase='error',message='安装超时，请检查网络和磁盘空间后重试')
    except (OSError,subprocess.SubprocessError):
        message={'creating':'创建独立环境失败，请检查 Python 安装和目录权限',
                 'installing':'训练组件安装失败，请检查网络、磁盘空间后重试',
                 'checking':'组件检查失败，请重新安装训练组件'}.get(state.get('phase'),'训练组件安装失败')
        state.update(phase='error',message=message)
    finally:
        if 'started_at' in state:state['finished_at']=time.monotonic()


def read_options(manager):
    path=manager.data_dir/'runtime-options.json'
    return json.loads(path.read_text(encoding='utf8')) if path.exists() else {}


def update_options(manager, changes):
    with _OPTIONS_LOCK:
        options=read_options(manager);options.update(changes)
        path=manager.data_dir/'runtime-options.json'
        _atomic_write(path,json.dumps(options).encode());path.chmod(0o600)
        return options


def wvp_options(manager):
    import os
    from .llm_provider import _default_env_path, load_env_file
    env={**load_env_file(_default_env_path()),**os.environ}
    saved=read_options(manager).get('gb28181',{})
    return {'url':saved.get('url') or env.get('GB28181_WVP_URL',''),
            'username':saved.get('username') or env.get('GB28181_USER','admin'),
            'password':saved.get('password') or env.get('GB28181_PASS','')}


def require_setup_admin(request):
    try:AuthStore.check(request.state.user,'admin')
    except AuthError as exc:raise HTTPException(exc.status,str(exc)) from None
    if request.state.user.get('username')=='local' and request.client.host not in ('127.0.0.1','::1','testclient'):
        raise HTTPException(403,'请在本机配置，或启用认证后由管理员操作')


def apply_options(manager, *, training=True, notifications=True):
    options=read_options(manager)
    name=options.get('training','')
    if training and name in candidates(manager) and candidates(manager)[name].is_file():manager.training.python=str(candidates(manager)[name])
    if notifications and 'notification_urls' in options:
        from .notifications import NotificationDispatcher
        manager.notifications.close()
        python=str(candidates(manager)['notify-venv']) if candidates(manager)['notify-venv'].exists() else sys.executable
        manager.notifications=NotificationDispatcher({'STEPEYE_NOTIFY_URLS':json.dumps(options['notification_urls'])},python_executable=python)


def router(manager):
    api=APIRouter()
    install_state={'phase':'idle','message':''}
    install_lock=threading.Lock()
    install_cancel=threading.Event()
    def admin(request):
        require_setup_admin(request)

    @api.get('/api/setup')
    def status(request:Request):
        admin(request)
        from .anomaly import dinov2_path
        try:dinov2_path(); dino=True
        except ValueError:dino=False
        progress=dict(install_state)
        started=progress.pop('started_at',None);finished=progress.pop('finished_at',None)
        progress['elapsed_seconds']=int((finished or time.monotonic())-started) if started else 0
        return {'training':manager.training.capabilities(), 'environments':[{'id':k,'name':{'train-venv':'独立训练环境','notify-venv':'独立通知环境','managed-training':'组件训练环境'}[k]} for k,p in candidates(manager).items() if p.is_file()],
                'training_install':progress, 'gb28181':{'configured':bool(wvp_options(manager)['url'])},
                'dinov2':dino,'onnx':importlib.util.find_spec('onnxruntime') is not None,
                'notifications':manager.notifications.snapshot(),'inspection':manager.inspection is not None}

    @api.put('/api/setup')
    def configure(body:RuntimeBody,request:Request):
        admin(request)
        options={}
        if body.training:
            path=candidates(manager).get(body.training)
            if not path or not path.is_file():raise HTTPException(409,'没有找到此独立环境')
            try:
                result=subprocess.run([str(path),'-c','import torch,ultralytics,onnx; print("ready")'],capture_output=True,timeout=20)
                if result.returncode:raise ValueError()
            except (subprocess.TimeoutExpired,ValueError):raise HTTPException(409,'环境检查失败，请安装完整训练依赖') from None
            options['training']=body.training
        if body.notification_urls is not None:
            from .notifications import _channels
            urls=[]
            for address in body.notification_urls:
                uri=urlsplit(address)
                if uri.scheme=='https':
                    if uri.netloc=='open.feishu.cn' and uri.path.startswith('/open-apis/bot/v2/hook/'):
                        address='feishu://'+uri.path.rsplit('/',1)[-1]
                    elif uri.netloc=='qyapi.weixin.qq.com' and uri.path=='/cgi-bin/webhook/send':
                        address='wecombot://'+parse_qs(uri.query).get('key',[''])[0]
                    elif uri.netloc=='oapi.dingtalk.com' and uri.path=='/robot/send':
                        address='dingtalk://'+parse_qs(uri.query).get('access_token',[''])[0]
                urls.append(address)
            _,channels,errors=_channels({'STEPEYE_NOTIFY_URLS':json.dumps(urls)})
            if errors:raise HTTPException(400,'通知地址格式不正确，请检查渠道与令牌')
            options['notification_urls']=urls
        update_options(manager,options)
        apply_options(manager,training=bool(body.training),notifications=body.notification_urls is not None)
        return {'ok':True}

    @api.put('/api/setup/gb28181')
    def configure_wvp(body:WvpBody,request:Request):
        admin(request)
        with _OPTIONS_LOCK:
            current=wvp_options(manager)
            url=(body.url.strip() or current['url']).rstrip('/')
            try:
                parts=urlsplit(url)
                valid=parts.scheme in ('http','https') and parts.hostname and not (parts.username or parts.password or parts.query or parts.fragment)
                _=parts.port
            except ValueError:
                valid=False
            if not valid:
                raise HTTPException(400,'请输入不含账号和参数的 HTTP 或 HTTPS 平台地址')
            update_options(manager,{'gb28181':{'url':url,'username':body.username.strip() or current['username'],
                                               'password':body.password or current['password']}})
        return {'ok':True}

    @api.post('/api/setup/training-install',status_code=202)
    def start_training_install(request:Request):
        admin(request)
        with install_lock:
            if install_state['phase'] in ('starting','creating','installing','checking','cancelling'):
                raise HTTPException(409,'训练环境正在安装')
            install_cancel.clear()
            install_state.pop('finished_at',None)
            install_state.update(phase='starting',message='正在准备安装',started_at=time.monotonic())
            threading.Thread(target=install_training,args=(manager,install_state,install_cancel),daemon=True).start()
        return dict(install_state)

    @api.post('/api/setup/training-cancel',status_code=202)
    def cancel_training_install(request:Request):
        admin(request)
        with install_lock:
            if install_state['phase'] not in ('starting','creating','installing','checking','cancelling'):
                raise HTTPException(409,'当前没有正在安装的训练组件')
            install_cancel.set()
            install_state.update(phase='cancelling',message='正在取消安装')
        return {'ok':True}

    @api.post('/api/setup/notification-test',status_code=202)
    def test_notification(request:Request):
        admin(request)
        return manager.notifications.submit({'id':'test-'+uuid.uuid4().hex,'station':'系统设置','kind':'test','severity':'info','message':'StepEye 通知测试'})

    @api.post('/api/setup/inspection',status_code=201)
    def inspection(body:InspectionBody,request:Request):
        admin(request)
        from .inspection import InspectionService,InspectionStore,Scenario
        from .llm_provider import VLMProvider,ProviderConfig
        from .ai_services import DynamicAIProvider
        import yaml
        sid='check-'+uuid.uuid4().hex[:8]
        scenario=Scenario(sid,body.name,body.instruction+'\n只返回 JSON：{"verdict":"PASS|NG|UNCERTAIN","confidence":0到1,"reason":"简短依据"}',True)
        path=manager.data_dir/'inspection-scenes.yaml'
        raw=yaml.safe_load(path.read_text(encoding='utf8')) if path.exists() else {'scenarios':[]}
        raw['scenarios'].append(scenario.__dict__)
        _atomic_write(path,yaml.safe_dump(raw,allow_unicode=True).encode())
        if manager.inspection is None:
            manager.inspection=InspectionService([scenario],InspectionStore(manager.data_dir/'sop.db'),manager.data_dir/'inspection',
                DynamicAIProvider(manager.ai_services,VLMProvider(ProviderConfig.from_env())),publish=manager._on_inspection_msg,refs_root=manager.data_dir)
            manager.inspection.start()
        else:manager.inspection.scenarios[sid]=scenario
        return {'id':sid,'name':body.name}
    return api
