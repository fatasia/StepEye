"""Official COCO metrics: size buckets, empty detections and protocol bounds."""
import pytest
from visual.coco_metrics import coco_bbox_metrics

pytest.importorskip('pycocotools')


def sample(size, predicted=True):
    box = [.1, .1, .1 + size/640, .1 + size/640]
    return {'id': str(size), 'reviewed': True, 'truth': [{'label': 'part', 'box': box}],
            'predictions': [{'label': 'part', 'box': box, 'confidence': .1}] if predicted else []}


def test_perfect_all_size_buckets_and_low_score():
    frames = [sample(size) for size in (20, 50, 120)]
    result = coco_bbox_metrics(['part', 'absent'], frames, {f['id']: (640, 640) for f in frames})
    assert result['status'] == 'available'
    assert len(result['metrics']) == 12
    assert all(value == pytest.approx(1) for value in result['metrics'].values())


def test_empty_predictions_and_missing_size_groups():
    result = coco_bbox_metrics(['part'], [sample(20, False)], {'20': (640, 640)})
    assert result['metrics']['AP'] == 0
    assert result['metrics']['AR_small'] == 0
    assert result['metrics']['AP_large'] is None


def test_no_truth_is_undefined_not_perfect():
    frame = sample(20); frame['truth'] = []
    result = coco_bbox_metrics(['part'], [frame], {'20': (640, 640)})
    assert all(value is None for value in result['metrics'].values())


def test_invalid_dimensions():
    with pytest.raises(ValueError, match='pixel dimensions'):
        coco_bbox_metrics(['part'], [sample(20)], {'20': (0, 640)})


def test_optional_dependency_absent_is_explicit(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, 'pycocotools.coco', None)
    result = coco_bbox_metrics(['part'], [sample(20)], {'20': (640, 640)})
    assert result['status'] == 'unavailable'
    assert 'metrics' not in result
