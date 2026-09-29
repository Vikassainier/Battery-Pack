"""Sizing engine: design heat load, cooling capacity, recommended inlet temperature, pump and radiator estimates, margins.

Q_relevant  = chosen philosophy (peak | moving average | sustained | drive-cycle)
Q_required  = Q_relevant + Q_ambient-gain (design)      the load the coolant loop must remove
Q_design    = Q_required × SF                           recommended cooling capacity (safety factor)

Peak vs sustained
-----------------
The instantaneous heat peak is a short transient: the pack's thermal mass absorbs it (ΔT = Q·t/C), so sizing the
heat exchanger for the peak over-sizes it. What the cooling system must reject is the *sustained /
thermally-equivalent* load, otherwise the pack temperature ratchets up. The four philosophies bracket this range;
all four are reported side by side and the consultant chooses which one governs.
"""
from __future__ import annotations

import math

from .schemas import LimitSettings
from .trace import TraceLog, fmt
from .units import AIR_CP

RHO_AIR_RADIATOR = 1.16          # kg/m³ at ~30 °C (air-side volume flow estimate only)
RADIATOR_APPROACH_K = 5.0        # minimum coolant-inlet-to-ambient approach for passive (radiator) rejection
PUMP_FLOW_ALLOWANCE = 1.10       # duty-point allowances suggested on top of the calculated pump duty
PUMP_PRESSURE_ALLOWANCE = 1.20

PHILOSOPHY_LABEL = {"peak": "Peak heat load", "moving_average": "Moving-average heat load",
                    "sustained": "Sustained heat load", "drive_cycle": "Drive-cycle thermal load"}
PHILOSOPHY_TEXT = {
    "peak": "Q_relevant = maximum instantaneous pack heat. Most conservative: ignores the thermal mass that absorbs short peaks, "
            "so it usually over-sizes the cooling system.",
    "moving_average": "Q_relevant = maximum trailing moving average of the pack heat over a window equal to the pack's thermal time constant "
                      "(or the value you set). Peaks shorter than the window are smoothed by the thermal mass.",
    "sustained": "Q_relevant = steady-state heat at the rated continuous discharge / charge C-rate, worst case over the SOC window at the target "
                 "temperature. Represents long climbs or DC fast charging where thermal mass no longer helps.",
    "drive_cycle": "Q_relevant = smallest constant heat-removal capacity that keeps the (lumped) pack below the target temperature over the whole "
                   "cycle, accounting for thermal mass, start temperature and ambient exchange. Between the average and the peak heat.",
}
PEAK_VS_SUSTAINED = (
    "Peak thermal load is the highest instantaneous heat generation; it lasts seconds to minutes and is largely absorbed by the thermal "
    "mass of the cells (temperature rise = Q·t / C). The sustained cooling requirement is the heat that must be continuously rejected to "
    "stop the pack temperature drifting upwards over a full cycle or a long duty. Sizing for the peak alone over-sizes the exchanger, "
    "pump and refrigeration; sizing for the average alone lets peaks push cells over the limit. The philosophies below bracket the "
    "range - the temperature prediction (Checks 1-4) shows what actually happens with the chosen design."
)


def design_load(philosophy: str, cands: dict[str, dict], sf: float, q_amb_gain_w: float, tr: TraceLog | None = None,
                sf_source: str = "assumed", amb_inputs: list[str] | None = None) -> dict:
    """Combine the candidate loads into the required/design capacity."""
    sel = cands[philosophy]
    if not sel.get("available"):
        raise ValueError(f"Design philosophy '{philosophy}' is not available: {sel.get('note', 'missing data')}")
    q_rel = sel["value_w"]
    gain = 0.0 if philosophy == "drive_cycle" else q_amb_gain_w      # the drive-cycle simulation already includes ambient exchange
    q_req = q_rel + gain
    q_des = q_req * sf
    out = {"philosophy": philosophy, "label": PHILOSOPHY_LABEL[philosophy], "candidates": cands, "q_relevant_w": q_rel,
           "q_ambient_gain_w": gain, "q_required_w": q_req, "safety_factor": sf, "q_design_w": q_des,
           "explanation": PHILOSOPHY_TEXT[philosophy], "peak_vs_sustained": PEAK_VS_SUSTAINED}
    if tr is not None:
        tr.calc("design.q_relevant", f"Relevant heat load ({PHILOSOPHY_LABEL[philosophy]})", q_rel / 1e3, "kW", "Q_relevant = " + PHILOSOPHY_TEXT[philosophy].split(":")[0][:80],
                sel.get("substitution", ""), sel.get("trace_inputs", []), note=sel.get("note", ""))
        tr.calc("design.q_amb", "Ambient heat gain at target temperature", gain / 1e3, "kW", "Q_amb = UA·max(0, T_amb − T_target)", "",
                amb_inputs or [], note="0 for the drive-cycle philosophy: ambient exchange is already inside the transient simulation" if philosophy == "drive_cycle" else "")
        tr.result("design.q_required", "Required cooling capacity", q_req / 1e3, "kW", "Q_required = Q_relevant + Q_ambient-gain",
                  f"{fmt(q_rel / 1e3)} + {fmt(gain / 1e3)}", ["design.q_relevant", "design.q_amb"])
        tr.input("design.sf", "Thermal safety factor", sf, "-", sf_source)
        tr.result("design.q_design", "Design (recommended) cooling capacity", q_des / 1e3, "kW", "Q_design = Q_required × SF",
                  f"{fmt(q_req / 1e3)} × {fmt(sf)}", ["design.q_required", "design.sf"])
    return out


def recommended_inlet_temperature(t_target_c: float, hot_offset_k: float, q_design_w: float, g_cool_eff_w_k: float, t_amb_c: float) -> dict:
    """Highest coolant inlet temperature that still holds the hottest cell at the target at the design load (steady state):

        T_in,max = T_target − ΔT_hot-cell-offset − Q_design / (ε·ṁ·cp)
    """
    d = q_design_w / g_cool_eff_w_k if g_cool_eff_w_k > 0 else float("inf")
    t_max = t_target_c - hot_offset_k - d
    rec = math.floor(t_max * 2.0) / 2.0 if math.isfinite(t_max) else float("nan")
    return {"t_in_max_c": t_max, "t_in_recommended_c": rec, "cell_to_coolant_dt_k": d, "hot_offset_k": hot_offset_k,
            "below_ambient": bool(t_max < t_amb_c), "chiller_required": bool(t_max < t_amb_c + RADIATOR_APPROACH_K)}


def pump_recommendation(flow_lpm: float, dp_total_pa: float, p_hyd_w: float, p_el_w: float, head_m: float) -> dict:
    return {"flow_lpm": flow_lpm, "dp_bar": dp_total_pa / 1e5, "dp_kpa": dp_total_pa / 1e3, "p_hydraulic_w": p_hyd_w, "p_electrical_w": p_el_w,
            "head_m": head_m, "duty_flow_lpm": flow_lpm * PUMP_FLOW_ALLOWANCE, "duty_dp_bar": dp_total_pa / 1e5 * PUMP_PRESSURE_ALLOWANCE,
            "allowances": f"Duty point = calculated flow ×{PUMP_FLOW_ALLOWANCE:g} and pressure ×{PUMP_PRESSURE_ALLOWANCE:g} (typical design allowances)."}


def radiator_estimate(q_design_w: float, p_hyd_w: float, t_in_c: float, dt_cool_k: float, t_amb_c: float, air_dt_k: float,
                      coolant_flow_lpm: float) -> dict:
    """Indicative heat-rejection requirement. Final radiator sizing needs air-side and exchanger-design data."""
    q_reject = q_design_w + p_hyd_w                                # pump hydraulic power ends up as heat in the coolant
    t_hot = t_in_c + dt_cool_k                                     # coolant entering the radiator ≈ pack outlet
    itd = t_hot - t_amb_c
    approach = t_in_c - t_amb_c
    out = {"q_reject_w": q_reject, "coolant_flow_lpm": coolant_flow_lpm, "coolant_inlet_to_radiator_c": t_hot, "coolant_outlet_from_radiator_c": t_in_c,
           "air_inlet_c": t_amb_c, "itd_k": itd, "approach_k": approach, "air_dt_assumed_k": air_dt_k}
    if itd > 0:
        out["ua_required_w_k"] = q_reject / itd                    # capacity per kelvin of inlet temperature difference
        out["effectiveness_required"] = dt_cool_k / itd
    else:
        out["ua_required_w_k"] = None
        out["effectiveness_required"] = None
    m_air = q_reject / (AIR_CP * air_dt_k) if air_dt_k > 0 else float("nan")
    out["air_mass_flow_kg_s"] = m_air
    out["air_volume_flow_m3_s"] = m_air / RHO_AIR_RADIATOR
    out["passive_feasible"] = bool(approach >= RADIATOR_APPROACH_K and (out["effectiveness_required"] or 1) < 0.8)
    notes = []
    if approach < RADIATOR_APPROACH_K:
        rel = f"{abs(approach):.1f} K {'below' if approach < 0 else 'above'}"
        notes.append(f"The specified coolant supply temperature of {t_in_c:.1f} °C is {rel} the {t_amb_c:.1f} °C ambient (a radiator needs at least "
                     f"{RADIATOR_APPROACH_K:g} K approach): a radiator alone cannot deliver it - an active refrigeration circuit (chiller) is required, "
                     "or a warmer supply temperature must be accepted (see the recommended maximum inlet temperature).")
    elif out["effectiveness_required"] is not None and out["effectiveness_required"] >= 0.8:
        notes.append(f"Required radiator effectiveness {out['effectiveness_required']:.2f} is very high: expect a large radiator or a chiller.")
    notes.append("Air-side assumptions: air enters at the ambient temperature, air temperature rise "
                 f"{air_dt_k:g} K, density {RHO_AIR_RADIATOR:g} kg/m³, no recirculation, pump heat included. "
                 "Final radiator sizing requires detailed air-side data (available face area, fan / ram-air flow, fin geometry, "
                 "coolant-side pressure drop) and an exchanger design calculation.")
    out["notes"] = notes
    return out


# ------------------------------------------------------------------------------------------------
# margins (thresholds come from LimitSettings - nothing hard-coded)
# ------------------------------------------------------------------------------------------------
def cooling_margin(installed_w: float | None, required_w: float, limits: LimitSettings) -> dict:
    """Cooling margin = installed / required. Class from the configurable limits (default: <0 % insufficient,
    0-10 % warning, 10-20 % moderate, >=20 % adequate)."""
    if installed_w is None or required_w <= 0:
        return {"available": False, "ratio": None, "margin_pct": None, "class": "N/A"}
    ratio = installed_w / required_w
    m = (ratio - 1.0) * 100.0
    if m < 0:
        cls = "INSUFFICIENT"
    elif m < limits.cooling_margin_warn_pct:
        cls = "WARNING"
    elif m < limits.cooling_margin_target_pct:
        cls = "MODERATE"
    else:
        cls = "ADEQUATE"
    return {"available": True, "ratio": ratio, "margin_pct": m, "class": cls, "installed_w": installed_w, "required_w": required_w,
            "warn_pct": limits.cooling_margin_warn_pct, "target_pct": limits.cooling_margin_target_pct}


def thermal_margin(t_allow_c: float | None, t_pred_c: float | None, limits: LimitSettings) -> dict:
    if t_allow_c is None or t_pred_c is None:
        return {"available": False, "margin_k": None, "class": "N/A"}
    m = t_allow_c - t_pred_c
    cls = "INSUFFICIENT" if m < 0 else ("WARNING" if m < limits.thermal_margin_warn_k else "ADEQUATE")
    return {"available": True, "margin_k": m, "class": cls, "t_allow_c": t_allow_c, "t_pred_c": t_pred_c, "warn_k": limits.thermal_margin_warn_k}
