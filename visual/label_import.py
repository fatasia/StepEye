"""Bounded YOLO ZIP ingestion. External labels remain unconfirmed drafts."""
from __future__ import annotations

import hashlib
import io
import json
import math
from pathlib import PurePosixPath
import stat
import zipfile

from PIL import Image
import yaml

from .labels import LabelError, LabelStore

MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_IMAGES = 200
IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.bmp', '.webp'}


def _entries(archive):
    entries = {}
    total = 0
    infos = archive.infolist()
    if len(infos) > 1000:
        raise LabelError('压缩包最多1000个条目', 413)
    for info in infos:
        name = info.orig_filename
        path = PurePosixPath(name)
        if (not name or len(name) > 500 or '\\' in name or ':' in name or name.startswith('/')
                or any(ord(c) < 32 for c in name) or any(p in ('', '.', '..') for p in name.rstrip('/').split('/'))):
            raise LabelError('压缩包包含非法路径')
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
            raise LabelError('压缩包不支持链接或特殊文件')
        if info.flag_bits & 1:
            raise LabelError('不支持加密压缩包')
        total += info.file_size
        if total > MAX_ARCHIVE_BYTES or info.file_size > 20 * 1024 * 1024:
            raise LabelError('解压总量最多100MB、单文件最多20MB', 413)
        key = str(path).casefold()
        if key in entries:
            raise LabelError('压缩包存在重复或大小写冲突路径')
        entries[key] = info
    return {str(PurePosixPath(v.filename)): v for v in entries.values() if not v.is_dir()}


def _read(archive, info, limit):
    if info.file_size > limit:
        raise LabelError('标注或类别文件超过大小限制', 413)
    with archive.open(info) as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise LabelError('文件超过大小限制', 413)
    return data


def _classes(archive, entries):
    candidates = [p for p in entries if PurePosixPath(p).name in ('classes.txt', 'data.yaml', 'dataset.yaml')]
    roots = {str(PurePosixPath(p).parent) for p in candidates}
    if len(roots) != 1:
        raise LabelError('请在数据集根目录提供 classes.txt 或 data.yaml 的 names；只支持一个数据集')
    root = next(iter(roots))
    lists = []
    for name in candidates:
        text = _read(archive, entries[name], 64 * 1024).decode('utf-8-sig')
        if name.endswith('.txt'):
            names = text.splitlines()
        else:
            # File paths in dataset YAML are deliberately ignored.
            data = yaml.safe_load(text)
            names = data.get('names') if isinstance(data, dict) else None
            if isinstance(names, dict):
                if list(sorted(names, key=str)) and all(type(k) is int for k in names) and set(names) == set(range(len(names))):
                    names = [names[i] for i in range(len(names))]
                else:
                    raise LabelError('YAML names 必须是连续整数索引或列表')
        if (not isinstance(names, list) or not 1 <= len(names) <= 100
                or any(not isinstance(n, str) or not n.strip() or n != n.strip() or len(n) > 100 for n in names)
                or len(set(names)) != len(names)):
            raise LabelError('类别表须为1–100个不重复名称')
        lists.append(names)
    if any(v != lists[0] for v in lists):
        raise LabelError('压缩包中的多个类别表不一致')
    return ('' if root == '.' else root + '/'), lists[0]


def _targets(raw, mapping):
    lines = raw.decode('utf-8-sig').splitlines()
    if len(lines) > 500:
        raise LabelError('每张最多500个检测框')
    targets = []
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 5:
            raise LabelError(f'第{n}行不是YOLO检测框；分割和姿态标注暂不支持')
        try:
            cid = int(fields[0])
            cx, cy, w, h = map(float, fields[1:])
        except ValueError:
            raise LabelError(f'第{n}行类别或坐标无效') from None
        xy = [cx-w/2, cy-h/2, cx+w/2, cy+h/2]
        if (cid not in mapping or not all(math.isfinite(v) for v in xy)
                or not (0 <= xy[0] < xy[2] <= 1 and 0 <= xy[1] < xy[3] <= 1)):
            raise LabelError(f'第{n}行类别或框范围无效')
        targets.append({'label_id': mapping[cid], 'xyxy_norm': xy})
    return targets


def import_dataset(labels: LabelStore, pid: str, raw: bytes, actor: str,
                   group_id: str = '', license_ref: str = '', synthetic: bool = False) -> dict:
    project = labels.get_project(pid)
    if type(synthetic) is not bool:
        raise LabelError('synthetic 必须为布尔值')
    if not raw or len(raw) > MAX_ARCHIVE_BYTES:
        raise LabelError('ZIP 不能为空且最多100MB', 413)
    if not isinstance(group_id, str) or len(group_id) > 200 or any(ord(c) < 32 for c in group_id):
        raise LabelError('来源组无效')
    if len(license_ref) > 500 or any(ord(c) < 32 for c in license_ref):
        raise LabelError('许可引用无效')
    digest = hashlib.sha256(raw).hexdigest()
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = _entries(archive)
            root, names = _classes(archive, entries)
            if root + 'manifest.json' in entries:
                manifest = json.loads(_read(archive, entries[root + 'manifest.json'], 1024 * 1024))
                if not isinstance(manifest, dict) or not isinstance(manifest.get('assets'), list):
                    raise LabelError('manifest.json 格式无效')
                # Conservative round-trip: any synthetic source keeps this whole group train-only.
                synthetic = synthetic or any(isinstance(a, dict) and a.get('synthetic') for a in manifest['assets'])
                synthetic = bool(synthetic)
            missing = [n for n in names if n not in project['classes']]
            if missing:
                raise LabelError('项目缺少类别：' + '、'.join(missing))
            mapping = {i: project['classes'].index(name) for i, name in enumerate(names)}
            prefix = root + 'images/'
            images = sorted(p for p in entries if p.startswith(prefix) and PurePosixPath(p).suffix.lower() in IMAGE_EXTS)
            if not 1 <= len(images) <= MAX_IMAGES:
                raise LabelError('images/ 中需要1–200张图片', 413)
            result = {'items': [], 'errors': [], 'skipped': [], 'total': len(images), 'classes': names,
                      'warnings': ['导入标注均待人工复核；空标签不会自动确认为背景。',
                                   '不沿用ZIP中的训练/验证划分；默认整包作为同一来源组，避免视频相邻帧跨集合。']}
            for name in images:
                try:
                    relative = PurePosixPath(name[len(prefix):])
                    label_name = root + 'labels/' + str(relative.with_suffix('.txt'))
                    targets = None
                    if label_name in entries:
                        targets = _targets(_read(archive, entries[label_name], 256 * 1024), mapping)
                    image = _read(archive, entries[name], 20 * 1024 * 1024)
                    if targets is not None:
                        with Image.open(io.BytesIO(image)) as decoded:
                            if decoded.getexif().get(274, 1) != 1:
                                raise LabelError('带EXIF旋转的标注图请先统一方向并重新导出，避免框错位')
                    asset = labels.add_asset(pid, image, relative.name, {
                        'source_kind': 'dataset_import', 'synthetic': synthetic, 'group_id': group_id.strip() or 'dataset-' + digest,
                        'license_ref': license_ref or project['license_ref'], 'archive_sha256': digest,
                        'import_path': name, 'original_split': relative.parts[0] if len(relative.parts) > 1 else '',
                    })
                    if targets is not None:
                        asset = labels.save_draft(asset['id'], targets, 0, actor, producer='human',
                                                  provenance={'source': 'yolo_zip', 'archive_sha256': digest, 'path': label_name})
                    result['items'].append(asset)
                except (LabelError, ValueError, OSError, Image.DecompressionBombError, zipfile.BadZipFile) as exc:
                    result['errors'].append({'name': name, 'error': str(exc)[:300]})
            result.update(imported=len(result['items']), failed=len(result['errors']))
            return result
    except LabelError:
        raise
    except (zipfile.BadZipFile, UnicodeError, yaml.YAMLError, ValueError, NotImplementedError, RuntimeError, RecursionError):
        raise LabelError('ZIP 或类别文件无法读取；请使用标准未加密YOLO数据集') from None
