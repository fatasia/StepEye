"""Build an audited onedir portable ZIP; never copy whole models/ or developer environments."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT
POLICY = Path(__file__).with_name('runtime-assets.json')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def asset_plan(root=ROOT, *, include_local_vlm=False, include_quality_extensions=False, monibuca=None,
               platform_label='windows'):
    root = Path(root).resolve()
    policy = json.loads(POLICY.read_text(encoding='utf8'))
    rows, omitted, seen = [], [], set()
    def add(source, target, group, *, expected_sha=None, required=False, review=False):
        source, target = Path(source), Path(target)
        parts = target.parts
        if (target.is_absolute() or '..' in parts or any(p in policy['forbidden_parts'] for p in parts)
                or source.is_symlink() or target.suffix.lower() in policy['forbidden_suffixes']
                or target.name == '.env' or target.name.endswith('.env')):
            raise ValueError(f'Forbidden runtime asset: {target}')
        if not source.is_file():
            if required: raise FileNotFoundError(f'Required runtime asset unavailable: {target}')
            omitted.append({'target': target.as_posix(), 'reason': 'not_installed'})
            return
        if target.as_posix() in seen: return
        actual = digest(source)
        if expected_sha and actual != expected_sha:
            raise ValueError(f'Asset fingerprint mismatch: {target}')
        seen.add(target.as_posix())
        rows.append({'source': str(source.resolve()), 'target': target.as_posix(), 'group': group,
                     'bytes': source.stat().st_size, 'sha256': actual, 'redistribution_review_required': review})
    for name in policy['source_trees']:
        folder = root / name
        for path in sorted(folder.rglob('*')):
            if not path.is_file(): continue
            relative = path.relative_to(root)
            if any(p in policy['forbidden_parts'] for p in relative.parts): continue
            if name == 'visual' and path.suffix != '.py' and not path.name.endswith('_LICENSE.txt'): continue
            if path.suffix.lower() in policy['forbidden_suffixes'] or path.name.startswith('.'): continue
            add(path, relative, 'application')
    for name in policy['core_models']:
        add(root / name, name, 'core_perception', required=True)
    optional_quality = policy.get('optional_quality_catalogs', [])
    for catalog in [*policy['catalogs'], *optional_quality]:
        relative = Path('models/public') / catalog
        path = root / relative
        if not path.is_file(): continue
        add(path, relative, 'model_metadata')
        data = json.loads(path.read_text(encoding='utf8'))
        if catalog in optional_quality and not include_quality_extensions:
            omitted.extend({'target': item['model_path'], 'reason': 'optional_quality_extension'}
                           for item in data.get('models', []))
            continue
        for item in data.get('models', []):
            name = item['model_path']
            resolved = (root / name).resolve()
            if not resolved.is_relative_to(root / 'models/public'):
                raise ValueError('Catalog model outside models/public')
            add(resolved, name, 'catalog_model', expected_sha=item['sha256'], review=True)
    for name in policy['extra_models']:
        add(root / name, name, 'perception_candidate', review=True)
    for folder in policy['mechanism_bundles']:
        for path in sorted((root / folder).glob('*')):
            if path.suffix.lower() in {'.onnx', '.json', '.txt', '.md'} or path.name.startswith('LICENSE'):
                add(path, path.relative_to(root), 'mechanism_candidate', review=True)
    default_media = REPO / '.v0/monibuca/monibuca_windows_amd64.exe' if platform_label == 'windows' else None
    media = Path(monibuca) if monibuca else default_media
    if media is None or not media.is_file():
        raise FileNotFoundError(f'{platform_label} 构建需要 --monibuca 指向原生媒体二进制')
    media_target = ('deploy/monibuca/monibuca_windows_amd64.exe' if platform_label == 'windows'
                    else f'deploy/monibuca/monibuca_{platform_label}_amd64')
    add(media, media_target, 'native_media', required=True, review=True)
    add(root / 'deploy/monibuca/config.yml', 'deploy/monibuca/config.yml', 'media_config', required=True)
    add(root / 'deploy/monibuca/runtime-v6.yaml', 'deploy/monibuca/runtime-v6.yaml', 'media_config')
    add(root / 'deploy/portable-README.md', 'deploy/portable-README.md', 'application')
    if include_local_vlm:
        local = REPO / '.v0/qwen-local'
        for name in ['Qwen3VL-2B-Instruct-Q4_K_M.gguf', 'mmproj-Qwen3VL-2B-Instruct-F16.gguf']:
            add(local / name, Path('deploy/local-vlm') / name, 'local_vlm', required=True, review=True)
        for path in sorted((local / 'runtime').glob('*')):
            if path.suffix.lower() == '.dll' or path.name.lower() in {'llama-server.exe', 'license', 'license.txt'}:
                add(path, Path('deploy/local-vlm/runtime') / path.name, 'local_vlm', review=True)
        if not any(row['target'].endswith('/llama-server.exe') for row in rows):
            raise FileNotFoundError('Local VLM runtime unavailable')
    return {'schema_version': 1, 'created_at': time.time(), 'platform': sys.platform,
            'platform_label': platform_label, 'assets': rows, 'omitted_optional_assets': omitted,
            'asset_bytes': sum(row['bytes'] for row in rows),
            'redistribution_review_required': any(row['redistribution_review_required'] for row in rows),
            'external_components': policy['external_components'],
            'local_vlm_included': include_local_vlm, 'quality_extensions_included': include_quality_extensions, 'artifact_kind': 'onedir_portable_not_installer'}


def stage_assets(plan, stage):
    stage = Path(stage).resolve()
    stage.mkdir(parents=True, exist_ok=True)
    for row in plan['assets']:
        target = (stage / row['target']).resolve()
        if not target.is_relative_to(stage): raise ValueError('Stage escape')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(row['source'], target)
        if digest(target) != row['sha256']: raise ValueError('Staged asset changed')


def verify_assets(plan, base):
    base = Path(base)
    errors = [row['target'] for row in plan['assets']
              if not (base / row['target']).is_file() or digest(base / row['target']) != row['sha256']]
    if errors: raise ValueError(f'Packaged asset mismatch: {errors[:5]}')
    return {'verified_assets': len(plan['assets']), 'asset_bytes': plan['asset_bytes'], 'mismatches': []}


def size_report(directory):
    directory = Path(directory)
    files = [(path, path.stat().st_size) for path in directory.rglob('*') if path.is_file()]
    groups = {}
    for path, size in files:
        relative = path.relative_to(directory)
        group = '/'.join(relative.parts[:2])
        groups[group] = groups.get(group, 0) + size
    return {'files': len(files), 'uncompressed_bytes': sum(size for _, size in files),
            'largest_groups': dict(sorted(groups.items(), key=lambda item: -item[1])[:25]),
            'largest_files': [{'path': path.relative_to(directory).as_posix(), 'bytes': size}
                              for path, size in sorted(files, key=lambda item: -item[1])[:20]]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--audit-only', action='store_true')
    parser.add_argument('--stage-only', action='store_true')
    parser.add_argument('--allow-evaluation-assets', action='store_true')
    parser.add_argument('--include-local-vlm', action='store_true')
    parser.add_argument('--include-quality-extensions', action='store_true', help='Bundle optional glue/pin segmentation weights')
    parser.add_argument('--monibuca', type=Path)
    parser.add_argument('--platform-label', default='windows' if sys.platform == 'win32' else
                        ('linux' if sys.platform.startswith('linux') else sys.platform),
                        help='产物平台标签(用于媒体二进制路径与 ZIP 名)')
    parser.add_argument('--skip-zip', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    if output == REPO or output in ROOT.parents or output.is_relative_to(ROOT):
        parser.error('Choose a separate new build directory')
    if (output / 'dist/stepeye-runtime').exists(): parser.error('Existing output is preserved; use a new directory')
    output.mkdir(parents=True, exist_ok=True)
    plan = asset_plan(include_local_vlm=args.include_local_vlm, include_quality_extensions=args.include_quality_extensions,
                      monibuca=args.monibuca, platform_label=args.platform_label)
    plan['evaluation_asset_opt_in'] = args.allow_evaluation_assets
    (output / 'assets-before.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({'asset_files': len(plan['assets']), 'asset_bytes': plan['asset_bytes'],
                      'redistribution_review_required': plan['redistribution_review_required']}), flush=True)
    if args.audit_only: return 0
    if plan['redistribution_review_required'] and not args.allow_evaluation_assets:
        parser.error('Pending model/media rights review; --allow-evaluation-assets is local evaluation only')
    stage = output / 'assets'
    stage_assets(plan, stage)
    (output / 'stage-verification.json').write_text(json.dumps(verify_assets(plan, stage), indent=2), encoding='utf8')
    if args.stage_only: return 0
    env = {**os.environ, 'STEPEYE_RUNTIME_STAGE': str(stage), 'MPLBACKEND': 'Agg'}
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--distpath', str(output / 'dist'),
               '--workpath', str(output / 'work'), str(ROOT / 'deploy/runtime_build.spec')]
    with (output / 'build.log').open('wb') as log:
        completed = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    if completed.returncode:
        print(f'Build failed; see {output / "build.log"}', file=sys.stderr)
        return completed.returncode
    bundle = output / 'dist/stepeye-runtime'
    verified = verify_assets(plan, bundle / '_internal')
    readme = ROOT / 'deploy/portable-README.md'
    if readme.is_file(): shutil.copy2(readme, bundle / 'README.md')
    (output / 'assets-after.json').write_text(json.dumps(verified, indent=2), encoding='utf8')
    requirements = sorted({f'{d.metadata["Name"]}=={d.version}' for d in importlib.metadata.distributions()})
    (output / 'installed-runtime.txt').write_text('\n'.join(requirements) + '\n', encoding='utf8')
    report = size_report(bundle)
    report.update(asset_verification=verified, local_vlm_included=args.include_local_vlm,
                  quality_extensions_included=args.include_quality_extensions,
                  artifact_kind='onedir_portable_not_installer', commercial_distribution_approved=False,
                  evaluation_assets_opted_in=args.allow_evaluation_assets)
    if not args.skip_zip:
        archive = output / f'stepeye-runtime-{args.platform_label}.zip'
        with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for path in sorted(bundle.rglob('*')):
                if path.is_file(): z.write(path, path.relative_to(bundle.parent).as_posix())
        report.update(zip_bytes=archive.stat().st_size, zip_sha256=digest(archive),
                      below_100mb=archive.stat().st_size < 100_000_000)
    (output / 'size-report.json').write_text(json.dumps(report, indent=2), encoding='utf8')
    print(json.dumps(report, ensure_ascii=True), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
