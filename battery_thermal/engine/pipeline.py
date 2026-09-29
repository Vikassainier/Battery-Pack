"""Analysis pipeline - orchestrates the calculation chain and assembles a JSON-ready, fully traceable result.

    Electrical load → Cell heat generation → Thermal accumulation → Cooling requirement → Cooling-system sizing

The pipeline contains no engineering formulas of its own; it wires the modules together, iterates where the models
are coupled (temperature-dependent resistance ↔ heat ↔ flow), and records every step in the trace log.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from .assumptions import CATALOG, build_register
from .checks import Check, crate_assessment, evaluate_checks
from .coldplate import ColdPlateError, ColdPlateResult, analyze_cold_plate
from .coolant import CoolantError, CoolantProps, coolant_properties
from .cooling import CoolingError, effective_coolant_dt, flow_requirements
from .electrical import OcvModel
from .heat import EntropicModelError, build_entropic_model, entropic_heat_w
from .interp import Interp1D, OutOfRangeError
from .load import LoadError, LoadProfile, build_load, load_summary
from .pack import ConfigError, derive_pack
from .pressure_drop import hydraulics
from .resistance import ResistanceModelError, build_resistance_model
from .schemas import AnalysisRequest
from .simulation import SimConfig, SimResult, simulate
from .sizing import (
    PEAK_VS_SUSTAINED, PHILOSOPHY_LABEL, cooling_margin, design_load, pump_recommendation, radiator_estimate,
    recommended_inlet_temperature, thermal_margin,
)
from .summary import summarize_heat
from .thermal import (
    ThermalNetwork, cell_to_cell_dt, estimate_ambient_ua, moving_average_peak, pack_thermal_capacity,
    required_capacity_drive_cycle, sustained_heat,
)
from .trace import TraceLog, fmt
from .units import lpm_to_kgs
from .validation import Issue, ERROR, INFO, WARNING, has_errors, validate_request

DEFAULT_MA_WINDOW_S = 300.0
MAX_ITER = 4
TOL = 0.005


class AnalysisAbort(Exception):
    def __init__(self, issue: Issue):
        self.issue = issue


def jsonable(o: Any):
    """JSON-safe conversion (numpy -> python, NaN/inf -> None)."""
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    if isinstance(o, np.ndarray):
        return jsonable(o.tolist())
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return f if math.isfinite(f) else None
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if hasattr(o, "to_dict"):
        return jsonable(o.to_dict())
    return o


def _r(a, nd=6):
    return np.round(np.asarray(a, float), nd).tolist()


def _src(req: AnalysisRequest, path: str) -> str:
    pv = req.provenance.get(path)
    if pv:
        return pv.source
    meta = CATALOG.get(path)
    return meta.default_source if meta else "user"


def blocked(req: AnalysisRequest, issues: list[Issue]) -> dict:
    return {"status": "blocked", "issues": [i.to_dict() for i in issues], "project": req.project.model_dump(),
            "message": "The analysis was not run: fix the errors listed above."}


# ==================================================================================================
def run_analysis(req: AnalysisRequest, *, mode: str = "full", _artefacts: dict | None = None) -> dict:
    """mode 'full': everything (series, trace, register). mode 'scalars': fast path used by sensitivity/optimisation.

    ``_artefacts`` (optional dict) receives internal objects (simulation, properties, ...) for the optimiser.
    """
    full = mode == "full"
    issues: list[Issue] = validate_request(req)
    if has_errors(issues):
        return blocked(req, issues)
    tr = TraceLog()
    cell, pk, th, co, plate = req.cell, req.pack, req.thermal, req.coolant, req.cold_plate
    try:
        pack = derive_pack(cell, pk, tr)
        rmodel = build_resistance_model(cell, req.resistance)
        ocv = OcvModel(cell, req.resistance.extrapolation)
        ent = build_entropic_model(cell, req.entropic, req.resistance.extrapolation)
        load = build_load(req, pack)
        dt_cool, dt_why = effective_coolant_dt(co)
        props = coolant_properties(co, co.inlet_c + dt_cool / 2.0)
    except (ResistanceModelError, EntropicModelError, LoadError, ConfigError, CoolingError, CoolantError) as exc:
        issues.append(Issue("MODEL_SETUP", ERROR, "", str(exc)))
        return blocked(req, issues)

    N = pack.n_cells
    have_mass = cell.mass_kg is not None and cell.cp_j_kg_k is not None
    c_pack = pack_thermal_capacity(cell.mass_kg, cell.cp_j_kg_k, N, th.extra_thermal_mass_j_k) if have_mass else None
    if th.ambient_ua_w_k is not None:
        ua, ua_info, ua_src = th.ambient_ua_w_k, {"source": "user"}, "user"
    else:
        ua_est, ua_info = estimate_ambient_ua(cell, N, th.ambient_h_w_m2k)
        ua, ua_src = (ua_est or 0.0), "estimated"
        if ua_est is None:
            issues.append(Issue("AMBIENT_UA_UNAVAILABLE", WARNING, "thermal.ambient_ua_w_k",
                                "Pack-to-ambient conductance could not be estimated (no cell dimensions) and none was given: heat exchange with the ambient is NOT modelled."))
    t0, t_tgt, t_amb, t_in = pk.t_initial_c, pk.t_target_max_c, pk.t_ambient_c, co.inlet_c
    couple = th.couple_resistance_to_temperature and ("T" in rmodel.depends_on)
    soc_mode = "file" if (req.cycle_options.soc_mode in ("auto", "file") and load.soc_file_pct is not None) else "integrate"
    if req.cycle_options.soc_mode == "file" and load.soc_file_pct is None:
        issues.append(Issue("SOC_FILE_UNAVAILABLE", WARNING, "cycle_options.soc_mode", "SOC from file was requested but is not available (no SOC column, or the cycle is repeated): SOC is integrated instead."))
    soc0 = float(load.soc_file_pct[0]) if soc_mode == "file" else pk.soc_initial_pct
    if soc_mode == "file":
        issues.append(Issue("SOC_FROM_FILE", INFO, "cycle.soc_pct", f"SOC taken from the cycle file (start {soc0:.1f} %); the initial-SOC input is not used."))
    cfg = SimConfig(couple_temperature=couple, soc_mode=soc_mode, soc_min_pct=pk.soc_min_pct, soc_max_pct=pk.soc_max_pct)
    cap_t = None
    if cell.capacity_vs_temp is not None:
        cap_t = Interp1D(cell.capacity_vs_temp.x, cell.capacity_vs_temp.y, "capacity-vs-temperature table", "T [°C]", req.resistance.extrapolation)

    def run_sim(network: ThermalNetwork | None, coupled: bool) -> SimResult:
        return simulate(load, pack, rmodel, ocv, ent, soc0, t0, network=network,
                        cfg=SimConfig(couple_temperature=coupled, soc_mode=soc_mode, soc_min_pct=pk.soc_min_pct, soc_max_pct=pk.soc_max_pct), cap_vs_temp=cap_t)

    try:
        # ---- pass 0: heat series with the cell held at its initial temperature ---------------------------------------
        sim0 = run_sim(None, False)
    except OutOfRangeError as exc:
        issues.append(Issue("RESISTANCE_RANGE", ERROR, "cell", str(exc), "Extend the datasheet data or explicitly enable clamp/linear extrapolation."))
        return blocked(req, issues)

    t, q0 = sim0.t, sim0.q_pack

    def candidates(q_series: np.ndarray, network: ThermalNetwork | None) -> dict[str, dict]:
        peak_i = int(np.argmax(q_series))
        c: dict[str, dict] = {"peak": {"available": True, "value_w": float(q_series[peak_i]), "t_s": float(t[peak_i]),
                                       "substitution": f"max Q_pack(t) = {fmt(q_series[peak_i] / 1e3)} kW at t = {t[peak_i]:.0f} s", "trace_inputs": ["heat.q_pack_pk"]}}
        w_user = th.moving_avg_window_s
        window = w_user if w_user is not None else DEFAULT_MA_WINDOW_S
        v, tt, wu = moving_average_peak(t, q_series, window)
        c["moving_average"] = {"available": True, "value_w": v, "t_s": tt, "window_s": wu, "window_source": "user" if w_user is not None else "default",
                               "substitution": f"max over t of (1/{wu:.0f} s)·∫Q dt = {fmt(v / 1e3)} kW at t = {tt:.0f} s", "trace_inputs": ["heat.q_pack_pk"]}
        # sustained: steady-state heat at the rated continuous currents
        try:
            cap = cell.capacity_ah
            lim = req.crate_limits
            c_dis = lim.cont_discharge_c if lim.cont_discharge_c is not None else cell.max_discharge_c
            c_chg = lim.charge_c if lim.charge_c is not None else cell.max_charge_c
            grid = list(np.linspace(pk.soc_min_pct, pk.soc_max_pct, 11))
            parts = {}
            if c_dis is not None:
                def q_dis(soc, T, i=c_dis * cap):
                    return max(i * i * rmodel.r_ohm(soc, T, False) + entropic_heat_w(i, T, ent.dudt_v_per_k(soc, T)), 0.0)
                parts["discharge"] = {**sustained_heat(q_dis, N, grid, t_tgt), "c_rate": c_dis}
            if c_chg is not None:
                def q_chg(soc, T, i=-c_chg * cap):
                    return max(i * i * rmodel.r_ohm(soc, T, True) + entropic_heat_w(i, T, ent.dudt_v_per_k(soc, T)), 0.0)
                parts["charge"] = {**sustained_heat(q_chg, N, grid, t_tgt), "c_rate": c_chg}
            if parts:
                key = max(parts, key=lambda k: parts[k]["q_pack_w"])
                c["sustained"] = {"available": True, "value_w": parts[key]["q_pack_w"], "governing": key, "parts": parts,
                                  "substitution": f"N·I²·R at {parts[key]['c_rate']:g} C ({key}), worst case SOC {parts[key]['soc_pct']:.0f} %, T = {t_tgt:g} °C → {fmt(parts[key]['q_pack_w'] / 1e3)} kW",
                                  "trace_inputs": []}
            else:
                c["sustained"] = {"available": False, "value_w": 0.0, "note": "no continuous C-rate defined"}
        except OutOfRangeError as exc:
            c["sustained"] = {"available": False, "value_w": 0.0, "note": f"resistance data do not cover the design temperature: {exc}"}
        # drive-cycle: minimum constant capacity with thermal mass
        if c_pack is not None:
            dc = required_capacity_drive_cycle(t, q_series, c_pack, t0, t_tgt, min(t0, t_in), ua, t_amb)
            c["drive_cycle"] = {"available": dc["feasible"], "value_w": dc["q_cap_w"] if dc["feasible"] else 0.0, "detail": {k: v for k, v in dc.items() if k not in ("removal_w", "t_c")},
                                "note": dc["note"], "substitution": f"bisection on constant capacity: T_max = {dc['t_max_at_cap_c']:.2f} °C ≤ {t_tgt:g} °C (no cooling: {dc['t_max_no_cooling_c']:.1f} °C)",
                                "trace_inputs": []}
            if dc["feasible"]:
                c["drive_cycle"]["removal_w"] = dc.get("removal_w")
        else:
            c["drive_cycle"] = {"available": False, "value_w": 0.0, "note": "cell mass and/or specific heat missing (thermal mass unknown)"}
        return c

    q_gain = ua * max(0.0, t_amb - t_tgt)
    plate_ok = plate is not None
    q_series = q0
    sim = sim0
    network: ThermalNetwork | None = None
    cp_res: ColdPlateResult | None = None
    m_actual = None
    dl: dict = {}
    fr: dict = {}
    converged, n_iter, prev_q = True, 0, None
    heat_dep = couple and plate_ok and c_pack is not None          # heat depends on the cooling design through R(T)
    m_min = lpm_to_kgs(req.limits.flow_min_lpm, props.rho)

    _register_heat_trace(tr, sim0, pack, rmodel, ent, req)          # the heat-chain trace is needed by the design-load nodes
    try:
        for n_iter in range(1, MAX_ITER + 1):
            dl = design_load(th.design_philosophy, candidates(q_series, network), th.safety_factor, q_gain, tr)
            fr = flow_requirements(dl["q_design_w"], N, pack.cells_per_module, props, dt_cool, tr, "design.q_design")
            if plate_ok:
                m_actual = lpm_to_kgs(plate.flow_lpm, props.rho) if plate.flow_lpm else max(fr["pack"]["m_dot_kg_s"], m_min)
                cp_res = analyze_cold_plate(plate, props, m_actual, N, tr)
                if c_pack is not None:
                    network = ThermalNetwork(c_pack, cp_res.g_cool_eff_w_k, t_in, ua, t_amb, cp_res.effectiveness, m_actual * props.cp)
                    sim = run_sim(network, couple)
                    q_series = sim.q_pack
            if not heat_dep:
                break
            if prev_q is not None and abs(dl["q_design_w"] - prev_q) <= TOL * max(prev_q, 1.0):
                break
            prev_q = dl["q_design_w"]
            converged = n_iter < MAX_ITER
    except (ColdPlateError, OutOfRangeError, ValueError) as exc:
        if isinstance(exc, ValueError) and "philosophy" in str(exc):
            issues.append(Issue("PHILOSOPHY_UNAVAILABLE", ERROR, "thermal.design_philosophy", str(exc)))
        else:
            issues.append(Issue("COOLING_MODEL", ERROR, "cold_plate", str(exc)))
        return blocked(req, issues)
    if not converged:
        issues.append(Issue("ITERATION_NOT_CONVERGED", WARNING, "", f"Temperature-coupled sizing loop did not converge to {TOL * 100:g} % in {MAX_ITER} passes."))

    summ = summarize_heat(sim, pack)
    _register_heat_trace(tr, sim, pack, rmodel, ent, req, summ)
    sim.flags["first_soc_below_0_s"] = _first_time(sim.t, sim.soc_pct < -1e-9)
    sim.flags["first_soc_above_100_s"] = _first_time(sim.t, sim.soc_pct > 100 + 1e-9)

    # ---- temperature prediction & uniformity -----------------------------------------------------------------------------
    has_plate = cp_res is not None
    has_t = has_plate and network is not None
    no_t_reason = ("no cold plate is defined" if not plate_ok else "cell mass and/or specific heat missing") if not has_t else ""
    out: dict[str, Any] = {}
    unif_series = None
    t_hot = dt_pack = dt_mod = None
    if has_t:
        q_removed = sim.q_cool_w
        spread, mald = th.cell_heat_spread_pct / 100.0, th.flow_maldistribution_pct / 100.0
        series_flow = plate.plate_arrangement == "series"
        u = cell_to_cell_dt(np.abs(q_removed), network.m_cp_w_k, np.maximum(sim.q_cell, 0.0), cp_res.r_total, plate.n_plates, series_flow, spread, mald)
        dt_pack, dt_mod = u["dt_pack_k"], u["dt_module_k"]
        t_hot = sim.t_cell_c + u["hot_cell_offset_k"]
        unif_series = u
        i_hot = int(np.argmax(t_hot))
        tr.calc("temp.t_avg_max", "Maximum average cell temperature", float(sim.t_cell_c.max()), "°C", "lumped transient solution", f"C·dT/dt = Q_gen − G_c(T−T_in) − G_a(T−T_amb); C = {fmt(network.c_pack_j_k)} J/K", [])
        tr.result("temp.t_hot_max", "Maximum predicted cell temperature (hottest cell)", float(t_hot.max()), "°C", "T_hot = T_avg + ΔT_cell-to-cell/2",
                  f"{fmt(sim.t_cell_c[i_hot])} + {fmt(u['hot_cell_offset_k'][i_hot])} at t = {sim.t[i_hot]:.0f} s", ["temp.t_avg_max"])
        tr.result("temp.dt_pack", "Cell-to-cell ΔT (pack, max)", float(dt_pack.max()), "K", "ΔT = Q_removed/(ṁ·cp)·f_maldist + 2·δ_Q·Q_cell·R_total",
                  f"path {fmt(u['dt_coolant_path_k'].max())} K + spread {fmt(u['dt_generation_spread_k'].max())} K", ["cp.r_total"])
        tr.result("temp.dt_module", "Module ΔT (max)", float(dt_mod.max()), "K", "ΔT_module = ΔT_coolant,plate + 2·δ_Q·Q_cell·R_total", "", ["cp.r_total"])
        tr.result("temp.t_out_max", "Maximum coolant outlet temperature", float(np.nanmax(sim.t_coolant_out_c)), "°C", "T_out = T_in + ε·(T_cell − T_in)", f"T_in = {t_in:g} °C, ε = {fmt(cp_res.effectiveness)}", ["cp.eps"])

    # uncooled comparison
    uncooled = None
    if c_pack is not None and full:
        try:
            uncooled = run_sim(ThermalNetwork(c_pack, 0.0, t_in, ua, t_amb), couple)
        except OutOfRangeError:
            uncooled = None

    # ---- design-condition performance of the cooling system ---------------------------------------------------------------
    q_req, q_des = dl["q_required_w"], dl["q_design_w"]
    sizing: dict[str, Any] = {"capacity": {"required_kw": q_req / 1e3, "design_kw": q_des / 1e3, "recommended_kw": math.ceil(q_des / 100.0) / 10.0 if q_des > 0 else 0.0,
                                           "safety_factor": th.safety_factor, "philosophy": dl["label"]},
                              "flow": {"required_lpm": fr["pack"]["lpm"], "required_kg_s": fr["pack"]["m_dot_kg_s"], "dt_cool_k": dt_cool, "dt_basis": dt_why,
                                       "actual_lpm": (m_actual / props.rho * 60000.0) if m_actual else None,
                                       "flow_source": ("specified by user" if (plate and plate.flow_lpm) else "= required flow (not below the minimum practical flow)") if plate_ok else "required only (no cold plate)"}}
    hot_off_design = 0.0
    cap_basis, installed_w, margin_reason = "", None, "no installed cooling capacity given and no cold plate defined"
    hyd = None
    if has_plate:
        hyd = hydraulics(plate, props, m_actual, req.pump.overall_efficiency, req.limits, tr)
        ud = cell_to_cell_dt(q_des, cp_res.m_cp_w_k, q_des / N, cp_res.r_total, plate.n_plates, plate.plate_arrangement == "series",
                             th.cell_heat_spread_pct / 100.0, th.flow_maldistribution_pct / 100.0)
        hot_off_design = ud["hot_cell_offset_k"]
        rec = recommended_inlet_temperature(t_tgt, hot_off_design, q_des, cp_res.g_cool_eff_w_k, t_amb)
        sizing["inlet_temperature"] = {**rec, "specified_c": t_in}
        sizing["pump"] = pump_recommendation(hyd.q_pack_lpm, hyd.dp_total_pa, hyd.p_hyd_w, hyd.p_elec_w, hyd.head_m)
        sizing["radiator"] = radiator_estimate(q_des, hyd.p_hyd_w, t_in, dt_cool, t_amb, req.radiator.air_dt_k, hyd.q_pack_lpm)
        capability = max(0.0, cp_res.g_cool_eff_w_k * (t_tgt - hot_off_design - t_in))
        sizing["plate_capability_w"] = capability
        tr.calc("sizing.plate_capability", "Cold-plate heat-removal capability at the target temperature", capability / 1e3, "kW",
                "Q_cap = ε·ṁ·cp·(T_target − ΔT_hot − T_in)", f"{fmt(cp_res.g_cool_eff_w_k)} × ({fmt(t_tgt)} − {fmt(hot_off_design)} − {fmt(t_in)})", ["cp.eps"])
        if req.installed_cooling_capacity_kw is not None:
            installed_w, cap_basis = req.installed_cooling_capacity_kw * 1e3, "installed capacity specified by the user"
        else:
            installed_w, cap_basis = capability, "calculated cold-plate heat-removal capability at the specified flow, inlet temperature and target temperature"
    elif req.installed_cooling_capacity_kw is not None:
        installed_w, cap_basis = req.installed_cooling_capacity_kw * 1e3, "installed capacity specified by the user"
    m = cooling_margin(installed_w, q_req, req.limits)
    if q_req <= 0:
        m = {"available": True, "ratio": float("inf"), "margin_pct": float("inf"), "class": "ADEQUATE", "installed_w": installed_w or 0.0, "required_w": 0.0,
             "warn_pct": req.limits.cooling_margin_warn_pct, "target_pct": req.limits.cooling_margin_target_pct} if installed_w is not None else m
    if m.get("available"):
        tr.result("margin.cooling", "Cooling margin", (m["ratio"] if math.isfinite(m["ratio"]) else None), "-", "Cooling margin = Installed capacity / Required capacity",
                  f"{fmt(m['installed_w'] / 1e3)} kW / {fmt(m['required_w'] / 1e3)} kW", ["design.q_required"] + (["sizing.plate_capability"] if "plate_capability_w" in sizing and not req.installed_cooling_capacity_kw else []))
    t_margin = thermal_margin(t_tgt, float(t_hot.max()), req.limits) if has_t else {"available": False, "class": "N/A"}
    if t_margin["available"]:
        tr.result("margin.thermal", "Thermal margin", t_margin["margin_k"], "K", "Thermal margin = T_allowed,max − T_predicted,max", f"{fmt(t_tgt)} − {fmt(float(t_hot.max()))}", ["temp.t_hot_max"])

    # ---- checks ---------------------------------------------------------------------------------------------------------
    crate_ctx = _crate_context(sim, req)
    computed = _computed_params(req, props, dt_cool, ua, ua_src, fr, cp_res, m_actual, plate)
    register = build_register(req, computed)
    n_assumed = sum(1 for r in register if r.source_class == "Assumed" and r.group != "Margins")     # engineering limits are policy, not data
    n_low = sum(1 for r in register if r.confidence == "Low" and r.group != "Margins")
    ctx = {
        "limits": req.limits, "pack": pk, "cell": cell, "coolant": co, "has_temperatures": has_t, "no_temperature_reason": no_t_reason,
        "t_hot_max_c": float(t_hot.max()) if has_t else None, "t_cell_min_c": float(sim.t_cell_c.min()),
        "dt_pack_max_k": float(dt_pack.max()) if has_t else None, "dt_module_max_k": float(dt_mod.max()) if has_t else None,
        "dt_components": {"coolant_path_k": float(unif_series["dt_coolant_path_k"].max()), "generation_spread_k": float(unif_series["dt_generation_spread_k"].max())} if has_t else {},
        "dt_max_info": _dt_max_info(sim, dt_pack) if has_t else {},
        "t_out_max_c": float(np.nanmax(sim.t_coolant_out_c)) if has_t else None, "dt_cool_k": dt_cool,
        "coolant_out_limit_c": co.max_outlet_c if co.max_outlet_c is not None else t_in + (co.allowable_dt_k if co.allowable_dt_k is not None else dt_cool),
        "cooling_margin": m, "capacity_basis": cap_basis, "margin_reason": margin_reason,
        "charging_occurs": bool(np.any(np.asarray([s in ("charge", "regen") for s in sim.state]))),
        "crate": crate_ctx, "hydraulics": hyd, "entropic": ent,
        "entropic_bound_w": ent.bound_w_per_cell(float(np.abs(sim.i_cell).max()), t0) if not ent.included else None,
        "sim_flags": sim.flags, "v_cell_min": float(sim.v_cell.min()), "v_cell_max": float(sim.v_cell.max()),
        "flow_adequacy": ({"actual_lpm": m_actual / props.rho * 60000.0, "required_lpm": fr["pack"]["lpm"],
                           "ratio": m_actual / fr["pack"]["m_dot_kg_s"] if fr["pack"]["m_dot_kg_s"] > 0 else float("inf")}
                          if (plate_ok and plate.flow_lpm) else None),
        "resistance_range": rmodel.usage() + ocv_usage(ocv), "data_quality": {"n_assumed": n_assumed, "n_low": n_low},
    }
    checks: list[Check] = evaluate_checks(ctx)
    for h in (hyd.flags if hyd else []):
        if h["severity"] in ("warning", "fail"):
            issues.append(Issue(h["code"], WARNING, "cold_plate", h["message"]))
    if cp_res:
        for w in cp_res.warnings:
            issues.append(Issue("COLDPLATE_NOTE", WARNING, "cold_plate", w))
    if sim.flags["n_infeasible"]:
        issues.append(Issue("POWER_INFEASIBLE", ERROR, "cycle", f"{sim.flags['n_infeasible']} sample(s) request more power than the pack can deliver (OCV²/4R); current was limited."))
    if sim.flags["first_soc_below_0_s"] is not None:
        issues.append(Issue("SOC_DEPLETED", ERROR, "pack.soc_initial_pct", f"SOC falls below 0 % at t = {sim.flags['first_soc_below_0_s']:.0f} s: the duty exceeds the pack energy from this initial SOC."))
    elif sim.flags["first_soc_above_100_s"] is not None:
        issues.append(Issue("SOC_OVERCHARGED", ERROR, "pack.soc_initial_pct", f"SOC exceeds 100 % at t = {sim.flags['first_soc_above_100_s']:.0f} s."))
    if not ent.included:
        issues.append(Issue("ENTROPIC_EXCLUDED", WARNING, "entropic", ent.status))
    if not has_t and plate_ok:
        issues.append(Issue("TEMPERATURE_NOT_PREDICTED", WARNING, "cell.mass_kg", "Temperature prediction skipped: cell mass and/or specific heat missing."))

    # ---- assemble ----------------------------------------------------------------------------------------------------------
    kpis = _kpis(pack, summ, dl, fr, hyd, m, t_margin, t_hot, dt_pack, has_t, sizing, crate_ctx)
    result: dict[str, Any] = {
        "status": "completed_with_errors" if has_errors(issues) else "ok",
        "project": req.project.model_dump(), "issues": [i.to_dict() for i in issues], "pack": pack.to_dict(),
        "kpis": kpis, "heat": {**summ, "entropic": ent.describe(), "n_infeasible": sim.flags["n_infeasible"]},
        "design": {k: v for k, v in dl.items() if k != "candidates"} | {"candidates": {k: {kk: vv for kk, vv in c.items() if kk not in ("removal_w", "trace_inputs")} for k, c in dl["candidates"].items()}},
        "cooling": {**{k: v for k, v in fr.items()}, "dt_basis": dt_why, "props": props.to_dict()},
        "cold_plate": cp_res.to_dict() if cp_res else None, "hydraulics": hyd.to_dict() if hyd else None, "sizing": sizing,
        "thermal": {"has_temperature": has_t, "c_pack_j_k": c_pack, "tau_s": (network.tau_s if network else None), "ambient_ua_w_k": ua, "ambient_ua_source": ua_src,
                    "ambient_ua_info": ua_info, "iterations": n_iter, "converged": converged, "coupled_resistance": couple,
                    "t_max_avg_c": float(sim.t_cell_c.max()), "t_hot_max_c": float(t_hot.max()) if has_t else None,
                    "t_max_uncooled_c": float(uncooled.t_cell_c.max()) if uncooled is not None else None,
                    "stored_heat_kwh": float(c_pack * (sim.t_cell_c.max() - sim.t_cell_c[0]) / 3.6e6) if c_pack else None,
                    "no_temperature_reason": no_t_reason},
        "margins": {"cooling": m, "thermal": t_margin},
        "checks": [c.to_dict() for c in checks],
        "models": {"resistance": {**rmodel.describe(), "usage": rmodel.usage()}, "ocv": ocv.describe(), "entropic": ent.describe(),
                   "load": {"source": load.source, "kind": load.kind, "notes": load.notes, "period_s": load.period_s, "repeats": load.repeats},
                   "soc_source": sim.flags["soc_source"], "time_integration": "sample-and-hold (zero-order hold); the last sample has zero duration"},
        "explanations": {"peak_vs_sustained": PEAK_VS_SUSTAINED, "philosophy": dl["explanation"],
                         "chain": ["Electrical load (P_batt or I_pack) →", "Cell heat generation (I²R + reversible) →", "Thermal accumulation (C·dT/dt) →",
                                   "Cooling requirement (Q_design, ṁ) →", "Cooling-system sizing (plate, pump, radiator)"],
                         "electrical_vs_heat": "Electrical energy drawn from the battery is not heat: only the loss I²R (and the reversible term) becomes heat. "
                                               f"Here the heat is {summ['electrical']['heat_to_throughput_pct'] or 0:.2f} % of the electrical throughput."},
        "assumptions": [r.to_dict() for r in register], "data_quality": {"n_assumed": n_assumed, "n_low": n_low, "n_total": len(register)},
    }
    if _artefacts is not None:
        _artefacts.update(sim=sim, props=props, ua=ua, c_pack=c_pack, t_in=t_in, t_amb=t_amb, t_tgt=t_tgt, pack=pack, plate=plate, dt_cool=dt_cool,
                          m_actual=m_actual, dl=dl, has_t=has_t)
    if full:
        result["trace"] = tr.to_dict()
        result["series"] = _series(sim, load, t_hot, dt_pack, dt_mod, uncooled, pack)
        result["load"] = load_summary(load, 3000)
    return jsonable(result)


# ==================================================================================================
def ocv_usage(ocv: OcvModel) -> list[dict]:
    out = []
    for tb in ocv.tables:
        for u in ([tb.use] if hasattr(tb, "use") else [tb.use_x, tb.use_y]):
            out.append(u.to_dict())
    return out


def _dt_max_info(sim: SimResult, dt_pack: np.ndarray) -> dict:
    i = int(np.argmax(dt_pack))
    q_rem, q_gen = float(sim.q_cool_w[i]), float(sim.q_pack[i])
    return {"t_s": float(sim.t[i]), "q_removed_w": q_rem, "q_generated_w": q_gen,
            "pulldown_dominated": bool(q_gen <= 0 or q_rem > 1.5 * q_gen)}


def _first_time(t: np.ndarray, mask: np.ndarray):
    idx = np.where(mask)[0]
    return float(t[idx[0]]) if len(idx) else None


def _crate_context(sim: SimResult, req: AnalysisRequest) -> dict:
    lim, cell = req.crate_limits, req.cell
    st = np.asarray(sim.state)
    c = sim.c_rate
    dt = sim.dt
    t = sim.t
    from_cell = []
    cont_d = lim.cont_discharge_c if lim.cont_discharge_c is not None else cell.max_discharge_c
    if lim.cont_discharge_c is None and cell.max_discharge_c is not None:
        from_cell.append("continuous discharge")
    peak_d = lim.peak_discharge_c if lim.peak_discharge_c is not None else cell.pulse_discharge_c
    dur_d = lim.peak_discharge_duration_s if lim.peak_discharge_duration_s is not None else cell.pulse_duration_s
    cont_c = lim.charge_c if lim.charge_c is not None else cell.max_charge_c
    cont_r = lim.regen_c if lim.regen_c is not None else cont_c
    peak_r = lim.peak_regen_c if lim.peak_regen_c is not None else cell.pulse_charge_c
    dur_r = lim.peak_regen_duration_s if lim.peak_regen_duration_s is not None else cell.pulse_duration_s
    out = {"discharge": crate_assessment(t, dt, np.where(st == "discharge", c, 0.0), cont_d, peak_d, dur_d, "discharge"),
           "charge": crate_assessment(t, dt, np.where(st == "charge", -c, 0.0), cont_c, cell.pulse_charge_c if cell.pulse_charge_c else None, dur_r, "charge"),
           "regen": crate_assessment(t, dt, np.where(st == "regen", -c, 0.0), cont_r, peak_r, dur_r, "regen"),
           "limits_from": ("continuous discharge limit taken from the cell datasheet; " if from_cell else "") + "other limits from step 5 / datasheet"}
    for k in ("charge", "regen"):                                   # a direction that never occurs is not a finding
        if out[k].get("max_c", 0.0) == 0.0 and out[k]["status"] != "N/A":
            out[k]["status"] = "N/A"
    return out


def _computed_params(req, props: CoolantProps, dt_cool, ua, ua_src, fr, cp_res, m_actual, plate) -> dict:
    comp: dict[str, tuple] = {}
    for name, path, val in (("density", "coolant.density_kg_m3", props.rho), ("specific heat", "coolant.cp_j_kg_k", props.cp),
                            ("conductivity", "coolant.k_w_mk", props.k), ("viscosity", "coolant.mu_pa_s", props.mu)):
        if name in props.from_correlation:
            comp[path] = (val, f"{props.description} at {props.t_eval_c:.1f} °C")
    if ua_src == "estimated" and ua:
        comp["thermal.ambient_ua_w_k"] = (ua, "estimated from cell volume, fill fraction 0.4, flat-pack shape factor 7 and h_ext")
    if cp_res is not None and req.cold_plate.k_plate_w_mk is None:
        comp["cold_plate.k_plate_w_mk"] = (cp_res.k_plate_w_mk, f"library value for {req.cold_plate.material}")
    if plate is not None and plate.flow_lpm is None and m_actual:
        comp["cold_plate.flow_lpm"] = (fr["pack"]["lpm"], "required flow from the sizing calculation")
    if req.thermal.moving_avg_window_s is None:
        pass
    return comp


def _register_heat_trace(tr: TraceLog, sim: SimResult, pack, rmodel, ent, req: AnalysisRequest, summ: dict | None = None) -> None:
    """Trace the heat chain at the instant of maximum pack heat (Input → formula → intermediate → result)."""
    cell = req.cell
    i = int(np.argmax(sim.q_pack))
    tr.input("in.soc", f"SOC at t = {sim.t[i]:.0f} s (max heat)", float(sim.soc_pct[i]), "%", "calculated")
    tr.input("in.t_cell", "Cell temperature at that instant", float(sim.t_cell_c[i] if np.isfinite(sim.t_cell_c[i]) else req.pack.t_initial_c), "°C", "calculated")
    tr.input("in.i_pack_pk", "Pack current at that instant", float(sim.i_pack[i]), "A", "calculated", "from the load profile (current given, or solved from battery power)")
    tr.calc("heat.i_cell_pk", "Cell current", float(sim.i_cell[i]), "A", "I_cell = I_pack / Np", f"{fmt(sim.i_pack[i])} / {pack.np}", ["in.i_pack_pk", "in.np"])
    tr.calc("heat.r_pk", "Cell resistance R", float(sim.r_cell_ohm[i] * 1e3), "mΩ", f"R = {rmodel.name.split(' - ')[-1]}", f"R({fmt(sim.soc_pct[i])} %, {fmt(sim.t_cell_c[i])} °C) × scale {rmodel.scale:g}", ["in.soc", "in.t_cell"],
            note=rmodel.description)
    tr.calc("heat.q_joule_pk", "Joule heat per cell", float(sim.q_joule_cell[i]), "W", "Q_joule = I²·R", f"{fmt(sim.i_cell[i])}² × {fmt(sim.r_cell_ohm[i])}", ["heat.i_cell_pk", "heat.r_pk"])
    tr.calc("heat.q_rev_pk", "Entropic (reversible) heat per cell", float(sim.q_rev_cell[i]), "W", "Q_rev = −I·T·dU/dT",
            f"−{fmt(sim.i_cell[i])} × {fmt(sim.t_cell_c[i] + 273.15)} K × {fmt(sim.dudt_mv_k[i])} mV/K", ["heat.i_cell_pk", "in.t_cell"], note=ent.status)
    tr.result("heat.q_cell_pk", "Total cell heat at peak", float(sim.q_cell[i]), "W", "Q_cell = Q_joule + Q_rev", f"{fmt(sim.q_joule_cell[i])} + {fmt(sim.q_rev_cell[i])}", ["heat.q_joule_pk", "heat.q_rev_pk"])
    tr.result("heat.q_module_pk", "Module heat at peak", float(sim.q_module[i]), "W", "Q_module = cells_per_module · Q_cell", f"{pack.cells_per_module} × {fmt(sim.q_cell[i])}", ["heat.q_cell_pk"])
    tr.result("heat.q_pack_pk", "Maximum pack heat", float(sim.q_pack[i] / 1e3), "kW", "Q_pack = Ns·Np·Q_cell  (= Ns·Np·I_cell²·R + reversible)", f"{pack.ns} × {pack.np} × {fmt(sim.q_cell[i])} / 1000", ["heat.q_cell_pk", "in.ns", "in.np"])
    tr.calc("heat.c_max", "Maximum C-rate", float(np.abs(sim.c_rate).max()), "C", "C-rate = I_cell / C_cell", f"{fmt(np.abs(sim.i_cell).max())} / {fmt(pack.cell_capacity_ah)}", ["in.cell_cap"])
    if summ:
        tr.result("heat.q_pack_avg", "Average pack heat", summ["avg_pack_heat_kw"], "kW", "Q_avg = Σ Q_pack,k·Δt_k / T_cycle", f"{fmt(summ['total_heat_kwh'] * 3600)} MJ / {fmt(summ['duration_s'])} s", ["heat.q_pack_pk"])
        tr.result("heat.e_total", "Total heat generated over the cycle", summ["total_heat_kwh"], "kWh", "E = Σ Q_pack,k·Δt_k / 3.6e6", f"{summ['n_samples']} samples, sample-and-hold", ["heat.q_pack_pk"])
        tr.result("heat.e_electrical", "Electrical energy delivered (NOT heat)", summ["electrical"]["terminal_discharge_kwh"], "kWh", "E = Σ V_pack·I_pack·Δt", "terminal energy is a different quantity from the heat above", [])


def _kpis(pack, summ, dl, fr, hyd, m, t_margin, t_hot, dt_pack, has_t, sizing, crate_ctx) -> dict:
    def k(label, value, unit, trace=None, status=None, sub=None):
        return {"label": label, "value": value, "unit": unit, "trace": trace, "status": status, "sub": sub}
    marg = m["margin_pct"] if m.get("available") and m["margin_pct"] is not None and math.isfinite(m["margin_pct"]) else None
    return {
        "battery": [k("Pack voltage (nominal)", pack.v_nom, "V", "pack.voltage"), k("Pack capacity", pack.capacity_ah, "Ah", "pack.capacity"),
                    k("Pack energy", pack.energy_kwh, "kWh", "pack.energy"), k("Maximum C-rate", max(summ["max_discharge_c"], summ["max_charge_c"]), "C", "heat.c_max",
                                                                                sub=f"discharge {summ['max_discharge_c']:.2f} / charge {summ['max_charge_c']:.2f}")],
        "thermal": [k("Maximum heat generation", summ["max_pack_heat_kw"], "kW", "heat.q_pack_pk", sub=f"{summ['max_cell_heat_w']:.1f} W/cell"),
                    k("Average heat generation", summ["avg_pack_heat_kw"], "kW", "heat.q_pack_avg", sub=f"{summ['avg_cell_heat_w']:.1f} W/cell"),
                    k("Total heat generated", summ["total_heat_kwh"], "kWh", "heat.e_total"),
                    k("Max predicted cell temperature", float(t_hot.max()) if has_t else None, "°C", "temp.t_hot_max" if has_t else None,
                      status=("FAIL" if t_margin.get("class") == "INSUFFICIENT" else "WARNING" if t_margin.get("class") == "WARNING" else "PASS") if has_t else None),
                    k("Cell-to-cell ΔT (est.)", float(dt_pack.max()) if has_t else None, "K", "temp.dt_pack" if has_t else None)],
        "cooling": [k("Required cooling capacity", dl["q_required_w"] / 1e3, "kW", "design.q_required", sub=dl["label"]),
                    k("Recommended cooling capacity", sizing["capacity"]["design_kw"], "kW", "design.q_design", sub=f"× SF {dl['safety_factor']:g}"),
                    k("Required coolant flow", fr["pack"]["lpm"], "L/min", "cool.lpm_pack", sub=f"ΔT {fr['dt_k']:.1f} K"),
                    k("Pressure drop (total)", hyd.dp_total_pa / 1e5 if hyd else None, "bar", "hyd.dp_total" if hyd else None,
                      sub=f"plates {hyd.dp_plates_total_pa / 1e3:.1f} kPa" if hyd else "no cold plate"),
                    k("Cooling margin", marg, "%", "margin.cooling" if m.get("available") else None,
                      status=("FAIL" if m.get("class") == "INSUFFICIENT" else "WARNING" if m.get("class") == "WARNING" else "PASS") if m.get("available") else None,
                      sub=m.get("class", "N/A").lower())],
    }


def _series(sim: SimResult, load: LoadProfile, t_hot, dt_pack, dt_mod, uncooled, pack) -> dict:
    n = len(sim.t)
    step = max(1, n // 6000)
    sl = slice(None, None, step)
    cum = np.concatenate([[0.0], np.cumsum(sim.q_pack[:-1] * sim.dt[:-1])]) / 3.6e6
    cum_j = np.concatenate([[0.0], np.cumsum(sim.q_joule_cell[:-1] * sim.dt[:-1] * pack.n_cells)]) / 3.6e6
    s = {"t": _r(sim.t[sl], 4), "soc_pct": _r(sim.soc_pct[sl], 5), "i_cell_a": _r(sim.i_cell[sl], 5), "i_module_a": _r(sim.i_module[sl], 5),
         "i_pack_a": _r(sim.i_pack[sl], 5), "c_rate": _r(sim.c_rate[sl], 6), "r_cell_mohm": _r(sim.r_cell_ohm[sl] * 1e3, 6),
         "ocv_v": _r(sim.ocv_v[sl], 5), "v_cell_v": _r(sim.v_cell[sl], 5), "v_pack_v": _r(sim.v_pack[sl], 4),
         "q_joule_cell_w": _r(sim.q_joule_cell[sl], 5), "q_rev_cell_w": _r(sim.q_rev_cell[sl], 5), "q_cell_w": _r(sim.q_cell[sl], 5),
         "q_module_w": _r(sim.q_module[sl], 4), "q_pack_kw": _r(sim.q_pack[sl] / 1e3, 6), "p_batt_kw": _r(sim.p_terminal_w[sl] / 1e3, 5),
         "p_chem_kw": _r(sim.p_chem_w[sl] / 1e3, 5), "cum_heat_kwh": _r(cum[sl], 6), "cum_joule_kwh": _r(cum_j[sl], 6), "state": sim.state[::step],
         "step_decimation": step}
    if load.speed_kmh is not None:
        s["speed_kmh"] = _r(load.speed_kmh[sl], 3)
    if load.p_aux_w is not None:
        s["p_aux_kw"] = _r(load.p_aux_w[sl] / 1e3, 4)
    if t_hot is not None or uncooled is not None:
        s["t_cell_c"] = _r(sim.t_cell_c[sl], 4)
        if t_hot is not None:
            s["t_hot_c"] = _r(t_hot[sl], 4)
            s["dt_pack_k"] = _r(dt_pack[sl], 4)
            s["dt_module_k"] = _r(dt_mod[sl], 4)
            s["t_coolant_out_c"] = _r(sim.t_coolant_out_c[sl], 4)
            s["q_cool_kw"] = _r(sim.q_cool_w[sl] / 1e3, 5)
            s["q_amb_kw"] = _r(sim.q_amb_w[sl] / 1e3, 5)
    if uncooled is not None:
        s["t_uncooled_c"] = _r(uncooled.t_cell_c[sl], 4)
    return s


_j = jsonable        # backwards-compatible alias
