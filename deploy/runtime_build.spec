# -*- mode: python ; coding: utf-8 -*-
"""Build a complete inference runtime from the explicit staged asset manifest."""
import os
from pathlib import Path
from PyInstaller.utils.hooks import (
    collect_data_files, collect_dynamic_libs, collect_submodules,
)

ROOT = Path(SPECPATH).parent
STAGE = Path(os.environ['STEPEYE_RUNTIME_STAGE']).resolve()
if not (STAGE / 'web' / 'index.html').is_file():
    raise ValueError('Runtime staging directory must contain web/index.html')

# Assets are curated by runtime_assets.py; never collect the repository models tree.
datas = [(str(STAGE / name), name) for name in
         ('web', 'configs', 'docs', 'models', 'deploy', 'visual') if (STAGE / name).is_dir()]
datas += collect_data_files('rapidocr_onnxruntime')
def runtime_module(name):
    # Library unit tests and benchmarks are not inference entry points.
    return not {'test', 'tests', 'benchmark', 'benchmarks'}.intersection(name.split('.'))


hidden = []
for package in ('visual', 'mediapipe.tasks', 'rapidocr_onnxruntime',
                'mcp', 'apprise', 'harvesters', 'websockets'):
    hidden += collect_submodules(package, filter=runtime_module)
hidden += ['uvicorn.logging', 'uvicorn.loops.auto',
           'uvicorn.protocols.http.auto', 'uvicorn.protocols.websockets.auto',
           'uvicorn.protocols.websockets.websockets_impl',
           'uvicorn.lifespan.on', 'scipy', 'onnxruntime', 'cryptography', 'PIL',
           'zxingcpp', 'matplotlib.backends.backend_agg']

analysis = Analysis(
    [str(ROOT / 'deploy' / 'runtime_entry.py')], pathex=[str(ROOT)],
    binaries=collect_dynamic_libs('mediapipe'), datas=datas,
    hiddenimports=sorted(set(hidden)), hookspath=[],
    hooksconfig={'matplotlib': {'backends': ['Agg']}}, runtime_hooks=[],
    excludes=['torch', 'torchvision', 'torchaudio', 'transformers', 'timm',
              'ultralytics', 'onnx', 'sklearn', 'skl2onnx', 'pytest', 'playwright',
              'IPython', 'jupyter', 'notebook', 'PyQt5', 'PyQt6', 'PySide2',
              'PySide6', 'tkinter', 'sounddevice', '_sounddevice_data',
              'mediapipe.tasks.python.test', 'mediapipe.tasks.python.benchmark'],
    noarchive=False, optimize=1,
)
archive = PYZ(analysis.pure)
icon = STAGE / 'web' / 'assets' / 'stepeye.ico'
exe = EXE(
    archive, analysis.scripts, [], exclude_binaries=True,
    name='stepeye-runtime', debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=True,
    icon=str(icon) if icon.is_file() else None,
)
collect = COLLECT(exe, analysis.binaries, analysis.datas,
                  strip=False, upx=False, name='stepeye-runtime')
