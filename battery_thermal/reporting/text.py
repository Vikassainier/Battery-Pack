"""Narrative content shared by the PDF and Excel reports: methodology, recommendations, limitations."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

METHODOLOGY: list[tuple[str, list[str]]] = [
    ("7.1 Calculation chain", [
        "The analysis follows the physical chain  Electrical load → Cell heat generation → Thermal accumulation → Cooling requirement → Cooling-system sizing. "
        "Electrical energy drawn from the battery is not heat; only the resistive loss and the reversible entropic term become heat.",
    ]),
    ("7.2 Electrical model", [
        "Pack quantities: V_pack = Ns·V_cell, C_pack = Np·C_cell, E_pack = Ns·Np·C_cell·V_nom; cell current I_cell = I_pack/Np; module current I_module = I_pack/Mp; C-rate = I_cell/C_cell (positive = discharge).",
        "Battery power to cell current: with p = P_batt/(Ns·Np) and terminal voltage V = OCV − I·R, the current solves R·I² − OCV·I + p = 0, I = 2p/(OCV + √(OCV² − 4Rp)). "
        "If OCV² < 4Rp the requested power exceeds the maximum power transfer; the current is limited and the step is flagged as infeasible.",
        "SOC by coulomb counting with sample-and-hold current: SOC_{k+1} = SOC_k − I_k·Δt_k/(3600·C_eff)·100. Battery power from vehicle speed: F = m(1+ε)a + Crr·m·g·cosθ + ½ρCd·A·v² + m·g·sinθ, "
        "P_wheel = F·v, P_batt = P_wheel/η (or P_wheel·η·f_regen when braking) + P_aux.",
    ]),
    ("7.3 Resistance model", [
        "Level 1: R = constant. Level 2: R(SOC). Level 3: R(T). Level 4: R(SOC,T) from a bilinear map, or the separable form R_SOC(SOC)·R_T(T)/R_T(T_ref) when only 1-D tables exist. "
        "Tables are interpolated linearly. Queries outside the tabulated range are blocked unless clamp or linear extrapolation is explicitly enabled; any use of extrapolation is reported.",
    ]),
    ("7.4 Heat-generation model", [
        "Joule heat Q_joule = I²·R per cell; reversible heat Q_rev = −I·T·dU/dT (T in kelvin) from the dU/dT table, from the OCV map by finite difference, or from a user estimate. "
        "Entropic heat is never dropped silently: if it cannot be calculated the result states so and bounds what was left out. Q_cell = Q_joule + Q_rev; Q_module = cells_per_module·Q_cell; "
        "Q_pack = Ns·Np·Q_cell. Energy integrals use sample-and-hold: E = Σ Q_k·Δt_k.",
    ]),
    ("7.5 Thermal model", [
        "The pack is a lumped thermal mass C = N·m_cell·cp + C_extra: C·dT/dt = Q_gen − G_c·(T − T_in) − G_a·(T − T_amb) with G_c = ε·ṁ·cp,cool, ε = 1 − exp(−NTU), NTU = G_plate/(ṁ·cp,cool), "
        "and G_a the pack-to-ambient conductance. Over each time step the equation is linear and is integrated exactly: T_{k+1} = T_eq + (T_k − T_eq)·exp(−Δt/τ), τ = C/(G_c + G_a). "
        "Cell-to-cell non-uniformity is a screening estimate: coolant temperature rise along the flow path, heat-generation spread and flow maldistribution.",
    ]),
    ("7.6 Design heat load", [
        "Four philosophies are always evaluated: peak (maximum instantaneous pack heat), moving average (maximum trailing average over the chosen window), sustained (steady-state heat at the rated continuous "
        "discharge/charge C-rate, worst case over the SOC window at the target temperature) and drive-cycle (smallest constant heat-removal capacity that keeps the lumped pack below the target temperature over the cycle). "
        "Q_required = Q_relevant + Q_ambient-gain; Q_design = Q_required × SF. Peak heat is not automatically the required cooling capacity: short peaks are absorbed by thermal mass, "
        "whereas the sustained load must be rejected continuously.",
    ]),
    ("7.7 Cooling requirement, cold plate and hydraulics", [
        "Coolant flow: ṁ = Q/(cp·ΔT); V̇ = ṁ/ρ. Cold-plate resistance chain per cell: R_total = R_contact + R_TIM + R_plate + R_conv with R_TIM = t/(k·A), R_plate = t/(k·A), "
        "R_conv = cells_per_plate/(h·η_fin·A_wet). U = 1/(R_total·A_ref) - a thermal resistance [K/W] is an absolute property of a specific geometry, while U [W/(m²·K)] normalises by a reference area.",
        "Channel correlations: laminar (Re < 2300) Shah-London f·Re and Nu for rectangular ducts; turbulent (Re > 4000) Haaland friction factor and Gnielinski Nusselt number; linear blend in the transition region. "
        "ΔP = f·(L/D_h)·½ρv² + K·½ρv² + external losses; hydraulic power = ΔP·V̇; electrical pump power = P_hyd/η_pump.",
    ]),
    ("7.8 Checks, margins and sensitivity", [
        "Eight engineering checks (maximum cell temperature, cell-to-cell ΔT, module ΔT, coolant temperature, cooling margin, absolute cell limits, C-rate, pressure drop) are reported as PASS / WARNING / FAIL; "
        "checks that cannot be evaluated are reported as N/A with the reason. Margin limits are configurable. Sensitivity is one-at-a-time: each parameter is perturbed and the whole analysis re-run.",
    ]),
]

LIMITATIONS = [
    "Screening-level tool. Temperatures come from a lumped (uniform-temperature) thermal model; cell-to-cell and module ΔT are estimates, not a substitute for CFD, a 1-D flow-network model or test.",
    "Cell data are only as good as the datasheet: DC resistance depends on SOC, temperature, pulse length and ageing; the resistance scale factor represents end-of-life growth only if set by the user.",
    "Entropic heat requires dU/dT data. Without it the term is excluded or estimated (reported explicitly).",
    "Heat generation excludes busbar/joint losses, cell-to-cell current-sharing effects beyond the stated spread, side reactions and self-heating from degradation.",
    "Coolant properties use built-in correlations (ρ, cp ≈ ±2 %, k ≈ ±5-10 %, μ ≈ ±10-15 %); supplier data should replace them for final design. Properties are evaluated at the mean design coolant temperature and held constant.",
    "Heat-transfer and pressure-drop correlations are for fully developed single-phase flow in rectangular channels; developing-flow, manifold and header effects, fin efficiency (unless entered) and bends are not modelled beyond a lumped loss coefficient.",
    "The drive-cycle sizing philosophy holds the heat series fixed while searching for the minimum constant capacity; the design optimiser does the same. Temperature feedback on resistance is included in the main simulation.",
    "Radiator/chiller figures are indicative heat-rejection requirements. Final radiator sizing requires detailed air-side and exchanger-design information (face area, fan/ram-air flow, fin geometry, coolant-side pressure drop).",
    "Time integration is sample-and-hold; short spikes between samples of a coarsely sampled cycle are not represented.",
    "Results depend on the confirmed inputs. Parameters marked Assumed / Low confidence in the assumptions register have not been verified and must be confirmed before design release.",
]


def report_meta(req, res) -> dict:
    payload = json.dumps(req.model_dump(mode="json"), sort_keys=True, default=str).encode()
    h = hashlib.sha1(payload).hexdigest()[:6].upper()
    now = datetime.now(timezone.utc)
    pn = (req.project.project_no or "NA").replace(" ", "")
    return {"report_id": f"BT-{pn}-{now:%Y%m%d}-{h}", "date": f"{now:%d %B %Y}", "iso": now.isoformat(timespec="seconds"), "hash": h}


def recommendations(res: dict, req) -> list[dict]:
    """Rule-based engineering recommendations derived from the results (each item: level, title, text)."""
    out: list[dict] = []
    add = lambda level, title, text: out.append({"level": level, "title": title, "text": text})  # noqa: E731
    d, S, C = res["design"], res["sizing"], res["cooling"]
    cap = S["capacity"]
    add("info", "Cooling requirement",
        f"Under the '{d['label']}' philosophy the pack must reject {cap['required_kw']:.2f} kW; with the safety factor {cap['safety_factor']:g} the recommended cooling capacity is "
        f"{cap['design_kw']:.2f} kW (≥ {cap['recommended_kw']:.1f} kW installed) at a coolant flow of {C['pack']['lpm']:.1f} L/min for a {C['dt_k']:.1f} K coolant temperature rise. "
        f"For comparison the peak heat is {d['candidates']['peak']['value_w'] / 1e3:.2f} kW"
        + (f", the moving-average {d['candidates']['moving_average']['value_w'] / 1e3:.2f} kW" if d['candidates']['moving_average']['available'] else "")
        + (f" and the sustained (continuous C-rate) load {d['candidates']['sustained']['value_w'] / 1e3:.2f} kW." if d['candidates']['sustained']['available'] else "."))
    by = {c["id"]: c for c in res["checks"]}
    fails = [c for c in res["checks"] if c["status"] == "FAIL"]
    warns = [c for c in res["checks"] if c["status"] == "WARNING" and not c["supplementary"]]
    for c in fails:
        add("fail", f"Check {c['id']} FAILED - {c['name']}", c["message"] + " " + _remedy(c["id"]))
    for c in warns:
        add("warning", f"Check {c['id']} warning - {c['name']}", c["message"] + " " + _remedy(c["id"]))
    na = [c for c in res["checks"] if c["status"] == "N/A" and not c["supplementary"]]
    if na:
        add("warning", "Checks not evaluated", "The following checks could not be evaluated: " + "; ".join(f"Check {c['id']} ({c['name']}): {c['message']}" for c in na))
    if not res["heat"]["entropic"]["included"]:
        add("warning", "Obtain entropic coefficient data", res["heat"]["entropic"]["status"] + " Request dU/dT vs SOC from the cell supplier (or measure it) and re-run.")
    m = res["models"]["resistance"]
    if m["level"] == 1:
        add("warning", "Use a resistance map", "The constant-resistance model was used. Cell resistance typically doubles between 25 °C and 0 °C and varies with SOC; provide R(SOC,T) data - especially for cold-climate and fast-charge cases.")
    if res["thermal"].get("t_max_uncooled_c") is not None and res["thermal"]["has_temperature"]:
        add("info", "Value of active cooling", f"Without active cooling the same duty would bring the pack to {res['thermal']['t_max_uncooled_c']:.1f} °C (target {req.pack.t_target_max_c:g} °C).")
    inlet = S.get("inlet_temperature")
    if inlet:
        add("warning" if inlet["chiller_required"] else "info", "Coolant supply temperature",
            f"To hold the hottest cell at the target at the design load the coolant supply may be up to {inlet['t_in_max_c']:.1f} °C (recommended ≤ {inlet['t_in_recommended_c']:.1f} °C); specified {inlet['specified_c']:g} °C."
            + (" This is within 5 K of (or below) the ambient design temperature: an active refrigeration circuit (chiller) is likely required." if inlet["chiller_required"] else ""))
    if res.get("hydraulics") and res["hydraulics"]["regime"] == "laminar":
        add("info", "Laminar flow in the plate", "The channel flow is laminar, so the convection resistance dominates the thermal chain; smaller hydraulic diameter, more channels or a turbulence-promoting geometry improve h at the cost of pressure drop (see the optimiser).")
    dq = res["data_quality"]
    if dq["n_assumed"]:
        low = [a for a in res["assumptions"] if a["confidence"] == "Low" and a["group"] != "Margins"]
        names = ", ".join(a["parameter"] for a in low[:8]) + (f" and {len(low) - 8} more" if len(low) > 8 else "")
        add("warning", "Confirm assumptions before design release", f"{dq['n_assumed']} parameters are unconfirmed engineering assumptions ({dq['n_low']} low confidence): {names}. Replace them with measured or supplier data.")
    add("info", "Verification", "Validate the cell-to-cell temperature spread and the flow distribution with a CFD or 1-D network model and confirm the predicted temperatures with a module-level thermal test before freezing the design. "
        "Final radiator / chiller sizing requires detailed air-side and exchanger-design information.")
    return out


def _remedy(check_id: str) -> str:
    return {
        "1": "Lower the coolant inlet temperature, increase the flow or the plate effectiveness (thinner/higher-conductivity TIM, more wetted area) or reduce the load.",
        "2": "Increase the coolant flow (smaller coolant rise), add parallel plates, reduce the heat-generation spread, or accept a larger target ΔT.",
        "3": "Reduce the coolant temperature rise across a module (higher flow, parallel branches) or the cell heat spread.",
        "4": "Increase the coolant flow or lower the inlet temperature so that the outlet stays below the limit.",
        "5": "Increase the installed cooling capacity or reduce the heat load until the margin meets the configured engineering target.",
        "6": "The cells exceed their absolute temperature limits - the design is not acceptable without additional cooling or a reduced load.",
        "7": "Reduce the C-rate demand (power limiting) or select a cell with a higher continuous/pulse rating.",
        "8": "Reduce the channel velocity or loop losses (larger channels, shorter paths, fewer plates in series) or select a higher-head pump.",
    }.get(check_id, "")


def verdict(res: dict) -> tuple[str, str]:
    """(level, sentence) - overall statement on the evaluated checks: level is 'fail', 'warning' or 'pass'."""
    fails = [c for c in res["checks"] if c["status"] == "FAIL"]
    warns = [c for c in res["checks"] if c["status"] == "WARNING" and not c["supplementary"]]
    if fails:
        return "fail", f"Design not acceptable as specified: {len(fails)} check(s) failed."
    if warns:
        return "warning", f"Design acceptable with reservations: {len(warns)} warning(s)."
    return "pass", "Design meets all evaluated checks."
