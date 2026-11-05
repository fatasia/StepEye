"""Fuse the frozen industrial-action linear head into a single 4-class ONNX.

Base VideoMAEv2-small forward_features (1,384) -> train-split normalization
-> linear head, exported end-to-end so the existing single-ONNX action
runtime chain can serve the candidate without platform changes.
Parity is checked twice: ONNX vs torch fused forward, and the fused
forward vs the registered head applied to raw base features.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np

HEAD_SHA = '8d811eab8b543c6cf6c12c3ede2f99a391ee1d6699ec288224ff0c787e272a28'


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--checkpoint', type=Path, required=True)
    p.add_argument('--source', type=Path, required=True, help='reviewed VideoMAEv2 code tree')
    p.add_argument('--head', type=Path, required=True, help='registered npz linear head')
    p.add_argument('--clip', type=Path, required=True, help='normalized float32 BCTHW sample NPY')
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    if args.out.exists():
        p.error('refusing to overwrite evidence')
    if digest(args.head) != HEAD_SHA:
        raise ValueError('Head npz does not match the registered candidate fingerprint')
    tool_dir = Path(__file__).resolve().parent
    sys.path.insert(0, str(tool_dir))
    sys.path.insert(0, str(tool_dir.parent))
    from action_export_v2 import load_model
    import torch

    head = np.load(args.head, allow_pickle=False)
    classes = [str(c) for c in head['classes']]
    weight = torch.from_numpy(head['weight'].astype(np.float32))
    bias = torch.from_numpy(head['bias'].astype(np.float32))
    mean = torch.from_numpy(head['mean'].astype(np.float32))
    std = torch.from_numpy(head['std'].astype(np.float32))
    if tuple(weight.shape) != (len(classes), 384):
        raise ValueError('Head weight shape does not match 384-dim features')

    base = load_model(args.checkpoint, args.source)
    array = np.load(args.clip, allow_pickle=False)
    if array.dtype == np.uint8 and array.shape == (16, 224, 224, 3):
        # 运行时同款预处理，保证导出样例与 ActionPredictor 输入完全一致。
        from visual.action_predict import normalize_clip
        array = normalize_clip(array)
    if array.shape != (1, 3, 16, 224, 224) or array.dtype != np.float32:
        raise ValueError('Expected normalized [1,3,16,224,224] float32 clip (or runtime uint8 RGB clip)')
    sample = torch.from_numpy(array)

    class FusedAction(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.base = base
            self.register_buffer('weight', weight)
            self.register_buffer('bias', bias)
            self.register_buffer('mean', mean)
            self.register_buffer('std', std)

        def forward(self, clip):
            features = self.base.forward_features(clip)
            x = (features - self.mean) / self.std
            return torch.nn.functional.linear(x, self.weight, self.bias)

    fused = FusedAction().eval()
    started = time.perf_counter()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with torch.inference_mode():
        features = base.forward_features(sample)
        if tuple(features.shape) != (1, 384):
            raise ValueError(f'Base forward_features shape {tuple(features.shape)} is not (1, 384)')
        expected = fused(sample).numpy()
        # Second check: the registered head applied by hand to raw base features.
        manual = (features.numpy() - head['mean'].astype(np.float32)) / head['std'].astype(np.float32)
        manual = manual @ head['weight'].T.astype(np.float32) + head['bias'].astype(np.float32)
        if float(np.max(np.abs(manual - expected))) > 1e-4:
            raise ValueError('Fused forward does not reproduce the registered head math')
        torch.onnx.export(fused, sample, str(args.out), input_names=['clip'],
                          output_names=['logits'], opset_version=17, dynamo=False,
                          external_data=False)
    import onnxruntime as ort
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(str(args.out), sess_options=options,
                                   providers=['CPUExecutionProvider'])
    if session.get_inputs()[0].shape != [1, 3, 16, 224, 224] or session.get_outputs()[0].shape != [1, len(classes)]:
        raise ValueError('Export shape mismatch')
    actual = session.run(None, {'clip': array})[0]
    error = float(np.max(np.abs(actual - expected)))
    if not np.allclose(expected, actual, atol=1e-3, rtol=1e-3):
        raise ValueError('ONNX parity mismatch')
    report = {
        'architecture': 'VideoMAEv2-small forward_features + train-split normalization + registered 4-class linear head',
        'base_source_sha256': '24fb71687fa3671b8387cadfbcbab0f72af695692e93cf1ecc82caa888626172',
        'head_sha256': HEAD_SHA,
        'onnx_sha256': digest(args.out),
        'input': [1, 3, 16, 224, 224], 'output': [1, len(classes)],
        'classes': classes,
        'parity_max_abs': error,
        'torch_vs_head_manual_max_abs': float(np.max(np.abs(manual - expected))),
        'torch_top_class': classes[int(expected[0].argmax())],
        'onnx_top_class': classes[int(actual[0].argmax())],
        'export_s': time.perf_counter() - started,
        'code_license': 'MIT (OpenGVLab); head trained in-house on Apache-2.0 IndustReal subset',
        'validation': 'single-clip export parity; candidate test metrics in industrial-action-candidates.json',
    }
    args.out.with_suffix('.export.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
