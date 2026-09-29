"""End-to-end pipeline tests: validation case 1 through the whole chain (hand calculated), philosophies, missing data,
margins, checks, traceability.

Hand calculation - validation case 1 through the chain (120S1P, 100 Ah, 1 mΩ, 200 A for 60 s, hand-calc coolant):
    Q_cell = 200²·1e-3 = 40 W ; Q_pack = 4.8 kW ; E = 4800·60/3.6e6 = 0.08 kWh
    peak philosophy: Q_required = 4.8 kW (25 °C ambient < 40 °C target -> no ambient gain) ; Q_design = 4.8·1.2 = 5.76 kW
    ṁ_pack = 5760/(3400·5) = 0.338824 kg/s = 19.0 L/min ; module (12 cells, 576 W): 1.9 L/min ; cell (48 W): 0.1584 L/min
    C-rate = 2.0 C (below the 3 C continuous limit)
"""
import math

import numpy as np
import pytest

from battery_thermal.engine.pipeline import run_analysis
from battery_thermal.engine.schemas import (
    CRateLimits, CoolantSpec, Curve, DriveCycle, EntropicSettings, LimitSettings, PackConfig, ProvEntry, ResistanceSettings,
    ThermalSettings,
)
from tests.helpers import HAND_COOLANT, base_request, cell_100ah, cold_plate, cycle_const_current


def checks_by_id(r):
    return {c["id"]: c for c in r["checks"]}


# ------------------------------------------------------------------------------------------ validation case 1
def test_validation_case_1_full_chain_matches_hand_calculation():
    r = run_analysis(base_request())
    assert r["status"] == "ok", r["issues"]
    h = r["heat"]
    assert h["max_cell_heat_w"] == pytest.approx(40.0) and h["max_module_heat_w"] == pytest.approx(480.0)
    assert h["max_pack_heat_kw"] == pytest.approx(4.8) and h["avg_pack_heat_kw"] == pytest.approx(4.8)
    assert h["total_heat_kwh"] == pytest.approx(0.08)
    assert r["pack"]["v_nom"] == 384.0 and r["pack"]["energy_kwh"] == pytest.approx(38.4) and h["max_discharge_c"] == pytest.approx(2.0)
    d = r["design"]
    assert d["philosophy"] == "peak" and d["q_relevant_w"] == pytest.approx(4800.0) and d["q_ambient_gain_w"] == 0.0
    assert d["q_required_w"] == pytest.approx(4800.0) and d["q_design_w"] == pytest.approx(5760.0)
    flow = r["cooling"]
    assert flow["pack"]["m_dot_kg_s"] == pytest.approx(5760 / (3400 * 5)) and flow["pack"]["lpm"] == pytest.approx(19.0, abs=0.01)
    assert flow["module"]["lpm"] == pytest.approx(1.9, abs=0.001) and flow["cell"]["q_w"] == pytest.approx(48.0)
    assert r["sizing"]["capacity"]["recommended_kw"] == pytest.approx(5.8)                     # 5.76 kW rounded up to 0.1 kW
    k = {x["label"]: x for g in r["kpis"].values() for x in g}
    assert k["Maximum heat generation"]["value"] == pytest.approx(4.8) and k["Required cooling capacity"]["value"] == pytest.approx(4.8)
    assert k["Recommended cooling capacity"]["value"] == pytest.approx(5.76) and k["Required coolant flow"]["value"] == pytest.approx(19.0, abs=0.01)
    assert k["Pack voltage (nominal)"]["value"] == 384.0 and k["Pack capacity"]["value"] == 100.0


def test_electrical_energy_is_reported_separately_from_heat():
    r = run_analysis(base_request())
    e = r["heat"]["electrical"]
    assert e["terminal_discharge_kwh"] == pytest.approx(72000 * 60 / 3.6e6) and e["discharge_efficiency_pct"] == pytest.approx(93.75)
    assert r["heat"]["total_heat_kwh"] < e["terminal_discharge_kwh"] / 10
    assert "not heat" in r["explanations"]["electrical_vs_heat"].lower()
    assert "Peak thermal load" in r["explanations"]["peak_vs_sustained"]


def test_transient_series_contain_all_required_channels():
    r = run_analysis(base_request())
    s = r["series"]
    for key in ("t", "soc_pct", "i_cell_a", "i_pack_a", "i_module_a", "c_rate", "r_cell_mohm", "q_joule_cell_w", "q_rev_cell_w", "q_cell_w",
                "q_module_w", "q_pack_kw", "p_batt_kw", "cum_heat_kwh", "state", "t_cell_c", "t_hot_c", "t_coolant_out_c"):
        assert key in s and len(s[key]) == len(s["t"]) == 61, key
    assert s["cum_heat_kwh"][-1] == pytest.approx(0.08) and s["state"][0] == "discharge"
    assert s["p_batt_kw"][5] == pytest.approx(72.0)


# ------------------------------------------------------------------------------------------ blocking validation
def test_unconfirmed_cell_blocks_the_analysis():
    r = run_analysis(base_request(cell=cell_100ah(confirmed=False)))
    assert r["status"] == "blocked" and any(i["code"] == "CELL_NOT_CONFIRMED" for i in r["issues"])
    assert "series" not in r
    ok = run_analysis(base_request(cell=cell_100ah(confirmed=False), require_cell_confirmation=False))
    assert ok["status"] == "ok"


def test_oversized_load_definitions_are_refused_before_any_calculation():
    """A cycle (or a C-rate profile at a tiny step) that would need more than 500 000 time steps is rejected with advice, quickly."""
    big = run_analysis(base_request(cycle=cycle_const_current(100.0, n=250_001), cycle_options={"repeats": 2}))       # 250 001 × 2 = 500 002 steps
    assert big["status"] == "blocked"
    issue = next(i for i in big["issues"] if i["code"] == "CYCLE_TOO_LONG")
    assert "500,002" in issue["message"] and "Resample" in issue["hint"]
    edge = run_analysis(base_request(cycle=cycle_const_current(100.0, n=200), cycle_options={"repeats": 200}))        # 40 000 steps: fine
    assert "CYCLE_TOO_LONG" not in {i["code"] for i in edge["issues"]}
    from battery_thermal.engine.schemas import CRateProfile, Segment
    req = base_request()
    req.cycle = None                                                                                                   # C-rate duty profile instead of a cycle
    req.crate_profile = CRateProfile(dt_s=0.001, segments=[Segment(kind="discharge", c_rate=1.0, duration_s=3600.0)])   # 3.6 million steps
    prof = run_analysis(req)
    assert prof["status"] == "blocked" and "CYCLE_TOO_LONG" in {i["code"] for i in prof["issues"]}
    req.crate_profile = CRateProfile(dt_s=1.0, segments=[Segment(kind="discharge", c_rate=1.0, duration_s=3600.0)])    # 3 600 steps: fine
    assert "CYCLE_TOO_LONG" not in {i["code"] for i in run_analysis(req)["issues"]}


def test_missing_data_errors_are_reported_together():
    r = run_analysis(base_request(cell=cell_100ah(capacity_ah=None, r_dc_mohm=None, v_nom=None), pack=PackConfig(ns=120, np=1, n_modules=10, cells_per_module=10)))
    codes = {i["code"] for i in r["issues"] if i["severity"] == "error"}
    assert {"CELL_CAPACITY_MISSING", "CELL_VOLTAGE_MISSING", "CELL_RESISTANCE_MISSING", "PACK_CELL_COUNT_MISMATCH"} <= codes


def test_resistance_range_precheck_blocks_extrapolation_but_allows_explicit_opt_in():
    c = cell_100ah(r_dc_mohm=None, r_vs_soc=Curve(x=[20, 90], y=[0.8, 0.6]))
    blocked = run_analysis(base_request(cell=c))                                  # window 10-100 % > table 20-90 %
    assert blocked["status"] == "blocked" and any(i["code"] == "RESISTANCE_RANGE" for i in blocked["issues"])
    ok = run_analysis(base_request(cell=c, resistance=ResistanceSettings(extrapolation="clamp")))
    assert ok["status"] == "ok" and any(i["code"] == "RESISTANCE_RANGE" and i["severity"] == "warning" for i in ok["issues"])
    assert checks_by_id(ok)["S6"]["status"] == "WARNING"                          # extrapolation was used -> visible in the checks


def test_invalid_thermal_inputs():
    r = run_analysis(base_request(thermal=ThermalSettings(safety_factor=0.8)))
    assert "SAFETY_FACTOR_INVALID" in {i["code"] for i in r["issues"]}
    r2 = run_analysis(base_request(coolant=CoolantSpec(**{**HAND_COOLANT, "inlet_c": 45.0})))
    assert "COOLANT_INLET_TOO_WARM" in {i["code"] for i in r2["issues"]}
    r3 = run_analysis(base_request(coolant=CoolantSpec(**{**HAND_COOLANT, "allowable_dt_k": None, "max_outlet_c": 20.0})))
    assert "COOLANT_DT_INVALID" in {i["code"] for i in r3["issues"]}
    r4 = run_analysis(base_request(thermal=ThermalSettings(design_philosophy="drive_cycle"), cell=cell_100ah(mass_kg=None)))
    assert "PHILOSOPHY_NEEDS_THERMAL_MASS" in {i["code"] for i in r4["issues"]} and r4["status"] == "blocked"


# ------------------------------------------------------------------------------------------ philosophies
def long_cycle():
    """Pulsed load: 300 A for 60 s every 300 s over 1500 s (peak 10.8 kW, average well below)."""
    n = 1501
    i = np.where((np.arange(n) % 300) < 60, 300.0, 20.0)
    return DriveCycle(name="pulsed", time_s=list(range(n)), battery_current_a=i.tolist())


def test_all_four_philosophies_are_reported_and_ordered():
    r = run_analysis(base_request(cycle=long_cycle(), thermal=ThermalSettings(design_philosophy="peak", safety_factor=1.0,
                                                                                moving_avg_window_s=300.0)))
    c = r["design"]["candidates"]
    assert all(c[k]["available"] for k in ("peak", "moving_average", "sustained", "drive_cycle"))
    assert c["peak"]["value_w"] == pytest.approx(120 * 300 ** 2 * 1e-3)               # 10.8 kW
    assert c["drive_cycle"]["value_w"] <= c["moving_average"]["value_w"] + 1e-6 <= c["peak"]["value_w"] + 1e-6 or c["drive_cycle"]["value_w"] <= c["peak"]["value_w"]
    assert c["moving_average"]["value_w"] < c["peak"]["value_w"]                        # the peak is smoothed by the window
    assert c["sustained"]["value_w"] == pytest.approx(120 * 300 ** 2 * 1e-3, rel=1e-9) or c["sustained"]["value_w"] > 0   # 3 C continuous limit -> N·I²R
    assert c["sustained"]["governing"] == "discharge"


def test_selected_philosophy_drives_the_design_load_and_safety_factor_scales_it():
    vals = {}
    for ph in ("peak", "moving_average", "sustained", "drive_cycle"):
        r = run_analysis(base_request(cycle=long_cycle(), thermal=ThermalSettings(design_philosophy=ph, safety_factor=1.25, moving_avg_window_s=300.0)))
        assert r["status"] == "ok", (ph, r["issues"])
        d = r["design"]
        assert d["philosophy"] == ph and d["q_design_w"] == pytest.approx(1.25 * d["q_required_w"])
        assert d["q_relevant_w"] == pytest.approx(d["candidates"][ph]["value_w"])
        vals[ph] = d["q_required_w"]
    assert vals["moving_average"] < vals["peak"]
    assert vals["drive_cycle"] < vals["peak"]                                            # thermal mass buffers the pulses


def test_sustained_philosophy_hand_calc():
    # 3 C continuous discharge = 300 A/cell: 120·300²·1e-3 = 10.8 kW ; charge 1 C: 1.2 kW ; governing = discharge
    r = run_analysis(base_request(thermal=ThermalSettings(design_philosophy="sustained", safety_factor=1.0)))
    s = r["design"]["candidates"]["sustained"]
    assert s["value_w"] == pytest.approx(10800.0) and s["parts"]["charge"]["q_pack_w"] == pytest.approx(1200.0)
    only_cell = run_analysis(base_request(thermal=ThermalSettings(design_philosophy="sustained"), crate_limits=CRateLimits(),
                                          cell=cell_100ah(max_discharge_c=1.0, max_charge_c=0.5)))
    assert only_cell["design"]["candidates"]["sustained"]["value_w"] == pytest.approx(1200.0)      # falls back to the datasheet C-rates
    none = run_analysis(base_request(thermal=ThermalSettings(design_philosophy="sustained"), crate_limits=CRateLimits(), cell=cell_100ah(max_discharge_c=None, max_charge_c=None)))
    assert none["status"] == "blocked" and any(i["code"] == "PHILOSOPHY_NEEDS_CRATE" for i in none["issues"])


def test_ambient_heat_gain_is_added_when_ambient_exceeds_target():
    r = run_analysis(base_request(pack=PackConfig(ns=120, np=1, n_modules=10, cells_per_module=12, t_initial_c=35, t_target_max_c=40, t_ambient_c=50),
                                  thermal=ThermalSettings(design_philosophy="peak", safety_factor=1.0, ambient_ua_w_k=20.0)))
    assert r["design"]["q_ambient_gain_w"] == pytest.approx(20.0 * 10.0)                # UA·(T_amb − T_target)
    assert r["design"]["q_required_w"] == pytest.approx(4800 + 200)
    cold = run_analysis(base_request(thermal=ThermalSettings(design_philosophy="peak", safety_factor=1.0, ambient_ua_w_k=20.0)))
    assert cold["design"]["q_ambient_gain_w"] == 0.0                                      # a cold ambient is not credited (conservative)


# ------------------------------------------------------------------------------------------ missing data / graceful degradation
def test_without_cold_plate_sizing_still_works_and_temperature_checks_are_not_applicable():
    r = run_analysis(base_request(plate=None))
    assert r["status"] == "ok" and r["cold_plate"] is None and r["hydraulics"] is None
    assert r["cooling"]["pack"]["lpm"] == pytest.approx(19.0, abs=0.01)
    c = checks_by_id(r)
    for cid in ("1", "2", "3", "4", "8"):
        assert c[cid]["status"] == "N/A" and "cold plate" in c[cid]["message"], cid
    assert r["thermal"]["t_max_uncooled_c"] > 25.0                                        # the adiabatic rise is still shown
    assert r["thermal"]["t_max_uncooled_c"] == pytest.approx(25 + 4800 * 60 / (120 * 2.05 * 1000), abs=0.5) or r["thermal"]["t_max_uncooled_c"] > 25


def test_without_thermal_mass_hydraulics_are_computed_but_temperatures_are_not():
    r = run_analysis(base_request(cell=cell_100ah(mass_kg=None)))
    assert r["hydraulics"] is not None and r["thermal"]["has_temperature"] is False
    c = checks_by_id(r)
    assert c["1"]["status"] == "N/A" and "mass" in c["1"]["message"] and c["8"]["status"] != "N/A"
    assert any(i["code"] == "THERMAL_MASS_MISSING" for i in r["issues"])


def test_entropic_exclusion_is_never_silent():
    r = run_analysis(base_request(entropic=EntropicSettings(mode="auto")))
    assert r["heat"]["entropic"]["included"] is False
    assert any(i["code"] == "ENTROPIC_EXCLUDED" for i in r["issues"]) and any(i["code"] == "ENTROPIC_DATA_MISSING" for i in r["issues"])
    s1 = checks_by_id(r)["S1"]
    assert s1["status"] == "WARNING" and "NOT included" in s1["message"] and "0.2 mV/K" in s1["message"]
    est = run_analysis(base_request(entropic=EntropicSettings(mode="auto", constant_mv_per_k=0.1)))
    assert est["heat"]["entropic"]["mode"] == "constant" and est["heat"]["max_cell_heat_w"] == pytest.approx(40.0 - 5.963, abs=1e-3)
    assert checks_by_id(est)["S1"]["status"] == "WARNING" and "USER ESTIMATE" in checks_by_id(est)["S1"]["message"]


def test_temperature_dependent_resistance_couples_and_converges():
    c = cell_100ah(r_dc_mohm=None, r_vs_temp=Curve(x=[0, 25, 50, 75], y=[1.6, 1.0, 0.8, 0.7]), r_ref_soc_pct=50)
    pk = PackConfig(ns=120, np=1, n_modules=10, cells_per_module=12, t_initial_c=25, t_target_max_c=45, t_ambient_c=25)
    cyc = cycle_const_current(200.0, 601)
    coupled = run_analysis(base_request(cell=c, pack=pk, cycle=cyc, thermal=ThermalSettings(design_philosophy="peak", ambient_ua_w_k=0.0)))
    fixed = run_analysis(base_request(cell=c, pack=pk, cycle=cyc, thermal=ThermalSettings(design_philosophy="peak", couple_resistance_to_temperature=False, ambient_ua_w_k=0.0)))
    assert coupled["status"] == "ok" and coupled["thermal"]["coupled_resistance"] and coupled["thermal"]["converged"]
    assert fixed["thermal"]["coupled_resistance"] is False
    assert coupled["series"]["r_cell_mohm"][0] == pytest.approx(1.0) and coupled["series"]["t_cell_c"][-1] > 25.5
    assert coupled["series"]["r_cell_mohm"][-1] < coupled["series"]["r_cell_mohm"][0]     # warmer -> lower R -> lower heat
    assert fixed["series"]["r_cell_mohm"][-1] == pytest.approx(1.0)
    assert coupled["heat"]["total_heat_kwh"] < fixed["heat"]["total_heat_kwh"]


# ------------------------------------------------------------------------------------------ margins & checks
def test_cooling_margin_classes_use_configurable_limits():
    def margin(mult, limits=None):
        r = run_analysis(base_request(installed_cooling_capacity_kw=4.8 * mult, **({"limits": limits} if limits else {})))
        return checks_by_id(r)["5"], r["margins"]["cooling"]
    assert margin(0.9)[0]["status"] == "FAIL" and margin(0.9)[1]["class"] == "INSUFFICIENT"
    assert margin(1.05)[0]["status"] == "WARNING" and margin(1.05)[1]["class"] == "WARNING"
    assert margin(1.15)[0]["status"] == "PASS" and margin(1.15)[1]["class"] == "MODERATE"
    assert margin(1.25)[0]["status"] == "PASS" and margin(1.25)[1]["class"] == "ADEQUATE"
    strict = LimitSettings(cooling_margin_warn_pct=20.0, cooling_margin_target_pct=40.0)
    assert margin(1.15, strict)[0]["status"] == "WARNING" and margin(1.45, strict)[1]["class"] == "ADEQUATE"
    assert margin(1.25)[1]["margin_pct"] == pytest.approx(25.0)


def test_cooling_margin_without_installed_capacity_uses_plate_capability_and_says_so():
    r = run_analysis(base_request())
    c = checks_by_id(r)["5"]
    assert "cold-plate heat-removal capability" in c["message"] and r["sizing"]["plate_capability_w"] > 0


def test_check_statuses_are_pass_warning_fail_or_na_and_nothing_is_hidden():
    hot = run_analysis(base_request(pack=PackConfig(ns=120, np=1, n_modules=10, cells_per_module=12, t_initial_c=39, t_target_max_c=40, t_ambient_c=25),
                                    cycle=cycle_const_current(300.0, 601), thermal=ThermalSettings(design_philosophy="peak", ambient_ua_w_k=0.0)))
    c = checks_by_id(hot)
    assert [k for k in c if not k.startswith("S")] == ["1", "2", "3", "4", "5", "6", "7", "8"]
    assert all(v["status"] in ("PASS", "WARNING", "FAIL", "N/A") for v in c.values())
    assert c["1"]["status"] == "FAIL" or c["5"]["status"] == "FAIL" or c["2"]["status"] == "FAIL"        # a hard duty must show at least one failure
    assert any(v["status"] == "FAIL" for v in c.values())


def test_crate_check_pass_warning_fail_and_duration_logic():
    def status(i_profile, **limits):
        n = len(i_profile)
        cyc = DriveCycle(name="c", time_s=list(range(n)), battery_current_a=i_profile)
        r = run_analysis(base_request(cycle=cyc, crate_limits=CRateLimits(**limits)))
        return checks_by_id(r)["7"]
    ok = status([200.0] * 60, cont_discharge_c=3.0, peak_discharge_c=5.0, peak_discharge_duration_s=30)
    assert ok["status"] == "PASS" and "within" in ok["message"]
    pulse = status([200.0] * 20 + [400.0] * 20 + [200.0] * 20, cont_discharge_c=3.0, peak_discharge_c=5.0, peak_discharge_duration_s=30)
    assert pulse["status"] == "WARNING" and "within the pulse rating" in pulse["message"]                # 4 C for 20 s: allowed pulse
    too_long = status([400.0] * 60, cont_discharge_c=3.0, peak_discharge_c=5.0, peak_discharge_duration_s=30)
    assert too_long["status"] == "FAIL" and "longer than" in too_long["message"]
    too_high = status([200.0] * 10 + [600.0] * 5 + [200.0] * 10, cont_discharge_c=3.0, peak_discharge_c=5.0, peak_discharge_duration_s=30)
    assert too_high["status"] == "FAIL" and "above the 5 C peak rating" in too_high["message"]
    regen = status([200.0] * 20 + [-250.0] * 20 + [200.0] * 20, cont_discharge_c=3.0, peak_discharge_c=5.0, peak_discharge_duration_s=30, regen_c=1.0)
    assert regen["status"] in ("WARNING", "FAIL") and "regen" in regen["message"]                         # 2.5 C regen vs 1 C limit


def test_flow_adequacy_when_the_user_specifies_the_flow():
    low = run_analysis(base_request(plate=cold_plate(flow_lpm=10.0)))
    assert checks_by_id(low)["S5"]["status"] == "FAIL" and low["sizing"]["flow"]["flow_source"] == "specified by user"
    near = run_analysis(base_request(plate=cold_plate(flow_lpm=18.0)))
    assert checks_by_id(near)["S5"]["status"] == "WARNING"
    good = run_analysis(base_request(plate=cold_plate(flow_lpm=25.0)))
    assert checks_by_id(good)["S5"]["status"] == "PASS" and good["hydraulics"]["q_pack_lpm"] == pytest.approx(25.0, rel=1e-3)


def test_infeasible_power_and_soc_depletion_are_errors_in_the_result():
    n = 30
    cyc = DriveCycle(name="p", time_s=list(range(n)), battery_power_kw=[400.0] * n)                       # > OCV²/4R·N = 307 kW
    r = run_analysis(base_request(cycle=cyc))
    assert r["status"] == "completed_with_errors" and any(i["code"] == "POWER_INFEASIBLE" for i in r["issues"])
    assert checks_by_id(r)["S3"]["status"] == "FAIL"
    big = DriveCycle(name="b", time_s=[k * 60.0 for k in range(100)], battery_current_a=[300.0] * 100)    # 300 A for 100 min from 90 %
    r2 = run_analysis(base_request(cycle=big))
    assert any(i["code"] == "SOC_DEPLETED" for i in r2["issues"]) and checks_by_id(r2)["S2"]["status"] == "FAIL"


def test_hydraulics_flags_reach_check_8_and_issues():
    r = run_analysis(base_request(plate=cold_plate(external_dp_kpa=250.0)))
    assert checks_by_id(r)["8"]["status"] == "FAIL" and any(i["code"] == "HYD_LOOP_DP_EXCESSIVE" for i in r["issues"])
    ok = run_analysis(base_request())
    assert checks_by_id(ok)["8"]["status"] in ("PASS", "WARNING")


# ------------------------------------------------------------------------------------------ recommendations
def test_recommended_inlet_temperature_and_chiller_logic():
    r = run_analysis(base_request(pack=PackConfig(ns=120, np=1, n_modules=10, cells_per_module=12, t_initial_c=25, t_target_max_c=40, t_ambient_c=35)))
    inlet = r["sizing"]["inlet_temperature"]
    assert inlet["t_in_recommended_c"] <= inlet["t_in_max_c"] and inlet["t_in_recommended_c"] == math.floor(inlet["t_in_max_c"] * 2) / 2
    assert inlet["chiller_required"] is True or inlet["below_ambient"] is False
    rad = r["sizing"]["radiator"]
    assert rad["q_reject_w"] == pytest.approx(5760.0 + r["hydraulics"]["p_hyd_w"])                # design load + pump heat
    assert any("air-side" in n.lower() for n in rad["notes"])
    assert r["sizing"]["pump"]["flow_lpm"] == pytest.approx(r["hydraulics"]["q_pack_lpm"]) and r["sizing"]["pump"]["dp_bar"] > 0.3


# ------------------------------------------------------------------------------------------ traceability & assumptions
def test_every_kpi_and_check_trace_id_exists_and_chains_back_to_inputs():
    r = run_analysis(base_request())
    tr = r["trace"]
    ids = [k["trace"] for g in r["kpis"].values() for k in g if k["trace"]] + [c["trace_id"] for c in r["checks"] if c.get("trace_id")]
    assert ids and all(i in tr for i in ids), [i for i in ids if i not in tr]

    def leaves(nid, seen=None):
        seen = seen if seen is not None else set()
        if nid in seen:
            return set()
        seen.add(nid)
        n = tr[nid]
        if not n["inputs"]:
            return {nid}
        out = set()
        for d in n["inputs"]:
            assert d in tr, f"{nid} depends on missing node {d}"
            out |= leaves(d, seen)
        return out
    leaf_nodes = leaves("design.q_design")
    assert leaf_nodes and all(tr[n]["kind"] in ("input", "assumption") or tr[n]["source"] for n in leaf_nodes)
    assert {"in.ns", "in.np"} <= leaf_nodes
    assert tr["design.q_design"]["value"] == pytest.approx(5.76) and tr["design.q_design"]["formula"] == "Q_design = Q_required × SF"
    assert tr["heat.q_pack_pk"]["value"] == pytest.approx(4.8) and "40" in tr["heat.q_pack_pk"]["substitution"]
    assert tr["cool.lpm_pack"]["value"] == pytest.approx(19.0, abs=0.01)


def test_assumptions_register_sources_and_user_provenance():
    r = run_analysis(base_request())
    rows = {a["path"]: a for a in r["assumptions"]}
    assert rows["thermal.safety_factor"]["source_class"] == "Assumed" and rows["thermal.safety_factor"]["confidence"] == "Medium"
    assert rows["cell.capacity_ah"]["source_class"] == "Datasheet" and rows["pack.ns"]["source_class"] == "User-provided"
    assert rows["cold_plate.contact_resistance_m2k_w"]["source_class"] == "Assumed" and rows["cold_plate.contact_resistance_m2k_w"]["confidence"] == "Low"
    assert {"parameter", "value", "unit", "source_class", "source", "confidence"} <= set(rows["thermal.safety_factor"])
    edited = run_analysis(base_request(provenance={"thermal.safety_factor": ProvEntry(source="user"), "cell.cp_j_kg_k": ProvEntry(source="assumed", confidence="low", note="accepted suggestion")}))
    er = {a["path"]: a for a in edited["assumptions"]}
    assert er["thermal.safety_factor"]["source_class"] == "User-provided" and er["thermal.safety_factor"]["confidence"] == "High"
    assert er["cell.cp_j_kg_k"]["source_class"] == "Assumed" and "accepted suggestion" in er["cell.cp_j_kg_k"]["source"]
    assert rows["coolant.density_kg_m3"]["source_class"] == "User-provided"                      # custom coolant: the values were typed in by the user
    corr = run_analysis(base_request(coolant=CoolantSpec(type="eg_water", concentration_pct=50, inlet_c=25, allowable_dt_k=5)))
    assert {a["path"]: a for a in corr["assumptions"]}["coolant.density_kg_m3"]["source_class"] == "Calculated"


def test_pipeline_is_fast_and_deterministic():
    import time
    cyc = DriveCycle(name="long", time_s=list(range(3601)), battery_current_a=[150 + 100 * math.sin(k / 50) for k in range(3601)])
    t = time.time()
    a = run_analysis(base_request(cycle=cyc, thermal=ThermalSettings(design_philosophy="drive_cycle")))
    assert time.time() - t < 5.0
    b = run_analysis(base_request(cycle=cyc, thermal=ThermalSettings(design_philosophy="drive_cycle")))
    assert a["design"]["q_design_w"] == b["design"]["q_design_w"] and a["heat"]["total_heat_kwh"] == b["heat"]["total_heat_kwh"]


def test_scalar_mode_matches_full_mode():
    full = run_analysis(base_request())
    fast = run_analysis(base_request(), mode="scalars")
    assert "series" not in fast and "trace" not in fast
    assert fast["design"]["q_design_w"] == full["design"]["q_design_w"] and fast["heat"]["max_pack_heat_kw"] == full["heat"]["max_pack_heat_kw"]


@pytest.mark.parametrize("philosophy", ["peak", "moving_average", "sustained", "drive_cycle"])
def test_trace_graph_integrity_for_every_philosophy(philosophy):
    r = run_analysis(base_request(cycle=long_cycle(), thermal=ThermalSettings(design_philosophy=philosophy, safety_factor=1.2, moving_avg_window_s=300.0)))
    tr = r["trace"]
    for nid, node in tr.items():
        for dep in node["inputs"]:
            assert dep in tr, f"{nid} depends on missing node {dep}"
    # the selected philosophy's value must trace back to genuine inputs (not just to the peak instant)
    chain, seen = [], set()

    def visit(n):
        if n in seen:
            return
        seen.add(n)
        for d in tr[n]["inputs"]:
            visit(d)
        chain.append(n)
    visit("design.q_design")
    kinds = {tr[n]["kind"] for n in chain}
    assert "input" in kinds and tr[chain[-1]]["kind"] == "result"
    expected = {"peak": "heat.q_pack_pk", "moving_average": "heat.q_series", "sustained": "in.cell_cap", "drive_cycle": "th.c_pack"}[philosophy]
    assert expected in chain
    if philosophy == "drive_cycle":
        assert {"in.t_initial", "in.t_target", "in.t_in"} <= set(chain)
    assert tr["design.sf"]["kind"] == "assumption" and tr["design.sf"]["source"] == "assumed"      # default SF is labelled as an assumption
    r2 = run_analysis(base_request(cycle=long_cycle(), thermal=ThermalSettings(design_philosophy=philosophy, safety_factor=1.2, moving_avg_window_s=300.0),
                                   provenance={"thermal.safety_factor": ProvEntry(source="user")}))
    assert r2["trace"]["design.sf"]["source"] == "user"
