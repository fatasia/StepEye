"""Device thermal alarms must reach storage even when camera quality is blocked."""
import numpy as np
from visual.dsl import StationConfig
from visual.runtime import StationRuntime
from visual.server import StationManager, Hub
from visual.fusion import SignalInbox


def test_thermal_signal_reaches_alert_store_with_dark_camera(tmp_path):
    manager = StationManager([], tmp_path / 'data', Hub())
    cfg = StationConfig.model_validate({
        'station': 'thermal-test', 'source': {'type': 'sim', 'width': 100, 'height': 100},
        'zones': [], 'hands': {'enabled': False},
        'quality': {'enabled': True, 'every_n': 1, 'brightness': [20, 235]},
        'thermal': {'enabled': True, 'rules': [{
            'id': 'overheat', 'source': 'sensor', 'name': 'temp', 'kind': 'temperature',
            'threshold': 60, 'min_s': .1, 'min_samples': 2}]}})
    rt = StationRuntime(cfg, tmp_path / 'rt')
    now = [1000.]
    rt.signal_inbox = SignalInbox(manager.signal_store, 'thermal-test', clock=lambda: now[0])
    rt.signal_inbox.bind_consumer()
    rt.listeners.append(manager._forwarder('thermal-test'))
    try:
        for i in range(4):
            now[0] = 1000 + i * .1
            rt.signal_inbox.push({'source': 'sensor', 'name': 'temp', 'sequence': i,
                'observed_at': now[0], 'ttl_ms': 1000, 'quality': 'ok',
                'value': {'temperature': 80., 'temperature_unit': 'C'}})
            rt.process_frame(i * .1, np.zeros((100, 100, 3), np.uint8))
        assert rt._quality_blocked
        assert len(rt.alerts) == 1
        assert rt.alerts[0]['kind'] == 'thermal_temperature'
        row = manager.alerts._db.execute('SELECT kind,message FROM alerts').fetchone()
        assert row[0] == 'overheat' and '80.00' in row[1]
    finally:
        rt.signal_inbox.close()
        rt.close()
        manager.stop()


def test_http_thermal_alarm_without_any_camera_frame(tmp_path):
    import yaml
    from fastapi.testclient import TestClient
    from visual.server import build_app
    config = tmp_path / 'thermal.yaml'
    config.write_text(yaml.safe_dump({
        'station': 'sensor-only', 'source': {'type': 'sim'}, 'hands': {'enabled': False},
        'zones': [], 'signal_sources': ['sensor'], 'thermal': {'enabled': True, 'rules': [{
            'id': 'hot', 'source': 'sensor', 'name': 'temp', 'kind': 'temperature',
            'threshold': 60, 'min_s': .1, 'min_samples': 2}]}}), encoding='utf-8')
    app = build_app([config], tmp_path / 'data')
    manager = app.state.manager
    rt = manager.get('sensor-only')
    now = [1000.]
    rt.signal_inbox.clock = lambda: now[0]
    rt.signal_inbox.capacity = 1
    rt.thermal_service._wall_clock = lambda: now[0]
    rt.thermal_service._monotonic = lambda: now[0]
    # No lifespan/capture start: prove a camera read is not a prerequisite.
    client = TestClient(app)
    try:
        for seq in range(3):
            now[0] = 1000 + seq * .2
            result = client.post('/api/stations/sensor-only/signals', json={
                'source': 'sensor', 'name': 'temp', 'sequence': seq, 'observed_at': now[0],
                'ttl_ms': 1000, 'quality': 'ok', 'value': {'temperature': 80., 'temperature_unit': 'C'}})
            assert result.status_code == 202, result.text
            assert result.json()['queued_for_sop'] == (seq == 0)
        assert rt.frame_idx == 0 and rt.raw is None
        assert manager.alerts._db.execute('SELECT COUNT(*) FROM alerts').fetchone()[0] == 1
        state = client.get('/api/stations/sensor-only/state').json()
        assert state['thermal']['rules']['hot']['status'] == 'alert'
        now[0] += 2
        assert client.get('/api/stations/sensor-only/state').json()['thermal']['rules']['hot']['status'] == 'unknown'
    finally:
        client.close()
        manager.stop()
