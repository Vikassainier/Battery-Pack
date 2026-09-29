"""Engineering performance checks: every check is reported as PASS / WARNING / FAIL (or N/A with the reason).

Nothing is hidden: failed and not-evaluable checks are part of the result, and thresholds come from the request
(``LimitSettings``, target temperatures, C-rate limits), never from hard-coded constants.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

import numpy as np

PASS, WARNING, FAIL, NA = "PASS", "WARNING", "FAIL", "N/A"
_RANK = {PASS: 0, NA: 0, WARNING: 1, FAIL: 2}


@dataclass
class Check:
    id: str
    name: str
    status: str
    value: str
    limit: str
    message: str
    supplementary: bool = False
    details: dict = field(default_factory=dict)
    trace_id: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def worst(*statuses: str) -> str:
    real = [s for s in statuses if s != NA]
    return max(real, key=lambda s: _RANK[s]) if real else NA


# --------------------------------------------------------------------------------------------------
def exceedance_events(t: np.ndarray, dt: np.ndarray, x: np.ndarray, thr: float) -> list[dict]:
    """Contiguous runs where x > thr. Duration = Σ Δt of the samples in the run (sample-and-hold)."""
    over = x > thr + 1e-12
    events, start = [], None
    for k, o in enumerate(over):
        if o and start is None:
            start = k
        if (not o or k == len(over) - 1) and start is not None:
            end = k if not o else k + 1
            events.append({"t_start_s": float(t[start]), "duration_s": float(np.sum(dt[start:end])), "max": float(x[start:end].max())})
            start = None
    return events


def crate_assessment(t, dt, c_abs, cont, peak, peak_dur, label) -> dict:
    """Assess a C-rate series against continuous / peak / duration limits."""
    if cont is None and peak is None:
        return {"label": label, "status": NA, "message": f"no {label} C-rate limit defined", "max_c": float(c_abs.max()) if len(c_abs) else 0.0}
    max_c = float(c_abs.max()) if len(c_abs) else 0.0
    if len(c_abs) == 0 or max_c <= 0:
        return {"label": label, "status": PASS, "message": f"no {label} current", "max_c": 0.0, "cont": cont, "peak": peak}
    lim_cont = cont if cont is not None else peak
    ev = exceedance_events(t, dt, c_abs, lim_cont)
    out: dict[str, Any] = {"label": label, "max_c": max_c, "cont": cont, "peak": peak, "peak_duration_s": peak_dur, "events": ev[:20], "n_events": len(ev)}
    if not ev:
        out.update(status=PASS, message=f"max {label} {max_c:.2f} C within the {lim_cont:g} C continuous limit")
        return out
    longest = max(e["duration_s"] for e in ev)
    out["longest_s"] = longest
    if cont is not None and peak is None:
        out.update(status=WARNING, message=f"{label} reaches {max_c:.2f} C, above the {cont:g} C continuous rating; no pulse rating is defined to justify it")
    elif peak is not None and max_c > peak + 1e-9:
        out.update(status=FAIL, message=f"{label} reaches {max_c:.2f} C, above the {peak:g} C peak rating")
    elif peak_dur is not None and longest > peak_dur + 1e-9:
        out.update(status=FAIL, message=f"{label} stays above the {cont:g} C continuous rating for {longest:.0f} s, longer than the {peak_dur:g} s allowed for pulses")
    else:
        extra = f" (longest {longest:.0f} s" + (f" of {peak_dur:g} s allowed)" if peak_dur else ")")
        out.update(status=WARNING, message=f"{label} exceeds the {cont:g} C continuous rating {len(ev)} time(s), peak {max_c:.2f} C, within the pulse rating{extra} - confirm the duty with the cell supplier")
    return out


def evaluate_checks(ctx: dict) -> list[Check]:
    """``ctx`` is assembled by the pipeline (see engine/pipeline.py)."""
    lim, pack, cell, co = ctx["limits"], ctx["pack"], ctx["cell"], ctx["coolant"]
    checks: list[Check] = []
    has_t = ctx["has_temperatures"]
    why_no_t = ctx.get("no_temperature_reason", "temperature prediction unavailable")

    # 1 ---- maximum cell temperature ------------------------------------------------------------------
    if has_t:
        t_max, tgt = ctx["t_hot_max_c"], pack.t_target_max_c
        margin = tgt - t_max
        st = FAIL if margin < 0 else (WARNING if margin < lim.thermal_margin_warn_k else PASS)
        msg = (f"Predicted hottest-cell temperature {t_max:.1f} °C vs target {tgt:g} °C: margin {margin:+.1f} K "
               f"(warning below {lim.thermal_margin_warn_k:g} K).")
        checks.append(Check("1", "Maximum cell temperature", st, f"{t_max:.1f} °C", f"≤ {tgt:g} °C", msg, details={"margin_k": margin}, trace_id="temp.t_hot_max"))
    else:
        checks.append(Check("1", "Maximum cell temperature", NA, "–", f"≤ {pack.t_target_max_c:g} °C", f"Not evaluated: {why_no_t}."))

    # 2 & 3 ---- ΔT ------------------------------------------------------------------------------------------
    for cid, name, key, tid in (("2", "Cell-to-cell temperature difference (pack)", "dt_pack_max_k", "temp.dt_pack"),
                                ("3", "Module temperature difference", "dt_module_max_k", "temp.dt_module")):
        if has_t:
            v, tgt = ctx[key], pack.target_delta_t_k
            st = FAIL if v > tgt else (WARNING if v > lim.dt_warn_fraction * tgt else PASS)
            info = ctx.get("dt_max_info", {})
            pull = (f" The maximum occurs at t = {info['t_s']:.0f} s where the coolant removes {info['q_removed_w'] / 1e3:.2f} kW against {info['q_generated_w'] / 1e3:.2f} kW generated: "
                    "it is driven by the pack being warmer than the coolant (initial pull-down), not by heat generation." if info.get("pulldown_dominated") else "")
            checks.append(Check(cid, name, st, f"{v:.1f} K", f"≤ {tgt:g} K",
                                f"Screening estimate {v:.1f} K vs target {tgt:g} K (warning above {lim.dt_warn_fraction * 100:.0f} % of target). "
                                "Includes coolant temperature rise along the flow path, heat-generation spread and flow maldistribution; verify by CFD/test." + pull,
                                details={"components": ctx.get("dt_components", {})}, trace_id=tid))
        else:
            checks.append(Check(cid, name, NA, "–", f"≤ {pack.target_delta_t_k:g} K", f"Not evaluated: {why_no_t}."))

    # 4 ---- coolant temperature ---------------------------------------------------------------------------
    limit_c = ctx["coolant_out_limit_c"]
    if has_t and ctx.get("t_out_max_c") is not None and np.isfinite(ctx["t_out_max_c"]):
        v = ctx["t_out_max_c"]
        tol = 0.2 * ctx["dt_cool_k"]
        st = PASS if v <= limit_c + 1e-9 else (WARNING if v <= limit_c + tol else FAIL)
        checks.append(Check("4", "Maximum coolant temperature", st, f"{v:.1f} °C", f"≤ {limit_c:.1f} °C",
                            f"Peak coolant outlet temperature {v:.1f} °C vs limit {limit_c:.1f} °C (inlet {co.inlet_c:g} °C).", trace_id="temp.t_out_max"))
    else:
        checks.append(Check("4", "Maximum coolant temperature", NA, "–", f"≤ {limit_c:.1f} °C", f"Not evaluated: {why_no_t if not has_t else 'no coolant loop defined'}."))

    # 5 ---- cooling capacity margin -----------------------------------------------------------------------
    m = ctx["cooling_margin"]
    if m["available"]:
        st = FAIL if m["class"] == "INSUFFICIENT" else (WARNING if m["class"] == "WARNING" else PASS)
        checks.append(Check("5", "Cooling capacity margin", st, f"{m['margin_pct']:+.1f} % ({m['class'].lower()})",
                            f"≥ {m['warn_pct']:g} % (target {m['target_pct']:g} %)",
                            f"Installed {m['installed_w'] / 1e3:.2f} kW vs required {m['required_w'] / 1e3:.2f} kW (ratio {m['ratio']:.2f}); basis: {ctx['capacity_basis']}.",
                            details=m, trace_id="margin.cooling"))
    else:
        checks.append(Check("5", "Cooling capacity margin", NA, "–", "", f"Not evaluated: {ctx.get('margin_reason', 'no installed cooling capacity or cold-plate capability available')}."))

    # 6 ---- absolute cell limits (datasheet) ---------------------------------------------------------------
    if has_t and (cell.t_op_max_c is not None or cell.t_rec_max_c is not None or cell.t_op_min_c is not None):
        sts, notes = [], []
        t_max, t_min = ctx["t_hot_max_c"], ctx["t_cell_min_c"]
        if cell.t_op_max_c is not None:
            s = FAIL if t_max > cell.t_op_max_c else PASS
            sts.append(s)
            notes.append(f"max {t_max:.1f} °C vs absolute limit {cell.t_op_max_c:g} °C")
        if cell.t_rec_max_c is not None:
            s = WARNING if t_max > cell.t_rec_max_c else PASS
            sts.append(s)
            if s == WARNING:
                notes.append(f"above the recommended {cell.t_rec_max_c:g} °C (accelerated ageing)")
        if cell.t_op_min_c is not None and t_min < cell.t_op_min_c:
            sts.append(FAIL)
            notes.append(f"min {t_min:.1f} °C below the {cell.t_op_min_c:g} °C operating limit")
        if ctx["charging_occurs"]:
            if cell.t_charge_min_c is not None and t_min < cell.t_charge_min_c:
                sts.append(FAIL)
                notes.append(f"charging/regen with cells at {t_min:.1f} °C, below the {cell.t_charge_min_c:g} °C charge limit (lithium plating risk)")
            if cell.t_charge_max_c is not None and t_max > cell.t_charge_max_c:
                sts.append(FAIL)
                notes.append(f"charging/regen with cells at {t_max:.1f} °C, above the {cell.t_charge_max_c:g} °C charge limit")
        lim_txt = " / ".join(x for x in (f"≤ {cell.t_op_max_c:g} °C" if cell.t_op_max_c is not None else "", f"rec. ≤ {cell.t_rec_max_c:g} °C" if cell.t_rec_max_c is not None else "") if x)
        checks.append(Check("6", "Maximum allowable cell temperature (datasheet)", worst(*sts) if sts else NA, f"{t_max:.1f} °C", lim_txt,
                            "; ".join(notes) + ".", details={"t_min_c": t_min}))
    else:
        checks.append(Check("6", "Maximum allowable cell temperature (datasheet)", NA, "–", "",
                            f"Not evaluated: {why_no_t if not has_t else 'no datasheet temperature limits available'}."))

    # 7 ---- C-rate -------------------------------------------------------------------------------------------
    ca = ctx["crate"]
    subs = [ca[k] for k in ("discharge", "charge", "regen") if ca.get(k) and ca[k]["status"] != NA]
    if subs:
        val = "; ".join(f"{s['label']} {s['max_c']:.2f} C" for s in subs)
        lim_t = "; ".join(f"{s['label']} ≤ {s['cont']:g}" + (f" (pulse {s['peak']:g} C, {s['peak_duration_s']:g} s)" if s.get("peak") and s.get("peak_duration_s") else (f" (pulse {s['peak']:g} C)" if s.get("peak") else "")) + " C" for s in subs if s.get("cont") is not None)
        checks.append(Check("7", "Maximum allowable C-rate", worst(*[s["status"] for s in subs]), val, lim_t, " | ".join(s["message"] for s in subs),
                            details={"assessments": subs, "limits_from": ca.get("limits_from", "")}, trace_id="heat.c_max"))
    else:
        checks.append(Check("7", "Maximum allowable C-rate", NA, "–", "", "Not evaluated: no C-rate limits were defined (step 5) and the cell datasheet gives none."))

    # 8 ---- pressure drop / hydraulics -------------------------------------------------------------------------
    h = ctx.get("hydraulics")
    if h is not None:
        fl = h.flags
        st = FAIL if any(f["severity"] == "fail" for f in fl) else (WARNING if any(f["severity"] == "warning" for f in fl) else PASS)
        msgs = [f["message"] for f in fl if f["severity"] in ("fail", "warning")]
        checks.append(Check("8", "Cooling-system pressure drop", st, f"{h.dp_total_pa / 1e3:.0f} kPa total ({h.dp_plates_total_pa / 1e3:.1f} kPa plates)",
                            f"plates ≤ {lim.plate_dp_warn_kpa:g} kPa, loop ≤ {lim.loop_dp_warn_kpa:g} kPa",
                            " ".join(msgs) if msgs else f"Pressure drop, velocity ({h.velocity_m_s:.2f} m/s) and flow ({h.q_pack_lpm:.1f} L/min) are within the configured limits.",
                            details={"flags": fl}, trace_id="hyd.dp_total"))
    else:
        checks.append(Check("8", "Cooling-system pressure drop", NA, "–", "", "Not evaluated: no cold plate defined (enable it on the thermal & cooling step)."))

    # ------------------------------------------------------------------------------------------------------------
    # supplementary checks (always shown)
    # ------------------------------------------------------------------------------------------------------------
    ent = ctx["entropic"]
    if ent.included and ent.mode in ("table", "map"):
        checks.append(Check("S1", "Entropic (reversible) heat", PASS, ent.mode, "", ent.status, True))
    elif ent.included:
        checks.append(Check("S1", "Entropic (reversible) heat", WARNING, "user estimate", "", ent.status, True))
    else:
        bound = ctx.get("entropic_bound_w")
        checks.append(Check("S1", "Entropic (reversible) heat", WARNING, "not included", "",
                            ent.status + (f" Indicative magnitude left out (±0.2 mV/K): up to ±{bound:.1f} W per cell at peak current." if bound else ""), True))
    sim = ctx["sim_flags"]
    if sim.get("first_soc_below_0_s") is not None or sim.get("first_soc_above_100_s") is not None:
        checks.append(Check("S2", "SOC window", FAIL, f"{sim['soc_min_seen']:.1f}…{sim['soc_max_seen']:.1f} %", "0…100 %",
                            "The load exceeds the pack's energy: SOC leaves 0-100 % - results beyond that point are not physical. Reduce the duty or raise the initial SOC.", True))
    elif sim.get("first_soc_below_min_s") is not None or sim.get("first_soc_above_max_s") is not None:
        checks.append(Check("S2", "SOC window", WARNING, f"{sim['soc_min_seen']:.1f}…{sim['soc_max_seen']:.1f} %", f"{pack.soc_min_pct:g}…{pack.soc_max_pct:g} %",
                            "SOC leaves the configured operating window during the cycle.", True))
    else:
        checks.append(Check("S2", "SOC window", PASS, f"{sim['soc_min_seen']:.1f}…{sim['soc_max_seen']:.1f} %", f"{pack.soc_min_pct:g}…{pack.soc_max_pct:g} %",
                            "SOC stays inside the operating window.", True))
    if sim.get("n_infeasible", 0) > 0:
        checks.append(Check("S3", "Power feasibility", FAIL, f"{sim['n_infeasible']} samples", "0",
                            "The requested battery power exceeds the maximum power transfer of the pack (OCV²/4R) in these samples - the current was limited and the load is not achievable.", True))
    else:
        checks.append(Check("S3", "Power feasibility", PASS, "0 samples", "0", "All requested power is deliverable by the cells (real solution of the terminal-power equation).", True))
    vmin, vmax = ctx["v_cell_min"], ctx["v_cell_max"]
    vs, vn = [], []
    if cell.v_min is not None and vmin < cell.v_min:
        vs.append(FAIL)
        vn.append(f"cell voltage falls to {vmin:.2f} V, below the {cell.v_min:g} V minimum")
    if cell.v_max is not None and vmax > cell.v_max:
        vs.append(FAIL)
        vn.append(f"cell voltage rises to {vmax:.2f} V, above the {cell.v_max:g} V maximum")
    checks.append(Check("S4", "Cell voltage window", worst(*vs) if vs else (PASS if cell.v_min is not None or cell.v_max is not None else NA),
                        f"{vmin:.2f}…{vmax:.2f} V", f"{cell.v_min if cell.v_min is not None else '–'}…{cell.v_max if cell.v_max is not None else '–'} V",
                        "; ".join(vn) + "." if vn else "Terminal voltage stays within the cell limits (V = OCV − I·R).", True))
    fl = ctx.get("flow_adequacy")
    if fl is not None:
        st = PASS if fl["ratio"] >= 1.0 else (WARNING if fl["ratio"] >= 0.9 else FAIL)
        checks.append(Check("S5", "Coolant flow adequacy", st, f"{fl['actual_lpm']:.1f} L/min", f"≥ {fl['required_lpm']:.1f} L/min",
                            f"Specified flow is {fl['ratio'] * 100:.0f} % of the required flow for the design load and allowable coolant ΔT.", True))
    ru = ctx.get("resistance_range", [])
    n_out = sum(u["outside_data_range"] for u in ru)
    checks.append(Check("S6", "Resistance / OCV data range", WARNING if n_out else PASS, f"{n_out} evaluations outside data", "0",
                        ("Extrapolation was explicitly enabled and was used - results outside the tabulated range are not backed by data." if n_out
                         else "All look-ups stayed inside the tabulated data range."), True, details={"usage": ru}))
    dq = ctx["data_quality"]
    checks.append(Check("S7", "Data quality", WARNING if dq["n_assumed"] else PASS, f"{dq['n_assumed']} assumed / {dq['n_low']} low-confidence", "",
                        (f"{dq['n_assumed']} parameter(s) are engineering assumptions not confirmed by the user - see 'Assumptions & Data Quality'." if dq["n_assumed"]
                         else "All parameters are user-provided, from the datasheet, or calculated."), True))
    return checks
