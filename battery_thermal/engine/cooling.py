"""Coolant flow requirement.

    Q = ṁ·cp·ΔT     ⇒     ṁ = Q / (cp·ΔT)          V̇ [L/min] = ṁ/ρ · 1000 · 60

ΔT is the allowable coolant temperature rise; if both an allowable ΔT and a maximum outlet temperature are given,
the more restrictive of ΔT_allow and (T_out,max − T_in) governs.
Flow is calculated for one cell, one module and the whole pack (uniform heat distribution assumed).
"""
from __future__ import annotations

from .coolant import CoolantProps
from .schemas import CoolantSpec
from .trace import TraceLog, fmt
from .units import kgs_to_lpm


class CoolingError(ValueError):
    pass


def effective_coolant_dt(spec: CoolantSpec) -> tuple[float, str]:
    cands = []
    if spec.allowable_dt_k is not None:
        cands.append((spec.allowable_dt_k, "allowable coolant ΔT"))
    if spec.max_outlet_c is not None:
        cands.append((spec.max_outlet_c - spec.inlet_c, "T_out,max − T_in"))
    if not cands:
        raise CoolingError("Give an allowable coolant ΔT and/or a maximum coolant outlet temperature.")
    dt, why = min(cands)
    if dt <= 0:
        raise CoolingError(f"The allowable coolant temperature rise is {dt:g} K ({why}): the outlet limit must be above the inlet temperature.")
    return dt, why


def mass_flow_kg_s(q_w: float, cp: float, dt_k: float) -> float:
    return q_w / (cp * dt_k)


def flow_requirements(q_pack_w: float, n_cells: int, cells_per_module: int, props: CoolantProps, dt_k: float,
                      tr: TraceLog | None = None, q_in_id: str | None = None, prefix: str = "cool") -> dict:
    """Required coolant flow for cell / module / pack from the design heat load (equal split of heat)."""
    q_cell = q_pack_w / n_cells
    q_mod = q_cell * cells_per_module
    out = {"dt_k": dt_k, "rho": props.rho, "cp": props.cp, "t_eval_c": props.t_eval_c}
    for lvl, q in (("cell", q_cell), ("module", q_mod), ("pack", q_pack_w)):
        m = mass_flow_kg_s(q, props.cp, dt_k)
        out[lvl] = {"q_w": q, "m_dot_kg_s": m, "lpm": kgs_to_lpm(m, props.rho), "ml_min": kgs_to_lpm(m, props.rho) * 1000.0}
    if tr is not None:
        tr.calc(f"{prefix}.rho", "Coolant density", props.rho, "kg/m³", "correlation / override", f"at {props.t_eval_c:.1f} °C: {props.description}", [])
        tr.calc(f"{prefix}.cp", "Coolant specific heat", props.cp, "J/(kg·K)", "correlation / override", f"at {props.t_eval_c:.1f} °C", [])
        tr.calc(f"{prefix}.dt", "Coolant temperature rise used", dt_k, "K", "ΔT = min(ΔT_allow, T_out,max − T_in)", "", [])
        for lvl in ("cell", "module", "pack"):
            o = out[lvl]
            tr.calc(f"{prefix}.q_{lvl}", f"Design heat per {lvl}", o["q_w"], "W", "Q_cell = Q_pack/N ; Q_module = cells_per_module·Q_cell",
                    f"{fmt(q_pack_w)} W → {fmt(o['q_w'])} W", [q_in_id] if q_in_id else [])
            tr.calc(f"{prefix}.mdot_{lvl}", f"Required coolant mass flow ({lvl})", o["m_dot_kg_s"], "kg/s", "ṁ = Q / (cp·ΔT)",
                    f"{fmt(o['q_w'])} / ({fmt(props.cp)} × {fmt(dt_k)})", [f"{prefix}.q_{lvl}", f"{prefix}.cp", f"{prefix}.dt"])
            tr.result(f"{prefix}.lpm_{lvl}", f"Required coolant flow ({lvl})", o["lpm"], "L/min", "V̇ = ṁ/ρ·1000·60",
                      f"{fmt(o['m_dot_kg_s'])} / {fmt(props.rho)} × 60000", [f"{prefix}.mdot_{lvl}", f"{prefix}.rho"])
    return out
