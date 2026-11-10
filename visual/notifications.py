"""可选 Apprise 通知：有限队列、按需线程、可终止子进程；告警落库由调用方负责。"""
from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import subprocess
import sys
import threading
from collections import OrderedDict, deque
from pathlib import Path
from urllib.parse import urlsplit

SCHEMES = {"mailto": "email", "mailtos": "email", "feishu": "feishu",
           "wecombot": "wecombot", "wechat": "wechat", "dingtalk": "dingtalk"}
ENV_CHANNELS = {"EMAIL": {"mailto", "mailtos"}, "FEISHU": {"feishu"},
                "WECOM": {"wecombot", "wechat"}, "DINGTALK": {"dingtalk"}}
ENV_KEYS = {"STEPEYE_NOTIFY_URLS", "STEPEYE_NOTIFY_PYTHON"} | {
    f"STEPEYE_NOTIFY_{name}_URL" for name in ENV_CHANNELS}
RESULTS = {"sent", "failed", "timeout", "unavailable", "invalid", "cancelled"}


def _channels(env):
    configured, errors, candidates = False, [], []
    raw = env.get("STEPEYE_NOTIFY_URLS", "").strip()
    if raw:
        configured = True
        try:
            urls = json.loads(raw) if len(raw) <= 32768 else None
            if not isinstance(urls, list) or len(urls) > 8:
                raise ValueError
            candidates = [(url, set(SCHEMES)) for url in urls]
        except (ValueError, TypeError):
            errors.append("invalid_url_list")
    else:
        for channel, schemes in ENV_CHANNELS.items():
            value = env.get(f"STEPEYE_NOTIFY_{channel}_URL", "").strip()
            if value:
                configured = True
                candidates.append((value, schemes))
    channels, seen, counts = [], set(), {}
    for value, allowed in candidates:
        try:
            if not isinstance(value, str) or len(value) > 4096 or any(c.isspace() or ord(c) < 32 for c in value):
                raise ValueError
            parts = urlsplit(value)
            if parts.scheme not in allowed or not parts.netloc:
                raise ValueError
            if value in seen:
                continue
            seen.add(value)
            kind = SCHEMES[parts.scheme]
            counts[kind] = counts.get(kind, 0) + 1
            channels.append((f"{kind}-{counts[kind]}", value))
        except (ValueError, TypeError):
            errors.append("unsupported_or_invalid_channel")
    return configured, channels, errors


class NotificationDispatcher:
    def __init__(self, env=None, *, capacity=128, timeout_s=10., record_limit=256,
                 transport=None, python_executable=None):
        if not isinstance(capacity, int) or not 1 <= capacity <= 1024:
            raise ValueError("invalid notification capacity")
        if not math.isfinite(timeout_s) or not .05 <= timeout_s <= 60:
            raise ValueError("invalid notification timeout")
        if not isinstance(record_limit, int) or not capacity + 1 <= record_limit <= 4096:
            raise ValueError("record_limit must cover queue plus active item")
        self.configured, self._channels, self._errors = _channels(os.environ if env is None else env)
        self.capacity, self.timeout_s, self.record_limit = capacity, timeout_s, record_limit
        self._transport = transport
        self._python = python_executable or (env or os.environ).get("STEPEYE_NOTIFY_PYTHON") or sys.executable
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._queue = deque()
        self._records = OrderedDict()
        self._thread = self._process = None
        self._active = None
        self._closed = False
        self._totals = {name: {"name": name, "status": "idle", "attempts": 0, "delivered": 0, "failed": 0}
                        for name, _ in self._channels}

    @classmethod
    def from_env(cls, env_file: Path | None = None, **options):
        values = {}
        if env_file is not None:
            from .llm_provider import load_env_file
            values.update({k: v for k, v in load_env_file(Path(env_file)).items() if k in ENV_KEYS})
        values.update({k: v for k, v in os.environ.items() if k in ENV_KEYS})
        return cls(values, **options)

    def submit(self, alert: dict) -> dict:
        names = [name for name, _ in self._channels]
        def result(reason):
            return {"accepted": reason == "queued", "reason": reason, "channels": names}
        with self._lock:
            if self._closed:
                return result("closed")
            if not self._channels:
                return result("disabled")
            alert_id = alert.get("id")
            if isinstance(alert_id, bool) or not isinstance(alert_id, (str, int)) or not 1 <= len(str(alert_id)) <= 80:
                return result("invalid_alert")
            key = str(alert_id)
            if key in self._records:
                return result("duplicate")
            if len(self._queue) >= self.capacity:
                return result("queue_full")
            if len(self._records) >= self.record_limit:
                for old in self._records:
                    if old != self._active and all(v["status"] not in {"queued", "sending"}
                                                   for v in self._records[old]["channels"]):
                        del self._records[old]
                        break
                else:
                    return result("queue_full")
            self._records[key] = {"alert_id": alert_id,
                                  "channels": [{"name": name, "status": "queued", "attempts": 0} for name in names]}
            title = f"[StepEye 告警] {str(alert.get('station', ''))[:80]} {str(alert.get('kind', ''))[:80]}"
            body = f"告警 #{alert_id} · {str(alert.get('severity', 'warn'))[:20]}\n{str(alert.get('message', ''))[:2048]}\n状态: 待处理"
            self._queue.append((key, title, body))
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="notifications", daemon=True)
                self._thread.start()
            return result("queued")

    def _run(self):
        while True:
            with self._lock:
                if self._closed or not self._queue:
                    self._active = self._thread = None  # 与 submit 原子交接，不丢任务。
                    return
                key, title, body = self._queue.popleft()
                self._active = key
            for name, url in self._channels:
                for attempt in range(1, 3):
                    with self._lock:
                        if self._closed:
                            return self._finish_closed()
                        row = next(r for r in self._records[key]["channels"] if r["name"] == name)
                        row.update(status="sending", attempts=attempt)
                        self._totals[name]["attempts"] += 1
                    try:
                        outcome = (self._transport(url, title, body, self.timeout_s, self._stop)
                                   if self._transport else self._send(url, title, body))
                        if outcome not in RESULTS:
                            outcome = "failed"
                    except Exception:
                        outcome = "failed"  # 不保留异常文本；SDK异常可能带 URL/凭据。
                    with self._lock:
                        row["status"] = "cancelled" if self._closed else outcome
                        self._totals[name]["status"] = row["status"]
                    if outcome in {"sent", "unavailable", "invalid", "cancelled"} or self._stop.is_set():
                        break
                    if attempt == 1 and self._stop.wait(.25):
                        break
                with self._lock:
                    field = "delivered" if row["status"] == "sent" else "failed"
                    self._totals[name][field] += 1

    def _finish_closed(self):
        # 调用方已持锁；不 join 采集线程或网络调用。
        self._active = self._thread = None

    def _send(self, url, title, body):
        payload = json.dumps({"url": url, "title": title, "body": body}, ensure_ascii=False).encode("utf-8")
        with self._lock:
            if self._closed:
                return "cancelled"
            proc = subprocess.Popen([self._python, "-m", "visual.notifications", "--send-one"],
                                    cwd=str(Path(__file__).resolve().parent.parent), stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self._process = proc
        try:
            output, _ = proc.communicate(payload, timeout=self.timeout_s)
            if proc.returncode != 0 or len(output) > 256:
                return "failed"
            outcome = json.loads(output).get("result")
            return outcome if outcome in RESULTS else "failed"
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return "timeout"
        except (ValueError, OSError):
            return "failed"
        finally:
            with self._lock:
                if self._process is proc:
                    self._process = None

    def snapshot(self) -> dict:
        with self._lock:
            return {"configured": self.configured, "enabled": bool(self._channels) and not self._closed,
                    "worker": "closed" if self._closed else "running" if self._thread else "idle",
                    "queued": len(self._queue), "inflight": self._active is not None,
                    "channels": [dict(v) for v in self._totals.values()],
                    "recent": [{"alert_id": v["alert_id"], "channels": [dict(r) for r in v["channels"]]}
                               for v in reversed(self._records.values())], "config_errors": list(self._errors)}

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._stop.set()
            self._queue.clear()
            for value in self._records.values():
                for row in value["channels"]:
                    if row["status"] in {"queued", "sending"}:
                        row["status"] = "cancelled"
            proc = self._process
        if proc is not None:
            try:
                proc.kill()
            except OSError:
                pass


def _sdk_send(spec):
    """只在一次性子进程导入可选 SDK。此函数可单独验证，不执行配置文件。"""
    configured, channels, errors = _channels({"STEPEYE_NOTIFY_URLS": json.dumps([spec["url"]])})
    if not configured or errors or len(channels) != 1:
        return "invalid"
    import apprise
    app = apprise.Apprise()
    if not app.add(channels[0][1]):
        return "invalid"
    for service in app:
        service.retry = 0  # 外层至多两次；不能让 URL 的 ?retry= 放大重试。
    return "sent" if app.notify(body=spec["body"], title=spec["title"], body_format=apprise.NotifyFormat.TEXT) else "failed"


def _child_main():
    logging.disable(logging.CRITICAL)
    outcome = "failed"
    try:
        spec = json.loads(sys.stdin.buffer.read(16384))
        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            outcome = _sdk_send(spec)
    except ImportError:
        outcome = "unavailable"
    except Exception:
        pass
    print(json.dumps({"result": outcome}))


if __name__ == "__main__" and sys.argv[1:] == ["--send-one"]:
    _child_main()
