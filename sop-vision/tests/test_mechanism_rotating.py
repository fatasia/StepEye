import builtins
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient
import numpy as np
from pydantic import ValidationError
import pytest

from visual.mechanism_rotating import (MAX_SPECTRUM_POINTS, RotatingBody,
                                       analyze_rotating, import_waveform)
from visual.mechanism_rotating_routes import rotating_router
from visual.mechanism_routes import MechanismReports, router


def payload(seconds=1):
    rate = 12000
    t = np.arange(rate * seconds) / rate
    signal = (1 + .8 * np.cos(2 * np.pi * 100 * t)) * np.sin(2 * np.pi * 2400 * t)
    return {'title': 'fixture AM waveform', 'unit': 'g', 'sample_rate_hz': rate,
            'rpm': 1200, 'band_hz': [1200, 5000],
            'orders': {'injected': 5, 'other': 3}, 'values': signal.tolist()}


def client_for(store):
    app = FastAPI()
    app.include_router(router(store))
    app.include_router(rotating_router(store))
    return TestClient(app)


def test_amplitude_modulation_has_expected_envelope_peak_not_health_verdict():
    pytest.importorskip('scipy')
    result = analyze_rotating(RotatingBody(**payload()))
    assert result['status'] == 'evidence_only'
    assert result['samples'] == 12000 and result['duration_s'] == 1
    assert result['sample_rate_hz'] == 12000 and result['unit'] == 'g'
    assert result['rms'] == pytest.approx(np.sqrt(.66))
    first = result['candidates'][0]
    assert first['candidate'] == 'injected'
    assert first['peaks'][0]['expected_hz'] == 100
    assert first['peaks'][0]['observed_hz'] == pytest.approx(100)
    assert first['evidence_score'] > result['candidates'][1]['evidence_score']
    assert result['rul_cycles'] is None and not result['supports_fault_root_cause']
    assert result['review_required'] and not result['production_approval']
    assert 'verdict' not in result and 'health_score' not in result
    json.dumps(result, allow_nan=False)


def test_constant_signal_is_unknown_without_healthy_candidates():
    data = payload()
    data['values'] = [4] * 12000
    result = analyze_rotating(data)
    assert result['status'] == 'unknown'
    assert result['reason'] == 'signal_has_no_usable_variation'
    assert result['candidates'] == [] and result['frequency_hz'] == []


def test_display_is_bounded_and_retains_known_peak():
    pytest.importorskip('scipy')
    result = analyze_rotating(payload(10))
    assert len(result['frequency_hz']) == len(result['envelope_psd']) <= MAX_SPECTRUM_POINTS
    assert result['spectrum_display']['full_bin_count'] == 10001
    peak = int(np.argmax(result['envelope_psd']))
    assert result['frequency_hz'][peak] == pytest.approx(100)
    assert result['candidates'][0]['peaks'][0]['observed_hz'] == 100


def test_csv_matches_json_and_checks_time_intervals():
    pytest.importorskip('scipy')
    data = payload()
    text = 'time_s,acceleration\n' + '\n'.join(
        f'{i / 12000:.12f},{value:.17g}' for i, value in enumerate(data['values']))
    expected = analyze_rotating(data)
    del data['values']
    data['csv_text'] = text
    actual = analyze_rotating(data)
    assert actual['sampling_timestamps_checked']
    assert not expected['sampling_timestamps_checked']
    assert actual['rms'] == pytest.approx(expected['rms'])
    assert actual['candidates'] == expected['candidates']


@pytest.mark.parametrize('text', [
    'time,value\n0,1', 'time_s,acceleration\n0,1\n0.02,2',
    'time_s,acceleration\n0,1\n0,2', 'time_s,acceleration\n0,nan',
    'time_s,acceleration\ninf,2', 'time_s,acceleration\n0,1,2',
    'time_s,acceleration\n0,', 'time_s,acceleration\n0,1000001',
    'time_s,acceleration\n0,1\n-0.01,2',
])
def test_csv_rejects_missing_nonfinite_and_irregular_samples(text):
    with pytest.raises(ValueError):
        import_waveform(text, 100)


@pytest.mark.parametrize('changes', [
    {'unit': 'A'}, {'rpm': 0}, {'rpm': float('nan')}, {'rpm': True},
    {'sample_rate_hz': 0}, {'band_hz': [1200, 7000]}, {'band_hz': [2000, 1000]},
    {'orders': {}}, {'orders': {'unknown': float('inf')}}, {'orders': {'../x': 5}},
    {'orders': {'above_nyquist': 100}}, {'values': [1] * 200001},
    {'values': [float('nan')] * 12000}, {'values': [True] * 12000},
    {'csv_text': 'time_s,acceleration\n0,1'}, {'values': None},
])
def test_invalid_json_contract_rejected(changes):
    with pytest.raises(ValidationError):
        RotatingBody(**(payload() | changes))


@pytest.mark.parametrize('count', [11999, 120001])
def test_duration_limit(count):
    with pytest.raises(ValueError, match='1–10秒'):
        analyze_rotating(payload() | {'values': [1] * count})


def test_persistent_report_uses_shared_list_export_and_immutable_store(tmp_path):
    pytest.importorskip('scipy')
    path = tmp_path / 'mechanism.sqlite'
    store = MechanismReports(path)
    try:
        client = client_for(store)
        response = client.post('/api/mechanism/rotating/analyses', json=payload())
        assert response.status_code == 201, response.text
        report = response.json()
        assert report['kind'] == 'bearing_envelope_evidence'
        assert report['actor'] == 'local' and len(report['input_sha256']) == 64
        rid = report['id']
        assert client.get(f'/api/mechanism/analyses/{rid}').json() == report
        assert client.get(f'/api/mechanism/analyses/{rid}/export').json() == report
        assert client.get('/api/mechanism/analyses').json()['items'][0]['kind'] == report['kind']
        with pytest.raises(sqlite3.IntegrityError):
            store._db.execute('UPDATE mechanism_reports SET body=?', ('{}',))
        store._db.rollback()
    finally:
        store.close()
    reopened = MechanismReports(path)
    try:
        assert reopened.get(rid) == report
    finally:
        reopened.close()


def test_busy_invalid_and_missing_dependency_http_statuses(tmp_path, monkeypatch):
    store = MechanismReports(tmp_path / 'mechanism.sqlite')
    try:
        client = client_for(store)
        assert client.get('/api/mechanism/rotating/capabilities').json()['orders_required']
        assert client.post('/api/mechanism/rotating/analyses', json=payload() | {'unit': 'A'}).status_code == 422
        assert client.post('/api/mechanism/rotating/analyses', json=payload() | {'values': [1] * 100}).status_code == 400
        store._slots.acquire()
        try:
            assert client.post('/api/mechanism/rotating/analyses', json=payload()).status_code == 429
        finally:
            store._slots.release()
        original_import = builtins.__import__
        def unavailable(name, *args, **kwargs):
            if name == 'scipy.signal':
                raise ImportError('fixture optional dependency unavailable')
            return original_import(name, *args, **kwargs)
        monkeypatch.setattr(builtins, '__import__', unavailable)
        response = client.post('/api/mechanism/rotating/analyses', json=payload())
        assert response.status_code == 503 and 'requirements-mechanism.txt' in response.text
        assert store.list()['total'] == 0
    finally:
        store.close()


def test_import_alone_does_not_load_scipy_or_start_worker():
    root = Path(__file__).resolve().parents[1]
    code = ('import sys,threading;before=set(threading.enumerate());'
            'from visual.mechanism_rotating_routes import rotating_router;'
            'assert "scipy" not in sys.modules;assert set(threading.enumerate())==before')
    result = subprocess.run([sys.executable, '-c', code], cwd=root, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
