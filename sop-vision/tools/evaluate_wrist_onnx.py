"""Evaluate visible wrist-strap/ring/watch ONNX once on the author's held-out images."""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from visual.detection_eval import evaluate_boxes
from visual.detector_registry import create_backend

CLASSES = ['ring', 'watch', 'wrist-strap']


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(model: Path, dataset: Path, audit: Path, output: Path):
    import cv2
    if output.exists():
        raise FileExistsError(output)
    source = json.loads((dataset / 'source.json').read_text(encoding='utf8'))
    original = json.loads(audit.read_text(encoding='utf8'))
    assets = {str(a['id']): a for a in original['assets']}
    selected = [a for a in source['images'] if a['split'] == 'test']
    if source['classes'] != CLASSES or not selected:
        raise ValueError('Expected the three-class author test dataset')
    # Fixed 640 native input and operating point; no test-based threshold tuning.
    backend = create_backend(model, CLASSES, threshold=.25, max_results=100)
    frames, timings = [], []
    try:
        metadata = backend.session.get_modelmeta().custom_metadata_map
        names = ast.literal_eval(metadata.get('names', 'None'))
        if isinstance(names, dict):
            names = [names[i] for i in range(len(names))]
        if names != CLASSES or list(backend.session.get_inputs()[0].shape) != [1, 3, 640, 640]:
            raise ValueError('Expected fixed 640 detection ONNX with exact class order')
        for item in selected:
            fid = str(item['id'])
            asset = assets[fid]
            path = dataset / 'images' / 'test' / f'{fid}.jpg'
            if asset['split'] != 'testing' or sha(path) != asset['sha256'] or asset['sha256'] != item['source_sha256']:
                raise ValueError('Author test/source image hash mismatch')
            frame = cv2.imread(str(path))
            if frame is None:
                raise ValueError('Unreadable test image')
            h, w = frame.shape[:2]
            start = time.perf_counter()
            hits = backend.detect(frame)
            timings.append((time.perf_counter() - start) * 1000)
            frames.append({'id': fid, 'reviewed': True,
                'truth': [{'label': t['label'], 'box': t['xyxy_norm']} for t in asset['targets']],
                'predictions': [{'label': hit.label, 'confidence': hit.confidence,
                    'box': [hit.xyxy[0]/w, hit.xyxy[1]/h, hit.xyxy[2]/w, hit.xyxy[3]/h]} for hit in hits]})
    finally:
        backend.close()
    report = {'name': 'visible wrist-strap / ring / watch detection',
        'scope': 'author same-session test; not independent factory or ESD compliance validation',
        'truth_producer': 'original public project author bounding boxes',
        'model_sha256': sha(model), 'source_sha256': sha(dataset / 'source.json'),
        'audit_sha256': sha(audit), 'test_images': len(frames), 'frames': frames,
        'cpu_inference_ms_including_first': timings,
        'evaluation': evaluate_boxes(CLASSES, frames, confidence_threshold=.25, iou_threshold=.5)}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf8') as stream:
        json.dump(report, stream, indent=2, ensure_ascii=False, allow_nan=False)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('model', 'dataset', 'audit', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    report = run(**vars(parser.parse_args()))
    print(json.dumps({'test_images': report['test_images'], 'summary': report['evaluation']['summary']}))
