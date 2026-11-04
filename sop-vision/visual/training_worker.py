"""Isolated local YOLO training worker; imported by the server without importing torch."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import sys
import time

import yaml

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.bmp', '.webp'}
SPEC_KEYS = {'dataset_yaml', 'base_model', 'output_dir', 'epochs', 'batch', 'device',
             'imgsz', 'base_sha256', 'dataset_version', 'dataset_sha256'}


def fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _local(path: Path, root: Path) -> Path:
    """Reject symlinked components as well as resolved paths outside the dataset."""
    if not path.resolve().is_relative_to(root):
        raise ValueError('数据文件路径越界')
    cursor = path
    while cursor != root:
        if cursor.is_symlink():
            raise ValueError('数据目录不允许符号链接')
        cursor = cursor.parent
    return path


def _json(path: Path) -> dict:
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError('数据清单过大')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('数据清单必须是对象')
    return value


def inspect_training_dataset(dataset_yaml: str | Path) -> dict:
    """Lightweight immutable-content fingerprint and structural split validation.

    This checks data structure, not label correctness, commercial rights or field accuracy.
    Parent jobs can call this without loading any inference/training runtime.
    """
    source = Path(dataset_yaml)
    if source.is_symlink() or not source.is_file() or source.stat().st_size > 1024 * 1024:
        raise ValueError('需要本机 dataset.yaml 文件')
    source = source.resolve()
    root = source.parent
    data = yaml.safe_load(source.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or set(data) - {'path', 'train', 'val', 'nc', 'names'}:
        raise ValueError('数据 YAML 仅允许 path/train/val/nc/names，禁止下载或额外指令')
    if data.get('path', '.') not in ('.', '') or any(data.get(s) != f'images/{s}' for s in ('train', 'val')):
        raise ValueError('数据路径必须为本目录 images/train 和 images/val')
    classes = data.get('names')
    if isinstance(classes, dict) and list(sorted(classes, key=str)):
        if set(classes) != set(range(len(classes))):
            raise ValueError('类别编号必须从0连续排列')
        classes = [classes[i] for i in range(len(classes))]
    if (not isinstance(classes, list) or not 1 <= len(classes) <= 100 or
            any(not isinstance(c, str) or not c.strip() or c != c.strip() or len(c) > 100 for c in classes) or
            len(set(classes)) != len(classes) or data.get('nc', len(classes)) != len(classes)):
        raise ValueError('需要非空且唯一的检测类别表')
    files = {source.relative_to(root).as_posix(): fingerprint(source)}
    metadata, manifest = {}, {}
    for name in ('classes.txt', 'metadata.json', 'manifest.json'):
        path = root / name
        if path.exists():
            _local(path, root)
            files[name] = fingerprint(path)
            if name == 'classes.txt' and path.read_text(encoding='utf-8-sig').splitlines() != classes:
                raise ValueError('classes.txt 与 YAML 类别顺序不一致')
            if name == 'metadata.json': metadata = _json(path)
            if name == 'manifest.json': manifest = _json(path)
    negatives = metadata.get('reviewed_negatives', [])
    hashes, counts, class_counts, assets = {}, {}, {}, {}
    annotation_by_image = {}
    from PIL import Image
    for split in ('train', 'val'):
        folder = _local(root / 'images' / split, root)
        labels = _local(root / 'labels' / split, root)
        if not folder.is_dir() or not labels.is_dir():
            raise ValueError('必须有独立 train/val 图片与标签目录')
        entries = list(folder.rglob('*'))
        if any(p.is_symlink() or (p.is_file() and p.suffix.lower() not in IMAGE_EXTS) for p in entries):
            raise ValueError('图片目录包含链接或不支持的文件')
        images = sorted(p for p in entries if p.is_file())
        if not images or len(images) > 100000:
            raise ValueError('每个集合必须有1–100000张图片')
        counts[split], class_counts[split], stems = len(images), set(), set()
        for image in images:
            _local(image, root)
            relative = image.relative_to(folder)
            if relative.with_suffix('') in stems:
                raise ValueError('同一集合存在同名不同扩展图片')
            stems.add(relative.with_suffix(''))
            if image.stat().st_size > 64 * 1024 * 1024:
                raise ValueError('单图超过64MB')
            with Image.open(image) as decoded:
                if getattr(decoded, 'n_frames', 1) != 1 or decoded.width * decoded.height > 40000000:
                    raise ValueError('需要单帧且不超过4000万像素的图片')
                decoded.verify()
            digest = fingerprint(image)
            if digest in hashes and hashes[digest] != split:
                raise ValueError('相同SHA图像跨训练/验证集合')
            hashes[digest] = split
            files[image.relative_to(root).as_posix()] = digest
            label = _local(labels / relative.with_suffix('.txt'), root)
            if not label.is_file() or label.stat().st_size > 1024 * 1024:
                raise ValueError('缺少配对标签或标签文件过大')
            lines = [line for line in label.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
            label_rel = label.relative_to(root).as_posix()
            if not lines and label_rel not in negatives:
                raise ValueError('空标签必须在metadata reviewed_negatives中人工确认')
            for line in lines:
                fields = line.split()
                if len(fields) != 5 or not re.fullmatch(r'\d+', fields[0]):
                    raise ValueError('仅支持YOLO检测框，不能把分割标注当检测框')
                cid = int(fields[0])
                x, y, w, h = map(float, fields[1:])
                if (not 0 <= cid < len(classes) or not all(math.isfinite(v) for v in (x, y, w, h)) or
                        not (0 < w <= 1 and 0 < h <= 1) or
                        min(x-w/2, y-h/2) < -1e-6 or max(x+w/2, y+h/2) > 1+1e-6):
                    raise ValueError('标签类别或坐标无效')
                class_counts[split].add(cid)
            # One image cannot teach contradictory targets, even within one split.
            # Numeric canonicalization ignores line order and harmless text formatting.
            canonical_labels = tuple(sorted((int(line.split()[0]), *(
                round(float(v), 6) for v in line.split()[1:])) for line in lines))
            label_signature = hashlib.sha256(json.dumps(canonical_labels, separators=(',', ':')).encode()).hexdigest()
            prior = annotation_by_image.get(digest)
            if prior is not None and prior[0] != label_signature:
                raise ValueError(f'相同SHA图像标注冲突: {prior[1]} / {label_rel}')
            annotation_by_image[digest] = (label_signature, label_rel)
            files[label_rel] = fingerprint(label)
            assets[image.stem] = (split, digest)
        if class_counts[split] != set(range(len(classes))):
            raise ValueError('每个类别必须在训练集和验证集都有正样本')
    if manifest:
        declared = manifest.get('assets', [])
        if not isinstance(declared, list) or len(declared) != len(assets) or len({a.get('id') for a in declared}) != len(assets):
            raise ValueError('版本清单未完整覆盖图片')
        if metadata.get('manifest_sha256') and metadata['manifest_sha256'] != files['manifest.json']:
            raise ValueError('版本清单SHA256不一致')
        groups = {}
        for asset in manifest.get('assets', []):
            if asset.get('state') not in ('confirmed', 'background'):
                raise ValueError('版本包含未经人工确认的标签')
            actual = assets.get(asset['id'])
            assignment = manifest.get('assignments', {}).get(asset['id'], {})
            if not actual or assignment.get('split') != actual[0] or asset.get('image_sha256') != actual[1]:
                raise ValueError('版本清单与图片指纹或集合不一致')
            group = assignment.get('group')
            if group in groups and groups[group] != actual[0]:
                raise ValueError('同一来源组跨训练/验证集合')
            groups[group] = actual[0]
            if asset.get('synthetic') and actual[0] == 'val':
                raise ValueError('合成样本不能进入独立验证集')
    canonical = json.dumps(sorted(files.items()), ensure_ascii=False, separators=(',', ':')).encode()
    return {'dataset_sha256': hashlib.sha256(canonical).hexdigest(), 'classes': classes,
            'images': counts, 'files': files, 'dataset_version': metadata.get('dataset_version_id', ''),
            'root': str(root)}


def _spec(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) != SPEC_KEYS:
        raise ValueError('训练规格字段不完整或包含额外字段')
    for name, low, high in (('epochs', 1, 300), ('batch', 1, 64), ('imgsz', 640, 640)):
        if type(value[name]) is not int or not low <= value[name] <= high:
            raise ValueError(f'{name} 超出允许范围')
    if value['device'] not in ('cpu', '0'):
        raise ValueError('device只支持cpu或单GPU 0')
    for name in ('base_sha256', 'dataset_sha256'):
        if not isinstance(value[name], str) or not re.fullmatch('[a-f0-9]{64}', value[name]):
            raise ValueError('必须固定基座与数据SHA256')
    if not isinstance(value['dataset_version'], str) or not value['dataset_version'] or len(value['dataset_version']) > 100:
        raise ValueError('必须提供数据版本ID')
    return dict(value)


def _runtime(output: Path):
    """Only called inside the disposable worker; disable downloads and integrations."""
    os.environ.update(YOLO_CONFIG_DIR=str(output / 'runtime-config'), YOLO_AUTOINSTALL='false',
                      YOLO_OFFLINE='true', WANDB_MODE='disabled', HF_HUB_OFFLINE='1')
    def offline(*args, **kwargs):
        raise OSError('训练worker禁止网络访问，请预先安装本机依赖和权重')
    socket.socket.connect = offline
    socket.socket.connect_ex = offline
    from ultralytics import YOLO, settings
    settings.update({name: False for name in ('sync', 'clearml', 'comet', 'dvc', 'mlflow', 'raytune', 'tensorboard', 'wandb')})
    return YOLO


def _onnx_contract(path: Path, classes: list[str]) -> None:
    import onnx
    graph = onnx.load(str(path), load_external_data=False)
    onnx.checker.check_model(graph)
    shape = lambda item: [d.dim_value for d in item.type.tensor_type.shape.dim]
    inputs, outputs = graph.graph.input, graph.graph.output
    if (len(inputs) != 1 or inputs[0].type.tensor_type.elem_type != 1 or shape(inputs[0]) != [1, 3, 640, 640] or
            len(outputs) != 1 or outputs[0].type.tensor_type.elem_type != 1 or len(shape(outputs[0])) != 3 or shape(outputs[0])[0] != 1 or
            shape(outputs[0])[1] != 4 + len(classes) or shape(outputs[0])[2] < 1000 or
            any(t.data_location == onnx.TensorProto.EXTERNAL for t in graph.graph.initializer)):
        raise ValueError('导出不是平台支持的单文件float32 YOLO原始输出')


def run_training(spec: dict, *, model_factory=None, export_check=None) -> dict:
    """Train -> independent validation -> raw ONNX candidate, never production apply."""
    spec = _spec(spec)
    base, output = Path(spec['base_model']), Path(spec['output_dir'])
    if base.is_symlink() or not base.is_file() or base.suffix.lower() != '.pt':
        raise ValueError('基座必须是管理员批准的本机PT文件')
    base, output = base.resolve(), output.absolute()
    if output.exists() or output.is_symlink():
        raise ValueError('输出目录必须不存在，历史训练结果不可覆盖')
    info = inspect_training_dataset(spec['dataset_yaml'])
    if output.resolve().is_relative_to(Path(info['root'])):
        raise ValueError('输出目录不能写入数据版本目录')
    if fingerprint(base) != spec['base_sha256'] or info['dataset_sha256'] != spec['dataset_sha256']:
        raise ValueError('基座或数据指纹变化，拒绝训练')
    if info['dataset_version'] and info['dataset_version'] != spec['dataset_version']:
        raise ValueError('数据版本ID不一致')
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    factory = model_factory or _runtime(output)
    # Generate a sanitized local-only YAML; never let trainer interpret user download keys.
    local_yaml = output / 'data.yaml'
    local_yaml.write_text(yaml.safe_dump({'path': info['root'], 'train': 'images/train', 'val': 'images/val',
                                        'names': info['classes'], 'nc': len(info['classes'])}, allow_unicode=True), encoding='utf-8')
    (output / 'spec.json').write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding='utf-8')
    model = factory(str(base), task='detect')
    model.train(data=str(local_yaml), epochs=spec['epochs'], batch=spec['batch'], device=spec['device'],
                imgsz=640, project=str(output), name='train', exist_ok=False, workers=0, amp=False,
                plots=False, save=True, cache=False, seed=42, deterministic=True, pretrained=False)
    best = Path(model.trainer.best).resolve()
    if not best.is_file() or not best.is_relative_to(output.resolve()):
        raise ValueError('训练没有生成本任务best.pt')
    trained = factory(str(best), task='detect')
    names = trained.names
    names = [names[i] for i in range(len(names))] if isinstance(names, dict) else list(names)
    if names != info['classes']:
        raise ValueError('训练模型类别顺序与数据版本不一致')
    validation = trained.val(data=str(local_yaml), split='val', imgsz=640, batch=spec['batch'],
                             device=spec['device'], workers=0, plots=False, project=str(output), name='validation')
    metrics = {str(k): float(v) for k, v in validation.results_dict.items()}
    if not metrics or not all(math.isfinite(v) for v in metrics.values()):
        raise ValueError('验证未返回有限指标')
    exported = Path(trained.export(format='onnx', imgsz=640, batch=1, dynamic=False,
                                  simplify=False, nms=False, opset=17, half=False, device='cpu')).resolve()
    if not exported.is_file() or exported.suffix.lower() != '.onnx' or not exported.is_relative_to(output.resolve()):
        raise ValueError('导出文件不在本训练任务目录')
    (export_check or _onnx_contract)(exported, names)
    if inspect_training_dataset(spec['dataset_yaml'])['dataset_sha256'] != spec['dataset_sha256'] or fingerprint(base) != spec['base_sha256']:
        raise ValueError('训练期间基座或数据发生变化，不接受候选结果')
    result = {'status': 'candidate', 'model_path': str(exported), 'model_sha256': fingerprint(exported),
              'checkpoint_path': str(best), 'checkpoint_sha256': fingerprint(best), 'classes': names,
              'metrics': metrics, 'base_sha256': spec['base_sha256'], 'dataset_sha256': spec['dataset_sha256'],
              'dataset_version': spec['dataset_version'], 'images': info['images'], 'elapsed_s': time.monotonic()-started,
              'production_ready': False, 'evaluation_scope': 'held-out validation; not independent field acceptance'}
    payload = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    for name in ('metrics.json', 'result.json'):
        (output / name).write_text(payload, encoding='utf-8')
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec', required=True, type=Path, help='Trusted local training-spec JSON')
    args = parser.parse_args(argv)
    try:
        if args.spec.stat().st_size > 65536: raise ValueError('训练规格过大')
        result = run_training(json.loads(args.spec.read_text(encoding='utf-8')))
        print(json.dumps({'status': result['status'], 'model_sha256': result['model_sha256']}))
        return 0
    except Exception as exc:
        print(json.dumps({'status': 'failed', 'error_type': type(exc).__name__, 'error': str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
