"""Box-level evaluation at one explicit IoU/confidence operating point, plus COCO-style AP."""
from __future__ import annotations

import bisect
from collections import Counter
import math


def iou(a, b):
    width = max(0., min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0., min(a[3], b[3]) - max(a[1], b[1]))
    intersection = width * height
    union = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - intersection
    return intersection / union if union > 0 else 0.


def validated_classes(classes):
    """Shared class-table contract for every metric entry point (also used by the release ledger)."""
    if not isinstance(classes, list) or not 1 <= len(classes) <= 100 or any(
            not isinstance(c, str) or not c.strip() or len(c) > 100 for c in classes):
        raise ValueError('需要1到100个有效类别')
    if len(set(classes)) != len(classes):
        raise ValueError('类别不能重复')
    return list(classes)


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


def _validated_frames(classes, frames):
    """Shared frame contract: explicit reviewed truth, unique ids, bounded box counts."""
    if not isinstance(frames, list) or not 1 <= len(frames) <= 500:
        raise ValueError('单次评测需要1到500帧')
    seen = set()
    for frame in frames:
        if not isinstance(frame, dict) or set(frame) != {'id', 'reviewed', 'truth', 'predictions'}:
            raise ValueError('每帧必须明确提供id/reviewed/truth/predictions')
        fid = frame['id']
        if not isinstance(fid, str) or not 1 <= len(fid) <= 200 or fid in seen:
            raise ValueError('帧ID无效或重复')
        seen.add(fid)
        if frame['reviewed'] is not True:
            raise ValueError('未复核帧不能作为评测真值')
        if any(not isinstance(items, list) or len(items) > 100 for items in (frame['truth'], frame['predictions'])):
            raise ValueError('每帧真值和预测分别最多100框')
        for item in frame['truth']:
            _box(item, classes)
        for item in frame['predictions']:
            _box(item, classes, True)


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
    validated_classes(classes)
    for value in (iou_threshold, confidence_threshold):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 1:
            raise ValueError('评测阈值须大于0且不超过1')
    _validated_frames(classes, frames)
    counts = {name: Counter() for name in classes}
    details = []
    for frame in frames:
        truth, predictions = frame['truth'], frame['predictions']
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
        details.append({'id': frame['id'], 'matches': matches, 'false_positives': false_positives, 'missed': missed,
                        'ignored_low_confidence': len(predictions)-len(selected)})
    totals = sum(counts.values(), Counter())
    return {'scope': 'externally supplied reviewed boxes; not deployment approval',
            'metric': 'single_operating_point', 'iou_threshold': iou_threshold,
            'confidence_threshold': confidence_threshold, 'frames': len(frames),
            'summary': _rates(totals), 'classes': {key: _rates(value) for key, value in counts.items()},
            'false_positive_frames': sum(bool(row['false_positives']) for row in details),
            'missed_frames': sum(bool(row['missed']) for row in details), 'details': details}


COCO_IOU_THRESHOLDS = [round(.5 + .05 * k, 2) for k in range(10)]
_RECALL_POINTS = 101


def _ap101(flags, total_truth):
    """101-point interpolated AP over one confidence-ordered tp/fp sequence; None without truth."""
    if total_truth <= 0:
        return None
    cumulative, precisions, recalls = 0, [], []
    for step, hit in enumerate(flags, start=1):
        cumulative += hit
        precisions.append(cumulative / step)
        recalls.append(cumulative / total_truth)
    for index in range(len(precisions) - 2, -1, -1):
        precisions[index] = max(precisions[index], precisions[index + 1])
    total = 0.
    for point in range(_RECALL_POINTS):
        first = bisect.bisect_left(recalls, point / (_RECALL_POINTS - 1))
        total += precisions[first] if first < len(recalls) else 0.
    return total / _RECALL_POINTS


def _frame_class_ious(frame):
    """Same-class prediction/truth IoU per frame, computed once and reused by every threshold sweep."""
    grouped_predictions, grouped_truth = {}, {}
    for index, item in enumerate(frame['predictions']):
        grouped_predictions.setdefault(item['label'], []).append(index)
    for index, item in enumerate(frame['truth']):
        grouped_truth.setdefault(item['label'], []).append(index)
    per_class = {}
    for name, truth_positions in grouped_truth.items():
        prediction_positions = grouped_predictions.get(name)
        if not prediction_positions:
            continue
        rows = {p: [iou(frame['predictions'][p]['box'], frame['truth'][g]['box']) for g in truth_positions]
                for p in prediction_positions}
        per_class[name] = (rows, truth_positions)
    return per_class


def _mean(values):
    defined = [value for value in values if value is not None]
    return sum(defined) / len(defined) if defined else None


def average_precision(classes, frames, *, iou_thresholds=None):
    """COCO-style AP@0.5 and AP@0.5:0.95 (101-point interpolation), sweeping all confidences.

    Same frame contract as evaluate_boxes; no confidence cutoff, so every prediction
    participates. Matching is one-to-one per frame: highest-IoU unmatched same-class
    truth at IoU >= threshold wins, ties go to the lowest truth index (same rule as
    evaluate_boxes). Classes without reviewed truth report None and are excluded from
    the mean, mirroring COCO. iou_thresholds overrides the thresholds averaged into
    ap50_95 (deduplicated); ap50 is always the IoU=0.5 AP.
    """
    validated_classes(classes)
    average_thresholds = COCO_IOU_THRESHOLDS if iou_thresholds is None else sorted(set(iou_thresholds))
    if not average_thresholds or any(isinstance(t, bool) or not isinstance(t, (int, float))
                                     or not math.isfinite(t) or not 0 < t <= 1 for t in average_thresholds):
        raise ValueError('IoU阈值须为(0,1]内的有限值')
    _validated_frames(classes, frames)
    sweep = sorted(set(average_thresholds) | {0.5})
    truth_counts = Counter(item['label'] for frame in frames for item in frame['truth'])
    ranked = {name: [] for name in classes}
    for f, frame in enumerate(frames):
        for p, prediction in enumerate(frame['predictions']):
            ranked[prediction['label']].append((-prediction['confidence'], f, p))
    for name in ranked:
        ranked[name].sort()
    frame_ious = [_frame_class_ious(frame) for frame in frames]
    class_ap = {name: {} for name in classes}
    for name in classes:
        for threshold in sweep:
            matched = [set() for _ in frames]
            flags = []
            for _, f, p in ranked[name]:
                entry = frame_ious[f].get(name)
                best, choice = -1., -1
                if entry is not None:
                    rows, truth_positions = entry
                    for position, g in enumerate(truth_positions):
                        value = rows[p][position]
                        if g in matched[f] or value < threshold or value <= best:
                            continue
                        best, choice = value, g
                if choice >= 0:
                    matched[f].add(choice)
                    flags.append(1)
                else:
                    flags.append(0)
            class_ap[name][threshold] = _ap101(flags, truth_counts[name])

    def overall(threshold):
        return _mean([class_ap[name][threshold] for name in classes])

    def summary(name):
        if truth_counts[name] == 0:
            return {'ap50': None, 'ap50_95': None}
        return {'ap50': class_ap[name][0.5],
                'ap50_95': _mean([class_ap[name][t] for t in average_thresholds])}

    return {'scope': 'computed on the supplied reviewed frames (holdout: exported val split); '
                     'not an independent unseen dataset; not deployment approval',
            'metric': 'average_precision',
            'iou_thresholds': list(average_thresholds),
            'ap50': overall(0.5),
            'ap50_95': _mean([overall(t) for t in average_thresholds]),
            'per_threshold': [{'iou_threshold': t, 'ap': overall(t)} for t in sweep],
            'classes': {name: summary(name) for name in classes}}
