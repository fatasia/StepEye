"""Install the fixed weihu actuator ONNX without loading Python checkpoints."""
from pathlib import Path
import argparse
import hashlib
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from visual.mechanism_actuator_classifier import MODEL_SHA, MANIFEST_SHA


def install(source, root):
    source = Path(source)
    target = Path(root) / 'models/mechanism/actuator-ensemble'
    manifest = target / 'manifest.json'
    if hashlib.sha256(manifest.read_bytes()).hexdigest() != MANIFEST_SHA:
        raise ValueError('安装目录 manifest 指纹不一致')
    if hashlib.sha256(source.read_bytes()).hexdigest() != MODEL_SHA:
        raise ValueError('源模型指纹不一致')
    destination = target / 'model.onnx'
    if destination.exists():
        if hashlib.sha256(destination.read_bytes()).hexdigest() != MODEL_SHA:
            raise ValueError('目标已有不同模型，不覆盖')
        return destination
    shutil.copyfile(source, destination)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    print(install(args.source, args.root))
