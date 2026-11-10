"""CUDA selection stays outside capture threads and the default CPU environment."""
from types import SimpleNamespace
import sys
import pytest
from visual.action_runtime import worker_python, session_options

class Options:
    def __init__(self): self.entries = {}
    def add_session_config_entry(self, key, value): self.entries[key] = value


def test_cpu_does_not_preload_cuda(monkeypatch):
    monkeypatch.setenv('STEPEYE_ACTION_DLL_DIRECTORY', 'missing-directory')
    ort = SimpleNamespace(SessionOptions=Options, preload_dlls=lambda **kw: pytest.fail('CPU preloaded CUDA'))
    options = session_options(ort, ['CPUExecutionProvider'], 2)
    assert options.intra_op_num_threads == 2 and options.entries == {}


def test_cuda_strict_and_explicit_mixed_provider(tmp_path, monkeypatch):
    monkeypatch.setenv('STEPEYE_ACTION_DLL_DIRECTORY', str(tmp_path))
    loaded = []
    ort = SimpleNamespace(SessionOptions=Options, preload_dlls=lambda **kw: loaded.append(kw))
    options = session_options(ort, ['CUDAExecutionProvider'], 2)
    assert loaded == [{'directory': str(tmp_path)}]
    assert options.entries['session.disable_cpu_ep_fallback'] == '1'
    assert not session_options(ort, ['CUDAExecutionProvider','CPUExecutionProvider'], 2).entries


def test_missing_explicit_cuda_directory_rejected(monkeypatch):
    monkeypatch.setenv('STEPEYE_ACTION_DLL_DIRECTORY', 'missing-directory')
    with pytest.raises(ValueError, match='directory'):
        session_options(SimpleNamespace(SessionOptions=Options), ['CUDAExecutionProvider'], 2)


def test_worker_python_is_deployment_config(monkeypatch):
    monkeypatch.delenv('STEPEYE_ACTION_PYTHON', raising=False)
    assert worker_python() == sys.executable
    monkeypatch.setenv('STEPEYE_ACTION_PYTHON', '/isolated/python')
    assert worker_python() == '/isolated/python'
