"""Process/HTTP doubles verify lifecycle, not actual model inference."""
from dataclasses import replace
import socket
import threading
import time
import httpx
import pytest
from visual.local_vlm import LocalVLMConfig, LocalVLMTransport, LocalVLMError, LocalVLMBusy

class Process:
    def __init__(self):
        self.code = None
        self.terminated = 0
    def poll(self): return self.code
    def terminate(self):
        self.terminated += 1
        self.code = 0
    def kill(self): self.code = -9
    def wait(self, timeout): return self.code

class Client:
    def __init__(self):
        self.posts = []
        self.ready = True
        self.entered = self.release = None
        self.fail = False
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def get(self, url, **kwargs):
        return httpx.Response(200 if self.ready else 503, json={'status': 'ok' if self.ready else 'loading'})
    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        if self.entered:
            self.entered.set()
            assert self.release.wait(2)
        if self.fail: raise httpx.ReadTimeout('fixture')
        return httpx.Response(200, json={'choices': []}, request=httpx.Request('POST', url))

@pytest.fixture
def setup(tmp_path):
    paths = [tmp_path / n for n in ('server.exe', 'model.gguf', 'mmproj.gguf')]
    for path in paths: path.write_bytes(b'fixture')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    cfg = LocalVLMConfig(*paths, port=port, idle_s=.05, startup_timeout_s=.15)
    processes, commands, options, http_options = [], [], [], []
    client = Client()
    def popen(command, **kwargs):
        commands.append(command)
        options.append(kwargs)
        process = Process()
        processes.append(process)
        return process
    def factory(**kwargs):
        http_options.append(kwargs)
        return client
    transport = LocalVLMTransport(cfg, popen=popen, client_factory=factory)
    yield transport, cfg, client, processes, commands, options, http_options
    transport.close()

def until(predicate):
    end = time.monotonic() + 2
    while not predicate() and time.monotonic() < end: time.sleep(.005)
    assert predicate()

def test_lazy_idle_release_restart_and_no_cloud_key(setup):
    transport, cfg, client, processes, commands, options, http_options = setup
    assert not processes and transport._idle_thread is None
    transport.post(cfg.endpoint, {'Authorization': 'cloud-secret'}, {'model': 'cloud', 'stream': True})
    request = client.posts[0][1]
    assert request['headers']['Authorization'] != 'cloud-secret'
    assert request['json']['model'] == 'stepeye-local' and request['json']['stream'] is False
    assert commands[0][commands[0].index('--host') + 1] == '127.0.0.1'
    assert commands[0][commands[0].index('--n-gpu-layers') + 1] == '99'
    assert options[0]['shell'] is False and http_options[0]['trust_env'] is False
    until(lambda: transport._idle_thread is None)
    assert processes[0].terminated == 1 and transport.snapshot()['state'] == 'cold'
    transport.post(cfg.endpoint, {}, {})
    assert len(processes) == 2

def test_warm_process_reused(setup):
    transport, cfg, _, processes, *_ = setup
    transport.config = replace(cfg, idle_s=.3)
    transport.post(cfg.endpoint, {}, {})
    transport.post(cfg.endpoint, {}, {})
    assert len(processes) == 1

def test_busy_immediate_and_close_cancels_inflight(setup):
    transport, cfg, client, processes, *_ = setup
    client.entered, client.release = threading.Event(), threading.Event()
    errors = []
    def first():
        try: transport.post(cfg.endpoint, {}, {})
        except LocalVLMError as e: errors.append(str(e))
    thread = threading.Thread(target=first)
    thread.start()
    assert client.entered.wait(1)
    start = time.monotonic()
    with pytest.raises(LocalVLMBusy): transport.post(cfg.endpoint, {}, {})
    assert time.monotonic() - start < .1
    transport.close()
    client.release.set()
    thread.join(2)
    assert errors and processes[0].terminated == 1
    with pytest.raises(LocalVLMError, match='已关闭'): transport.post(cfg.endpoint, {}, {})
    assert len(processes) == 1 and transport._idle_thread is None

def test_health_timeout_releases_without_retry(setup):
    transport, cfg, client, processes, *_ = setup
    client.ready = False
    with pytest.raises(LocalVLMError, match='启动超时'): transport.post(cfg.endpoint, {}, {})
    assert len(processes) == 1 and processes[0].terminated == 1
    assert not client.posts and transport._idle_thread is None

def test_request_timeout_stops_process(setup):
    transport, cfg, client, processes, *_ = setup
    client.fail = True
    with pytest.raises(LocalVLMError, match='请求失败或超时'): transport.post(cfg.endpoint, {}, {})
    assert processes[0].terminated == 1

def test_occupied_port_does_not_touch_external_process(setup):
    transport, cfg, _, processes, *_ = setup
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', cfg.port))
        occupied.listen()
        with pytest.raises(LocalVLMError, match='占用'): transport.post(cfg.endpoint, {}, {})
        assert not processes and occupied.getsockname()[1] == cfg.port

def test_missing_model_and_wrong_endpoint(setup):
    transport, cfg, _, processes, *_ = setup
    with pytest.raises(LocalVLMError, match='固定回环'): transport.post('http://example.com', {}, {})
    cfg.mmproj.unlink()
    with pytest.raises(LocalVLMError, match='文件缺失'): transport.post(cfg.endpoint, {}, {})
    assert not processes

def test_config_values_and_bounds(tmp_path):
    assert LocalVLMConfig.from_values({}) is None
    with pytest.raises(ValueError): LocalVLMConfig.from_values({'STEPEYE_LOCAL_VLM_BIN': 'server.exe'})
    cfg = LocalVLMConfig.from_values({'STEPEYE_LOCAL_VLM_BIN': 'bin/server.exe',
        'STEPEYE_LOCAL_VLM_MODEL': 'models/a.gguf', 'STEPEYE_LOCAL_VLM_MMPROJ': 'models/b.gguf'})
    assert cfg.binary.is_absolute() and cfg.gpu_layers == 99 and cfg.request_timeout_s == 15
    with pytest.raises(ValueError): replace(cfg, port=80)
    with pytest.raises(ValueError): replace(cfg, idle_s=float('nan'))

def test_process_crash_no_automatic_restart(setup):
    transport, cfg, _, processes, *_ = setup
    transport.config = replace(cfg, idle_s=.3)
    transport.post(cfg.endpoint, {}, {})
    processes[0].code = 2
    time.sleep(.02)
    assert len(processes) == 1
    transport.post(cfg.endpoint, {}, {})
    assert len(processes) == 2

def test_singleton_configuration_and_close_all(tmp_path):
    import visual.local_vlm as module
    module.close_all()
    cfg = LocalVLMConfig(tmp_path/'server', tmp_path/'model.gguf', tmp_path/'mmproj.gguf')
    first = module.get_transport(cfg)
    assert module.get_transport(cfg) is first
    with pytest.raises(LocalVLMError): module.get_transport(replace(cfg, port=cfg.port+1))
    module.close_all()
    assert first.snapshot()['state'] == 'closed'

def test_terminate_escalates_only_owned_process():
    import subprocess
    class Stubborn(Process):
        def terminate(self): self.terminated += 1
        def wait(self, timeout):
            if self.code is None: raise subprocess.TimeoutExpired('fixture', timeout)
            return self.code
    owned = Stubborn()
    LocalVLMTransport._terminate(owned)
    assert owned.terminated == 1 and owned.code == -9


def test_start_failure_and_exit_are_bounded(setup):
    transport, cfg, client, processes, *_ = setup
    def failed(*args, **kwargs): raise OSError('fixture missing dll')
    transport._popen = failed
    with pytest.raises(LocalVLMError, match='启动失败'): transport.post(cfg.endpoint, {}, {})
    assert not processes and transport._idle_thread is None
    def exited(*args, **kwargs):
        process = Process()
        process.code = 2
        processes.append(process)
        return process
    transport._popen = exited
    with pytest.raises(LocalVLMError, match='已退出'): transport.post(cfg.endpoint, {}, {})
    assert len(processes) == 1 and not client.posts
