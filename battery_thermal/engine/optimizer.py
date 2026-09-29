"""Cooling-design optimiser (step 10 of the workflow).

Searches cold-plate / flow variants for the lowest pump power that still satisfies the thermal and hydraulic
constraints. The heat series from the base run is held fixed (temperature feedback on resistance neglected), so each
candidate needs only the cold-plate model, the hydraulics and the lumped thermal integration - a few milliseconds.

Constraints (all configurable through the request): hottest-cell temperature ≤ target (with the thermal-margin
warning band), cell-to-cell ΔT ≤ target, coolant outlet ≤ limit, plate ΔP, loop ΔP, channel velocity.
"""
from __future__ import annotations

import itertools
import math

import numpy as np

from .coldplate import ColdPlateError, analyze_cold_plate
from .coolant import coolant_properties
from .pipeline import run_analysis
from .pressure_drop import hydraulics
from .schemas import AnalysisRequest, ColdPlateSpec
from .thermal import ThermalNetwork, cell_to_cell_dt, thermal_step
from .units import kgs_to_lpm, lpm_to_kgs

VARIABLES = {"flow_lpm": "Coolant flow", "channel_height_mm": "Channel height", "channel_width_mm": "Channel width",
             "n_channels": "Number of channels", "tim_thickness_mm": "TIM thickness"}


def _evaluate(req: AnalysisRequest, art: dict, plate: ColdPlateSpec, flow_lpm: float) -> dict:
    props = art["props"]
    m = lpm_to_kgs(flow_lpm, props.rho)
    sim, N = art["sim"], art["pack"].n_cells
    cp = analyze_cold_plate(plate, props, m, N)
    hyd = hydraulics(plate, props, m, req.pump.overall_efficiency, req.limits)
    net = ThermalNetwork(art["c_pack"], cp.g_cool_eff_w_k, art["t_in"], art["ua"], art["t_amb"], cp.effectiveness, m * props.cp)
    t, q = sim.t, sim.q_pack
    temp = req.pack.t_initial_c
    th = req.thermal
    spread, mald = th.cell_heat_spread_pct / 100.0, th.flow_maldistribution_pct / 100.0
    series_flow = plate.plate_arrangement == "series"
    t_hot_max = dt_pack_max = t_out_max = -1e9
    for k in range(len(t)):
        dt = t[k + 1] - t[k] if k < len(t) - 1 else 0.0
        t_next, t_mean, qc, _ = thermal_step(temp, q[k], dt, net)
        u = cell_to_cell_dt(abs(qc), net.m_cp_w_k, max(q[k] / N, 0.0), cp.r_total, plate.n_plates, series_flow, spread, mald)
        t_hot_max = max(t_hot_max, temp + u["hot_cell_offset_k"])
        dt_pack_max = max(dt_pack_max, u["dt_pack_k"])
        t_out_max = max(t_out_max, art["t_in"] + cp.effectiveness * (temp - art["t_in"]))
        temp = t_next
    lim = req.limits
    out_limit = req.coolant.max_outlet_c if req.coolant.max_outlet_c is not None else art["t_in"] + art["dt_cool"]
    viol = {"t_hot": max(0.0, t_hot_max - req.pack.t_target_max_c), "dt_pack": max(0.0, dt_pack_max - req.pack.target_delta_t_k),
            "t_out": max(0.0, t_out_max - out_limit), "dp_plate": max(0.0, hyd.dp_plates_total_pa / 1e3 - lim.plate_dp_warn_kpa) / lim.plate_dp_warn_kpa,
            "dp_loop": max(0.0, hyd.dp_total_pa / 1e3 - lim.loop_dp_warn_kpa) / lim.loop_dp_warn_kpa,
            "velocity": max(0.0, hyd.velocity_m_s - lim.max_velocity_warn_m_s) / lim.max_velocity_warn_m_s}
    return {"t_hot_max_c": t_hot_max, "dt_pack_max_k": dt_pack_max, "t_out_max_c": t_out_max, "dp_plates_kpa": hyd.dp_plates_total_pa / 1e3,
            "dp_total_kpa": hyd.dp_total_pa / 1e3, "velocity_m_s": hyd.velocity_m_s, "reynolds": hyd.reynolds, "regime": hyd.regime,
            "p_hyd_w": hyd.p_hyd_w, "p_elec_w": hyd.p_elec_w, "r_total_k_w": cp.r_total, "effectiveness": cp.effectiveness,
            "violations": viol, "violation_total": float(sum(viol.values())), "feasible": all(v <= 1e-9 for v in viol.values())}


def optimize_cooling(req: AnalysisRequest, variables: list[str] | None = None, n_top: int = 8) -> dict:
    if req.cold_plate is None:
        return {"ok": False, "message": "Define the cold plate first (thermal & cooling step)."}
    art: dict = {}
    base = run_analysis(req, mode="scalars", _artefacts=art)
    if base.get("status") not in ("ok", "completed_with_errors") or not art.get("has_t"):
        return {"ok": False, "issues": base.get("issues", []), "message": "The base case must run with a temperature prediction (cell mass, specific heat and a cold plate)."}
    variables = variables or ["flow_lpm", "channel_height_mm", "channel_width_mm", "n_channels", "tim_thickness_mm"]
    p0 = req.cold_plate
    flow0 = base["hydraulics"]["q_pack_lpm"]
    grids = {
        "flow_lpm": [flow0 * f for f in (0.5, 0.75, 1.0, 1.5, 2.0, 3.0)] if "flow_lpm" in variables else [flow0],
        "channel_height_mm": [p0.channel_height_mm * f for f in (0.75, 1.0, 1.5, 2.0)] if "channel_height_mm" in variables else [p0.channel_height_mm],
        "channel_width_mm": [p0.channel_width_mm * f for f in (0.75, 1.0, 1.5)] if "channel_width_mm" in variables else [p0.channel_width_mm],
        "n_channels": sorted({max(1, round(p0.n_channels * f)) for f in (0.5, 1.0, 2.0)}) if "n_channels" in variables else [p0.n_channels],
        "tim_thickness_mm": [p0.tim_thickness_mm * f for f in (0.5, 1.0)] if "tim_thickness_mm" in variables else [p0.tim_thickness_mm],
    }
    cands = []
    for flow, h, w, n, tim in itertools.product(*[grids[k] for k in ("flow_lpm", "channel_height_mm", "channel_width_mm", "n_channels", "tim_thickness_mm")]):
        plate = p0.model_copy(update={"channel_height_mm": h, "channel_width_mm": w, "n_channels": n, "tim_thickness_mm": tim})
        try:
            m = _evaluate(req, art, plate, flow)
        except (ColdPlateError, ValueError, ZeroDivisionError):
            continue
        cands.append({"design": {"flow_lpm": flow, "channel_height_mm": h, "channel_width_mm": w, "n_channels": n, "tim_thickness_mm": tim}, **m})
    base_eval = _evaluate(req, art, p0, flow0)
    feas = sorted([c for c in cands if c["feasible"]], key=lambda c: (c["p_elec_w"], c["design"]["flow_lpm"]))
    infeas = sorted([c for c in cands if not c["feasible"]], key=lambda c: c["violation_total"])
    return {"ok": True, "n_evaluated": len(cands), "n_feasible": len(feas), "base": {"design": {"flow_lpm": flow0, "channel_height_mm": p0.channel_height_mm,
            "channel_width_mm": p0.channel_width_mm, "n_channels": p0.n_channels, "tim_thickness_mm": p0.tim_thickness_mm}, **base_eval},
            "best": feas[:n_top], "closest_infeasible": infeas[:3] if not feas else [], "variables": {k: VARIABLES[k] for k in variables},
            "note": "Heat series held fixed from the base run; constraints from the request (targets, limits). Minimum pump electrical power objective.",
            "objective": "minimise pump electrical power subject to all constraints"}
