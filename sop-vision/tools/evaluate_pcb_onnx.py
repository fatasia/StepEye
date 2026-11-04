"""Fixed operating-point PCB evaluation on all 2051 original author val images."""
from __future__ import annotations

import argparse
import ast
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from visual.detection_eval import evaluate_boxes
from visual.detector_registry import create_backend

CLASSES = ['SH', 'SP', 'SC', 'OP', 'MB', 'HB', 'CS', 'CFO', 'BMFO']
ARCHIVE_SHA = 'b256846a1f2cf17e6e88be49c551ebf2c7551601e08691f50c70dc367a218817'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def truths(data):
    """Use original boxes; only subpixel boundary overshoot is clipped and counted."""
    if {c['id']: c['name'] for c in data['categories']} != dict(enumerate(CLASSES, 1)):
        raise ValueError('PCB class order mismatch')
    images = {i['id']: i for i in data['images']}
    result, clipped = defaultdict(list), 0
    for annotation in data['annotations']:
        image = images[annotation['image_id']]
        width, height = image['width'], image['height']
        x, y, w, h = annotation['bbox']
        if min(w, h) <= 0 or min(x, y) < -.02 or x+w > width+.02 or y+h > height+.02:
            raise ValueError('Unexpected PCB box geometry')
        box = [max(0., x)/width, max(0., y)/height, min(width, x+w)/width, min(height, y+h)/height]
        clipped += int(x < 0 or y < 0 or x+w > width or y+h > height)
        result[image['id']].append({'label': CLASSES[annotation['category_id']-1], 'box': box})
    return result, clipped


def run(model: Path, archive: Path, output: Path):
    import cv2
    import numpy as np
    if output.exists():
        raise FileExistsError(output)
    if sha(archive) != ARCHIVE_SHA:
        raise ValueError('Official PCB archive SHA mismatch')
    model_sha = sha(model)
    frames, timings = [], []
    backend = create_backend(model, CLASSES, threshold=.25, max_results=100)
    try:
        names = ast.literal_eval(backend.session.get_modelmeta().custom_metadata_map.get('names', 'None'))
        if isinstance(names, dict):
            names = [names[i] for i in range(len(names))]
        if names != CLASSES or backend.session.get_inputs()[0].shape != [1, 3, 640, 640]:
            raise ValueError('Expected fixed640 PCB nine-class ONNX')
        with zipfile.ZipFile(archive) as source:
            annotation_raw = source.read('Data_COCO/annotations/instances_val2017.json')
            data = json.loads(annotation_raw)
            if len(data['images']) != 2051:
                raise ValueError('Expected all 2051 author holdout images')
            truth, clipped = truths(data)
            for item in data['images']:
                raw = source.read('Data_COCO/val2017/' + item['file_name'])
                frame = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
                if frame is None or frame.shape[:2] != (item['height'], item['width']):
                    raise ValueError('PCB source dimensions mismatch')
                h, w = frame.shape[:2]
                started = time.perf_counter()
                hits = backend.detect(frame)
                timings.append((time.perf_counter()-started)*1000)
                frames.append({'id': item['file_name'], 'source_sha256': hashlib.sha256(raw).hexdigest(),
                    'reviewed': True, 'truth': truth[item['id']],
                    'predictions': [{'label': hit.label, 'confidence': hit.confidence,
                        'box': [hit.xyxy[0]/w, hit.xyxy[1]/h, hit.xyxy[2]/w, hit.xyxy[3]/h]} for hit in hits]})
    finally:
        backend.close()
    if sha(model) != model_sha:
        raise ValueError('Model changed during evaluation')
    report = {'model_sha256': model_sha, 'archive_sha256': ARCHIVE_SHA,
        'annotations_sha256': hashlib.sha256(annotation_raw).hexdigest(),
        'scope': 'original author val held out from training; not independent factory acceptance',
        'test_images': len(frames), 'clipped_boxes': clipped, 'frames': frames,
        'cpu_inference_ms_including_first': timings, 'median_inference_ms': statistics.median(timings),
        'evaluation': evaluate_boxes(CLASSES, frames, confidence_threshold=.25, iou_threshold=.5),
        'average_precision': None, 'ap_note': 'Predictions thresholded at .25; no full AP claim'}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf8') as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('model', 'archive', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    result = run(**vars(parser.parse_args()))
    print(json.dumps({'images': result['test_images'], 'summary': result['evaluation']['summary']}))
