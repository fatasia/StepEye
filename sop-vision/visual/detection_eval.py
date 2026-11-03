"""Box-level evaluation at one explicit IoU/confidence operating point, not COCO AP."""
from __future__ import annotations

from collections import Counter
import math


def iou(a, b):
    width = max(0., min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0., min(a[3], b[3]) - max(a[1], b[1]))
    intersection = width * height
    union = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - intersection
    return intersection / union if union > 0 else 0.


def _box(item, classes, prediction=False):
    if not isinstance(item, dict) or set(item) != ({'label', 'box', 'confidence'} if prediction else {'label', 'box'}):
        raise ValueError('框字段必须为label/box；预测另含confidence')
    if item['label'] not in classes:
        raise ValueError('类别不在评测类别表中')
    box = item['box']
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        raise ValueError('框必须为归一化xyxy四元组')
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1 for v in box):
        raise ValueError('框坐标须为0到1的有限值')
    if box[0] >= box[2] or box[1] >= box[3]:
        raise ValueError('框面积必须大于0')
    if prediction:
        c = item['confidence']
        if isinstance(c, bool) or not isinstance(c, (int, float)) or not math.isfinite(c) or not 0 <= c <= 1:
            raise ValueError('预测置信度无效')


def _rates(counts):
    tp, fp, fn = (counts.get(k, 0) for k in ('tp', 'fp', 'fn'))
    return {'tp': tp, 'fp': fp, 'fn': fn,
            'precision': tp/(tp+fp) if tp+fp else None,
            'recall': tp/(tp+fn) if tp+fn else None,
            'f1': 2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None}


def evaluate_boxes(classes, frames, *, iou_threshold=.5, confidence_threshold=.25):
    """Confidence-ordered, class-aware one-to-one matching; duplicates are FP.

    Every input frame must explicitly contain reviewed truth. Empty truth means an
    intentionally reviewed negative frame, not a missing annotation file.
    """
    if not isinstance(classes, list) or not 1 <= len(classes) <= 100 or any(not isinstance(c, str) or not c.strip() or len(c)>100 for c in classes):
        raise ValueError('需要1到100个有效类别')
    if len(set(classes)) != len(classes):
        raise ValueError('类别不能重复')
    if not isinstance(frames, list) or not 1 <= len(frames) <= 500:
        raise ValueError('单次评测需要1到500帧')
    for value in (iou_threshold, confidence_threshold):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 1:
            raise ValueError('评测阈值须大于0且不超过1')
    counts = {name: Counter() for name in classes}
    seen, details = set(), []
    for frame in frames:
        if not isinstance(frame, dict) or set(frame) != {'id', 'reviewed', 'truth', 'predictions'}:
            raise ValueError('每帧必须明确提供id/reviewed/truth/predictions')
        fid = frame['id']
        if not isinstance(fid, str) or not 1 <= len(fid) <= 200 or fid in seen:
            raise ValueError('帧ID无效或重复')
        seen.add(fid)
        if frame['reviewed'] is not True:
            raise ValueError('未复核帧不能作为评测真值')
        truth, predictions = frame['truth'], frame['predictions']
        if any(not isinstance(items, list) or len(items)>100 for items in (truth, predictions)):
            raise ValueError('每帧真值和预测分别最多100框')
        for item in truth: _box(item, classes)
        for item in predictions: _box(item, classes, True)
        selected = sorted(((i, p) for i, p in enumerate(predictions) if p['confidence'] >= confidence_threshold),
                          key=lambda pair: -pair[1]['confidence'])
        matched, false_positives, matches = set(), [], []
        for pi, prediction in selected:
            candidates = [(iou(prediction['box'], gt['box']), gi) for gi, gt in enumerate(truth)
                          if gi not in matched and gt['label'] == prediction['label']]
            overlap, gi = max(candidates, default=(0., -1))
            if overlap >= iou_threshold:
                matched.add(gi)
                counts[prediction['label']]['tp'] += 1
                matches.append({'prediction': pi, 'truth': gi, 'iou': overlap})
            else:
                false_positives.append(pi)
                counts[prediction['label']]['fp'] += 1
        missed = [i for i in range(len(truth)) if i not in matched]
        for i in missed: counts[truth[i]['label']]['fn'] += 1
        details.append({'id': fid, 'matches': matches, 'false_positives': false_positives, 'missed': missed,
                        'ignored_low_confidence': len(predictions)-len(selected)})
    totals = sum(counts.values(), Counter())
    return {'scope': 'externally supplied reviewed boxes; not deployment approval',
            'metric': 'single_operating_point', 'iou_threshold': iou_threshold,
            'confidence_threshold': confidence_threshold, 'frames': len(frames),
            'summary': _rates(totals), 'classes': {key: _rates(value) for key, value in counts.items()},
            'false_positive_frames': sum(bool(row['false_positives']) for row in details),
            'missed_frames': sum(bool(row['missed']) for row in details), 'details': details}
