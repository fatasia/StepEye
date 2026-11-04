"""退化 RUL 服务：bundle 指纹、契约校验与真实数据端到端。"""
import json
from pathlib import Path

import numpy as np
import pytest

from visual.mechanism_degradation import (DegBody, DegError, DegradationService,
                                          DegWindow, pinned_bundle)
from visual import mechanism_degradation_worker as worker

ROOT = Path(__file__).resolve().parents[1]


def make_body(rms, baseline, unit='Bearing-test'):
    return DegBody(dataset='FEMTO-PRONOSTIA-LearningSet',
                   windows=[DegWindow(unit_id=unit, baseline_rms=baseline, rms_values=rms)])


def real_observation(fraction=0.4):
    """从基线特征缓存取一个真实留出试验观测段（40% 寿命处）。"""
    rows = list(csv_reader())
    trial = [r for r in rows if r['bearing'] == 'Bearing1_2']
    cut = int(len(trial) * fraction)
    obs = trial[:cut]
    rms = np.asarray([max(float(r['rms_h']), float(r['rms_v'])) for r in obs])
    baseline = float(np.median(rms[:3]))
    return rms.tolist(), baseline, float(trial[-1]['t_min'])


def csv_reader():
    import csv
    cache = ROOT.parent / '.v0' / 'degradation-rul' / 'results' / 'features.csv'
    return csv.DictReader(cache.open())


def test_bundle_fingerprint_intact(tmp_path):
    service = DegradationService(ROOT)
    assert pinned_bundle(ROOT).is_dir()
    assert service.capabilities()['available'] is True
    tampered = tmp_path / 'x'
    with pytest.raises(DegError, match='不可用'):
        pinned_bundle(tampered)


def test_contract_rejects_duplicates_and_bad_rms():
    win = DegWindow(unit_id='a', baseline_rms=1.0, rms_values=[1.0, 1.1, 1.2, 1.1, 1.0])
    with pytest.raises(ValueError, match='Duplicate'):
        DegBody(dataset='FEMTO-PRONOSTIA-LearningSet', windows=[win, win])
    with pytest.raises(ValueError):
        DegWindow(unit_id='a', baseline_rms=1.0, rms_values=[1.0, 0.0, 1.2, 1.1, 1.0])


def test_worker_similarity_on_real_holdout():
    """真实留出试验（Bearing1_2，40% 寿命）端到端：相似性 RUL 与基线报告同数量级。"""
    rms, baseline, duration = real_observation(0.4)
    body = make_body(rms, baseline)
    result = worker_run(body)
    win = result['windows'][0]
    assert win['method'] == 'similarity' and win['matched_bearing']
    # 基线报告该试验同点 similarity 预测（35.2 分钟 MAE）量级核对
    report = json.loads((ROOT.parent / '.v0' / 'degradation-rul' / 'results' /
                         'baseline_report.json').read_text(encoding='utf-8'))
    ref = next(p for p in report['trials']['Bearing1_2']['predictions']['similarity']
               if abs(p['t_obs_min'] - round(duration * 0.4)) <= 1)
    assert abs(win['rul_min'] - ref['pred_rul_min']) < 2.0, (win['rul_min'], ref)
    assert 0 <= win['rul_min'] <= report['rul_cap_min']


def worker_run(body):
    import subprocess, sys, tempfile
    with tempfile.TemporaryDirectory() as td:
        request = Path(td) / 'request.json'
        request.write_text(json.dumps({'root': str(ROOT), 'body': body.model_dump()}),
                           encoding='utf-8')
        p = subprocess.run([sys.executable, '-m', 'visual.mechanism_degradation_worker',
                            '--request', str(request)],
                           capture_output=True, text=True, cwd=str(ROOT), timeout=60)
        assert p.returncode == 0, p.stderr[-500:]
        return json.loads(p.stdout)


def test_service_analyze_and_close():
    service = DegradationService(ROOT)
    rms, baseline, _ = real_observation(0.55)
    report = service.analyze(make_body(rms, baseline, unit='servo-1'))
    assert report['windows'][0]['unit_id'] == 'servo-1'
    assert report['scope'].startswith('trend-level')
    assert service.close() is True
    assert service.capabilities()['available'] is False
    with pytest.raises(DegError, match='已关闭'):
        service.analyze(make_body(rms, baseline))
