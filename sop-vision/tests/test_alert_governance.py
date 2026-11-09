"""告警治理测试(V7-B3):风暴抑制 / 规则预算 / 误报分析 / 旧库兼容 / 并发安全。

时间全部经 gov._clock 注入,不依赖真实睡眠;库均为 tmp_path 下真实 AlertStore。
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from visual.alert_governance import (
    CounterStore,
    GovernedAlerts,
    RuleBudget,
    RuleCooldown,
    StormSuppressor,
    false_positive_report,
    load_cooldown_setting,
    save_cooldown_setting,
)
from visual.alerts import AlertStore

STORM_KEY = ("ST", "safety", "unattended")


def _make_gov(tmp_path: Path, storm: StormSuppressor | None = None,
              budget: RuleBudget | None = None) -> GovernedAlerts:
    return GovernedAlerts(AlertStore(tmp_path / "alerts.db"), storm=storm, budget=budget)


def _fire(gov: GovernedAlerts, t: float, key=STORM_KEY, severity="warn",
          ref_id: str | None = None):
    gov._clock = lambda: t
    return gov.create(key[0], key[1], key[2], severity, f"msg@{t}", ref_id=ref_id)


# ---------------- 风暴抑制 ----------------
def test_storm_suppression_aggregates_and_releases(tmp_path):
    gov = _make_gov(tmp_path, storm=StormSuppressor(window_s=300, threshold=5,
                                                    summary_interval_s=60, quiet_s=120))
    t = 0.0
    for _ in range(4):  # 未达阈值:逐条入库
        rec, new = _fire(gov, t)
        assert new is True and rec is not None
        t += 1.0
    rec, new = _fire(gov, t)  # 第 5 条:入库且此刻进入抑制态
    assert new is True
    t += 1.0
    for _ in range(59):  # 抑制态:逐条吞掉,不入库
        rec, new = _fire(gov, t)
        assert rec is None and new is False
        t += 1.0
    rec, new = _fire(gov, t, severity="error")  # 距进入抑制已满 60s:出风暴摘要
    assert new is True and rec is not None
    assert "[风暴摘要]" in rec["message"] and "已抑制 60 条" in rec["message"]
    assert "300s 窗口" in rec["message"]
    assert rec["severity"] == "error"  # 抑制样本中混入的 error 应成为摘要级别
    assert rec["ref_id"] is None
    rows = gov.store.list(source="safety")
    assert len(rows) == 6 and sum(1 for r in rows if "[风暴摘要]" in r["message"]) == 1
    # 静默(超过 quiet_s=120s)后自动解除,恢复逐条入库
    rec, new = _fire(gov, t + 121.0)
    assert new is True and rec is not None and "[风暴摘要]" not in rec["message"]
    st = gov.storm.storm_stats()
    assert st["active_keys"] == []  # 已解除
    assert st["suppressed_total"] == 60  # 摘要吞掉的 60 条全程如实累计
    # 解除后窗口重新累计(解除那条本身计入新窗口):再触发 3 条正常后,
    # 第 5 条(含解除那条)入库并重新进入抑制,随后再次被吞
    t2 = t + 122.0
    for _ in range(3):
        _, new = _fire(gov, t2)
        assert new is True
        t2 += 1.0
    _, new = _fire(gov, t2)
    assert new is True
    assert gov.storm.storm_stats()["active_keys"] == [STORM_KEY]
    _, new = _fire(gov, t2 + 1.0)
    assert new is False


def test_storm_state_survives_key_isolation(tmp_path):
    gov = _make_gov(tmp_path, storm=StormSuppressor(window_s=300, threshold=2,
                                                    summary_interval_s=10_000))
    _fire(gov, 0.0, key=("A", "sop", "k1"))
    _fire(gov, 1.0, key=("A", "sop", "k1"))
    _, new = _fire(gov, 2.0, key=("A", "sop", "k1"))
    assert new is False  # k1 已抑制
    _, new = _fire(gov, 3.0, key=("B", "sop", "k1"))
    assert new is True  # 不同 station 独立计数
    _, new = _fire(gov, 2.0 + 301.0, key=("A", "sop", "k1"))  # 静默超窗:解除并恢复入库
    assert new is True
    st = gov.storm.storm_stats()
    assert st["active_keys"] == []
    assert st["suppressed_total"] == 1 and st["suppressed_since_summary"] == 0  # 残余如实累计


# ---------------- 规则预算 ----------------
def test_budget_drops_beyond_hourly_cap(tmp_path):
    gov = _make_gov(tmp_path, budget=RuleBudget(max_per_hour=5))
    t = 0.0
    for _ in range(5):
        _, new = _fire(gov, t)
        assert new is True
        t += 1.0
    _, new = _fire(gov, t)
    assert new is False  # 超限不入库
    assert gov.budget.dropped_by_key() == {STORM_KEY: 1}
    st = gov.stats()
    assert st["dropped"] == 1 and st["total"] == 5
    # 滑动窗口:1 小时后名额恢复
    _, new = _fire(gov, t + 3601.0)
    assert new is True


def test_budget_counts_dedup_writeback(tmp_path):
    gov = _make_gov(tmp_path, storm=StormSuppressor(window_s=300, threshold=10**9),
                    budget=RuleBudget(max_per_hour=2))
    _, new = _fire(gov, 0.0, ref_id="r1")  # 入库,预算 1
    assert new is True
    _, new = _fire(gov, 1.0, ref_id="r1")  # 回写防重不新建,但占预算名额(限流偏保守)
    assert new is False
    _, new = _fire(gov, 2.0, ref_id="r2")  # 名额已被回写占用:拒绝并计 dropped
    assert new is False
    assert gov.budget.dropped_by_key() == {STORM_KEY: 1}
    assert gov.stats()["total"] == 1


def test_budget_skips_suppressed(tmp_path):
    gov = _make_gov(tmp_path, storm=StormSuppressor(window_s=300, threshold=2,
                                                    summary_interval_s=10_000),
                    budget=RuleBudget(max_per_hour=10**6))
    _, new = _fire(gov, 0.0)
    assert new is True
    _, new = _fire(gov, 1.0)  # 达到阈值:入库并进入抑制
    assert new is True
    _, new = _fire(gov, 2.0)  # 抑制态:不走入库路径,不占预算
    assert new is False
    _, new = _fire(gov, 3.0, ref_id="r2")  # 抑制优先于预算,同样拒绝
    assert new is False
    assert gov.budget.dropped_by_key() == {}
    assert gov.stats()["total"] == 2


# ---------------- 误报分析 ----------------
def test_false_positive_report_numbers(tmp_path):
    store = AlertStore(tmp_path / "alerts.db")
    a1, _ = store.create("ST", "sop", "missing_step", "error", "漏", ref_id="c1")
    a2, _ = store.create("ST", "sop", "missing_step", "error", "漏", ref_id="c2")
    store.create("ST", "sop", "missing_step", "error", "漏", ref_id="c3")
    b1, _ = store.create("ST", "safety", "unattended", "warn", "无人", ref_id=None)
    store.update(a1["id"], status="false_alarm", handler="甲")
    store.update(a2["id"], status="false_alarm", handler="乙")
    store.update(b1["id"], status="done", handler="丙")
    with store._lock:  # 造一条两天前的老数据,验证时间窗
        store._db.execute("UPDATE alerts SET created='2020-01-01T00:00:00' WHERE id=?",
                          (a1["id"],))
        store._db.commit()
    report = false_positive_report(store, hours=24)
    assert report["hours"] == 24 and report["since"] >= "2020-01-02"
    rules = {(r["source"], r["kind"]): r for r in report["rules"]}
    a = rules[("sop", "missing_step")]
    assert a["total"] == 2 and a["false_alarm"] == 1  # a1 已滑出 24h 窗口
    assert a["false_alarm_ratio"] == 0.5
    assert a["false_alarm_handlers"] == {"乙": 1}
    b = rules[("safety", "unattended")]
    assert b["total"] == 1 and b["false_alarm"] == 0 and b["false_alarm_ratio"] == 0.0
    assert b["false_alarm_handlers"] == {}  # done 处置与 handler 丙不进误报分布
    empty = false_positive_report(AlertStore(tmp_path / "empty.db"))
    assert empty["rules"] == []
    store.close()


# ---------------- 旧库兼容 ----------------
def test_legacy_db_opens_and_governance_works(tmp_path):
    """治理模块不加任何字段:治理版本之前建出的库(手工按旧 schema 建)打开不崩、全功能可用。"""
    db_path = tmp_path / "alerts.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, station TEXT,"
        " source TEXT, kind TEXT, severity TEXT, message TEXT, ref_id TEXT,"
        " status TEXT DEFAULT 'pending', handler TEXT, comment TEXT, created TEXT,"
        " updated TEXT, pushed INTEGER DEFAULT 0)")
    conn.execute(
        "INSERT INTO alerts (station, source, kind, severity, message, ref_id, status,"
        " created, updated, pushed) VALUES ('OLD','sop','missing_step','error','旧告警',"
        "NULL,'done','2020-01-01T00:00:00','2020-01-01T00:00:00',1)")
    conn.commit()
    conn.close()
    gov = _make_gov(tmp_path)
    legacy = gov.list()
    assert len(legacy) == 1 and legacy[0]["station"] == "OLD" and legacy[0]["pushed"] == 1
    rec, new = gov.create("OLD", "sop", "missing_step", "error", "新告警", ref_id="c9")
    assert new is True and rec["status"] == "pending"
    st = gov.stats()
    assert st["total"] == 2 and st["done"] == 1 and st["dropped"] == 0
    assert false_positive_report(gov.store, hours=24)["rules"][0]["kind"] == "missing_step"
    gov.close()


# ---------------- 并发线程安全 ----------------
def test_concurrent_ingest_conservation(tmp_path):
    """多线程并发 ingest:无异常,且 触发数 = 入库数 + 抑制数(风暴)+ dropped(预算)。"""
    gov = _make_gov(tmp_path,
                    storm=StormSuppressor(window_s=3600, threshold=8, summary_interval_s=10**9),
                    budget=RuleBudget(max_per_hour=10**6))
    total, errors = 400, []
    counter = {"stored": 0}
    ck = threading.Lock()

    def worker(base: int) -> None:
        for i in range(total // 8):
            try:
                _, new = gov.create("ST", "safety", "unattended", "warn", f"m{base}-{i}")
                if new:
                    with ck:
                        counter["stored"] += 1
            except Exception as exc:  # pragma: no cover - 失败要浮出
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert errors == []
    st = gov.stats()
    assert st["total"] + st["storm_suppressed"] + st["dropped"] == total  # 守恒
    assert st["total"] == counter["stored"] == 8  # 前 8 条入库,其余被风暴抑制
    assert st["storm_suppressed"] == total - 8 and st["dropped"] == 0

    # 纯预算路径并发(禁风暴):50 入库,其余 dropped,守恒依旧
    gov2 = _make_gov(tmp_path / "b", storm=StormSuppressor(window_s=3600, threshold=10**9),
                     budget=RuleBudget(max_per_hour=50))
    errors2 = []

    def budget_worker(base: int) -> None:
        for i in range(10):
            try:
                gov2.create("ST", "sop", "k", "warn", f"m{base}-{i}")
            except Exception as exc:  # pragma: no cover
                errors2.append(exc)

    threads = [threading.Thread(target=budget_worker, args=(k,)) for k in range(16)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert errors2 == []
    st2 = gov2.stats()
    assert st2["total"] == 50 and st2["dropped"] == 110 and st2["total"] + st2["dropped"] == 160


# ---------------- 门面透传 ----------------
def test_governed_facade_delegates(tmp_path):
    gov = _make_gov(tmp_path)
    rec, new = gov.create("ST", "sop", "k", "error", "漏", ref_id="c1")
    assert new is True
    assert gov.get(rec["id"])["message"] == "漏"
    gov.update(rec["id"], status="false_alarm", handler="甲")
    assert gov.list(status="false_alarm")[0]["id"] == rec["id"]
    gov.mark_pushed(rec["id"])
    assert gov.get(rec["id"])["pushed"] == 1
    st = gov.stats()
    assert {"total", "pending", "processing", "done", "false_alarm",
            "dropped", "storm_suppressed"} <= set(st)


def test_rule_cooldown_blocks_repeats_until_interval_passes(tmp_path):
    store = AlertStore(tmp_path / "alerts.db")
    gov = GovernedAlerts(store, cooldown=RuleCooldown(60))
    gov._clock = lambda: 1000.0
    first, new1 = gov.create("ST", "safety", "person_absent", "warn", "第 1 条")
    again, new2 = gov.create("ST", "safety", "person_absent", "warn", "间隔内重复")
    gov._clock = lambda: 1061.0  # type: ignore[assignment]
    later, new3 = gov.create("ST", "safety", "person_absent", "warn", "间隔后")
    assert new1 is True and new2 is False and new3 is True
    stats = gov.stats()
    assert stats["cooldown_dropped"] == 1 and stats["storm_suppressed"] == 0


def test_rule_cooldown_disabled_by_default_passthrough(tmp_path):
    store = AlertStore(tmp_path / "alerts.db")
    gov = GovernedAlerts(store)  # 默认 RuleCooldown(0)
    for i in range(5):
        rec, new = gov.create("ST", "safety", "person_absent", "warn", f"第 {i} 条")
        assert new is True
    assert gov.stats()["cooldown_dropped"] == 0


def test_cooldown_does_not_block_storm_summary(tmp_path):
    store = AlertStore(tmp_path / "alerts.db")
    gov = GovernedAlerts(store, storm=StormSuppressor(threshold=3, summary_interval_s=0),
                         cooldown=RuleCooldown(3600))
    gov._clock = lambda: 500.0
    for i in range(3):
        gov.create("ST", "safety", "person_absent", "warn", f"触发 {i}")
    summary, new = gov.create("ST", "safety", "person_absent", "warn", "风暴延续")
    assert summary is not None and new is True and summary["message"].startswith("[风暴摘要]")


def test_governance_counters_survive_restart(tmp_path):
    path = tmp_path / "counters.json"
    store = AlertStore(tmp_path / "alerts.db")
    gov = GovernedAlerts(store, cooldown=RuleCooldown(60, counters=CounterStore(path)))
    gov._clock = lambda: 1000.0
    gov.create("ST", "safety", "person_absent", "warn", "第 1 条")
    gov.create("ST", "safety", "person_absent", "warn", "被冷却")
    assert gov.stats()["cooldown_dropped"] == 1

    # 模拟进程重启:新实例从同一计数文件恢复,不再从 0 开始。
    gov2 = GovernedAlerts(AlertStore(tmp_path / "alerts2.db"),
                          cooldown=RuleCooldown(60, counters=CounterStore(path)))
    assert gov2.stats()["cooldown_dropped"] == 1


def test_cooldown_setting_roundtrip(tmp_path):
    path = tmp_path / "alert-governance.json"
    assert load_cooldown_setting(path) is None
    save_cooldown_setting(path, 45)
    assert load_cooldown_setting(path) == 45.0
    path.write_text("{broken", encoding="utf-8")
    assert load_cooldown_setting(path) is None


def test_rule_cooldown_set_interval_clears_previous_state(tmp_path):
    cooldown = RuleCooldown(60)
    assert cooldown.admit(("ST", "safety", "x"), 100.0) is True
    assert cooldown.admit(("ST", "safety", "x"), 110.0) is False
    cooldown.set_interval(0)
    assert cooldown.admit(("ST", "safety", "x"), 111.0) is True
