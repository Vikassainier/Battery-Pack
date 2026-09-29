"""Cold-plate thermal resistance chain, heat-transfer coefficient and overall U.

Per cell (all in K/W, referenced to the cell contact area A_cell):

    R_contact = R''_c / A_cell                     cell ↔ TIM interface (area-specific contact resistance R''_c)
    R_TIM     = t_TIM / (k_TIM · A_cell)
    R_plate   = t_plate / (k_plate · A_cell)        1-D conduction through the plate under the cell
    R_conv    = cells_per_plate / (h · η_fin · A_wet,plate)      coolant-side convection; A_wet,plate = n_ch·2(w+h)·L
    R_total   = R_contact + R_TIM + R_plate + R_conv

Thermal resistance [K/W] is an absolute property of a *specific* part of a specific geometry (it adds in series).
The overall heat-transfer coefficient U [W/(m²·K)] = 1 / (R_total · A_ref) normalises it by a chosen reference area, so
it depends on that choice (cell contact area vs. plate footprint) and is used to compare layers / technologies.

Coolant side (ε-NTU with an isothermal cell wall, C_r = 0):
    G_pack = N / R_total,cell ;  NTU = G_pack / (ṁ·cp) ;  ε = 1 − exp(−NTU) ;  Q_removed = ε·ṁ·cp·(T_cell − T_in)
Valid for plates in series or parallel (identical plates at one wall temperature).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict, field

from .channel import (
    RectChannel, flow_regime, nusselt, reynolds, thermal_entry_length,
)
from .coolant import CoolantProps
from .schemas import ColdPlateSpec
from .trace import TraceLog, fmt

MATERIAL_K = {"aluminium_6061": 167.0, "aluminium_3003": 160.0, "copper": 390.0, "stainless_304": 16.2}


class ColdPlateError(ValueError):
    pass


def plate_conductivity(plate: ColdPlateSpec) -> tuple[float, str]:
    if plate.k_plate_w_mk is not None:
        return plate.k_plate_w_mk, "user value"
    if plate.material == "custom":
        raise ColdPlateError("A custom plate material needs a thermal conductivity (k_plate_w_mk).")
    return MATERIAL_K[plate.material], f"library value for {plate.material}"


@dataclass
class ColdPlateResult:
    # flow in the channels
    m_dot_plate_kg_s: float
    m_dot_channel_kg_s: float
    velocity_m_s: float
    dh_m: float
    reynolds: float
    regime: str
    prandtl: float
    alpha: float
    nusselt: float
    h_w_m2k: float
    l_thermal_entry_m: float
    thermally_developing: bool
    # geometry
    cells_per_plate: float
    a_wet_plate_m2: float
    # per-cell thermal resistances [K/W]
    r_contact: float
    r_tim: float
    r_plate: float
    r_conv: float
    r_total: float
    # overall coefficients
    u_cell_w_m2k: float
    u_plate_w_m2k: float
    u_conv_only_w_m2k: float
    g_pack_w_k: float
    r_pack_k_w: float
    # ε-NTU
    m_cp_w_k: float
    ntu: float
    effectiveness: float
    g_cool_eff_w_k: float
    k_plate_w_mk: float
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def temp_rise_cell_to_coolant(self, q_cell_w: float) -> float:
        return q_cell_w * self.r_total


def analyze_cold_plate(plate: ColdPlateSpec, props: CoolantProps, m_dot_pack: float, n_cells: int,
                       tr: TraceLog | None = None) -> ColdPlateResult:
    if m_dot_pack <= 0:
        raise ColdPlateError("Coolant flow must be > 0 to evaluate the cold plate.")
    warn: list[str] = []
    k_plate, k_src = plate_conductivity(plate)
    ch = RectChannel(plate.channel_width_mm * 1e-3, plate.channel_height_mm * 1e-3, plate.channel_length_mm * 1e-3)
    m_plate = m_dot_pack / plate.n_plates if plate.plate_arrangement == "parallel" else m_dot_pack
    m_ch = m_plate / plate.n_channels
    v = m_ch / (props.rho * ch.area)
    re = reynolds(props.rho, v, ch.dh, props.mu)
    pr = props.pr
    nu = nusselt(re, pr, ch.alpha, plate.nu_boundary)
    h = nu * props.k / ch.dh
    l_th = thermal_entry_length(re, pr, ch.dh) if re < 2300 else 0.0
    developing = re < 2300 and l_th > ch.length_m

    cpp = n_cells / plate.n_plates
    a_wet = plate.n_channels * ch.wetted_area
    a_cell = plate.cell_contact_area_m2
    r_contact = plate.contact_resistance_m2k_w / a_cell
    r_tim = plate.tim_thickness_mm * 1e-3 / (plate.tim_k_w_mk * a_cell)
    r_plate = plate.thickness_mm * 1e-3 / (k_plate * a_cell)
    r_conv = cpp / (h * plate.fin_efficiency * a_wet)
    r_tot = r_contact + r_tim + r_plate + r_conv
    if a_cell * cpp > plate.cooling_area_m2 * 1.0001:
        warn.append(f"Cell contact area × cells per plate = {a_cell * cpp:.3f} m² exceeds the plate cooling area {plate.cooling_area_m2:.3f} m²: "
                    "the geometry is inconsistent (cells cannot all touch the plate).")
    if developing:
        warn.append(f"Thermal entry length ≈ {l_th:.2f} m exceeds the channel length {ch.length_m:.2f} m: the flow is thermally developing, "
                    "so the fully-developed Nu used here is conservative.")
    if 2300 <= re < 4000:
        warn.append("Flow is in the laminar-turbulent transition region: correlations are least reliable and the flow can be unstable.")

    u_cell = 1.0 / (r_tot * a_cell)
    r_plate_total = r_tot / cpp
    u_plate = 1.0 / (r_plate_total * plate.cooling_area_m2)
    g_pack = n_cells / r_tot
    m_cp = m_dot_pack * props.cp
    ntu = g_pack / m_cp
    eps = 1.0 - math.exp(-ntu)
    res = ColdPlateResult(m_plate, m_ch, v, ch.dh, re, flow_regime(re), pr, ch.alpha, nu, h, l_th, developing, cpp, a_wet,
                          r_contact, r_tim, r_plate, r_conv, r_tot, u_cell, u_plate, 1.0 / (r_conv * a_cell), g_pack, 1.0 / g_pack,
                          m_cp, ntu, eps, eps * m_cp, k_plate, warn)
    if tr is not None:
        _trace(tr, plate, props, res, ch, a_cell)
    return res


def _trace(tr: TraceLog, plate: ColdPlateSpec, props: CoolantProps, r: ColdPlateResult, ch: RectChannel, a_cell: float) -> None:
    tr.input("cp.a_cell", "Cell contact area", a_cell, "m²", "user")
    tr.input("cp.t_tim", "TIM thickness", plate.tim_thickness_mm, "mm", "user")
    tr.input("cp.k_tim", "TIM conductivity", plate.tim_k_w_mk, "W/(m·K)", "user")
    tr.input("cp.rc", "Contact resistance (area-specific)", plate.contact_resistance_m2k_w, "m²K/W", "user")
    tr.input("cp.t_plate", "Plate thickness", plate.thickness_mm, "mm", "user")
    tr.input("cp.k_plate", "Plate conductivity", r.k_plate_w_mk, "W/(m·K)", "calculated")
    tr.calc("cp.dh", "Hydraulic diameter", ch.dh * 1e3, "mm", "D_h = 4A/P = 2wh/(w+h)", f"2×{plate.channel_width_mm:g}×{plate.channel_height_mm:g}/({plate.channel_width_mm:g}+{plate.channel_height_mm:g})", [])
    tr.calc("cp.v", "Channel velocity", r.velocity_m_s, "m/s", "v = ṁ_ch / (ρ·w·h)", f"{fmt(r.m_dot_channel_kg_s)} / ({fmt(props.rho)} × {fmt(ch.area)})", ["cool.rho"])
    tr.calc("cp.re", "Reynolds number", r.reynolds, "-", "Re = ρ·v·D_h/μ", f"{fmt(props.rho)} × {fmt(r.velocity_m_s)} × {fmt(ch.dh)} / {fmt(props.mu)}", ["cp.v", "cp.dh"], note=f"regime: {r.regime}")
    tr.calc("cp.nu", "Nusselt number", r.nusselt, "-", "Shah-London (laminar) / Gnielinski (turbulent) / blend", f"Re = {fmt(r.reynolds)}, Pr = {fmt(r.prandtl)}, α = {fmt(r.alpha)}", ["cp.re"])
    tr.calc("cp.h", "Convective coefficient", r.h_w_m2k, "W/(m²·K)", "h = Nu·k/D_h", f"{fmt(r.nusselt)} × {fmt(props.k)} / {fmt(ch.dh)}", ["cp.nu", "cp.dh"])
    tr.calc("cp.r_contact", "Contact resistance", r.r_contact, "K/W", "R = R''/A_cell", f"{fmt(plate.contact_resistance_m2k_w)} / {fmt(a_cell)}", ["cp.rc", "cp.a_cell"])
    tr.calc("cp.r_tim", "TIM resistance", r.r_tim, "K/W", "R = t/(k·A)", f"{fmt(plate.tim_thickness_mm * 1e-3)} / ({fmt(plate.tim_k_w_mk)} × {fmt(a_cell)})", ["cp.t_tim", "cp.k_tim", "cp.a_cell"])
    tr.calc("cp.r_plate", "Plate conduction resistance", r.r_plate, "K/W", "R = t/(k·A)", f"{fmt(plate.thickness_mm * 1e-3)} / ({fmt(r.k_plate_w_mk)} × {fmt(a_cell)})", ["cp.t_plate", "cp.k_plate", "cp.a_cell"])
    tr.calc("cp.r_conv", "Coolant convection resistance", r.r_conv, "K/W", "R = cells_per_plate / (h·η_fin·A_wet)", f"{fmt(r.cells_per_plate)} / ({fmt(r.h_w_m2k)} × {fmt(plate.fin_efficiency)} × {fmt(r.a_wet_plate_m2)})", ["cp.h"])
    tr.result("cp.r_total", "Overall thermal resistance (per cell)", r.r_total, "K/W", "R_total = R_contact + R_TIM + R_plate + R_conv",
              f"{fmt(r.r_contact)} + {fmt(r.r_tim)} + {fmt(r.r_plate)} + {fmt(r.r_conv)}", ["cp.r_contact", "cp.r_tim", "cp.r_plate", "cp.r_conv"])
    tr.result("cp.u_cell", "Overall heat-transfer coefficient U (cell contact area)", r.u_cell_w_m2k, "W/(m²·K)", "U = 1/(R_total·A_cell)", f"1/({fmt(r.r_total)} × {fmt(a_cell)})", ["cp.r_total", "cp.a_cell"])
    tr.calc("cp.ntu", "NTU", r.ntu, "-", "NTU = (N/R_total)/(ṁ·cp)", f"{fmt(r.g_pack_w_k)} / {fmt(r.m_cp_w_k)}", ["cp.r_total"])
    tr.calc("cp.eps", "Effectiveness", r.effectiveness, "-", "ε = 1 − exp(−NTU)", f"1 − exp(−{fmt(r.ntu)})", ["cp.ntu"])
