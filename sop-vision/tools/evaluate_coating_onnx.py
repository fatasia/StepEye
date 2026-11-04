"""Fixed operating-point CoatingVision three-class evaluation on the block-split test set."""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from visual.detection_eval import evaluate_boxes
from visual.detector_registry import create_backend

CLASSES = ['crack', 'delamination', 'pinhole']


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def dataset_fingerprint(root: Path) -> str:
    """Same split contract as training: fingerprint every test image and label file."""
    canonical = {}
    for folder in ('images/test', 'labels/test'):
        for path in sorted((root / folder).rglob('*')):
            if path.is_file():
                canonical[path.relative_to(root).as_posix()] = sha(path)
    payload = json.dumps(canonical, sort_keys=True).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def truths(root: Path):
    """YOLO txt truth; empty-label images stay in as negatives to penalize false alarms."""
    truth, images, clipped = defaultdict(list), [], 0
    test_images = root / 'images' / 'test'
    for image_path in sorted(test_images.rglob('*')):
        if not image_path.is_file():
            continue
        label_path = (root / 'labels' / 'test' / image_path.relative_to(test_images)).with_suffix('.txt')
        if not label_path.is_file():
            raise ValueError(f'missing paired label for {image_path.name}')
        for line in label_path.read_text(encoding='utf-8-sig').splitlines():
            if not line.strip():
                continue
            fields = line.split()
            cid, cx, cy, w, h = int(fields[0]), *map(float, fields[1:])
            if not 0 <= cid < len(CLASSES):
                raise ValueError('class id out of range')
            if min(w, h) <= 0:
                clipped += 1
                continue
            # Mask-derived boxes round to sub-pixel overshoot; clamp inside [0,1].
            x0, y0 = max(0.0, cx - w / 2), max(0.0, cy - h / 2)
            x1, y1 = min(1.0, cx + w / 2), min(1.0, cy + h / 2)
            if min(w, h) <= 0 or x1 - x0 <= 0:
                clipped += 1
                continue
            truth[image_path.name].append({
                'label': CLASSES[cid],
                'box': [x0, y0, x1, y1]})
        images.append(image_path)
    if not images:
        raise ValueError('no test images')
    return truth, images, clipped


def combine(rows):
    counts = {key: sum(row[key] for row in rows) for key in ('tp', 'fp', 'fn')}
    tp, fp, fn = (counts[key] for key in ('tp', 'fp', 'fn'))
    return {**counts, 'precision': tp/(tp+fp) if tp+fp else None,
            'recall': tp/(tp+fn) if tp+fn else None,
            'f1': 2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None}


def evaluate_all(frames):
    reports = []
    for start in range(0, len(frames), 500):
        batch = [{k: frame[k] for k in ('id', 'reviewed', 'truth', 'predictions')}
                 for frame in frames[start:start + 500]]
        reports.append(evaluate_boxes(CLASSES, batch, confidence_threshold=.25, iou_threshold=.5))
    return {**reports[0], 'frames': len(frames),
            'summary': combine([r['summary'] for r in reports]),
            'classes': {name: combine([r['classes'][name] for r in reports]) for name in CLASSES},
            'false_positive_frames': sum(r['false_positive_frames'] for r in reports),
            'missed_frames': sum(r['missed_frames'] for r in reports),
            'details': [d for r in reports for d in r['details']]}


def run(model: Path, dataset: Path, output: Path):
    import cv2
    import numpy as np
    if output.exists():
        raise FileExistsError(output)
    model_sha, dataset_sha = sha(model), dataset_fingerprint(dataset)
    frames, timings = [], []
    backend = create_backend(model, CLASSES, threshold=.25, max_results=100)
    try:
        truth, images, clipped = truths(dataset)
        for image_path in images:
            frame = cv2.imread(str(image_path))
            if frame is None:
                raise ValueError(f'undecodable test image {image_path.name}')
            h, w = frame.shape[:2]
            started = time.perf_counter()
            hits = backend.detect(frame)
            timings.append((time.perf_counter()-started)*1000)
            frames.append({'id': image_path.name,
                'source_sha256': sha(image_path), 'reviewed': True,
                'truth': truth[image_path.name],
                'predictions': [{'label': hit.label, 'confidence': hit.confidence,
                    'box': [hit.xyxy[0]/w, hit.xyxy[1]/h, hit.xyxy[2]/w, hit.xyxy[3]/h]} for hit in hits]})
    finally:
        backend.close()
    if sha(model) != model_sha:
        raise ValueError('Model changed during evaluation')
    report = {'model_sha256': model_sha, 'dataset_sha256': dataset_sha,
        'scope': 'mask-derived labels from RGBA segmentation channels verified against '
                 'classification labels; block split keeps same-batch images together; '
                 'no independent annotator pass; not factory acceptance',
        'test_images': len(frames), 'clipped_boxes': clipped,
        'cpu_inference_ms_including_first': timings[0] if timings else None,
        'median_inference_ms': statistics.median(timings) if timings else None,
        'evaluation': evaluate_all(frames),
        'average_precision': None, 'ap_note': 'Predictions thresholded at .25; no full AP claim'}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf8') as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('model', 'dataset', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    result = run(**vars(parser.parse_args()))
    print(json.dumps({'images': result['test_images'], 'summary': result['evaluation']['summary']}))
