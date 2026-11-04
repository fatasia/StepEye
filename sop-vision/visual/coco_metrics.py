"""Optional official COCO bbox metrics for frozen, reviewed detection reports."""
from __future__ import annotations

from contextlib import redirect_stdout
import io

from .detection_eval import _validated_frames, validated_classes

METRICS = ('AP', 'AP50', 'AP75', 'AP_small', 'AP_medium', 'AP_large',
           'AR1', 'AR10', 'AR100', 'AR_small', 'AR_medium', 'AR_large')


def coco_bbox_metrics(classes, frames, dimensions):
    """Use original pixel sizes for COCO area ranges; missing groups return null."""
    validated_classes(classes)
    _validated_frames(classes, frames)
    try:
        from pycocotools.coco import COCO
        from pycocotools.cocoeval import COCOeval
    except ImportError:
        return {'status': 'unavailable', 'reason': 'install requirements-evaluation.txt'}

    categories = [{'id': i + 1, 'name': name} for i, name in enumerate(classes)]
    category_ids = {row['name']: row['id'] for row in categories}
    images, annotations, predictions = [], [], []
    for image_id, frame in enumerate(frames, 1):
        width, height = dimensions[frame['id']]
        if any(type(v) is not int or v <= 0 for v in (width, height)):
            raise ValueError('COCO metrics require original positive pixel dimensions')
        images.append({'id': image_id, 'width': width, 'height': height})
        for key in ('truth', 'predictions'):
            for item in frame[key]:
                x0, y0, x1, y1 = item['box']
                bbox = [x0 * width, y0 * height, (x1-x0) * width, (y1-y0) * height]
                row = {'image_id': image_id, 'category_id': category_ids[item['label']], 'bbox': bbox}
                if key == 'truth':
                    annotations.append({**row, 'id': len(annotations) + 1,
                                        'area': bbox[2]*bbox[3], 'iscrowd': 0})
                else:
                    predictions.append({**row, 'score': item['confidence']})
    # COCO prints progress; isolate it from the worker's JSON/stdout protocol.
    with redirect_stdout(io.StringIO()):
        truth = COCO()
        truth.dataset = {'info': {}, 'images': images, 'categories': categories, 'annotations': annotations}
        truth.createIndex()
        if predictions:
            detected = truth.loadRes(predictions)
        else:
            detected = COCO()
            detected.dataset = {**truth.dataset, 'annotations': []}
            detected.createIndex()
        evaluator = COCOeval(truth, detected, 'bbox')
        evaluator.params.imgIds = [row['id'] for row in images]
        evaluator.params.catIds = [row['id'] for row in categories]
        evaluator.evaluate()
        evaluator.accumulate()
        evaluator.summarize()
    return {'status': 'available', 'implementation': 'pycocotools.COCOeval/bbox',
            'metrics': {name: float(value) if value >= 0 else None
                        for name, value in zip(METRICS, evaluator.stats)},
            'max_detections': [1, 10, 100], 'area_unit': 'original_image_pixels_squared',
            'scope': 'reviewed boxes, no crowd/ignore annotations; exported validation split'}
