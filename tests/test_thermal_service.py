"""Real inbox/database ingress without a camera consumer; clock-controlled geometry-free signals."""
import pytest

from visual.fusion import SignalAuditStore, SignalError, SignalInbox
from visual.thermal import ThermalCfg, ThermalRule
from visual.thermal_service import ThermalSignalService


@pytest.fixture
def rig(tmp_path):
    clock = [1000., 10.]
    alerts = []
    cfg = ThermalCfg(enabled=True, rules=[ThermalRule(
        id='hot', source='sensor', name='temperature', kind='temperature',
        threshold=60, min_s=.2, min_samples=3)])
    service = ThermalSignalService(cfg, alerts.append,
                                   wall_clock=lambda:clock[0], monotonic=lambda:clock[1])
    store = SignalAuditStore(tmp_path / 'signals.db')
    inbox = SignalInbox(store, 'station', capacity=1, clock=lambda:clock[0])
    inbox.bind_observer(service.observe, service.keys)
    yield clock, alerts, service, store, inbox
    inbox.close()
    service.close()
    store.close()


def packet(clock, seq, **changes):
    data = dict(source='sensor', name='temperature', sequence=seq,
                observed_at=clock[0], ttl_ms=1000, quality='ok',
                value={'temperature':70., 'temperature_unit':'C'})
    data.update(changes)
    return data


def tick(clock):
    clock[0] += .11
    clock[1] += .11


def test_no_camera_full_sop_queue_still_alerts_and_retains_queue(rig):
    clock, alerts, service, store, inbox = rig
    first = inbox.push(packet(clock, 0))
    assert first['queued_for_sop'] is True
    for seq in range(1, 140):
        tick(clock)
        result = inbox.push(packet(clock, seq))
        assert result['queued_for_sop'] is False
        assert result['observer_status'] == 'processed'
    assert inbox.depth == 1
    assert len(alerts) == 1 and alerts[0]['kind'] == 'thermal_temperature'
    assert service.snapshot()['rules']['hot']['status'] == 'alert'
    assert store.audit('station')[0]['reason'] == 'thermal_observer_only'
    # Full queue never silently accepts unrelated SOP inputs.
    with pytest.raises(SignalError) as exc:
        inbox.push(packet(clock, 0, source='plc', name='ready'))
    assert exc.value.code == 'queue_full'
    # Original queue content is not consumed by the observer (now expired).
    assert next(r for r in store.audit('station', 500) if r['id'] == first['audit_id'])['state'] == 'queued'


def test_duplicate_and_snapshots_do_not_confirm_and_ttl_goes_unknown(rig):
    clock, alerts, service, store, inbox = rig
    sample = packet(clock, 0)
    inbox.push(sample)
    assert inbox.push(sample)['status'] == 'duplicate'
    for _ in range(10):
        assert service.snapshot()['rules']['hot']['status'] == 'pending'
    assert not alerts
    clock[1] += 1.01
    clock[0] += 1.01
    assert service.snapshot()['rules']['hot']['status'] == 'unknown'
    assert not alerts


def test_observer_runs_outside_inbox_lock_and_sop_receives_original(rig):
    clock, alerts, service, store, inbox = rig
    # An emit callback can query the inbox without deadlocking.
    service._emit = lambda alert: alerts.append((alert, inbox.depth))
    for seq in range(3):
        inbox.push(packet(clock, seq))
        tick(clock)
    assert alerts[0][1] == 1
    inbox.bind_consumer()
    events = inbox.drain(0.)
    assert len(events) == 1 and events[0].data['sequence'] == 0


def test_bad_quality_does_not_confirm_and_close_clears(rig):
    clock, alerts, service, store, inbox = rig
    for seq in range(3):
        inbox.push(packet(clock, seq, quality='uncertain'))
        tick(clock)
    assert not alerts
    assert service.snapshot()['rules']['hot']['status'] == 'unknown'
    service.close()
    inbox.push(packet(clock, 3))
    assert service.snapshot()['rules']['hot']['reason'] == 'station_stopped'
