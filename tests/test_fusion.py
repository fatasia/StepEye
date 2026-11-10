from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from visual.events import SIGNAL
from visual.fusion import SignalAuditStore, SignalError, SignalInbox, SignalPacket


def packet(sequence=1, **changes):
    return {"source": "plc-1", "name": "locked", "sequence": sequence, "observed_at": 1000.0,
            "ttl_ms": 5000, "quality": "ok", "value": True, **changes}


@pytest.fixture
def env(tmp_path):
    clock = [1000.0]
    store = SignalAuditStore(tmp_path / "signals.db")
    inbox = SignalInbox(store, "ST", clock=lambda: clock[0])
    yield store, inbox, clock
    inbox.close()
    store.close()


def test_queue_uses_frame_clock_and_preserves_provenance(env):
    store, inbox, clock = env
    receipt = inbox.push(packet())
    assert receipt["status"] == "accepted" and inbox.depth == 1
    inbox.bind_consumer()
    clock[0] = 1001.0
    events = inbox.drain(12.5)
    assert len(events) == 1
    event = events[0]
    assert event.type == SIGNAL and event.ts == 12.5 and event.name == "locked" and event.value is True
    assert event.data == {"source": "plc-1", "sequence": 1, "observed_at": 1000.0, "received_at": 1000.0,
                          "consumed_at": 1001.0, "expires_at": 1005.0, "valid_until_ts": 16.5,
                          "quality": "ok", "audit_id": receipt["audit_id"]}
    assert store.audit("ST")[0]["state"] == "consumed"
    assert store.audit("ST")[0]["engine_ts"] == 12.5 and inbox.depth == 0


@pytest.mark.parametrize("change,status,code", [
    ({"observed_at": 990.0}, 410, "expired"),
    ({"observed_at": 1002.01}, 422, "future_clock"),
])
def test_reception_rejects_stale_or_future_and_audits(env, change, status, code):
    store, inbox, _ = env
    with pytest.raises(SignalError) as error:
        inbox.push(packet(**change))
    assert (error.value.status, error.value.code) == (status, code)
    assert inbox.depth == 0 and store.audit("ST")[0]["reason"] == code
    assert inbox.push(packet())["status"] == "accepted"  # 被拒绝的序号没有占位


def test_future_clock_exact_tolerance_is_allowed(env):
    _, inbox, _ = env
    assert inbox.push(packet(observed_at=1002.0))["status"] == "accepted"


def test_duplicate_is_idempotent_even_after_newer_sequence(env):
    _, inbox, _ = env
    assert inbox.push(packet(1))["status"] == "accepted"
    assert inbox.push(packet(2))["status"] == "accepted"
    assert inbox.push(packet(1))["status"] == "duplicate"
    assert inbox.push(packet(2))["status"] == "duplicate"
    assert inbox.depth == 2


def test_same_sequence_conflict_and_unknown_lower_sequence_rejected(env):
    _, inbox, _ = env
    inbox.push(packet(5))
    with pytest.raises(SignalError) as conflict:
        inbox.push(packet(5, value=False))
    assert conflict.value.code == "sequence_conflict" and conflict.value.status == 409
    with pytest.raises(SignalError) as old:
        inbox.push(packet(4))
    assert old.value.code == "out_of_order" and old.value.status == 409
    assert inbox.depth == 1


def test_queue_full_rejects_without_advancing_sequence(tmp_path):
    store = SignalAuditStore(tmp_path / "signals.db")
    inbox = SignalInbox(store, "ST", capacity=1, clock=lambda: 1000.0)
    inbox.push(packet(1))
    assert inbox.push(packet(1))["status"] == "duplicate"  # 满队列也可确认重试
    with pytest.raises(SignalError) as error:
        inbox.push(packet(2))
    assert error.value.status == 429 and error.value.code == "queue_full"
    inbox.bind_consumer(); assert len(inbox.drain(0.0)) == 1
    assert inbox.push(packet(2))["status"] == "accepted"
    inbox.close(); store.close()


def test_expiry_is_checked_again_at_consume_time(env):
    store, inbox, clock = env
    inbox.push(packet(ttl_ms=1000))
    inbox.bind_consumer()
    clock[0] = 1001.0
    assert inbox.drain(5.0) == []
    assert store.audit("ST")[0]["state"] == "expired"
    assert store.audit("ST")[0]["reason"] == "expired_before_consume"
    assert inbox.push(packet(ttl_ms=1000))["status"] == "duplicate"


@pytest.mark.parametrize("quality", ["uncertain", "invalid"])
def test_non_ok_quality_is_preserved_for_engine_invalidation(env, quality):
    _, inbox, _ = env
    inbox.push(packet(quality=quality))
    inbox.bind_consumer()
    event = inbox.drain(1.0)[0]
    assert event.conf == 0.0 and event.data["quality"] == quality


def test_capture_thread_binding_is_required_and_exclusive(env):
    _, inbox, _ = env
    inbox.push(packet())
    with pytest.raises(RuntimeError, match="采集线程"):
        inbox.drain(0.0)
    inbox.bind_consumer()
    errors = []
    def wrong_thread():
        for call in [inbox.bind_consumer, lambda: inbox.drain(0.0)]:
            try: call()
            except RuntimeError as error: errors.append(str(error))
    t = threading.Thread(target=wrong_thread); t.start(); t.join()
    assert len(errors) == 2 and inbox.depth == 1
    assert len(inbox.drain(0.0)) == 1


def test_parallel_retries_enqueue_only_once(env):
    _, inbox, _ = env
    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(lambda _: inbox.push(packet())["status"], range(30)))
    assert statuses.count("accepted") == 1 and statuses.count("duplicate") == 29
    inbox.bind_consumer()
    assert len(inbox.drain(2.0)) == 1


def test_parallel_sources_and_scope_sequence_are_independent(env):
    store, inbox, _ = env
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda i: inbox.push(packet(source=f"plc-{i}")), range(24)))
    assert all(r["status"] == "accepted" for r in results) and inbox.depth == 24
    other = SignalInbox(store, "OTHER", clock=lambda: 1000.0)
    assert other.push(packet())["status"] == "accepted"
    other.close()
    inbox.bind_consumer(); assert len(inbox.drain(3.0)) == 24


def test_restart_does_not_replay_accepted_sequences(tmp_path):
    path = tmp_path / "signals.db"
    store = SignalAuditStore(path)
    inbox = SignalInbox(store, "ST", clock=lambda: 1000.0)
    inbox.push(packet(3))
    store.close()  # 模拟进程退出，内存队列丢失但序号已持久化
    restarted_store = SignalAuditStore(path)
    assert restarted_store.recover_abandoned(at=1001.0) == 1
    restarted = SignalInbox(restarted_store, "ST", clock=lambda: 1001.0)
    assert restarted.push(packet(3))["status"] == "duplicate"
    with pytest.raises(SignalError) as old:
        restarted.push(packet(2))
    assert old.value.code == "out_of_order"
    restarted.bind_consumer(); assert restarted.drain(0.0) == []
    assert restarted.push(packet(4))["status"] == "accepted"
    restarted.close(); restarted_store.close()


def test_caller_mutation_cannot_change_queued_signal(env):
    _, inbox, _ = env
    original = packet(value={"ok": [1, 2]})
    inbox.push(original)
    original["value"]["ok"].append(3)
    inbox.bind_consumer()
    assert inbox.drain(1.0)[0].value == {"ok": [1, 2]}


@pytest.mark.parametrize("change", [
    {"sequence": True}, {"sequence": -1}, {"sequence": 2**63}, {"sequence": "1"},
    {"ttl_ms": 0}, {"ttl_ms": 600001}, {"observed_at": float("nan")}, {"observed_at": True},
    {"quality": "maybe"}, {"source": "a/b"}, {"value": float("inf")}, {"value": b"bytes"},
    {"value": "x" * 16385}, {"unknown_field": "ignored?"},
])
def test_invalid_packet_is_rejected_without_queueing(env, change):
    _, inbox, _ = env
    with pytest.raises(SignalError) as error:
        inbox.push(packet(**change))
    assert error.value.status == 422 and inbox.depth == 0


def test_audit_exposes_fingerprint_not_raw_value_and_close_marks_abandoned(env):
    store, inbox, _ = env
    inbox.push(packet(value={"device_measurement": 3.14}))
    assert len(store.audit("ST")[0]["fingerprint"]) == 64
    assert "value" not in store.audit("ST")[0]
    inbox.close()
    assert store.audit("ST")[0]["state"] == "abandoned"
    with pytest.raises(SignalError) as error:
        inbox.push(packet(2))
    assert error.value.status == 503


def test_repeated_value_with_distinct_names_is_not_deduplicated(env):
    _, inbox, _ = env
    inbox.push(packet(name="locked"))
    inbox.push(packet(name="torque_ok"))
    assert inbox.depth == 2


def test_legacy_compatibility_assigns_atomic_persistent_sequence(tmp_path):
    path = tmp_path / "signals.db"
    store = SignalAuditStore(path)
    inbox = SignalInbox(store, "ST", clock=lambda: 1000.0)
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(lambda _: inbox.push_legacy("sn", "SN001"), range(30)))
    assert sorted(r["sequence"] for r in receipts) == list(range(1, 31))
    inbox.bind_consumer()
    events = inbox.drain(3.5)
    assert all(e.ts == 3.5 and e.source == "legacy-http" and e.data["legacy"] for e in events)
    assert all(e.data["expires_at"] == 1002.0 for e in events)
    assert store.audit("ST")[0]["source"] == "legacy-http"
    inbox.close(); store.close()
    restarted_store = SignalAuditStore(path)
    restarted = SignalInbox(restarted_store, "ST", clock=lambda: 1001.0)
    assert restarted.push_legacy("sn", "SN002")["sequence"] == 31
    restarted.close(); restarted_store.close()


def test_explicit_packets_cannot_claim_legacy_source(env):
    _, inbox, _ = env
    with pytest.raises(SignalError) as error:
        inbox.push(packet(source="legacy-http"))
    assert error.value.code == "reserved_source"


def test_signal_key_count_is_bounded_without_blocking_existing_keys(tmp_path):
    store = SignalAuditStore(tmp_path / "signals.db")
    inbox = SignalInbox(store, "ST", clock=lambda: 1000.0, max_keys=2)
    inbox.push(packet(name="a")); inbox.push(packet(name="b"))
    with pytest.raises(SignalError) as error:
        inbox.push(packet(name="c"))
    assert error.value.status == 429 and error.value.code == "source_limit"
    assert inbox.push(packet(2, name="a"))["status"] == "accepted"
    inbox.close(); store.close()
