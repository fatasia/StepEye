"""通知不出网：mock渠道、真实可终止Python子进程、可选SDK解析契约。"""
from __future__ import annotations

import builtins
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from visual.notifications import NotificationDispatcher, _sdk_send


def alert(i=1):
    return {"id": i, "station": "A-1", "kind": "missing_step", "severity": "warn", "message": "未完成工步"}


def env(*urls):
    return {"STEPEYE_NOTIFY_URLS": json.dumps(urls)}


def wait_for(predicate, timeout=4):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("notification test timed out")


def idle(notifier):
    wait_for(lambda: notifier.snapshot()["worker"] == "idle")


def test_unconfigured_has_no_sdk_import_or_thread(monkeypatch):
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name == "apprise":
            raise AssertionError("optional dependency imported")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    notifier = NotificationDispatcher({})
    assert not notifier.snapshot()["configured"]
    assert notifier.submit(alert())["reason"] == "disabled"
    assert notifier._thread is None
    notifier.close(); notifier.close()
    assert notifier.submit(alert())["reason"] == "closed"


@pytest.mark.parametrize("url", ["https://example.com/private", "http://127.0.0.1", "file:///secret",
                                  "apprises://host/key", "feishu://", "feishu://key\nother", 12])
def test_rejects_arbitrary_or_malformed_urls_without_echo(url):
    notifier = NotificationDispatcher(env(url))
    snap = notifier.snapshot()
    assert snap["configured"] and not snap["enabled"]
    assert snap["config_errors"] == ["unsupported_or_invalid_channel"]
    assert "example.com" not in json.dumps(snap) and "secret" not in json.dumps(snap)
    assert notifier._thread is None


@pytest.mark.parametrize("raw", ["not-json", "{}", "null", "1", '["feishu://x",' , '"feishu://x"'])
def test_invalid_json_list_is_safe_and_blocks_legacy_fallback(raw):
    notifier = NotificationDispatcher({"STEPEYE_NOTIFY_URLS": raw})
    assert notifier.snapshot()["configured"]
    assert notifier.snapshot()["config_errors"] == ["invalid_url_list"]


def test_four_channel_vars_and_union_override_and_duplicate_destinations():
    values = {"STEPEYE_NOTIFY_EMAIL_URL": "mailtos://user:dummy@smtp.example.com",
              "STEPEYE_NOTIFY_FEISHU_URL": "feishu://dummy",
              "STEPEYE_NOTIFY_WECOM_URL": "wecombot://dummy", "STEPEYE_NOTIFY_DINGTALK_URL": "dingtalk://dummy"}
    n = NotificationDispatcher(values)
    assert [r["name"] for r in n.snapshot()["channels"]] == ["email-1", "feishu-1", "wecombot-1", "dingtalk-1"]
    values.update(env("wechat://corp:dummy@1/@all", "wechat://corp:dummy@1/@all"))
    n = NotificationDispatcher(values)
    assert [r["name"] for r in n.snapshot()["channels"]] == ["wechat-1"]
    values.update(env())
    assert not NotificationDispatcher(values).snapshot()["enabled"]
    values["STEPEYE_NOTIFY_URLS"] = ""
    assert len(NotificationDispatcher(values).snapshot()["channels"]) == 4
    n = NotificationDispatcher({"STEPEYE_NOTIFY_EMAIL_URL": "feishu://dummy"})
    assert not n.snapshot()["enabled"]


def test_from_env_only_notify_keys_and_process_env_wins(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith("STEPEYE_NOTIFY_"):
            monkeypatch.delenv(key)
    path = tmp_path / ".env"
    path.write_text('OTHER_SECRET=ignored\nSTEPEYE_NOTIFY_FEISHU_URL=feishu://filevalue\n', encoding="utf-8")
    monkeypatch.setenv("STEPEYE_NOTIFY_FEISHU_URL", "feishu://envvalue")
    n = NotificationDispatcher.from_env(path)
    assert n._channels == [("feishu-1", "feishu://envvalue")]
    assert "envvalue" not in json.dumps(n.snapshot())


def test_retry_is_bounded_and_success_per_channel_not_resent():
    calls = []
    def send(url, title, body, timeout, stop):
        calls.append(url)
        assert "A-1" in title and "未完成工步" in body
        if url.startswith("feishu"):
            return "sent"
        raise RuntimeError("never echo dummy-token")
    n = NotificationDispatcher(env("feishu://dummy-token", "dingtalk://dummy-token"), transport=send)
    assert n.submit(alert())["accepted"]
    idle(n)
    assert calls == ["feishu://dummy-token", "dingtalk://dummy-token", "dingtalk://dummy-token"]
    assert n.submit(alert())["reason"] == "duplicate"
    rows = n.snapshot()["recent"][0]["channels"]
    assert [(r["status"], r["attempts"]) for r in rows] == [("sent", 1), ("failed", 2)]
    assert "dummy-token" not in json.dumps(n.snapshot())
    n.close()


def test_transient_failure_succeeds_second_attempt_and_worker_exits_then_restarts():
    calls = []
    def send(*args):
        calls.append(1)
        return "failed" if len(calls) == 1 else "sent"
    n = NotificationDispatcher(env("feishu://dummy"), transport=send)
    n.submit(alert()); idle(n)
    assert n._thread is None
    assert n.snapshot()["recent"][0]["channels"][0]["attempts"] == 2
    n.submit(alert(2)); idle(n)
    assert len(calls) == 3 and n.snapshot()["channels"][0]["delivered"] == 2
    n.close()


def test_bounded_queue_concurrent_submit_and_close_without_waiting():
    started, release = threading.Event(), threading.Event()
    def send(*args):
        started.set(); release.wait(4)
        return "sent"
    n = NotificationDispatcher(env("feishu://dummy"), capacity=2, record_limit=3, transport=send)
    n.submit(alert()); assert started.wait(2)
    results = []
    threads = [threading.Thread(target=lambda i=i: results.append(n.submit(alert(i)))) for i in range(2, 14)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert sum(r["accepted"] for r in results) == 2
    assert sum(r["reason"] == "queue_full" for r in results) == 10
    start = time.monotonic(); n.close()
    assert time.monotonic() - start < .2
    assert n.snapshot()["queued"] == 0
    assert all(row["status"] == "cancelled" for item in n.snapshot()["recent"] for row in item["channels"])
    release.set()


def test_record_history_is_bounded_and_rejects_invalid_alert():
    n = NotificationDispatcher(env("feishu://dummy"), capacity=1, record_limit=2, transport=lambda *args: "sent")
    assert n.submit({})["reason"] == "invalid_alert"
    for i in range(8):
        n.submit(alert(i)); idle(n)
    assert len(n.snapshot()["recent"]) == 2
    assert n.snapshot()["recent"][0]["alert_id"] == 7
    n.close()


def hanging_process(monkeypatch):
    original, processes = subprocess.Popen, []
    def spawn(args, **kwargs):
        # 保留真实OS进程/stdio/timeout，仅把工作负载替换为本机sleep，不联网。
        proc = original([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        processes.append(proc)
        return proc
    monkeypatch.setattr(subprocess, "Popen", spawn)
    return processes


def test_real_hung_child_times_out_twice_then_is_reaped(monkeypatch):
    processes = hanging_process(monkeypatch)
    n = NotificationDispatcher(env("feishu://dummy"), timeout_s=.08)
    n.submit(alert()); idle(n)
    assert len(processes) == 2 and all(p.poll() is not None for p in processes)
    assert n.snapshot()["recent"][0]["channels"][0] == {"name": "feishu-1", "status": "timeout", "attempts": 2}
    n.close()


def test_close_kills_real_running_child_and_does_not_retry(monkeypatch):
    processes = hanging_process(monkeypatch)
    n = NotificationDispatcher(env("feishu://dummy"), timeout_s=30)
    n.submit(alert())
    wait_for(lambda: bool(processes))
    start = time.monotonic(); n.close()
    assert time.monotonic() - start < .3
    wait_for(lambda: processes[0].poll() is not None)
    wait_for(lambda: n._thread is None)
    assert len(processes) == 1


def test_sdk_adapter_uses_explicit_plain_text_and_no_attachments(monkeypatch):
    calls = []
    service = SimpleNamespace(retry=100)
    class SDK:
        def __iter__(self):
            return iter([service])
        def add(self, url):
            calls.append(("url", url)); return True
        def notify(self, **kwargs):
            calls.append(("notify", kwargs)); return True
    monkeypatch.setitem(sys.modules, "apprise", SimpleNamespace(Apprise=SDK, NotifyFormat=SimpleNamespace(TEXT="text")))
    assert _sdk_send({"url": "feishu://dummy", "title": "test", "body": "<b>plain</b>"}) == "sent"
    assert service.retry == 0
    assert calls[-1][1] == {"title": "test", "body": "<b>plain</b>", "body_format": "text"}


def test_sdk_failure_or_invalid_url_is_not_success(monkeypatch):
    monkeypatch.setitem(sys.modules, "apprise", SimpleNamespace(Apprise=lambda: SimpleNamespace(add=lambda url: False)))
    assert _sdk_send({"url": "feishu://dummy", "title": "test", "body": "test"}) == "invalid"
    assert _sdk_send({"url": "https://example.com", "title": "test", "body": "test"}) == "invalid"


def test_real_sdk_parses_all_allowed_plugins_without_sending():
    python = Path(__file__).resolve().parents[1] / ".v0" / "notify-venv"
    python = python / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        pytest.skip("optional Apprise verification venv not installed")
    script = '''
import apprise
from visual.notifications import _sdk_send
apprise.Apprise.notify = lambda self, **kwargs: all(service.retry == 0 for service in self)
urls = ["mailto://user:dummy@smtp.example.com?to=operator@example.com",
        "mailtos://user:dummy@smtp.example.com?to=operator@example.com",
        "feishu://00000000-0000-0000-0000-000000000000?retry=100", "wecombot://dummy-bot-key",
        "wechat://dummycorp:dummysecret@1000002/@all", "dingtalk://" + "a" * 64]
assert all(_sdk_send({"url":u,"title":"test","body":"test"}) == "sent" for u in urls)
print("sdk_parse_ok", apprise.__version__)
'''
    result = subprocess.run([str(python), "-c", script], cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0  # 失败不回显SDK输出，避免模式形成凭据泄漏。
    assert "sdk_parse_ok" in result.stdout
