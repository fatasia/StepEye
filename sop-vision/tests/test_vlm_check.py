"""vlm_check 与常驻安全配置的离线测试(纯逻辑,不联网)。"""
from __future__ import annotations

from pathlib import Path

from visual.dsl import Cond, Evidence, Step, StationConfig, VlmCheck, load_config
from visual.engine import Engine
from visual.events import Event, OBJ_STATE


def test_vlm_check_key_stable_and_describe():
    a = VlmCheck(prompt="连接器已插合")
    b = VlmCheck(prompt="连接器已插合")
    assert a.key == b.key and a.key.startswith("vlm:")
    c = Cond(vlm_check=a)
    assert c.kind == "vlm_check" and c.zone == a.key
    assert "VLM 判定" in c.describe() and "连接器" in c.describe()


def _cfg_dict(tmp_path: Path, evidence: dict) -> Path:
    cfg = {
        "station": "VLM-ST", "source": {"type": "sim", "loop": False},
        "zones": [{"id": "fixture", "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]]}],
        "cycle": {"start": {"obj_state": {"zone": "fixture", "state": "present"}},
                  "end": {"obj_state": {"zone": "fixture", "state": "absent"}}},
        "steps": [{"id": "S1", "name": "插接", "evidence": evidence}],
    }
    p = tmp_path / "st.yaml"
    import yaml
    p.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return p


def test_vlm_check_config_loads_without_zone(tmp_path):
    """vlm_check 的伪区域不需要预定义 zone。"""
    cfg = load_config(_cfg_dict(tmp_path, {"all": [{"vlm_check": {"prompt": "连接器已插合"}}]}))
    prog_evidence = cfg.steps[0].evidence
    assert prog_evidence.conds[0].kind == "vlm_check"


def test_vlm_check_condition_satisfied_by_runtime_verdict_event(tmp_path):
    """运行时把判定结果写成 OBJ_STATE(vlm:xxx, ok),引擎按普通状态条件求值。"""
    cfg = load_config(_cfg_dict(tmp_path, {"all": [{"vlm_check": {"prompt": "连接器已插合"}}]}))
    key = cfg.steps[0].evidence.conds[0].vlm_check.key
    e = Engine(__import__("visual.dsl", fromlist=["Program"]).Program.from_config(cfg))
    cond = cfg.steps[0].evidence.conds[0]
    assert e._cond_true(cond, 1.0) is False  # 未判定前不成立
    e.process(Event(1.0, OBJ_STATE, zone=key, state="ok"))
    assert e._cond_true(cond, 1.1) is True


def test_vlm_prompt_wraps_json_contract():
    from visual.runtime import StationRuntime
    text = StationRuntime._vlm_prompt("连接器已插合")
    assert "连接器已插合" in text and "verdict" in text and "yes|no|unclear" in text


def test_safety_cfg_rules_shape():
    from visual.dsl import SafetyCfg, SafetyRuleCfg
    cfg = SafetyCfg(enabled=True, rules=[SafetyRuleCfg(id="u1", kind="person_absent", zone="post", min_s=5.0)])
    assert cfg.rules[0].severity == "warn" and cfg.rules[0].min_s == 5.0


def test_evidence_rejects_multiple_conditions():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Cond(hand_dwell={"zone": "a"}, vlm_check={"prompt": "p"})
    with pytest.raises(ValidationError):
        Step(id="S", evidence=Evidence(all=[{"vlm_check": {"prompt": "p"}, "obj_state": {"zone": "a", "state": "x"}}]))
