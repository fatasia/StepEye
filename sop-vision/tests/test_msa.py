"""平衡交叉ANOVA手工构造对照；不把软件fixture视为真实MSA研究。"""
import json
import math

import pytest

from visual.msa import analyze_agreement, analyze_grr


def measurements(parts=(0,10,20),interaction=0):
    return [{"part":f"P{i}","operator":f"O{j}","trial":k,"value":level+bias+repeat+interaction*((-1)**(i+j))}
            for i,level in enumerate(parts) for j,bias in enumerate((-1,1)) for k,repeat in enumerate((-.5,.5))]


def test_balanced_anova_matches_orthogonal_hand_calculation():
    result = analyze_grr(measurements(),lsl=-10,usl=30)
    anova = {r["source"]:r for r in result["anova"]}
    assert anova["part"]["ss"] == pytest.approx(800)
    assert anova["operator"]["ss"] == pytest.approx(12)
    assert anova["interaction"]["ss"] == pytest.approx(0)
    assert anova["repeatability"]["ss"] == pytest.approx(3)
    assert anova["repeatability"]["df"] == 6
    components = result["components"]
    assert components["repeatability"]["variance"] == pytest.approx(.5)
    assert components["operator"]["variance"] == pytest.approx(2)
    assert components["part_to_part"]["variance"] == pytest.approx(100)
    assert components["grr"]["variance"] == pytest.approx(2.5)
    assert components["grr"]["percent_study_variation"] == pytest.approx(100*math.sqrt(2.5/102.5))
    assert components["grr"]["percent_contribution"] == pytest.approx(100*2.5/102.5)
    assert components["grr"]["percent_tolerance"] == pytest.approx(600*math.sqrt(2.5)/40)
    assert result["ndc"] == 8
    assert "interaction" in result["clamped_components"]
    assert result["production_approved"] is False and result["assumptions"] == "unverified"


def test_interaction_is_retained_and_never_pooled_without_testing():
    result = analyze_grr(measurements((0,10),interaction=2))
    assert result["components"]["interaction"]["variance"] == pytest.approx(15.75)
    assert result["components"]["operator"]["variance"] == 0
    assert result["components"]["part_to_part"]["variance"] == pytest.approx(42)
    assert result["components"]["grr"]["variance"] == pytest.approx(16.25)
    assert all(r["p_value"] is None for r in result["anova"])


def test_zero_variation_is_undefined_not_perfect_measurement_system():
    rows = measurements()
    for row in rows: row["value"] = 1
    result = analyze_grr(rows)
    assert result["ndc"] is None
    assert result["components"]["grr"]["percent_study_variation"] is None
    assert result["production_approved"] is False
    json.dumps(result,allow_nan=False)


@pytest.mark.parametrize("mode",["missing","duplicate","one_operator","one_repeat","invalid_value","one_sided"])
def test_invalid_or_unsupported_design_is_rejected(mode):
    rows = measurements(); options={}
    if mode=="missing": rows.pop()
    if mode=="duplicate": rows.append(dict(rows[0]))
    if mode=="one_operator": rows=[r for r in rows if r["operator"]=="O0"]
    if mode=="one_repeat": rows=[r for r in rows if r["trial"]==0]
    if mode=="invalid_value": rows[0]["value"]=float("nan")
    if mode=="one_sided": options={"lsl":0}
    with pytest.raises(ValueError): analyze_grr(rows,**options)


def classifications(reference=False):
    values={"P1":{"A":["OK","OK"],"B":["OK","OK"]},
            "P2":{"A":["NG","NG"],"B":["NG","NG"]},
            "P3":{"A":["OK","NG"],"B":["OK","OK"]}}
    return [{"part":p,"operator":o,"trial":i,"label":label,
             **({"reference":"OK" if p=="P1" else "NG"} if reference else {})}
            for p,ops in values.items() for o,labels in ops.items() for i,label in enumerate(labels)]


def test_repeatability_between_operators_kappa_and_reference_accuracy_differ():
    result = analyze_agreement(classifications(reference=True))
    within={r["operator"]:r for r in result["within_operator"]}
    assert within["A"]["repeatability"] == pytest.approx(2/3)
    assert within["B"]["repeatability"] == 1
    assert within["A"]["reference_accuracy"] == pytest.approx(5/6)
    assert within["B"]["reference_accuracy"] == pytest.approx(4/6)
    assert result["between_operator_per_trial"] == pytest.approx(5/6)
    assert result["all_operators_all_trials_part_agreement"] == pytest.approx(2/3)
    assert result["pairwise_cohen_kappa"][0]["kappa"] == pytest.approx(2/3)
    assert result["reference_coverage"] == 1


def test_without_reference_agreement_is_not_accuracy_and_single_category_kappa_undefined():
    rows=classifications()
    for row in rows: row["label"]="OK"
    result=analyze_agreement(rows)
    assert result["between_operator_per_trial"] == 1
    assert result["pairwise_cohen_kappa"][0]["kappa"] is None
    assert result["accuracy_status"] == "not_measured_without_reference"
    assert all(r["reference_accuracy"] is None for r in result["within_operator"])


def test_conflicting_reference_rejected_and_partial_coverage_explicit():
    rows=classifications(reference=True)
    rows[0]["reference"]="NG"
    with pytest.raises(ValueError): analyze_agreement(rows)
    rows=classifications()
    rows[0]["reference"]="OK"
    result=analyze_agreement(rows)
    assert result["accuracy_status"] == "partial_reference"
    assert result["reference_coverage"] == pytest.approx(1/3)
