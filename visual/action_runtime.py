"""Explicit action-worker environment and CUDA DLL loading, outside capture threads."""
import os
import sys
from pathlib import Path


def worker_python():
    return os.environ.get('STEPEYE_ACTION_PYTHON') or sys.executable


def session_options(ort, providers, threads):
    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    if providers[0] == 'CUDAExecutionProvider':
        # Reuse a deployed CUDA/cuDNN directory without importing the training stack.
        directory = os.environ.get('STEPEYE_ACTION_DLL_DIRECTORY')
        if directory and not Path(directory).is_dir():
            raise ValueError('Action CUDA library directory is unavailable')
        if hasattr(ort, 'preload_dlls'):
            ort.preload_dlls(directory=directory)
        if 'CPUExecutionProvider' not in providers:
            options.add_session_config_entry('session.disable_cpu_ep_fallback', '1')
    return options
