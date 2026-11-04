"""固定19图胶路评测；隔离OpenCV4作为Darknet参考，主环境仅运行ONNX。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import zipfile

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
HOLDOUT_SHA = '5fd644fbaf110aa5b5d5666f476613bd683da327385f57ba8461d0211280159f'


def images(archive):
    import cv2
    if hashlib.sha256(archive.read_bytes()).hexdigest() != HOLDOUT_SHA:
        raise ValueError('固定holdout哈希不匹配')
    with zipfile.ZipFile(archive) as z:
        for name in sorted(n for n in z.namelist() if n.lower().endswith('.png')):
            raw = z.read(name)
            image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
            label = z.read(str(Path(name).with_suffix('.txt')).replace('\\', '/')).decode('utf-8-sig')
            yield name, image, label, hashlib.sha256(raw).hexdigest()


def reference(args):
    import cv2
    from export_glue_onnx import CFG_SHA, WEIGHTS_SHA
    for path, sha in [(args.cfg, CFG_SHA), (args.weights, WEIGHTS_SHA)]:
        if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise ValueError('固定源哈希不匹配')
    cv2.setNumThreads(2)
    net = cv2.dnn.readNetFromDarknet(str(args.cfg), str(args.weights))
    net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
    net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
    records = []
    for index, (name, image, label, sha) in enumerate(images(args.holdout)):
        blob = cv2.dnn.blobFromImage(image, 1 / 255, (416, 416), swapRB=True, crop=False)
        net.setInput(blob)
        rows = np.concatenate(net.forward(net.getUnconnectedOutLayersNames()))
        np.savez_compressed(args.output / f'reference-{index}.npz', blob=blob, rows=rows)
        records.append({'name': name, 'image_sha256': sha, 'cv2': cv2.__version__})
    (args.output / 'reference.json').write_text(json.dumps(records, indent=2), encoding='utf-8')


def evaluate(args):
    import cv2
    from visual.glue_yolov4 import CLASSES, GlueYoloV4Backend, decode, preprocess
    from visual.detection_eval import evaluate_boxes
    if args.output.exists():
        raise ValueError('输出已存在，保留之前证据')
    args.output.mkdir(parents=True)
    command = [str(args.reference_python), str(Path(__file__).resolve()), '--reference-only']
    for name in ('cfg', 'weights', 'holdout', 'output'):
        command.extend(['--' + name, str(getattr(args, name).resolve())])
    subprocess.run(command, check=True, timeout=120, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    model = GlueYoloV4Backend(args.model, CLASSES)
    frames, references, parity, timings = [], [], [], []
    try:
        for index, (name, image, label, sha) in enumerate(images(args.holdout)):
            height, width = image.shape[:2]
            with np.load(args.output / f'reference-{index}.npz', allow_pickle=False) as reference:
                blob, rows = reference['blob'], reference['rows']
            np.testing.assert_allclose(preprocess(image), blob, atol=1e-7, rtol=0)
            started = time.perf_counter()
            prediction = model.session.run(None, {'images': blob})[0]
            timings.append((time.perf_counter() - started) * 1000)
            actual = decode(prediction, width, height)
            expected = decode(rows[None], width, height)
            if len(actual) != len(expected):
                raise ValueError(f'检出数量不一致: {name}')
            box_error = confidence_error = 0.
            for a, b in zip(actual, expected):
                if a.label != b.label:
                    raise ValueError(f'类别不一致: {name}')
                box_error = max(box_error, float(np.max(abs(np.array(a.xyxy) - b.xyxy))))
                confidence_error = max(confidence_error, abs(a.confidence - b.confidence))
            if box_error > 1 or confidence_error > .002:
                raise ValueError(f'数值对齐未通过: {name}')
            truth = []
            for line in label.splitlines():
                cid, cx, cy, bw, bh = map(float, line.split())
                truth.append({'label': CLASSES[int(cid)], 'box': [cx-bw/2, cy-bh/2, cx+bw/2, cy+bh/2]})
            def normalized(hits):
                return [{'label': hit.label, 'box': (np.array(hit.xyxy)/[width,height,width,height]).tolist(),
                         'confidence': hit.confidence} for hit in hits]
            frames.append({'id': name, 'reviewed': True, 'truth': truth, 'predictions': normalized(actual)})
            references.append({**frames[-1], 'predictions': normalized(expected)})
            parity.append({'image': name, 'image_sha256': sha, 'detections': len(actual),
                           'box_max_abs_error_px': box_error, 'confidence_max_abs_error': confidence_error})
        report = {'model_sha256': hashlib.sha256(args.model.read_bytes()).hexdigest(),
                  'holdout_sha256': HOLDOUT_SHA, 'threshold': .25, 'nms_iou': .45, 'matching_iou': .5,
                  'source_scope': 'author holdout; no target-factory independent evaluation',
                  'reference_threshold_note': 'OpenCV zeros class scores below .2; comparison uses fixed .25',
                  'evaluation': evaluate_boxes(CLASSES, frames), 'reference_evaluation': evaluate_boxes(CLASSES, references),
                  'parity': parity, 'inference_ms': timings, 'inference_median_ms': float(np.median(timings)),
                  'usage': 'evaluation_only', 'license_status': 'weights/data permission not declared'}
        (args.output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        (args.output / 'frames.json').write_text(json.dumps(frames, indent=2), encoding='utf-8')
        print(json.dumps({'summary': report['evaluation']['summary'], 'median_ms': report['inference_median_ms'],
                          'max_box_error_px': max(r['box_max_abs_error_px'] for r in parity),
                          'max_conf_error': max(r['confidence_max_abs_error'] for r in parity)}))
    finally:
        model.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('cfg', 'weights', 'holdout', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--model', type=Path)
    parser.add_argument('--reference-python', type=Path)
    parser.add_argument('--reference-only', action='store_true')
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    reference(args) if args.reference_only else evaluate(args)
