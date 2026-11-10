"""固定YOLOv8-Pose表盘图的一次性CPU推理；保留关键点，不走框检测器。"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time


def predict(spec):
    import cv2
    import numpy as np
    import onnxruntime as ort
    from PIL import Image
    from .gauge import KEYPOINTS, MODEL_SHA256, pinned_model
    root = Path(spec['root'])
    model = pinned_model(root)
    with Image.open(spec['image_path']) as image:
        rgb = np.asarray(image.convert('RGB'))
    height, width = rgb.shape[:2]
    scale = min(640 / width, 640 / height)
    nw, nh = max(1, round(width * scale)), max(1, round(height * scale))
    px, py = (640 - nw) // 2, (640 - nh) // 2
    canvas = np.full((640, 640, 3), 114, dtype=np.uint8)
    canvas[py:py + nh, px:px + nw] = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_LINEAR)
    tensor = np.ascontiguousarray(canvas.transpose(2, 0, 1)[None], dtype=np.float32) / 255
    options = ort.SessionOptions(); options.intra_op_num_threads = 2; options.inter_op_num_threads = 1
    session = ort.InferenceSession(str(model), sess_options=options, providers=['CPUExecutionProvider'])
    inputs, outputs = session.get_inputs(), session.get_outputs()
    if (len(inputs) != 1 or inputs[0].name != 'images' or inputs[0].type != 'tensor(float)'
            or inputs[0].shape != [1, 3, 640, 640] or len(outputs) != 1
            or outputs[0].type != 'tensor(float)' or outputs[0].shape != [1, 17, 8400]):
        raise ValueError('fixed gauge pose graph mismatch')
    started = time.perf_counter()
    pred = session.run(None, {'images': tensor})[0]
    elapsed = (time.perf_counter() - started) * 1000
    if pred.shape != (1, 17, 8400) or not np.isfinite(pred).all():
        raise ValueError('invalid gauge pose output')
    rows = pred[0].T
    candidates = rows[rows[:, 4] >= .5]
    boxes = [[float(r[0] - r[2] / 2), float(r[1] - r[3] / 2), float(r[2]), float(r[3])]
             for r in candidates]
    keep = cv2.dnn.NMSBoxes(boxes, candidates[:, 4].tolist(), .5, .5) if boxes else []
    detections = []
    for index in np.asarray(keep).reshape(-1):
        row = candidates[int(index)]
        points = row[5:].reshape(4, 3)
        if np.any(points[:, 2] < .5) or row[2] <= 0 or row[3] <= 0:
            continue
        normalized = (points[:, :2] - [px, py]) / scale / [width, height]
        if np.any(normalized < 0) or np.any(normalized > 1):
            continue
        box = np.array([row[0] - row[2] / 2, row[1] - row[3] / 2,
                        row[0] + row[2] / 2, row[1] + row[3] / 2])
        box = np.clip((box - [px, py, px, py]) / scale / [width, height, width, height], 0, 1)
        detections.append({'bbox_xyxy_norm': box.tolist(), 'confidence': float(row[4]),
                           'keypoints': [{'name': name, 'x_norm': float(point[0]), 'y_norm': float(point[1]),
                                          'confidence': float(points[i, 2])}
                                         for i, (name, point) in enumerate(zip(KEYPOINTS, normalized))]})
        if len(detections) == 8:
            break
    pinned_model(root)
    return {'model_sha256': MODEL_SHA256, 'detections': detections, 'inference_ms': elapsed}


if __name__ == '__main__':
    try:
        if len(sys.argv) != 3 or sys.argv[1] != '--request':
            raise ValueError('private request required')
        with Path(sys.argv[2]).open('rb') as stream: raw = stream.read(8193)
        if len(raw) > 8192: raise ValueError('request limit exceeded')
        print(json.dumps(predict(json.loads(raw)), allow_nan=False))
    except Exception:
        print(json.dumps({'error': 'gauge_pose_failed'}))
        raise SystemExit(1)
