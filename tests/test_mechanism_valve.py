import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import numpy as np
from pydantic import ValidationError
import pytest

import visual.mechanism_valve as valve
from visual.mechanism_routes import MechanismReports, router
from visual.mechanism_valve_routes import valve_router


ROOT = Path(__file__).resolve().parents[1]


def payload():
    t = np.arange(400) / 100
    response = np.clip((t - 1.2) / .2, 0, 1)
    values = np.stack([190 - response * 50, response * 120], axis=1)
    return {'title': 'synthetic protocol fixture, not field evidence',
            'channels': ['PS1', 'PS2'], 'units': ['bar', 'bar'], 'sample_rate_hz': 100,
            'values': values.tolist(), 'clock_id': 'fixture-clock',
            'timestamps_s': {'PS1': (8 + t).tolist(), 'PS2': (8 + t).tolist()},
            'command_edge': {'time_s': 0, 'clock_id': 'fixture-clock',
                             'source': 'generated replay timeline', 'kind': 'replay_fixture'}}


def app_client(store, shadow):
    app = FastAPI()
    app.include_router(router(store))
    app.include_router(valve_router(store, shadow))
    return TestClient(app)


@pytest.mark.parametrize('offset', [-.01, 0, .01])
def test_timestamp_gate_boundary(offset):
    body = payload()
    for channel in ('PS1', 'PS2'):
        body['timestamps_s'][channel] = [v + offset for v in body['timestamps_s'][channel]]
    result = valve.validate_timestamps(valve.ValveBody(**body))
    assert result['status'] == 'supported'
    assert result['offset_samples']['PS1'] == pytest.approx(offset * 100)


@pytest.mark.parametrize('change,code', [
    ('missing_command', 'no_independent_command_edge'),
    ('missing_clock', 'missing_pressure_clock'), ('clock_mismatch', 'clock_id_mismatch'),
    ('missing_times', 'missing_channel_timestamps'), ('one_channel', 'missing_channel_timestamps'),
    ('offset', 'command_window_offset'), ('skew', 'channel_skew'),
    ('gap', 'timestamp_gap_or_order'), ('reversed', 'timestamp_gap_or_order'),
    ('drift', 'timestamp_grid_drift'),
])
def test_missing_or_inconsistent_evidence_never_calls_model(tmp_path, monkeypatch, change, code):
    body = payload()
    if change == 'missing_command': body.pop('command_edge')
    if change == 'missing_clock': body.pop('clock_id')
    if change == 'clock_mismatch': body['command_edge']['clock_id'] = 'other-clock'
    if change == 'missing_times': body.pop('timestamps_s')
    if change == 'one_channel': body['timestamps_s'].pop('PS2')
    if change == 'offset': body['command_edge']['time_s'] = -.02
    if change == 'skew': body['timestamps_s']['PS2'] = [t + .02 for t in body['timestamps_s']['PS2']]
    if change == 'gap': body['timestamps_s']['PS1'][200] += .003
    if change == 'reversed': body['timestamps_s']['PS1'].reverse()
    if change == 'drift': body['timestamps_s']['PS1'] = (8 + np.arange(400) * .01009).tolist()
    shadow = valve.ValveShadow(tmp_path)
    monkeypatch.setattr(shadow, '_run', lambda *_: pytest.fail('unsupported input must not start model'))
    result = shadow.analyze(body)
    assert result['status'] == 'unsupported' and result['score'] is None
    assert code in {v['code'] for v in result['quality']['reasons']}
    assert result['threshold_exceeded'] is None and not result['alarm_generated']
    assert result['validation_scope'] == ('uploaded_timestamp_claims' if change == 'missing_command' else 'replay_fixture')
    shadow.close()


@pytest.mark.parametrize('changes', [
    {'channels': ['PS2', 'PS1']}, {'units': ['psi', 'psi']}, {'sample_rate_hz': 99},
    {'sample_rate_hz': '100'}, {'values': [[1, 2]] * 399}, {'values': [[1, 2, 3]] * 400},
    {'values': [[float('nan'), 1]] * 400}, {'values': [[True, 1]] * 400},
    {'model_path': '/somewhere/model.onnx'}, {'sync_ok': True}, {'expected_lead_s': 0},
    {'timestamps_s': {'PS1': [8] * 399}},
])
def test_strict_contract_does_not_accept_override_or_missing_values(changes):
    with pytest.raises(ValidationError):
        valve.ValveBody(**(payload() | changes))


def test_fixed_asset_checks_sha_and_manifest_without_loading_ort(tmp_path):
    assert not valve.ValveShadow(tmp_path).capabilities()['available']
    target = tmp_path / 'models/mechanism/valve-fusion'
    target.mkdir(parents=True)
    manifest = json.loads((ROOT / 'models/mechanism/valve-fusion/manifest.json').read_text())
    (target / 'manifest.json').write_text(json.dumps(manifest))
    (target / 'ae.onnx').write_bytes(b'x' * valve.MODEL_BYTES)
    with pytest.raises(valve.ValveError, match='校验失败'):
        valve.pinned_asset(tmp_path)
    manifest['channels'].reverse()
    (target / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(valve.ValveError, match='固定契约'):
        valve.pinned_asset(tmp_path)


def test_bounded_slot_and_close(tmp_path):
    shadow = valve.ValveShadow(tmp_path)
    shadow._slot.acquire()
    try:
        with pytest.raises(valve.ValveError) as caught:
            shadow.analyze(payload())
        assert caught.value.status == 429
    finally:
        shadow._slot.release()
    shadow.close(); shadow.close()
    assert shadow.capabilities()['reason'] == 'closed'
    with pytest.raises(valve.ValveError) as caught:
        shadow.analyze(payload())
    assert caught.value.status == 503


def replace_with_sleeping_child(monkeypatch):
    real_popen = subprocess.Popen
    processes, started = [], threading.Event()
    def sleeping_child(*args, **kwargs):
        process = real_popen([sys.executable, '-c', 'import time;time.sleep(60)'], **kwargs)
        processes.append(process)
        started.set()
        return process
    monkeypatch.setattr(valve, 'pinned_asset', lambda *_: Path('unused-fixture'))
    monkeypatch.setattr(valve.subprocess, 'Popen', sleeping_child)
    return processes, started


def test_real_child_timeout_kills_and_releases_slot(tmp_path, monkeypatch):
    processes, _ = replace_with_sleeping_child(monkeypatch)
    shadow = valve.ValveShadow(tmp_path, timeout_s=.05)
    tick = time.monotonic()
    with pytest.raises(valve.ValveError) as caught:
        shadow.analyze(payload())
    assert time.monotonic() - tick < 3  # Includes a child that never reads stdin on Windows.
    assert caught.value.status == 504
    assert len(processes) == 1 and processes[0].poll() is not None
    assert shadow._process is None and not shadow._slot.locked()
    shadow.close()


def test_close_cancels_only_own_inflight_child(tmp_path, monkeypatch):
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'],
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    processes, started = replace_with_sleeping_child(monkeypatch)
    shadow = valve.ValveShadow(tmp_path, timeout_s=10)
    errors = []
    def analyze():
        try: shadow.analyze(payload())
        except valve.ValveError as error: errors.append(error.status)
    worker = threading.Thread(target=analyze)
    try:
        worker.start()
        assert started.wait(3)
        tick = time.monotonic(); shadow.close()
        assert time.monotonic() - tick < 1
        worker.join(3)
        assert not worker.is_alive() and errors == [503]
        assert processes[0].poll() is not None and unrelated.poll() is None
    finally:
        shadow.close()
        unrelated.kill(); unrelated.wait(timeout=3)
        worker.join(3)


def test_unsupported_http_persists_and_exports_without_model(tmp_path):
    store = MechanismReports(tmp_path / 'reports.sqlite')
    shadow = valve.ValveShadow(tmp_path)
    try:
        body = payload(); body.pop('timestamps_s')
        client = app_client(store, shadow)
        assert not client.get('/api/mechanism/valve/capabilities').json()['available']
        response = client.post('/api/mechanism/valve/analyses', json=body)
        assert response.status_code == 201, response.text
        report = response.json()
        assert report['kind'] == 'valve_fusion_shadow' and report['result']['score'] is None
        assert report['result']['status'] == 'unsupported'
        assert client.get(f"/api/mechanism/analyses/{report['id']}/export").json() == report
        with pytest.raises(sqlite3.IntegrityError):
            store._db.execute('DELETE FROM mechanism_reports')
        store._db.rollback()
        assert client.post('/api/mechanism/valve/analyses', json=payload()).status_code == 503
        assert store.list()['total'] == 1
    finally:
        store.close(); shadow.close()


def test_real_pinned_graph_supplied_parity_through_http(tmp_path):
    fixture = ROOT / '.v0/mechanism/valve-api-fixture.json'
    shadow = valve.ValveShadow(ROOT)
    if not fixture.is_file() or not shadow.capabilities()['available']:
        pytest.skip('optional pinned local valve model/parity replay not installed')
    store = MechanismReports(tmp_path / 'reports.sqlite')
    try:
        body = json.loads(fixture.read_text(encoding='utf8'))
        response = app_client(store, shadow).post('/api/mechanism/valve/analyses', json=body)
        assert response.status_code == 201, response.text
        result = response.json()['result']
        assert result['status'] == 'shadow_evidence'
        assert result['score'] == pytest.approx(.8171583414077759, abs=1e-5)
        assert result['model_sha256'] == valve.MODEL_SHA256 and not result['threshold_exceeded']
        assert result['validation_scope'] == 'replay_fixture' and not result['trusted_device_source']
        assert not result['alarm_generated'] and result['control_effect'] == 'none'
        assert shadow._process is None
    finally:
        store.close(); shadow.close()


def test_import_and_constructor_do_not_load_ort_or_start_worker():
    code = ('import sys,threading;from pathlib import Path;before=set(threading.enumerate());'
            'from visual.mechanism_valve import ValveShadow;v=ValveShadow(Path("."));'
            'v.capabilities();assert "onnxruntime" not in sys.modules;'
            'assert "torch" not in sys.modules;assert set(threading.enumerate())==before;v.close()')
    completed = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, timeout=15)
    assert completed.returncode == 0, completed.stderr.decode(errors='replace')
