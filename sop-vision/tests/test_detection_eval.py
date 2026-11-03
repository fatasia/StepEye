import pytest
from fastapi.testclient import TestClient
from visual.detection_eval import evaluate_boxes
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
