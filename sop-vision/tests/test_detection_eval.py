import pytest
from fastapi.testclient import TestClient
from visual.detection_eval import average_precision, evaluate_boxes
from visual.server import build_app
from visual.auth import AuthStore


def box(label='part', confidence=None):
    item = {'label': label, 'box': [.1, .1, .4, .4]}
    if confidence is not None: item['confidence'] = confidence
    return item


def frame(truth=None, predictions=None):
    return {'id': 'f1', 'reviewed': True, 'truth': truth or [], 'predictions': predictions or []}


def test_duplicate_predictions_and_wrong_class_count_as_errors():
    report = evaluate_boxes(['part', 'other'], [frame([box()], [box(confidence=.9), box(confidence=.8), box('other', .7)])])
    assert report['summary']['tp'] == 1
    assert report['summary']['fp'] == 2
    assert report['summary']['precision'] == pytest.approx(1/3)
    assert report['classes']['other']['recall'] is None


def test_wrong_class_is_false_positive_and_missed_truth():
    report = evaluate_boxes(['part', 'other'], [frame([box()], [box('other', .9)])])
    assert report['summary'] == dict(tp=0, fp=1, fn=1, precision=0., recall=0., f1=0.)


def test_empty_reviewed_negative_is_not_perfect_recall():
    report = evaluate_boxes(['part'], [frame()])
    assert report['summary']['recall'] is None
    assert report['summary']['precision'] is None


def test_low_confidence_does_not_hide_missed_truth():
    report = evaluate_boxes(['part'], [frame([box()], [box(confidence=.1)])])
    assert report['summary']['fn'] == 1
    assert report['details'][0]['ignored_low_confidence'] == 1


@pytest.mark.parametrize('change', [{'reviewed': False}, {'truth': None}, {'predictions': [box(confidence=float('nan'))]},
                                  {'truth': [box('unknown')]}, {'id': ''}, {'truth': [{'label':'part','box':[0,0,0,1]}]}])
def test_invalid_truth_or_prediction_is_rejected(change):
    value = frame()
    value.update(change)
    with pytest.raises(ValueError):
        evaluate_boxes(['part'], [value])


def test_same_frame_cannot_be_evaluated_twice():
    with pytest.raises(ValueError):
        evaluate_boxes(['part'], [frame(), frame()])


def test_api_requires_operator_and_rejects_invalid_frames(tmp_path):
    auth = AuthStore(tmp_path / 'auth.db')
    auth.set_user('viewer', 'ViewerPassword123', 'viewer')
    auth.set_user('operator', 'OperatorPassword123', 'operator')
    app = build_app([], tmp_path / 'data', auth=auth)
    payload = {'classes': ['part'], 'frames': [frame([box()], [box(confidence=.9)])]}
    with TestClient(app) as client:
        assert client.post('/api/evaluations/detection', json=payload).status_code == 401
        client.post('/api/auth/login', json={'username':'viewer','password':'ViewerPassword123'})
        assert client.post('/api/evaluations/detection', json=payload).status_code == 403
        client.post('/api/auth/login', json={'username':'operator','password':'OperatorPassword123'})
        response = client.post('/api/evaluations/detection', json=payload)
        assert response.status_code == 200 and response.json()['summary']['tp'] == 1
        payload['frames'][0]['reviewed'] = False
        assert client.post('/api/evaluations/detection', json=payload).status_code == 422
    auth.close()


def ap_frame(fid, truth, predictions):
    return {'id': fid, 'reviewed': True, 'truth': truth, 'predictions': predictions}


def test_ap_perfect_predictions_score_one():
    frames = [ap_frame('a', [box()], [box(confidence=.9)]),
              ap_frame('b', [box()], [box(confidence=.8)])]
    report = average_precision(['part'], frames)
    assert report['metric'] == 'average_precision'
    assert report['ap50'] == 1.0 and report['ap50_95'] == 1.0
    assert report['classes']['part'] == {'ap50': 1.0, 'ap50_95': 1.0}


def test_ap_known_partial_curve_matches_hand_computed_value():
    # 3 truths; confidence order TP(.9), FP(.8), TP(.7) with identical boxes (IoU=1 at every threshold).
    # Suffix-max precision [1, 2/3, 2/3], recall steps 1/3, 1/3, 2/3:
    # 34 recall points <= 1/3 hit precision 1, 33 points in (1/3, 2/3] hit 2/3 -> AP = 56/101.
    frames = [ap_frame('a', [box(), box()], [box(confidence=.9)]),
              ap_frame('b', [], [box(confidence=.8)]),
              ap_frame('c', [box()], [box(confidence=.7)])]
    report = average_precision(['part'], frames)
    assert report['ap50'] == pytest.approx(56 / 101)
    assert report['ap50_95'] == pytest.approx(56 / 101)
    assert [row['ap'] for row in report['per_threshold']] == [pytest.approx(56 / 101)] * 10


def test_ap_all_false_positives_is_zero_when_truth_exists():
    frames = [ap_frame('a', [box()], [box('other', .9)])]
    report = average_precision(['part', 'other'], frames)
    assert report['ap50'] == 0.0
    assert report['classes']['part']['ap50'] == 0.0
    assert report['classes']['other']['ap50'] is None  # no truth -> excluded from the mean


def test_ap_without_any_truth_is_none():
    frames = [ap_frame('a', [], [box(confidence=.9)])]
    report = average_precision(['part'], frames)
    assert report['ap50'] is None and report['ap50_95'] is None
    assert report['classes']['part'] == {'ap50': None, 'ap50_95': None}


def test_ap_duplicate_prediction_is_single_match_and_late_fp_does_not_lower_ap():
    # One truth, two identical detections: first is TP, duplicate is FP.
    # Recall reaches 1.0 at precision 1, so COCO suffix-max keeps AP at 1.0.
    frames = [ap_frame('a', [box()], [box(confidence=.9), box(confidence=.8)])]
    report = average_precision(['part'], frames)
    assert report['ap50'] == 1.0 and report['ap50_95'] == 1.0


def test_ap_partial_overlap_only_counts_at_lower_iou_thresholds():
    # Boxes overlap with IoU = 2/3: matches COCO thresholds 0.50-0.65 (4 of 10), fails 0.70+.
    truth = {'label': 'part', 'box': [0., 0., .5, .5]}
    hit = {'label': 'part', 'box': [0., .1, .5, .6], 'confidence': .9}
    report = average_precision(['part'], [ap_frame('a', [truth], [hit])])
    assert report['ap50'] == 1.0
    assert report['ap50_95'] == pytest.approx(0.4)


def test_ap_mean_excludes_classes_without_truth():
    frames = [ap_frame('a', [box('part')], [box('part', .9)])]
    report = average_precision(['part', 'other'], frames)
    assert report['ap50'] == report['classes']['part']['ap50'] == 1.0
    assert report['classes']['other'] == {'ap50': None, 'ap50_95': None}


def test_ap_reuses_frame_validation_and_checks_thresholds():
    valid = [ap_frame('a', [], [])]
    with pytest.raises(ValueError):
        average_precision(['part'], [ap_frame('a', [box('unknown')], [])])
    with pytest.raises(ValueError):
        average_precision(['part'], [{'id': 'a', 'reviewed': False, 'truth': [], 'predictions': []}])
    with pytest.raises(ValueError):
        average_precision([], valid)
    with pytest.raises(ValueError):
        average_precision(['part'], valid, iou_thresholds=[])
    with pytest.raises(ValueError):
        average_precision(['part'], valid, iou_thresholds=[0])
    with pytest.raises(ValueError):
        average_precision(['part'], valid, iou_thresholds=[1.5])
