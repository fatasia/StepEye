"""Single-shape CPU/CUDA parity and actual provider profiling; no model conversion."""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import time
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from visual.action_predict import normalize_clip, model_sha256, action_result
from visual.action_runtime import session_options


def probe(model, clip, classes, sha256, output):
    import onnxruntime as ort
    if output.exists():
        raise FileExistsError(output)
    if model_sha256(model) != sha256:
        raise ValueError('Action model SHA mismatch')
    tensor = normalize_clip(np.load(clip, allow_pickle=False))
    names = json.loads(classes.read_text(encoding='utf8'))
    output.mkdir(parents=True)
    cpu = ort.InferenceSession(str(model), session_options(ort, ['CPUExecutionProvider'], 2), providers=['CPUExecutionProvider'])
    reference = cpu.run(None, {cpu.get_inputs()[0].name: tensor})[0]
    del cpu
    options = session_options(ort, ['CUDAExecutionProvider'], 2)
    options.enable_profiling = True
    options.profile_file_prefix = str(output / 'cuda-profile')
    started = time.perf_counter()
    gpu = ort.InferenceSession(str(model), options, providers=['CUDAExecutionProvider'])
    gpu.disable_fallback()
    loaded_ms = (time.perf_counter() - started) * 1000
    available = gpu.get_providers()
    if available[0] != 'CUDAExecutionProvider':
        raise ValueError('Strict CUDA session was not created')
    started = time.perf_counter()
    actual = gpu.run(None, {gpu.get_inputs()[0].name: tensor})[0]
    inference_ms = (time.perf_counter() - started) * 1000
    profile_path = Path(gpu.end_profiling())
    del gpu
    events = json.loads(profile_path.read_text(encoding='utf8'))
    providers = Counter(e.get('args', {}).get('provider') for e in events
                        if e.get('cat') == 'Node' and e.get('args', {}).get('provider'))
    if providers.get('CUDAExecutionProvider', 0) == 0 or providers.get('CPUExecutionProvider', 0):
        raise ValueError('Profile does not show strict CUDA execution')
    if not np.allclose(reference, actual, rtol=.005, atol=.005):
        raise ValueError('CPU/CUDA logits exceed parity tolerance')
    if int(reference.argmax()) != int(actual.argmax()):
        raise ValueError('CPU/CUDA top class differs')
    report = {'model_sha256': sha256, 'ort_version': ort.__version__,
              'node_provider_counts': dict(providers), 'session_providers': available, 'cpu_fallback_disabled': True,
              'max_abs_logit_difference': float(np.max(np.abs(reference-actual))),
              'cpu_prediction': action_result(reference, names, sha256),
              'cuda_prediction': action_result(actual, names, sha256, inference_ms),
              'session_load_ms': loaded_ms, 'first_profiled_inference_ms': inference_ms,
              'profile': profile_path.name, 'shape': list(tensor.shape),
              'scope': 'single public clip, CPU/CUDA parity and operator assignment; not industrial accuracy'}
    (output/'report.json').write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf8')
    return report

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('model', 'clip', 'classes', 'output'):
        p.add_argument('--'+name, type=Path, required=True)
    p.add_argument('--sha256', required=True)
    print(json.dumps(probe(**vars(p.parse_args()))))
