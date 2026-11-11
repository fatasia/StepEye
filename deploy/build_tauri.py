"""用指定运行包构建 Tauri 桌面安装包，不修改桌面源码或现有构建。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
try:
    from .runtime_provenance import verify_provenance
except ImportError:
    from runtime_provenance import verify_provenance

ROOT = Path(__file__).resolve().parents[1]


def stage(runtime: Path, output: Path, version: str) -> Path:
    runtime, output = runtime.resolve(), output.resolve()
    if not (runtime / 'stepeye-runtime.exe').is_file() or not (runtime / '_internal').is_dir():
        raise ValueError('运行目录不完整')
    verify_provenance(ROOT,runtime)
    if output.exists() or output == ROOT or output in ROOT.parents or output.is_relative_to(ROOT):
        raise ValueError('请使用源码目录外的新构建目录')
    desktop = ROOT / 'desktop'
    output.mkdir(parents=True)
    shutil.copytree(desktop / 'src-tauri', output / 'src-tauri',
                    ignore=shutil.ignore_patterns('runtime', 'target', 'gen'))
    shutil.copytree(desktop / 'ui', output / 'ui')
    shutil.copytree(runtime, output / 'src-tauri' / 'runtime')
    config = output / 'src-tauri' / 'tauri.conf.json'
    settings = json.loads(config.read_text(encoding='utf8'))
    settings['version'] = version
    config.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--version', default='0.10.0')
    args = parser.parse_args()
    if sys.platform != 'win32': parser.error('Windows 安装包需在 Windows 上构建')
    try: output = stage(args.runtime, args.output, args.version)
    except ValueError as error: parser.error(str(error))
    npx = shutil.which('npx.cmd') or shutil.which('npx')
    if not npx: parser.error('需要安装 Node.js 和 npm')
    env = {**os.environ, 'CARGO_TARGET_DIR': str(output / 'target')}
    return subprocess.run([npx, '--yes', '@tauri-apps/cli@2', 'build'], cwd=output, env=env).returncode


if __name__ == '__main__':
    raise SystemExit(main())
