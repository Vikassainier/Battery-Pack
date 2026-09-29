"""Phase 10: the built-in validation cases (hand calculation vs complete software chain) and their API."""
import math

import numpy as np
import pytest
from fastapi.testclient import TestClient

from battery_thermal.api.main import app
from battery_thermal.validation_cases import cases as vc
from battery_thermal.validation_cases.cases import (
    CASE_BUILDERS, Row, fre_laminar, list_cases, max_trailing_average, nu_h1, plate_hand, run_all, run_case,
)

client = TestClient(app)


@pytest.fixture(scope="module")
def results():
    return {c["id"]: c for c in run_all()}


def row(c, name):
    return next(r for r in c["rows"] if r["name"].startswith(name))


# ------------------------------------------------------------------------------------------ every case passes
def test_all_validation_cases_pass_and_each_row_reports_hand_and_software(results):
    assert list(results) == ["vc1_constant_current", "vc2_time_varying", "vc3_road_load", "vc4_cold_plate_hydraulics", "vc5_thermal_accumulation"]
    for cid, c in results.items():
        assert c["passed"], (cid, [r for r in c["rows"] if not r["passed"]], c.get("error"))
        assert len(c["rows"]) >= 5 and c["hand_calc"] and c["title"] and c["description"]
        for r in c["rows"]:
            assert r["rel_err"] is not None and r["rel_err"] <= r["tol"] and r["software"] is not None
    assert sum(len(c["rows"]) for c in results.values()) >= 50


# ------------------------------------------------------------------------------------------ hand values pinned as typed constants
# (independent of the arithmetic inside cases.py: these are the numbers written in the phase documents)
def test_vc1_hand_values_are_the_specification_numbers(results):
    c = results["vc1_constant_current"]
    assert row(c, "Cell current")["hand"] == 200.0 and row(c, "Cell C-rate")["hand"] == 2.0
    assert row(c, "Heat per cell")["hand"] == pytest.approx(40.0) and row(c, "Heat per cell")["software"] == pytest.approx(40.0)
    assert row(c, "Pack heat")["hand"] == pytest.approx(4800.0) and row(c, "Pack heat")["software"] == pytest.approx(4800.0)
    assert row(c, "Heat per module")["software"] == pytest.approx(480.0)
    assert row(c, "Terminal power")["software"] == pytest.approx(72.0) and row(c, "Discharge efficiency")["software"] == pytest.approx(93.75)
    assert row(c, "Design cooling capacity")["software"] == pytest.approx(5.76)
    assert row(c, "Coolant mass flow")["software"] == pytest.approx(0.338824, abs=1e-6) and row(c, "Coolant volume flow")["software"] == pytest.approx(19.0, abs=0.01)


def test_vc2_hand_values(results):
    c = results["vc2_time_varying"]
    assert row(c, "Peak pack heat")["software"] == pytest.approx(10.8) and row(c, "Average pack heat")["software"] == pytest.approx(4.45)
    assert row(c, "Total heat")["software"] == pytest.approx(0.370833, abs=1e-6) and row(c, "Final SOC")["software"] == pytest.approx(80.9722, abs=1e-4)
    assert row(c, "Moving-average")["software"] == pytest.approx(6.375) and row(c, "Design cooling")["software"] == pytest.approx(7.65)


def test_vc3_hand_values(results):
    c = results["vc3_road_load"]
    assert row(c, "Rolling")["software"] == pytest.approx(158.868, abs=0.005) and row(c, "Aerodynamic")["software"] == pytest.approx(291.127, abs=0.005)
    assert row(c, "Tractive")["software"] == pytest.approx(449.995, abs=0.01) and row(c, "Wheel power")["software"] == pytest.approx(12.4999, abs=0.001)
    assert row(c, "Battery power")["software"] == pytest.approx(14.3888, abs=0.001) and row(c, "Cell current")["software"] == pytest.approx(37.92, abs=0.01)


def test_vc4_hand_values_match_the_phase_6_and_7_documents(results):
    c = results["vc4_cold_plate_hydraulics"]
    assert row(c, "Channel velocity")["software"] == pytest.approx(0.32985, abs=1e-5) and row(c, "Reynolds")["software"] == pytest.approx(282.4, abs=0.05)
    assert row(c, "R_total")["software"] == pytest.approx(0.20264, abs=5e-6) and row(c, "Overall U")["software"] == pytest.approx(246.74, abs=0.02)
    assert row(c, "Effectiveness")["software"] == pytest.approx(0.4604, abs=5e-5) and row(c, "Darcy")["software"] == pytest.approx(0.25828, abs=1e-4)
    assert row(c, "Total pressure drop")["software"] == pytest.approx(34.786, abs=0.002) and row(c, "Hydraulic pump")["software"] == pytest.approx(9.18, abs=0.01)
    assert row(c, "Electrical pump")["software"] == pytest.approx(22.95, abs=0.02)


def test_vc5_hand_values(results):
    c = results["vc5_thermal_accumulation"]
    assert row(c, "Pack thermal capacity")["software"] == pytest.approx(246_000.0)
    assert row(c, "Adiabatic")["software"] == pytest.approx(36.7073, abs=1e-4)
    assert row(c, "Effective coolant conductance")["software"] == pytest.approx(463.0, abs=0.1) and row(c, "Thermal time constant")["software"] == pytest.approx(531.3, abs=0.1)
    assert row(c, "Cooled average-cell temperature")["software"] == pytest.approx(32.016, abs=1e-3)


# ------------------------------------------------------------------------------------------ the machinery can fail
def test_a_wrong_hand_value_fails_the_row_and_the_case(monkeypatch):
    def broken():
        c = vc.vc1()
        c.rows[4] = Row(c.rows[4].name, c.rows[4].hand * 1.01, c.rows[4].unit, c.rows[4].get)          # hand value 1 % off
        return c
    monkeypatch.setitem(CASE_BUILDERS, "vc1_constant_current", broken)
    out = run_case("vc1_constant_current")
    assert out["passed"] is False
    bad = [r for r in out["rows"] if not r["passed"]]
    assert [r["name"] for r in bad] == ["Pack heat  Q_pack = Ns·Np·Q_cell"] and bad[0]["rel_err"] == pytest.approx(0.01 / 1.01, rel=1e-6)


def test_a_missing_software_value_fails_the_row_with_the_reason(monkeypatch):
    def broken():
        c = vc.vc1()
        c.rows.append(Row("Value the engine does not report", 1.0, "-", lambda r: r["heat"]["no_such_key"]))
        return c
    monkeypatch.setitem(CASE_BUILDERS, "vc1_constant_current", broken)
    out = run_case("vc1_constant_current")
    assert out["passed"] is False and out["rows"][-1]["software"] is None and "KeyError" in out["rows"][-1]["error"]


def test_a_blocked_request_is_reported_not_raised(monkeypatch):
    def broken():
        c = vc.vc1()
        c.request.cell.confirmed = False
        return c
    monkeypatch.setitem(CASE_BUILDERS, "vc1_constant_current", broken)
    out = run_case("vc1_constant_current")
    assert out["passed"] is False and "blocked" in out["error"] and out["rows"] == []


# ------------------------------------------------------------------------------------------ closed-form helpers
def test_shah_london_polynomials_hit_the_literature_anchors():
    assert fre_laminar(1.0) == pytest.approx(56.91, abs=0.05) and fre_laminar(0.0) == pytest.approx(96.0)
    assert nu_h1(1.0) == pytest.approx(3.61, abs=0.01) and nu_h1(0.0) == pytest.approx(8.235)
    assert nu_h1(0.25) == pytest.approx(8.235 * 0.647561, abs=1e-4)                # the value used in the phase-6 hand calculation


def test_max_trailing_average_matches_brute_force_integration():
    segs = [(100.0, 100), (300.0, 100), (-150.0, 50), (0.0, 50)]
    k = 120 * 1e-3
    dt = 0.05
    t = np.arange(0, 300, dt)
    q = np.zeros_like(t)
    edges = np.cumsum([0] + [s for _, s in segs])
    for (i, _), lo, hi in zip(segs, edges, edges[1:]):
        q[(t >= lo) & (t < hi)] = k * i * i
    for window in (50.0, 100.0, 200.0, 250.0):
        n = int(round(window / dt))
        cs = np.concatenate([[0.0], np.cumsum(q) * dt])
        brute = max((cs[j] - cs[j - n]) / window for j in range(n, len(t) + 1))
        assert max_trailing_average(segs, k, window) == pytest.approx(brute, rel=2e-3)
    assert max_trailing_average(segs, k, 100.0) == pytest.approx(10800.0)          # a 100 s window fits the whole peak
    assert max_trailing_average(segs, k, 300.0) == pytest.approx(4450.0)           # the whole cycle


def test_plate_hand_scales_with_flow_as_theory_predicts():
    a, b = plate_hand(0.2), plate_hand(0.4)
    assert b["v"] == pytest.approx(2 * a["v"]) and b["re"] == pytest.approx(2 * a["re"]) and b["f"] == pytest.approx(a["f"] / 2)      # laminar: f ∝ 1/Re
    assert b["dp_ch"] == pytest.approx(2 * a["dp_ch"])                                                                              # ΔP ∝ v in laminar flow
    assert b["dp_min"] == pytest.approx(4 * a["dp_min"])                                                                            # minor loss ∝ v²
    assert b["h"] == pytest.approx(a["h"]) and b["eps"] < a["eps"]                                                                  # fully developed Nu: h independent of flow; ε falls with ṁ


# ------------------------------------------------------------------------------------------ API
def test_api_lists_cases_with_hand_calculation_text():
    r = client.get("/api/validation/cases")
    assert r.status_code == 200
    items = r.json()
    assert [i["id"] for i in items] == list(CASE_BUILDERS) and all(isinstance(i["hand_calc"], str) and len(i["hand_calc"]) > 100 for i in items)
    assert items == list_cases()


def test_api_runs_all_cases_and_a_subset_and_rejects_unknown_ids():
    r = client.post("/api/validation/run", json={})
    assert r.status_code == 200 and len(r.json()) == 5 and all(c["passed"] for c in r.json())
    c1 = r.json()[0]
    assert {"name", "hand", "software", "unit", "rel_err", "tol", "passed"} <= set(c1["rows"][0])
    sub = client.post("/api/validation/run", json={"ids": ["vc2_time_varying"]})
    assert sub.status_code == 200 and [c["id"] for c in sub.json()] == ["vc2_time_varying"]
    bad = client.post("/api/validation/run", json={"ids": ["nope"]})
    assert bad.status_code == 422 and "nope" in bad.json()["detail"] and "vc1_constant_current" in bad.json()["detail"]
