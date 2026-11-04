"""CPU export of a pinned public pipeline visual-leak research candidate."""
from __future__ import annotations
import argparse
import hashlib
import importlib
from pathlib import Path
import pickletools
import zipfile

SHA256 = '8670e5cef00b77e891cc48ce60c16c46b1d59e220c8f49a68db7f399680838ba'
CLASSES = ['crack', 'gas', 'leak', 'no leak', 'water']
GLOBALS = {
    '__builtin__ set', 'collections OrderedDict', 'torch FloatStorage', 'torch HalfStorage',
    'torch LongStorage', 'torch Size', 'torch._utils _rebuild_parameter', 'torch._utils _rebuild_tensor_v2',
    'torch.nn.modules.activation SiLU', 'torch.nn.modules.batchnorm BatchNorm2d',
    'torch.nn.modules.container ModuleList', 'torch.nn.modules.container Sequential',
    'torch.nn.modules.conv Conv2d', 'torch.nn.modules.linear Identity', 'torch.nn.modules.pooling MaxPool2d',
    'torch.nn.modules.upsampling Upsample', 'ultralytics.nn.modules.block Attention',
    'ultralytics.nn.modules.block Bottleneck', 'ultralytics.nn.modules.block C2PSA',
    'ultralytics.nn.modules.block C3k', 'ultralytics.nn.modules.block C3k2',
    'ultralytics.nn.modules.block DFL', 'ultralytics.nn.modules.block PSABlock',
    'ultralytics.nn.modules.block SPPF', 'ultralytics.nn.modules.conv Concat',
    'ultralytics.nn.modules.conv Conv', 'ultralytics.nn.modules.conv DWConv',
    'ultralytics.nn.modules.head Detect', 'ultralytics.nn.tasks DetectionModel',
}


def inspect(checkpoint):
    checkpoint = Path(checkpoint)
    if hashlib.sha256(checkpoint.read_bytes()).hexdigest() != SHA256:
        raise ValueError('Pipeline checkpoint SHA mismatch')
    with zipfile.ZipFile(checkpoint) as archive:
        raw = archive.read(next(n for n in archive.namelist() if n.endswith('/data.pkl')))
    ops = list(pickletools.genops(raw))
    found = {arg for opcode, arg, _ in ops if opcode.name == 'GLOBAL'}
    if found != GLOBALS or any(opcode.name == 'STACK_GLOBAL' for opcode, _, _ in ops):
        raise ValueError('Pipeline checkpoint global contract changed')
    return sorted(found)


def export(checkpoint, output):
    output = Path(output)
    if output.exists() or output.suffix != '.onnx':
        raise ValueError('Use a new ONNX output path')
    globals_found = inspect(checkpoint)
    import torch
    from ultralytics import YOLO
    torch.set_num_threads(2)
    allowed = []
    for item in globals_found:
        module, name = item.split(' ')
        allowed.append(getattr(importlib.import_module('builtins' if module == '__builtin__' else module), name))
    # Only audited installed classes, never author modules or weights_only=False.
    with torch.serialization.safe_globals(allowed):
        checkpoint_data = torch.load(checkpoint, weights_only=True, map_location='cpu')
    model = checkpoint_data['model'].float().eval()
    if model.names != dict(enumerate(CLASSES)):
        raise ValueError('Unexpected pipeline classes')
    output.parent.mkdir(parents=True, exist_ok=True)
    model.task = 'detect'; model.pt_path = str(output.with_suffix('.pt'))
    wrapper = YOLO('yolo11n.yaml', task='detect')
    wrapper.model = model
    wrapper.overrides = {'task': 'detect', 'model': model.pt_path, 'imgsz': 640}
    exported = wrapper.export(format='onnx', imgsz=640, batch=1, dynamic=False,
                              simplify=False, nms=False, opset=17, device='cpu')
    if Path(exported).resolve() != output.resolve():
        raise ValueError('Unexpected export destination')
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(export(args.checkpoint, args.output))
