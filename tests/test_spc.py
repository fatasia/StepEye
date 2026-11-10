"""SPC数值与边界：NIST公开例子是计算对照，不是现场数据。"""
import json
import math

import pytest

from visual.spc import analyze_spc, defect_pareto


def rows(values): return [{"value":v} for v in values]


def test_nist_individuals_example_matches_published_limits():
    result = analyze_spc(rows([49.6,47.6,49.9,51.3,47.8,51.2,52.6,52.4,53.6,52.1]))
    assert result["mean"] == pytest.approx(50.81)
    assert result["moving_range"]["limits"]["center"] == pytest.approx(16.9/9)
    assert result["individuals"]["limits"]["lcl"] == pytest.approx(45.8159101655)
    assert result["individuals"]["limits"]["ucl"] == pytest.approx(55.8040898345)
    assert result["control_status"] == "no_rule1_signals"
    assert result["assumptions"] == "unverified" and result["production_approved"] is False


def test_frozen_baseline_detects_later_shift_without_relearning_limits():
    baseline = [9.,10.,11.,10.]*8
    result = analyze_spc(rows(baseline+[30.]*5), lsl=5, usl=15, baseline_count=32)
    old = analyze_spc(rows(baseline), lsl=5, usl=15)
    assert result["individuals"]["limits"] == old["individuals"]["limits"]
    assert result["capability"]["cp"] == old["capability"]["cp"]
    assert result["capability"]["cpk"] < old["capability"]["cpk"]
    assert result["capability"]["ppk"] < result["capability"]["cpk"]
    assert result["control_status"] == "signals_detected"
    assert {p["index"] for p in result["signals"] if p["chart"] == "I"} == set(range(32,37))
    assert result["observed_spec"]["outside"] == 5


def test_cp_uses_within_sigma_pp_uses_ddof1_overall_and_negative_cpk_allowed():
    values = [0,2,0,2]
    result = analyze_spc(rows(values),lsl=-3,usl=3)
    assert result["within_sigma"] == pytest.approx(2/1.128)
    assert result["overall_sigma"] == pytest.approx(math.sqrt(4/3))
    assert result["capability"]["cp"] == pytest.approx(6/(6*2/1.128))
    assert result["capability"]["pp"] == pytest.approx(6/(6*math.sqrt(4/3)))
    outside = analyze_spc(rows([10,11,10,11]),lsl=0,usl=1)
    assert outside["capability"]["cpk"] < 0 and outside["capability"]["ppk"] < 0


def test_constant_and_no_spec_never_return_infinity_or_prove_capable():
    result = analyze_spc(rows([1.,1.,1.]),lsl=0,usl=2)
    assert result["control_status"] == "undefined_zero_variation"
    assert result["capability"]["cp"] is None and result["capability"]["pp"] is None
    assert result["individuals"]["limits"] is None
    json.dumps(result,allow_nan=False)
    none = analyze_spc(rows([1,2]))
    assert none["capability"]["cpk"] is None
    assert none["observed_spec"]["fraction"] is None


def test_one_sided_spec_exposes_only_one_sided_indices():
    result = analyze_spc(rows([1,2,1,2]),usl=3)
    assert result["capability"]["cp"] is None
    assert result["capability"]["cpk"] == result["capability"]["cpu"]
    assert result["capability"]["cpl"] is None


@pytest.mark.parametrize("value",[None,True,"3",float("nan"),float("inf"),1e101])
def test_invalid_values_are_rejected_not_silently_dropped(value):
    with pytest.raises(ValueError): analyze_spc(rows([1,value,2]))


@pytest.mark.parametrize("options",[{"baseline_count":1},{"baseline_count":4},{"baseline_count":True},
                                    {"lsl":2,"usl":1},{"usl":float("nan")}])
def test_invalid_baseline_or_specs(options):
    with pytest.raises(ValueError): analyze_spc(rows([1,2,3]),**options)


def test_pareto_aggregates_counts_and_does_not_invent_yield():
    result = defect_pareto([{"defect":"scratch","count":3},{"defect":"dent"},{"defect":"scratch","count":2}])
    assert result["total_defects"] == 6 and result["yield"] is None
    assert [p["defect"] for p in result["items"]] == ["scratch","dent"]
    assert result["items"][0]["fraction"] == pytest.approx(5/6)
    assert result["items"][-1]["cumulative_fraction"] == 1
    assert defect_pareto([{"defect":"scratch","count":0}])["status"] == "no_defects_counted"


@pytest.mark.parametrize("count",[-1,1.5,True,10**9+1])
def test_invalid_pareto_count(count):
    with pytest.raises(ValueError): defect_pareto([{"defect":"x","count":count}])
