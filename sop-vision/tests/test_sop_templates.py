"""SOP 模板与 dsl.Step 字段严格对齐；占位区域必须显式映射。"""
import pytest

from visual import sop_templates
from visual.dsl import Step


def test_templates_cover_industries_and_are_valid_steps():
    industries = {t["industry"] for t in sop_templates.list_templates()}
    assert industries == {"3C", "锂电", "汽车电子", "仓储物流"}
    for template in sop_templates.list_templates():
        steps = sop_templates.template_steps(template["id"])
        assert len(steps) == len(template["steps"])
        parsed = sop_templates.validate_steps(steps)
        # 首步无前驱可起步，其余串行紧跟上一步
        assert steps[0]["after"] == []
        for previous, current in zip(steps, steps[1:]):
            assert current["after"] == [previous["id"]]
        assert all(s.optional is False for s in parsed)


def test_placeholder_zones_survive_until_mapped():
    steps = sop_templates.template_steps("3c-assembly")
    zones = [c["obj_state"]["zone"] for s in steps for c in s["evidence"]["all"] if "obj_state" in c]
    hand = [c["hand_dwell"]["zone"] for s in steps for c in s["evidence"]["all"] if "hand_dwell" in c]
    assert any(z.startswith(sop_templates.PLACEHOLDER_PREFIX) for z in zones + hand)


def test_zone_mapping_replaces_and_rejects_unknown_keys():
    steps = sop_templates.template_steps("3c-assembly", zone_map={"上料区": "zone-a", "锁附位": "zone-b"})
    mapped = [c["hand_dwell"]["zone"] for s in steps for c in s["evidence"]["all"] if "hand_dwell" in c]
    assert "zone-b" in mapped
    leftovers = [c["obj_state"]["zone"] for s in steps for c in s["evidence"]["all"] if "obj_state" in c]
    assert any(z.startswith(sop_templates.PLACEHOLDER_PREFIX) for z in leftovers)  # 未映射仍占位
    with pytest.raises(ValueError, match="未使用"):
        sop_templates.template_steps("3c-assembly", zone_map={"不存在的区域": "x"})


def test_unknown_template_or_industry_rejected():
    with pytest.raises(ValueError, match="模板不存在"):
        sop_templates.template_steps("ghost")
    with pytest.raises(ValueError, match="未知行业"):
        sop_templates.list_templates("纺织")


def test_all_templates_pass_step_validation_after_full_mapping():
    for template in sop_templates.list_templates():
        zones = {}
        for step in sop_templates.template_steps(template["id"]):
            for cond in step["evidence"]["all"]:
                ((_, body),) = cond.items()
                zone = body.get("zone", "")
                if zone.startswith(sop_templates.PLACEHOLDER_PREFIX):
                    zones[zone[len(sop_templates.PLACEHOLDER_PREFIX):]] = f"mapped-{zone}"
        steps = sop_templates.template_steps(template["id"], zone_map=zones)
        assert not any(
            cond[kind].get("zone", "").startswith(sop_templates.PLACEHOLDER_PREFIX)
            for step in steps for cond in step["evidence"]["all"]
            for kind in cond if kind in ("obj_state", "hand_dwell"))
        sop_templates.validate_steps(steps)
