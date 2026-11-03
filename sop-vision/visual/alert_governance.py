"""告警治理(V7-B3):风暴抑制 / 规则预算 / 误报分析,组合 AlertStore 使用。

AlertStore.create 的既有"冷却"只有 ref_id 回写防重(同 station+source+kind+ref_id 且
状态为 pending/processing 时只更新 updated 不新建),没有时间维度;safety/anomaly 等
ref_id 为空的源每条必新建,是风暴的主要来源。本模块在其上叠加三层治理,不改动
AlertStore 的表结构(无新字段,旧库天然兼容):

1. 风暴抑制 StormSuppressor:滑动窗口内同 (station, source, kind) 触发达到阈值后进入
   抑制态,后续触发不再逐条入库,按周期聚合成一条"风暴摘要"告警(含被抑制条数与
   时间窗);窗口静默后自动解除、恢复逐条。抑制态在内存(进程重启后重新累计窗口);
   摘要由后续触发的到达惰性驱动,静默后未入摘要的残余抑制计数在 storm_stats() 中
   如实报告而不单独入库。
2. 规则预算 RuleBudget:每 (station, source, kind) 滑动 1 小时入库上限,超限只计
   dropped 计数不入库,stats() 如实汇总。预算在决策时预留(防重回写、create 失败均
   不回退名额,限流偏保守);风暴摘要本身是降频产物,不占预算。
3. false_positive_report:按规则聚合误报处置情况,纯读不改数据。

用法(路由接线示例):
    gov = GovernedAlerts(AlertStore(path))
    rec, new = gov.create(...)   # 返回语义与 AlertStore.create 一致
    if new:                      # new=False 且 rec=None 表示被抑制/超预算,无需推送
        push(rec)
"""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Any

_TS_FMT = "%Y-%m-%dT%H:%M:%S"
_SEVERITY_RANK = {"info": 0, "warn": 1, "error": 2, "critical": 3}


def _severity_rank(severity: str | None) -> int:
    return _SEVERITY_RANK.get(str(severity or "").lower(), 1)


class StormSuppressor:
    """滑动窗口风暴抑制(内存态,线程安全)。

    决策语义:窗口内(含当前这条)未达 threshold 时逐条入库(stored);达到阈值的那条
    仍入库并进入抑制态;此后的触发被抑制(suppressed),每满 summary_interval_s 聚合出
    一条摘要(summary,携带自上条摘要以来被抑制的条数与样本最高 severity)。距上一次
    触发超过 quiet_s 视为窗口静默,由下一条触发惰性解除并重新累计。
    """

    def __init__(self, window_s: float = 300.0, threshold: int = 10,
                 summary_interval_s: float = 60.0, quiet_s: float | None = None):
        self.window_s = float(window_s)
        self.threshold = int(threshold)
        self.summary_interval_s = float(summary_interval_s)
        self.quiet_s = self.window_s if quiet_s is None else float(quiet_s)
        self._lock = threading.Lock()
        # key -> [hits(窗口内触发时刻,抑制后不再增长,内存有界), in_storm, last_trigger,
        #         last_summary, suppressed_total, suppressed_since_summary, max_severity]
        self._states: dict[tuple, dict[str, Any]] = {}

    def decide(self, key: tuple, severity: str | None, now: float) -> tuple[str, int]:
        """登记一次触发并给出动作 ("stored"|"suppressed"|"summary", n)。

        锁内只做内存决策,入库在调用方锁外执行(照 alerts.py 的锁纪律,
        避免与 AlertStore 内部锁交叉等待)。
        """
        with self._lock:
            st = self._states.get(key)
            if st is None:
                st = self._states[key] = {"hits": deque(), "in_storm": False,
                                          "last_trigger": 0.0, "last_summary": 0.0,
                                          "suppressed_total": 0,
                                          "suppressed_since_summary": 0, "max_severity": 1}
            if st["in_storm"] and now - st["last_trigger"] > self.quiet_s:
                # 静默解除:残余未入摘要的抑制计数保留在 suppressed_total 供统计
                st["in_storm"] = False
                st["hits"].clear()
                st["suppressed_since_summary"] = 0
            st["last_trigger"] = now
            if st["in_storm"]:
                st["suppressed_total"] += 1
                st["suppressed_since_summary"] += 1
                st["max_severity"] = max(st["max_severity"], _severity_rank(severity))
                if now - st["last_summary"] >= self.summary_interval_s:
                    n = st["suppressed_since_summary"]
                    st["suppressed_since_summary"] = 0
                    st["last_summary"] = now
                    return "summary", n
                return "suppressed", 0
            hits = st["hits"]
            hits.append(now)
            while hits and now - hits[0] > self.window_s:
                hits.popleft()
            if len(hits) >= self.threshold:
                # 达到阈值的那条已入库;从下一刻起进入抑制,摘要周期从此刻起算
                st["in_storm"] = True
                st["last_summary"] = now
                st["max_severity"] = _severity_rank(severity)
            return "stored", 0

    def take_peak_severity(self, key: tuple) -> str:
        """取出并重置该键抑制样本中的最高 severity(仅由摘要入库路径调用)。"""
        with self._lock:
            st = self._states.get(key)
            rank = st["max_severity"] if st else 1
            if st:
                st["max_severity"] = 1
            return {0: "info", 1: "warn", 2: "error", 3: "critical"}.get(rank, "warn")

    def storm_stats(self) -> dict[str, Any]:
        with self._lock:
            active = [k for k, st in self._states.items() if st["in_storm"]]
            pending = sum(st["suppressed_since_summary"] for st in self._states.values())
            total = sum(st["suppressed_total"] for st in self._states.values())
            return {"suppressed_total": total, "suppressed_since_summary": pending,
                    "active_keys": active}


class RuleBudget:
    """每 (station, source, kind) 滑动小时窗入库上限(内存态,线程安全)。"""

    def __init__(self, max_per_hour: int = 60, window_s: float = 3600.0):
        self.max_per_hour = int(max_per_hour)
        self.window_s = float(window_s)
        self._lock = threading.Lock()
        self._hits: dict[tuple, deque] = {}
        self._dropped: dict[tuple, int] = {}

    def admit(self, key: tuple, now: float) -> bool:
        """是否放行入库;超限返回 False 并累计 dropped(检查与登记同锁,原子)。"""
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] > self.window_s:
                hits.popleft()
            if len(hits) >= self.max_per_hour:
                self._dropped[key] = self._dropped.get(key, 0) + 1
                return False
            hits.append(now)
            return True

    def dropped_by_key(self) -> dict[tuple, int]:
        with self._lock:
            return dict(self._dropped)

    def dropped_total(self) -> int:
        with self._lock:
            return sum(self._dropped.values())


class RuleCooldown:
    """同键最小入库间隔(B3 统一冷却;默认 0=关闭,经 STEPEYE_ALERT_COOLDOWN_S 开启)。

    与 ref_id 回写防重互补:防重只挡同 ref 的未决告警,本类挡"同键高频重复",
    间隔内重复触发只计 dropped 不入库。风暴摘要不经过本闸门。
    """

    def __init__(self, min_interval_s: float = 0.0):
        self.min_interval_s = max(0.0, float(min_interval_s))
        self._lock = threading.Lock()
        self._last: dict[tuple, float] = {}
        self._dropped = 0

    def admit(self, key: tuple, now: float) -> bool:
        if self.min_interval_s <= 0:
            return True
        with self._lock:
            if now - self._last.get(key, -math.inf) < self.min_interval_s:
                self._dropped += 1
                return False
            self._last[key] = now
            return True

    def dropped_total(self) -> int:
        with self._lock:
            return self._dropped


class GovernedAlerts:
    """AlertStore 治理门面:create 叠加风暴/预算闸门,其余方法透传给 store。

    透传 get/list/update/mark_pushed/stats 基础/close(经 __getattr__),
    本类不拥有 store 的生命周期,close 由持有方负责(与 server._owns_alerts 一致)。
    """

    def __init__(self, store: Any, storm: StormSuppressor | None = None,
                 budget: RuleBudget | None = None, cooldown: RuleCooldown | None = None):
        self.store = store
        self.storm = storm or StormSuppressor()
        self.budget = budget or RuleBudget()
        self.cooldown = cooldown or RuleCooldown(0)
        self._clock = time.time  # 测试注入用,业务代码不要改

    def create(self, station: str, source: str, kind: str, severity: str, message: str,
               ref_id: str | None = None) -> tuple[dict[str, Any] | None, bool]:
        """语义与 AlertStore.create 对齐:(记录|None, 是否新入库值得推送)。

        stored → (记录, True);防重回写 → (记录, False);被抑制/超预算 → (None, False);
        摘要 → (摘要记录, True)。摘要 ref_id 恒为 None,避免落进 store 的回写防重。
        """
        now = self._clock()
        key = (station, source, kind)
        action, n = self.storm.decide(key, severity, now)
        if action == "summary":
            msg = (f"[风暴摘要] {source}/{kind} 于 {self.storm.window_s:.0f}s 窗口内"
                   f"已抑制 {n} 条,聚合入库")
            sev = self.storm.take_peak_severity(key)
            rec, _ = self.store.create(station, source, kind, sev, msg, ref_id=None)
            return rec, True
        if action == "suppressed":
            return None, False
        if not self.cooldown.admit(key, now):
            return None, False
        if not self.budget.admit(key, now):
            return None, False
        return self.store.create(station, source, kind, severity, message, ref_id=ref_id)

    def stats(self) -> dict[str, Any]:
        """store.stats() 基础上如实追加治理计数:dropped(预算拒绝)、cooldown_dropped
        (冷却拦截)与 storm_suppressed。"""
        base = self.store.stats()
        base["dropped"] = self.budget.dropped_total()
        base["cooldown_dropped"] = self.cooldown.dropped_total()
        base["storm_suppressed"] = self.storm.storm_stats()["suppressed_total"]
        return base

    def __getattr__(self, name: str) -> Any:
        return getattr(self.store, name)


def false_positive_report(store: Any, hours: float = 24) -> dict[str, Any]:
    """按规则(station+source+kind)聚合误报处置情况,纯读不改数据。

    返回 {"hours", "since", "rules": [{station, source, kind, total, false_alarm,
    false_alarm_ratio, false_alarm_handlers}]}。handlers 为把该规则判为误报的
    处置人分布(handler 为空的处置不计入)。时间窗按 created 本地时间字符串比较
    (该格式字典序即时间序),created >= since 的告警计入。
    """
    since = time.strftime(_TS_FMT, time.localtime(time.time() - hours * 3600))
    # 同包协作:直接复用 store 的连接与锁做只读查询,避免绕开锁纪律
    with store._lock:
        rows = store._db.execute(
            "SELECT station, source, kind, status, handler FROM alerts WHERE created >= ?",
            (since,)).fetchall()
    agg: dict[tuple, dict[str, Any]] = {}
    for station, source, kind, status, handler in rows:
        slot = agg.setdefault((station, source, kind),
                              {"station": station, "source": source, "kind": kind,
                               "total": 0, "false_alarm": 0, "false_alarm_handlers": {}})
        slot["total"] += 1
        if status == "false_alarm":
            slot["false_alarm"] += 1
            if handler:
                slot["false_alarm_handlers"][handler] = slot["false_alarm_handlers"].get(handler, 0) + 1
    rules = sorted(agg.values(), key=lambda r: -r["total"])
    for r in rules:
        r["false_alarm_ratio"] = round(r["false_alarm"] / r["total"], 4) if r["total"] else 0.0
    return {"hours": hours, "since": since, "rules": rules}
