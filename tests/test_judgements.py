import itertools
import pytest

from visual.judgements import aggregate_judgements
from visual.inspection import InspectionService, InspectionStore, Scenario


def result(verdict, confidence=.8):
    return dict(ok=True, verdict=verdict, confidence=confidence, reason=verdict)


@pytest.mark.parametrize('order', list(itertools.permutations(['PASS', 'NG', 'UNCERTAIN'])))
def test_negative_cannot_be_hidden_by_pass(order):
    assert aggregate_judgements([result(v) for v in order])[0] == 'NG'


def test_unknown_prevents_pass_and_lowest_confidence_is_preserved():
    assert aggregate_judgements([result('PASS'), result('UNCERTAIN')])[0] == 'UNCERTAIN'
    assert aggregate_judgements([result('PASS', .9), result('PASS', .4)])[1] == .4


@pytest.mark.parametrize('bad', [result('YES'), result('PASS', None), result('PASS', True),
                                result('PASS', float('nan')), result('NG', 1.1)])
def test_invalid_result_is_not_a_pass(bad):
    with pytest.raises(ValueError):
        aggregate_judgements([result('PASS'), bad])


@pytest.mark.parametrize('mode', ['single', 'first_article'])
def test_actual_inspection_multiframe_path(tmp_path, mode):
    class Provider:
        def __init__(self):
            diff = dict(ok=True, raw='{"differences":[]}')
            self.script = iter([diff, result('PASS'), diff, result('NG')] if mode == 'first_article'
                               else [result('PASS'), result('NG')])

        def inspect(self, *args):
            return next(self.script)

    store = InspectionStore(tmp_path / 'inspection.db')
    service = InspectionService([], store, tmp_path / 'images', Provider())
    try:
        (tmp_path / 'a.jpg').write_bytes(b'fixture')
        (tmp_path / 'b.jpg').write_bytes(b'fixture')
        scenario = Scenario('test', 'test', mode=mode, ok_refs=(str(tmp_path/'a.jpg'),), ng_refs=(str(tmp_path/'b.jpg'),))
        verdict = service._judge(scenario, tmp_path, ['a.jpg', 'b.jpg'])
        assert verdict[0] == 'NG'
    finally:
        service.stop(close_store=True)


def test_missing_difference_evidence_cannot_become_pass(tmp_path):
    class FailedDiff:
        def inspect(self, *args):
            return {'ok': True, 'raw': '{"verdict":"PASS"}'}
    store = InspectionStore(tmp_path / 'inspection.db')
    service = InspectionService([], store, tmp_path / 'images', FailedDiff())
    try:
        (tmp_path / 'a.jpg').write_bytes(b'fixture')
        with pytest.raises(RuntimeError, match='差异分析失败'):
            service._judge(Scenario('test', 'test', mode='first_article',
                                    ok_refs=(str(tmp_path/'a.jpg'),), ng_refs=(str(tmp_path/'a.jpg'),)), tmp_path, ['a.jpg'])
    finally:
        service.stop(close_store=True)


def test_missing_reference_does_not_call_model(tmp_path):
    class Never:
        def inspect(self, *args):
            raise AssertionError('reference failure must not call the model')
    store = InspectionStore(tmp_path / 'inspection.db')
    service = InspectionService([], store, tmp_path / 'images', Never())
    try:
        (tmp_path / 'image.jpg').write_bytes(b'fixture')
        sc = Scenario('test', 'test', mode='first_article', ok_refs=('missing',), ng_refs=('missing',))
        with pytest.raises(RuntimeError, match='参考图缺失'):
            service._judge(sc, tmp_path, ['image.jpg'])
    finally:
        service.stop(close_store=True)
