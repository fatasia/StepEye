"""校验构建运行包与当前源码一致，防止桌面包带入旧版页面。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def digest(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def source_hashes(root: Path) -> dict[str,str]:
    policy=json.loads((root/'deploy/runtime-assets.json').read_text(encoding='utf8'))
    result={}
    for name in ('web','visual','configs'):
        for path in sorted((root/name).rglob('*')):
            if not path.is_file():continue
            relative=path.relative_to(root)
            if any(part in policy['forbidden_parts'] for part in relative.parts):continue
            if name=='visual' and path.suffix!='.py' and not path.name.endswith('_LICENSE.txt'):continue
            if path.suffix.lower() in policy['forbidden_suffixes'] or path.name.startswith('.') or path.name.endswith('.env'):continue
            if path.is_symlink():raise ValueError('源码资源不能是符号链接')
            result[relative.as_posix()]=digest(path)
    return result


def write_provenance(root: Path, runtime: Path) -> None:
    build_files=('deploy/build_runtime.py','deploy/runtime_provenance.py','deploy/runtime_build.spec','deploy/runtime_entry.py','deploy/requirements-runtime.txt',
                 'deploy/runtime-assets.json','requirements-training.txt')
    values={'schema':1,'runtime_sha256':digest(runtime/'stepeye-runtime.exe'),
            'build_files':{name:digest(root/name) for name in build_files}, 'sources':source_hashes(root)}
    path=runtime/'_internal/deploy/runtime-provenance.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(values,ensure_ascii=False,sort_keys=True),encoding='utf8')


def verify_provenance(root: Path, runtime: Path) -> None:
    path=runtime/'_internal/deploy/runtime-provenance.json'
    if not path.is_file():raise ValueError('运行包缺少构建指纹，请先重新构建运行包')
    try: saved=json.loads(path.read_text(encoding='utf8'))
    except (OSError,ValueError):raise ValueError('运行包构建指纹无效') from None
    current=source_hashes(root)
    build_files=('deploy/build_runtime.py','deploy/runtime_provenance.py','deploy/runtime_build.spec','deploy/runtime_entry.py','deploy/requirements-runtime.txt',
                 'deploy/runtime-assets.json','requirements-training.txt')
    expected_build={name:digest(root/name) for name in build_files}
    if saved.get('schema')!=1 or saved.get('sources')!=current or saved.get('build_files')!=expected_build:
        raise ValueError('运行包与当前源码不一致，请先重新构建运行包')
    if saved.get('runtime_sha256')!=digest(runtime/'stepeye-runtime.exe'):
        raise ValueError('运行程序与构建指纹不一致')
    for name,expected in current.items():
        asset=runtime/'_internal'/name
        if not asset.is_file() or digest(asset)!=expected:
            raise ValueError(f'运行包资源与源码不一致: {name}')
