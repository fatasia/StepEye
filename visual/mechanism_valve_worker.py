"""一次性冻结阀门ONNX进程；无训练器、pickle或服务驻留。"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path


def predict(spec):
    import numpy as np
    import onnxruntime as ort
    from .mechanism_valve import MODEL_SHA256, pinned_asset
    root = Path(spec['root'])
    path = pinned_asset(root)
    values = np.asarray(spec['values'], dtype=np.float32)
    if values.shape != (400, 2) or not np.isfinite(values).all():
        raise ValueError('finite float32[400,2] required')
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(str(path), sess_options=options, providers=['CPUExecutionProvider'])
    inputs, outputs = session.get_inputs(), session.get_outputs()
    if (len(inputs) != 1 or len(outputs) != 1 or inputs[0].name != 'values'
            or inputs[0].type != 'tensor(float)' or inputs[0].shape[1:] != [400, 2]
            or len(inputs[0].shape) != 3 or outputs[0].name != 'score'
            or outputs[0].type != 'tensor(float)' or len(outputs[0].shape) != 2
            or outputs[0].shape[1] != 1):
        raise ValueError('fixed valve graph contract mismatch')
    started = time.perf_counter()
    output = session.run(['score'], {'values': values[None]})[0]
    elapsed = (time.perf_counter() - started) * 1000
    if output.shape != (1, 1) or not np.isfinite(output).all() or output[0, 0] < 0:
        raise ValueError('invalid fused evidence')
    pinned_asset(root)
    return {'score': float(output[0, 0]), 'model_sha256': MODEL_SHA256, 'inference_ms': elapsed}


if __name__ == '__main__':
    try:
        if len(sys.argv) != 3 or sys.argv[1] != '--request':
            raise ValueError('private request file required')
        with Path(sys.argv[2]).open('rb') as stream:
            raw = stream.read(64 * 1024 + 1)
        if len(raw) > 64 * 1024:
            raise ValueError('IPC input limit exceeded')
        print(json.dumps(predict(json.loads(raw)), allow_nan=False))
    except Exception:
        print(json.dumps({'error': 'valve_worker_failed'}))
        raise SystemExit(1)
