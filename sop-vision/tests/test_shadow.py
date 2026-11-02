"""影子模式(shadow)测试:入库/真值/报告数学 + forwarder 采集 + inspection 采集。"""
from __future__ import annotations

from pathlib import Path

from visual.server import StationManager
from visual.shadow import ShadowStore

CFG = Path(__file__).resolve().parents[1] / "configs" / "demo_sim.yaml"


def _fast_cfg_text() -> str:
    text = CFG.read_text(encoding="utf-8")
    return text.replace("realtime: true", "realtime: false").replace("loop: true", "loop: false")


def test_shadow_store_lifecycle(tmp_path):
    s = ShadowStore(tmp_path / "shadow.db")
    i1 = s.add("sop_cycle", "ST", "C1", "PASS", None, {"result": "PASS"})
    s.add("sop_cycle", "ST", "C2", "PASS", None, {})
    s.add("sop_cycle", "ST", "C3", "FAIL", None, {})
    s.set_truth(i1, "PASS")
    rep = s.report(kind="sop_cycle")
    d = rep["kinds"]["sop_cycle"]
    assert d["unlabeled"] == 2 and d["labeled"] == 1 and d["agree_rate"] == 1.0
    s.set_truth(i1, "NG")
    d = s.report(kind="sop_cycle")["kinds"]["sop_cycle"]
    assert d["miss"] == 1 and d["agree_rate"] == 0.0  # 系统说 PASS 真值 NG = 漏检


def test_shadow_report_miss_vs_false_alarm(tmp_path):
    s = ShadowStore(tmp_path / "shadow.db")
    i1 = s.add("inspection", "sc", "T1", "PASS", 0.9, {})
    i2 = s.add("inspection", "sc", "T2", "NG", 0.9, {})
    s.set_truth(i1, "NG")  # 系统 PASS 真值 NG → 漏检
    s.set_truth(i2, "PASS")  # 系统 NG 真值 PASS → 误杀
    d = s.report()["kinds"]["inspection"]
    assert d["miss"] == 1 and d["false_alarm"] == 1 and d["labeled"] == 2
    assert d["agree_rate"] == 0.0
    assert s.report(kind="sop_cycle")["kinds"] == {}  # 其他类不受影响
    s.close()


def test_shadow_recorder_and_inspection_collection(tmp_path):
    cfg_path = tmp_path / "st.yaml"
    cfg_path.write_text(_fast_cfg_text(), encoding="utf-8")
    store = ShadowStore(tmp_path / "shadow.db")
    m = StationManager([cfg_path], tmp_path / "data",
                       hub=type("H", (), {"publish": staticmethod(lambda msg: None)})(),
                       shadow=True)
    assert m.shadow_store is not None
    # 影子采集挂在 runtime 监听器上,须走引擎事件链而不是 forwarder
    m.runtimes["DEMO-ST01"]._on_engine_event("cycle_end", {
        "cycle_id": "C1", "result": "FAIL", "violations": [], "rules_hash": "h"})
    m._on_inspection_msg({"type": "event", "kind": "inspection", "data": {
        "id": "T9", "scenario": "glove_check", "status": "done", "verdict": "NG", "confidence": 0.9, "reason": "裸手"}})
    rep = m.shadow_store.report()  # manager 内部创建的 store,与上面 store 是不同 db 文件
    assert rep["kinds"]["sop_cycle"]["unlabeled"] == 1
    row = rep["kinds"]["inspection"]
    assert row["unlabeled"] == 1
    m.runtimes["DEMO-ST01"].stop()
    store.close()
    m.alerts.close()
    m.store.close()
    if m.shadow_store is not None:
        m.shadow_store.close()
