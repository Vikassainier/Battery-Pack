"""Sensitivity analysis, cooling optimiser and the analysis API endpoints."""
import numpy as np
import pytest
from fastapi.testclient import TestClient

from battery_thermal.api.main import app
from battery_thermal.engine.optimizer import optimize_cooling
from battery_thermal.engine.pipeline import run_analysis
from battery_thermal.engine.schemas import DriveCycle, PackConfig, ThermalSettings
from battery_thermal.engine.sensitivity import sensitivity_analysis
from tests.helpers import base_request, cold_plate, cell_100ah

client = TestClient(app)


def pulsed(n=1201):
    i = np.where((np.arange(n) % 300) < 60, 300.0, 40.0)
    return DriveCycle(name="p", time_s=list(range(n)), battery_current_a=i.tolist())


def req(**kw):
    kw.setdefault("cycle", pulsed())
    kw.setdefault("thermal", ThermalSettings(design_philosophy="moving_average", moving_avg_window_s=300, safety_factor=1.2))
    kw.setdefault("plate", cold_plate(flow_lpm=20.0))
    kw.setdefault("pack", PackConfig(ns=120, np=1, n_modules=10, cells_per_module=12, t_initial_c=28, t_target_max_c=38, t_ambient_c=30, target_delta_t_k=6))
    return base_request(**kw)


def level(s, key, label):
    row = next(p for p in s["params"] if p["key"] == key)
    return next(lv for lv in row["levels"] if lv["label"] == label)


# ------------------------------------------------------------------------------------------- sensitivity
def test_sensitivity_directions_and_hand_calc_scaling():
    s = sensitivity_analysis(req(), only=["resistance", "coolant_inlet", "coolant_flow", "tim_thickness", "tim_k", "c_rate", "ambient"])
    assert s["ok"] and s["base"]["max_heat_kw"] == pytest.approx(10.8)
    # heat ∝ R (constant R): +10 % resistance -> +10 % heat
    assert level(s, "resistance", "+10 %")["metrics"]["max_heat_kw"] == pytest.approx(10.8 * 1.1)
    # heat ∝ I² : +10 % load -> ×1.21 (hand calc 10.8·1.21 = 13.068 kW)
    assert level(s, "c_rate", "+10 %")["metrics"]["max_heat_kw"] == pytest.approx(13.068)
    assert level(s, "c_rate", "+20 %")["metrics"]["max_heat_kw"] == pytest.approx(10.8 * 1.44)
    # thermal-only parameters do not change the heat generated
    for key, lab in (("coolant_inlet", "+5 K"), ("coolant_flow", "+25 %"), ("tim_thickness", "+50 %"), ("tim_k", "-50 %")):
        assert level(s, key, lab)["metrics"]["max_heat_kw"] == pytest.approx(10.8)
    T = lambda key, lab: level(s, key, lab)["metrics"]["t_hot_max_c"]      # noqa: E731
    base_t = s["base"]["t_hot_max_c"]
    assert T("coolant_inlet", "+5 K") > base_t > T("coolant_inlet", "-5 K")
    assert T("coolant_flow", "+25 %") < base_t < T("coolant_flow", "-25 %")
    assert T("tim_thickness", "+50 %") > base_t > T("tim_thickness", "-50 %")
    assert T("tim_k", "+25 %") < base_t < T("tim_k", "-25 %")
    assert T("ambient", "+10 K") > T("ambient", "-10 K")
    assert level(s, "resistance", "+20 %")["metrics"]["q_required_kw"] > s["base"]["q_required_kw"]
    assert level(s, "resistance", "+20 %")["metrics"]["flow_lpm"] > s["base"]["flow_lpm"]              # more heat -> more flow
    d = level(s, "resistance", "+10 %")
    assert d["delta"]["max_heat_kw"] == pytest.approx(1.08) and d["delta_pct"]["max_heat_kw"] == pytest.approx(10.0)


def test_sensitivity_covers_all_requested_parameters_and_reports_blocked_levels():
    s = sensitivity_analysis(req())
    keys = {p["key"] for p in s["params"]}
    assert {"resistance", "ambient", "coolant_inlet", "coolant_flow", "tim_thickness", "tim_k", "c_rate", "soc", "cell_temperature",
            "channel_width", "channel_height", "channel_count"} <= keys
    assert {o["key"] for o in s["outputs"]} == {"max_heat_kw", "q_required_kw", "flow_lpm", "t_hot_max_c"}
    soc = level(s, "soc", "+20 % pts")
    assert soc["status"] in ("skipped", "blocked")                                                   # 108 % SOC is reported, not silently dropped
    tc = level(s, "cell_temperature", "+10 K")
    assert tc["status"] == "blocked" and "target" in tc["reason"].lower()                            # initial T above target -> validation error shown
    for out, items in s["tornado"].items():
        spans = [i["high"] - i["low"] for i in items]
        assert spans == sorted(spans, reverse=True)


def test_sensitivity_without_cold_plate_skips_plate_parameters():
    s = sensitivity_analysis(req(plate=None), only=["tim_k", "coolant_flow", "resistance"])
    by = {p["key"]: p for p in s["params"]}
    assert by["tim_k"]["skipped"] and by["coolant_flow"]["skipped"] and by["resistance"]["levels"]


def test_sensitivity_on_invalid_base_case_reports_errors():
    s = sensitivity_analysis(req(cell=cell_100ah(confirmed=False)))
    assert s["ok"] is False and any(i["code"] == "CELL_NOT_CONFIRMED" for i in s["issues"])


# ------------------------------------------------------------------------------------------- optimiser
def test_optimiser_returns_ranked_feasible_designs_that_the_full_model_confirms():
    r = req()
    o = optimize_cooling(r)
    assert o["ok"] and o["n_evaluated"] > 100 and o["n_feasible"] > 0
    best = o["best"]
    p = [b["p_elec_w"] for b in best]
    assert p == sorted(p) and all(b["feasible"] for b in best)
    assert best[0]["p_elec_w"] <= o["base"]["p_elec_w"] or not o["base"]["feasible"]
    # apply the best design to the real model: constant-R cell -> the fast evaluator must reproduce the full pipeline
    d = best[0]["design"]
    plate = r.cold_plate.model_copy(update={"flow_lpm": d["flow_lpm"], "channel_height_mm": d["channel_height_mm"], "channel_width_mm": d["channel_width_mm"],
                                            "n_channels": d["n_channels"], "tim_thickness_mm": d["tim_thickness_mm"]})
    full = run_analysis(r.model_copy(update={"cold_plate": plate}), mode="scalars")
    assert full["thermal"]["t_hot_max_c"] == pytest.approx(best[0]["t_hot_max_c"], abs=1e-6)
    assert full["hydraulics"]["p_elec_w"] == pytest.approx(best[0]["p_elec_w"], rel=1e-9)
    assert full["hydraulics"]["dp_total_kpa"] == pytest.approx(best[0]["dp_total_kpa"], rel=1e-9)


def test_optimiser_reports_closest_infeasible_when_targets_cannot_be_met():
    r = req(pack=PackConfig(ns=120, np=1, n_modules=10, cells_per_module=12, t_initial_c=28, t_target_max_c=29, t_ambient_c=30, target_delta_t_k=0.5))
    o = optimize_cooling(r)
    assert o["ok"] and o["n_feasible"] == 0 and o["closest_infeasible"] and not o["closest_infeasible"][0]["feasible"]
    assert optimize_cooling(req(plate=None))["ok"] is False
    assert optimize_cooling(req(cell=cell_100ah(mass_kg=None)))["ok"] is False


# ------------------------------------------------------------------------------------------- API
def payload(r):
    return r.model_dump(mode="json")


def test_api_analyze_returns_the_full_traceable_result():
    j = client.post("/api/analyze", json=payload(base_request())).json()
    assert j["status"] == "ok" and j["design"]["q_design_w"] == pytest.approx(5760.0)
    assert j["trace"]["design.q_design"]["value"] == pytest.approx(5.76) and len(j["series"]["t"]) == 61
    assert [c["id"] for c in j["checks"] if not c["id"].startswith("S")] == ["1", "2", "3", "4", "5", "6", "7", "8"]


def test_api_analyze_blocked_and_validation_errors():
    j = client.post("/api/analyze", json=payload(base_request(cell=cell_100ah(confirmed=False)))).json()
    assert j["status"] == "blocked" and j["issues"][0]["severity"] == "error"
    bad = payload(base_request())
    bad["pack"].pop("ns")
    assert client.post("/api/analyze", json=bad).status_code == 422


def test_api_sensitivity_and_optimize_endpoints():
    s = client.post("/api/sensitivity", json={"request": payload(req()), "only": ["resistance", "c_rate"]}).json()
    assert s["ok"] and len(s["params"]) == 2 and s["params"][0]["levels"][0]["status"] == "ok"
    o = client.post("/api/optimize", json={"request": payload(req()), "variables": ["flow_lpm"]}).json()
    assert o["ok"] and o["n_evaluated"] >= 6 and isinstance(o["best"][0]["p_elec_w"], float)
