"""评测回归的离线测试:清单校验、汇总与对比逻辑,不联网。"""
from __future__ import annotations

import pytest

from visual.regress import diff_runs, load_manifest, summarize


def _row(img, expect, pred):
    return {"image": img, "expect": expect, "pred": pred, "conf": 0.9, "ok": True, "reason": "", "latency_s": 1.0}


def test_load_manifest_validates(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text("name: t\nprompt: 判断\ncases:\n  - {image: a.jpg, expect: NG}\n", encoding="utf-8")
    m = load_manifest(p)
    assert m["name"] == "t" and m["cases"][0]["expect"] == "NG"
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: t\nprompt: ''\ncases: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="prompt"):
        load_manifest(bad)
    nocase = tmp_path / "nc.yaml"
    nocase.write_text("name: t\nprompt: p\ncases: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="cases 为空"):
        load_manifest(nocase)


def test_summarize_counts_errors_and_agreement():
    rows = [_row("a.jpg", "NG", "NG"), _row("b.jpg", "NG", "PASS"), _row("c.jpg", "NG", "ERROR")]
    out = summarize("t", type("C", (), {"model": "m", "base_url": "u"})(), rows)
    assert out["n"] == 3 and out["agree"] == 1 and out["error_n"] == 1
    assert out["agree_rate"] == 0.5  # ERROR 不计入一致率分母


def test_diff_runs_regressions_improvements_changed():
    before = {"agree_rate": 1.0, "rows": [_row("a.jpg", "NG", "NG"), _row("b.jpg", "NG", "PASS"), _row("c.jpg", "NG", "NG")]}
    after = {"agree_rate": 0.5, "rows": [_row("a.jpg", "NG", "PASS"), _row("b.jpg", "NG", "NG"), _row("c.jpg", "PASS", "PASS")]}
    d = diff_runs(before, after)
    assert [r["image"] for r in d["regressions"]] == ["a.jpg"]  # 原对现错
    assert [r["image"] for r in d["improvements"]] == ["b.jpg"]  # 原错现对
    assert [r["image"] for r in d["changed"]] == ["c.jpg"]  # 都错但结论变了
    assert d["agree_rate"]["delta"] == -0.5


def test_diff_runs_handles_missing_images():
    before = {"agree_rate": None, "rows": [_row("a.jpg", "NG", "NG"), _row("gone.jpg", "NG", "NG")]}
    after = {"agree_rate": 1.0, "rows": [_row("a.jpg", "NG", "NG")]}
    d = diff_runs(before, after)
    assert d["regressions"] == [] and d["agree_rate"]["delta"] is None  # 缺图不误报,无基线不算 delta
