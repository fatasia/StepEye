"""Export the pinned public hairnet candidate on CPU; no remote pickle execution."""
from __future__ import annotations

import argparse
import hashlib
import importlib
from pathlib import Path
import pickletools
import zipfile

SOURCE_SHA256 = 'bd4b28125aeb0208fed815df1e50c5f44f2f545dc4188b8625e9b398d9b6c8dc'
ALLOWED_GLOBALS = {
    '__builtin__ set', 'collections OrderedDict', 'torch FloatStorage',
    'torch HalfStorage', 'torch LongStorage', 'torch Size',
    'torch._utils _rebuild_parameter', 'torch._utils _rebuild_tensor_v2',
    'torch.nn.modules.activation SiLU', 'torch.nn.modules.batchnorm BatchNorm2d',
    'torch.nn.modules.container ModuleList', 'torch.nn.modules.container Sequential',
    'torch.nn.modules.conv Conv2d', 'torch.nn.modules.pooling MaxPool2d',
    'torch.nn.modules.upsampling Upsample', 'ultralytics.nn.modules.block Bottleneck',
    'ultralytics.nn.modules.block C2f', 'ultralytics.nn.modules.block DFL',
    'ultralytics.nn.modules.block SPPF', 'ultralytics.nn.modules.conv Concat',
    'ultralytics.nn.modules.conv Conv', 'ultralytics.nn.modules.head Detect',
    'ultralytics.nn.tasks DetectionModel',
}


def checkpoint_globals(path: Path) -> list[str]:
    """Require the exact reviewed artifact before resolving any checkpoint class."""
    if hashlib.sha256(path.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise ValueError('Hairnet checkpoint SHA mismatch')
    with zipfile.ZipFile(path) as archive:
        raw = archive.read(next(n for n in archive.namelist() if n.endswith('/data.pkl')))
    found = set()
    for opcode, argument, _ in pickletools.genops(raw):
        if opcode.name == 'STACK_GLOBAL':
            raise ValueError('Unexpected dynamic pickle global')
        if opcode.name == 'GLOBAL':
            found.add(argument)
    if found != ALLOWED_GLOBALS:
        raise ValueError('Hairnet checkpoint globals changed')
    return sorted(found)


def export(checkpoint: Path, output: Path) -> Path:
    if output.exists() or output.suffix != '.onnx':
        raise ValueError('Use a new ONNX output path')
    globals_found = checkpoint_globals(checkpoint)
    import torch
    from ultralytics import YOLO
    torch.set_num_threads(2)
    allowed = []
    for item in globals_found:
        module, attribute = item.split(' ')
        module = 'builtins' if module == '__builtin__' else module
        allowed.append(getattr(importlib.import_module(module), attribute))
    # Only installed, explicitly reviewed classes are allowed. Never weights_only=False.
    with torch.serialization.safe_globals(allowed):
        weights = torch.load(checkpoint, map_location='cpu', weights_only=True)
    model = weights['model'].float().eval()
    if model.names != {0: 'hair_net'}:
        raise ValueError('Unexpected hairnet categories')
    output.parent.mkdir(parents=True, exist_ok=True)
    model.task = 'detect'
    model.pt_path = str(output.with_suffix('.pt'))
    wrapper = YOLO('yolov8n.yaml', task='detect')
    wrapper.model = model
    wrapper.overrides = {'task': 'detect', 'model': model.pt_path, 'imgsz': 640}
    result = wrapper.export(format='onnx', imgsz=640, batch=1, dynamic=False,
                            simplify=False, nms=False, opset=17, device='cpu')
    if Path(result).resolve() != output.resolve():
        raise ValueError('Unexpected export destination')
    return Path(result)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(export(args.checkpoint, args.output))
