"""Built-in validation cases: closed-form hand calculations versus the complete software chain.

Every hand value is computed here with plain arithmetic (no engine code), the request is run through the same
``run_analysis`` that serves a customer project, and the two are compared row by row within a stated relative tolerance.
A case passes only if every row passes.

    VC1  constant current, 120S1P, 200 A            Q_cell = I²R = 40 W ; Q_pack = 4.8 kW           (specification case 1)
    VC2  time-varying current with regen and rest   piece-wise constant I → E, peak, average, SOC    (specification case 2)
    VC3  road-load: 100 km/h cruise                 F = F_roll + F_aero → P_wheel → P_batt → I → Q
    VC4  cold-plate resistance chain + hydraulics   Shah-London laminar channel, ε-NTU, ΔP, pump power
    VC5  lumped thermal accumulation                adiabatic and ε-NTU cooled temperature, closed form
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

from ..engine.pipeline import run_analysis
from ..engine.schemas import (
    AnalysisRequest, ColdPlateSpec, CoolantSpec, CRateLimits, CellSpec, DriveCycle, EntropicSettings, PackConfig, ThermalSettings, VehicleParams,
)

G_ACC = 9.80665
HAND_COOLANT = dict(type="custom", density_kg_m3=1070.0, cp_j_kg_k=3400.0, k_w_mk=0.39, mu_pa_s=0.004, inlet_c=25.0, allowable_dt_k=5.0)
RHO, CP, K_COOL, MU = 1070.0, 3400.0, 0.39, 0.004


@dataclass(frozen=True)
class Row:
    name: str
    hand: float
    unit: str
    get: Callable[[dict], float]
    tol: float = 1e-6                 # relative tolerance


@dataclass
class Case:
    id: str
    title: str
    description: str
    hand_calc: str
    request: AnalysisRequest
    rows: list[Row] = field(default_factory=list)


# ------------------------------------------------------------------------------------------------ request builders
def _cell(**kw) -> CellSpec:
    base = dict(name="Validation cell 100 Ah", chemistry="LFP", capacity_ah=100, v_nom=3.2, v_max=3.65, v_min=2.5, r_dc_mohm=1.0, mass_kg=2.05, cp_j_kg_k=1000.0,
                t_op_min_c=-20, t_op_max_c=60, t_rec_min_c=15, t_rec_max_c=45, max_discharge_c=3.0, max_charge_c=1.0, pulse_discharge_c=5.0, pulse_duration_s=30.0,
                length_mm=174, width_mm=71, height_mm=207, form_factor="prismatic", confirmed=True)
    base.update(kw)
    return CellSpec(**base)


def _plate(**kw) -> ColdPlateSpec:
    base = dict(material="custom", k_plate_w_mk=200.0, thickness_mm=2.0, channel_width_mm=8.0, channel_height_mm=2.0, n_channels=5, channel_length_mm=1000.0,
                cooling_area_m2=0.3, n_plates=10, plate_arrangement="parallel", tim_thickness_mm=0.5, tim_k_w_mk=2.0, contact_resistance_m2k_w=1e-4,
                cell_contact_area_m2=0.02, minor_loss_k=1.5, external_dp_kpa=30.0)
    base.update(kw)
    return ColdPlateSpec(**base)


def _request(cycle: DriveCycle, *, thermal: ThermalSettings | None = None, plate: ColdPlateSpec | None = None, crate: CRateLimits | None = None, **kw) -> AnalysisRequest:
    return AnalysisRequest(
        cell=_cell(),
        pack=PackConfig(ns=120, np=1, n_modules=10, cells_per_module=12, soc_initial_pct=90, soc_min_pct=10, soc_max_pct=100, t_initial_c=25, t_target_max_c=40,
                        target_delta_t_k=5, t_ambient_c=25),
        cycle=cycle, thermal=thermal or ThermalSettings(design_philosophy="peak", safety_factor=1.2), entropic=EntropicSettings(mode="excluded"),
        coolant=CoolantSpec(**HAND_COOLANT), cold_plate=plate or _plate(),
        crate_limits=crate or CRateLimits(cont_discharge_c=3.0, peak_discharge_c=5.0, peak_discharge_duration_s=30, charge_c=2.0, regen_c=2.0), **kw)


def _const_current(i_a: float, seconds: int) -> DriveCycle:
    n = seconds + 1
    return DriveCycle(name="constant current", time_s=[float(k) for k in range(n)], battery_current_a=[i_a] * n)


# ------------------------------------------------------------------------------------------------ VC1
def vc1() -> Case:
    ns, np_, cap, r_ohm, i_pack, secs, sf = 120, 1, 100.0, 1e-3, 200.0, 60, 1.2
    i_cell = i_pack / np_
    q_cell = i_cell ** 2 * r_ohm
    q_pack = ns * np_ * q_cell
    v_term = (3.2 - i_cell * r_ohm) * ns
    e_kwh = q_pack * secs / 3.6e6
    m_dot = q_pack * sf / (CP * 5.0)
    return Case(
        "vc1_constant_current", "VC1 · Constant current: 100 Ah / 3.2 V / 1 mΩ cell, 120S1P, 200 A",
        "Specification case 1. Joule heat only (entropic term explicitly excluded, constant resistance). The electrical output is not heat.",
        "I_cell = I_pack/Np = 200 A ;  C-rate = 200/100 = 2 C\n"
        "Q_cell = I²R = 200² × 0.001 = 40 W ;  Q_module = 12 × 40 = 480 W ;  Q_pack = Ns·Np·Q_cell = 120 × 40 = 4800 W\n"
        "V_term = (3.2 − 200·0.001)·120 = 360 V ;  P_term = 360 × 200 = 72 kW ;  P_chem = 3.2·120·200 = 76.8 kW ;  η = 72/76.8 = 93.75 %\n"
        "E_heat = 4800 W × 60 s = 0.08 kWh ;  SOC(60 s) = 90 − 200·60/(3600·100)·100 = 86.667 %\n"
        "Design: Q_design = 4.8 kW × SF 1.2 = 5.76 kW ;  ṁ = 5760/(3400·5) = 0.338824 kg/s = 0.338824/1070·60000 = 18.999 L/min",
        _request(_const_current(i_pack, secs)),
        [Row("Cell current", i_cell, "A", lambda r: r["heat"]["max_cell_current_a"]),
         Row("Cell C-rate", i_cell / cap, "C", lambda r: r["heat"]["max_discharge_c"]),
         Row("Heat per cell  Q_cell = I²R", q_cell, "W", lambda r: r["heat"]["max_cell_heat_w"]),
         Row("Heat per module (12 cells)", 12 * q_cell, "W", lambda r: r["heat"]["max_module_heat_w"]),
         Row("Pack heat  Q_pack = Ns·Np·Q_cell", q_pack, "W", lambda r: r["heat"]["max_pack_heat_kw"] * 1e3),
         Row("Average pack heat", q_pack, "W", lambda r: r["heat"]["avg_pack_heat_kw"] * 1e3),
         Row("Total heat over 60 s", e_kwh, "kWh", lambda r: r["heat"]["total_heat_kwh"]),
         Row("Terminal power (electrical, NOT heat)", v_term * i_pack / 1e3, "kW", lambda r: r["heat"]["peak_terminal_power_kw"]),
         Row("Discharge efficiency", (v_term * i_pack) / (3.2 * ns * i_pack) * 100, "%", lambda r: r["heat"]["electrical"]["discharge_efficiency_pct"]),
         Row("SOC after 60 s", 90 - i_pack * secs / (3600 * cap) * 100, "%", lambda r: r["heat"]["soc_end_pct"]),
         Row("Design cooling capacity (× SF 1.2)", q_pack * sf / 1e3, "kW", lambda r: r["sizing"]["capacity"]["design_kw"]),
         Row("Coolant mass flow, pack", m_dot, "kg/s", lambda r: r["cooling"]["pack"]["m_dot_kg_s"]),
         Row("Coolant volume flow, pack", m_dot / RHO * 60000, "L/min", lambda r: r["cooling"]["pack"]["lpm"])])


# ------------------------------------------------------------------------------------------------ VC2
def vc2() -> Case:
    ns, r_ohm, cap = 120, 1e-3, 100.0
    segs = [(100.0, 100), (300.0, 100), (-150.0, 50), (0.0, 50)]                # (A, s) - piece-wise constant, sample-and-hold
    cur = [i for i, s in segs for _ in range(s)] + [0.0]                       # 301 samples, the last has zero duration
    q_seg = [ns * i * i * r_ohm for i, _ in segs]                             # pack heat per segment [W]
    e_j = sum(q * s for q, (_, s) in zip(q_seg, segs))
    win = 200.0
    ma = max_trailing_average(segs, ns * r_ohm, win)
    d_ah = sum(i * s for i, s in segs) / 3600
    cyc = DriveCycle(name="time-varying", time_s=[float(k) for k in range(301)], battery_current_a=cur)
    return Case(
        "vc2_time_varying", "VC2 · Time-varying current: 100 A → 300 A → −150 A (regen) → rest",
        "Specification case 2. Piece-wise constant pack current with sample-and-hold integration; also checks the moving-average design heat load (window 200 s).",
        "Segments (pack current / duration):  100 A × 100 s | 300 A × 100 s | −150 A × 50 s | 0 A × 50 s      (R = 1 mΩ, 120S1P)\n"
        "Q_pack = 120·I²·R:  1200 W | 10 800 W | 2700 W | 0 W\n"
        "E = 1200·100 + 10800·100 + 2700·50 = 1 335 000 J = 0.370833 kWh ;  peak = 10.8 kW ;  average = 1 335 000/300 = 4450 W\n"
        "ΔAh = (100·100 + 300·100 − 150·50)/3600 = 9.02778 Ah  →  SOC_end = 90 − 9.02778 = 80.9722 %\n"
        "Moving average (trailing window 200 s): best window ends at t = 250 s:  (1200·50 + 10800·100 + 2700·50)/200 = 6375 W ;  Q_design = 6.375 × 1.2 = 7.65 kW",
        _request(cyc, thermal=ThermalSettings(design_philosophy="moving_average", moving_avg_window_s=win, safety_factor=1.2)),
        [Row("Peak pack heat", max(q_seg) / 1e3, "kW", lambda r: r["heat"]["max_pack_heat_kw"]),
         Row("Peak cell heat", max(q_seg) / ns, "W", lambda r: r["heat"]["max_cell_heat_w"]),
         Row("Peak module heat (12 cells)", max(q_seg) / ns * 12, "W", lambda r: r["heat"]["max_module_heat_w"]),
         Row("Average pack heat", e_j / 300.0 / 1e3, "kW", lambda r: r["heat"]["avg_pack_heat_kw"]),
         Row("Total heat generated", e_j / 3.6e6, "kWh", lambda r: r["heat"]["total_heat_kwh"]),
         Row("Final SOC", 90 - d_ah / cap * 100, "%", lambda r: r["heat"]["soc_end_pct"]),
         Row("Maximum discharge C-rate", 3.0, "C", lambda r: r["heat"]["max_discharge_c"]),
         Row("Maximum charge (regen) C-rate", 1.5, "C", lambda r: r["heat"]["max_charge_c"]),
         Row("Moving-average heat load (200 s window)", ma / 1e3, "kW", lambda r: r["design"]["candidates"]["moving_average"]["value_w"] / 1e3),
         Row("Design cooling capacity (× SF 1.2)", ma * 1.2 / 1e3, "kW", lambda r: r["sizing"]["capacity"]["design_kw"])])


def max_trailing_average(segs: list[tuple[float, int]], k: float, window: float) -> float:
    """Maximum over t of the trailing mean of Q = k·I² for a piece-wise constant current (exact integration of the steps).

    The trailing mean is piece-wise linear in t, so its maximum lies where the window's end or start meets a segment edge."""
    edges = [0.0]
    for _, s in segs:
        edges.append(edges[-1] + s)

    def integral(a: float, b: float) -> float:
        return sum(k * i * i * max(0.0, min(b, hi) - max(a, lo)) for (i, _), lo, hi in zip(segs, edges, edges[1:]))
    cands = {e for e in edges} | {e + window for e in edges}
    return max(integral(t - window, t) / window for t in cands if window <= t <= edges[-1])


# ------------------------------------------------------------------------------------------------ VC3
def vc3() -> Case:
    m, crr, cd, area, rw, eta, aux_kw, rho_air = 1800.0, 0.009, 0.28, 2.2, 0.32, 0.90, 0.5, 1.225
    v = 100.0 / 3.6
    f_roll = crr * m * G_ACC
    f_aero = 0.5 * rho_air * cd * area * v * v
    f = f_roll + f_aero
    p_wheel = f * v
    p_batt = p_wheel / eta + aux_kw * 1e3
    ns = 120
    ocv, r_ohm = 3.2, 1e-3
    p_cell = p_batt / ns
    i_cell = 2 * p_cell / (ocv + math.sqrt(ocv ** 2 - 4 * r_ohm * p_cell))
    q_pack = ns * i_cell ** 2 * r_ohm
    n = 61
    cyc = DriveCycle(name="cruise 100 km/h", time_s=[float(k) for k in range(n)], speed_kmh=[100.0] * n)
    veh = VehicleParams(mass_kg=m, crr=crr, cd=cd, frontal_area_m2=area, wheel_radius_m=rw, drivetrain_eff=eta, aux_load_kw=aux_kw, air_density=rho_air)
    idx = 30
    return Case(
        "vc3_road_load", "VC3 · Road load: 1800 kg vehicle at a steady 100 km/h → battery power → cell current → heat",
        "Speed-only cycle: F = F_acc + F_roll + F_aero + F_grade, P_wheel = F·v, P_batt = P_wheel/η + P_aux, then the power → current quadratic and Q = I²R.",
        "v = 100/3.6 = 27.7778 m/s ;  F_roll = Crr·m·g = 0.009·1800·9.80665 = 158.868 N ;  F_aero = ½ρCdA·v² = 0.3773·771.605 = 291.127 N\n"
        "F = 449.995 N ;  P_wheel = F·v = 12.4999 kW ;  P_batt = 12.4999/0.90 + 0.5 = 14.3888 kW\n"
        "p = P_batt/120 = 119.9 W per cell ;  R·I² − OCV·I + p = 0  →  I = 2p/(OCV + √(OCV² − 4Rp)) = 37.92 A  (OCV = 3.2 V, R = 1 mΩ)\n"
        "Q_cell = I²R = 1.438 W ;  Q_pack = 120 × Q_cell = 172.6 W",
        _request(cyc, vehicle=veh),
        [Row("Rolling-resistance force", f_roll, "N", lambda r: r["load"]["road"]["f_roll_n"][idx]),
         Row("Aerodynamic drag force", f_aero, "N", lambda r: r["load"]["road"]["f_aero_n"][idx]),
         Row("Tractive force", f, "N", lambda r: r["load"]["road"]["f_tractive_n"][idx]),
         Row("Wheel power", p_wheel / 1e3, "kW", lambda r: r["load"]["road"]["p_wheel_kw"][idx]),
         Row("Battery power  P_wheel/η + P_aux", p_batt / 1e3, "kW", lambda r: r["series"]["p_batt_kw"][idx]),
         Row("Cell current from P = V·I", i_cell, "A", lambda r: r["series"]["i_cell_a"][idx]),
         Row("Pack heat", q_pack, "W", lambda r: r["series"]["q_pack_kw"][idx] * 1e3, tol=1e-5)])


# ------------------------------------------------------------------------------------------------ VC4 / VC5 closed forms
def fre_laminar(a: float) -> float:
    """Shah & London: Darcy f·Re for a rectangular duct of aspect ratio a = h/w (96 for parallel plates, 56.91 for a square duct)."""
    return 96 * (1 - 1.3553 * a + 1.9467 * a ** 2 - 1.7012 * a ** 3 + 0.9564 * a ** 4 - 0.2537 * a ** 5)


def nu_h1(a: float) -> float:
    """Shah & London: fully developed laminar Nusselt number, constant heat flux (H1) - 8.235 for parallel plates, 3.61 for a square duct."""
    return 8.235 * (1 - 2.0421 * a + 3.0853 * a ** 2 - 2.4765 * a ** 3 + 1.0578 * a ** 4 - 0.1861 * a ** 5)


def plate_hand(m_pack: float, n_cells: int = 120) -> dict:
    """Closed-form cold-plate + hydraulics chain of the hand-calculation plate (8×2 mm × 5 channels, L = 1 m, 10 parallel plates, 12 cells per plate)."""
    w, h, L, n_ch, n_pl = 8e-3, 2e-3, 1.0, 5, 10
    a = h / w
    dh = 2 * w * h / (w + h)
    area = w * h
    m_ch = m_pack / n_pl / n_ch
    v = m_ch / (RHO * area)
    re = RHO * v * dh / MU
    pr = MU * CP / K_COOL
    nu = nu_h1(a)
    h_conv = nu * K_COOL / dh
    a_wet = n_ch * 2 * (w + h) * L
    cells_per_plate = n_cells / n_pl
    r_contact = 1e-4 / 0.02
    r_tim = 0.5e-3 / (2.0 * 0.02)
    r_plate = 2e-3 / (200.0 * 0.02)
    r_conv = cells_per_plate / (h_conv * 1.0 * a_wet)
    r_tot = r_contact + r_tim + r_plate + r_conv
    ntu = (n_cells / r_tot) / (m_pack * CP)
    eps = 1 - math.exp(-ntu)
    f = fre_laminar(a) / re
    q_dyn = 0.5 * RHO * v * v
    dp_ch = f * (L / dh) * q_dyn
    dp_min = 1.5 * q_dyn
    dp_pl = dp_ch + dp_min
    dp_tot = dp_pl + 30e3
    vdot = m_pack / RHO
    p_hyd = dp_tot * vdot
    return dict(v=v, re=re, pr=pr, nu=nu, h=h_conv, r_contact=r_contact, r_tim=r_tim, r_plate=r_plate, r_conv=r_conv, r_tot=r_tot, u_cell=1 / (r_tot * 0.02), ntu=ntu, eps=eps,
                f=f, dp_ch=dp_ch, dp_min=dp_min, dp_tot=dp_tot, p_hyd=p_hyd, p_el=p_hyd / 0.4, g_c=eps * m_pack * CP)


def vc4() -> Case:
    m_pack = 4800.0 / (CP * 5.0)                                                # 0.282353 kg/s (Q = 4800 W, ΔT = 5 K)
    lpm = m_pack / RHO * 60000.0
    hd = plate_hand(m_pack)
    return Case(
        "vc4_cold_plate_hydraulics", "VC4 · Cold-plate resistance chain and channel hydraulics (8×2 mm channels, 10 parallel plates)",
        "Laminar Shah-London correlations, R_total = R_contact + R_TIM + R_plate + R_conv, ε-NTU and pressure drop, at a specified pack flow of 15.832 L/min.",
        "Coolant ρ = 1070, cp = 3400, k = 0.39, μ = 0.004 ;  ṁ_pack = 4800/(3400·5) = 0.282353 kg/s ;  channel 8 × 2 mm (α = 0.25), 5 per plate, L = 1 m\n"
        "D_h = 2·8·2/10 = 3.2 mm ;  ṁ_ch = 0.282353/10/5 ;  v = ṁ_ch/(ρA) = 0.32985 m/s ;  Re = ρvD_h/μ = 282.4 (laminar) ;  Pr = μcp/k = 34.87\n"
        "Nu_H1(0.25) = 8.235·0.647561 = 5.3327 ;  h = Nu·k/D_h = 649.9 W/m²K ;  A_wet = 5·2(0.008+0.002)·1 = 0.1 m² ;  R_conv = 12/(h·A_wet) = 0.18463 K/W\n"
        "R_contact = 1e-4/0.02 = 0.0050 ;  R_TIM = 0.5e-3/(2·0.02) = 0.0125 ;  R_plate = 2e-3/(200·0.02) = 0.0005 ;  R_total = 0.20264 K/W ;  U = 1/(R_total·0.02) = 246.74 W/m²K\n"
        "NTU = (120/R_total)/(ṁcp) = 0.6169 ;  ε = 1 − e^(−NTU) = 0.4604\n"
        "f·Re = 96(1 − 1.3553α + 1.9467α² − 1.7012α³ + 0.9564α⁴ − 0.2537α⁵) = 72.936 ;  f = 0.25828 ;  ΔP_ch = f(L/D_h)·½ρv² = 4698 Pa ;  ΔP_minor = 1.5·½ρv² = 87.3 Pa\n"
        "ΔP_total = 4.786 + 30 = 34.786 kPa ;  P_hyd = ΔP·V̇ = 34786·2.6386e-4 = 9.18 W ;  P_el = P_hyd/0.4 = 22.95 W",
        _request(_const_current(200.0, 60), plate=_plate(flow_lpm=lpm)),
        [Row("Channel velocity", hd["v"], "m/s", lambda r: r["cold_plate"]["velocity_m_s"]),
         Row("Reynolds number", hd["re"], "-", lambda r: r["cold_plate"]["reynolds"]),
         Row("Prandtl number", hd["pr"], "-", lambda r: r["cold_plate"]["prandtl"]),
         Row("Nusselt number (laminar, H1)", hd["nu"], "-", lambda r: r["cold_plate"]["nusselt"], 1e-4),
         Row("Convective coefficient h", hd["h"], "W/(m²·K)", lambda r: r["cold_plate"]["h_w_m2k"], 1e-4),
         Row("R_contact", hd["r_contact"], "K/W", lambda r: r["cold_plate"]["r_contact"]),
         Row("R_TIM", hd["r_tim"], "K/W", lambda r: r["cold_plate"]["r_tim"]),
         Row("R_plate", hd["r_plate"], "K/W", lambda r: r["cold_plate"]["r_plate"]),
         Row("R_conv", hd["r_conv"], "K/W", lambda r: r["cold_plate"]["r_conv"], 1e-4),
         Row("R_total per cell", hd["r_tot"], "K/W", lambda r: r["cold_plate"]["r_total"], 1e-4),
         Row("Overall U (cell contact area)", hd["u_cell"], "W/(m²·K)", lambda r: r["cold_plate"]["u_cell_w_m2k"], 1e-4),
         Row("NTU", hd["ntu"], "-", lambda r: r["cold_plate"]["ntu"], 1e-4),
         Row("Effectiveness ε", hd["eps"], "-", lambda r: r["cold_plate"]["effectiveness"], 1e-4),
         Row("Darcy friction factor", hd["f"], "-", lambda r: r["hydraulics"]["f_darcy"], 1e-4),
         Row("Channel pressure drop", hd["dp_ch"], "Pa", lambda r: r["hydraulics"]["dp_channel_pa"], 1e-4),
         Row("Minor pressure drop", hd["dp_min"], "Pa", lambda r: r["hydraulics"]["dp_minor_pa"], 1e-4),
         Row("Total pressure drop (incl. 30 kPa external)", hd["dp_tot"] / 1e3, "kPa", lambda r: r["hydraulics"]["dp_total_pa"] / 1e3, 1e-4),
         Row("Hydraulic pump power", hd["p_hyd"], "W", lambda r: r["hydraulics"]["p_hyd_w"], 1e-4),
         Row("Electrical pump power (η = 0.4)", hd["p_el"], "W", lambda r: r["hydraulics"]["p_elec_w"], 1e-4)])


def vc5() -> Case:
    ns, cap_cell, m_cell, cp_cell = 120, 100.0, 2.05, 1000.0
    secs, q_pack, sf = 600, 4800.0, 1.2
    c_pack = ns * m_cell * cp_cell
    t0, t_in = 25.0, 25.0
    m_pack = q_pack * sf / (CP * 5.0)                                            # flow sized for Q_design = 5760 W
    hd = plate_hand(m_pack)
    g_c = hd["g_c"]
    tau = c_pack / g_c
    t_eq = t_in + q_pack / g_c
    t_end = t_eq + (t0 - t_eq) * math.exp(-secs / tau)
    t_adiabatic = t0 + q_pack * secs / c_pack
    return Case(
        "vc5_thermal_accumulation", "VC5 · Lumped thermal accumulation: 4.8 kW for 600 s, adiabatic and with ε-NTU coolant exchange",
        "Pack thermal capacity C = N·m·cp, exact exponential integration of C·dT/dt = Q − G_c(T − T_in) with G_c = ε·ṁ·cp (no ambient exchange).",
        "C = 120 × 2.05 × 1000 = 246 000 J/K ;  Q = 200²·0.001·120 = 4800 W for 600 s\n"
        "Adiabatic (no coolant, no ambient exchange):  T(600 s) = 25 + 4800·600/246000 = 36.7073 °C\n"
        "Cooled: flow sized for Q_design = 4800 × 1.2 = 5760 W → ṁ = 5760/(3400·5) = 0.338824 kg/s ;  cold-plate chain as in VC4 at this flow gives ε and G_c = ε·ṁ·cp\n"
        "τ = C/G_c ;  T_eq = T_in + Q/G_c ;  T(600 s) = T_eq + (T_0 − T_eq)·exp(−600/τ)\n"
        "Result: G_c = 463.0 W/K ;  τ = 531.3 s ;  T_eq = 25 + 4800/463.0 = 35.37 °C ;  T(600 s) = 35.37 − 10.37·e^(−600/531.3) = 32.016 °C",
        _request(_const_current(200.0, secs), thermal=ThermalSettings(design_philosophy="peak", safety_factor=sf, ambient_ua_w_k=0.0)),
        [Row("Pack thermal capacity C = N·m·cp", c_pack, "J/K", lambda r: r["thermal"]["c_pack_j_k"]),
         Row("Adiabatic end temperature (no cooling)", t_adiabatic, "°C", lambda r: r["thermal"]["t_max_uncooled_c"]),
         Row("Effective coolant conductance G_c = ε·ṁ·cp", g_c, "W/K", lambda r: r["cold_plate"]["g_cool_eff_w_k"], 1e-4),
         Row("Thermal time constant τ = C/G_c", tau, "s", lambda r: r["thermal"]["tau_s"], 1e-4),
         Row("Cooled average-cell temperature at 600 s", t_end, "°C", lambda r: r["thermal"]["t_max_avg_c"], 1e-4)])


CASE_BUILDERS: dict[str, Callable[[], Case]] = {"vc1_constant_current": vc1, "vc2_time_varying": vc2, "vc3_road_load": vc3, "vc4_cold_plate_hydraulics": vc4, "vc5_thermal_accumulation": vc5}


# ------------------------------------------------------------------------------------------------ running
def list_cases() -> list[dict]:
    out = []
    for cid, build in CASE_BUILDERS.items():
        c = build()
        out.append({"id": cid, "title": c.title, "description": c.description, "hand_calc": c.hand_calc, "n_rows": len(c.rows)})
    return out


def run_case(cid: str) -> dict:
    case = CASE_BUILDERS[cid]()
    res = run_analysis(case.request, max_series_points=None)
    out = {"id": case.id, "title": case.title, "description": case.description, "hand_calc": case.hand_calc, "rows": [], "passed": False}
    if res.get("status") == "blocked":
        out["error"] = "The validation request was blocked by input validation: " + "; ".join(i["message"] for i in res["issues"] if i["severity"] == "error")
        return out
    for row in case.rows:
        try:
            software = float(row.get(res))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            out["rows"].append({"name": row.name, "hand": row.hand, "software": None, "unit": row.unit, "rel_err": None, "tol": row.tol, "passed": False, "error": f"{type(exc).__name__}: {exc}"})
            continue
        rel = abs(software - row.hand) / max(abs(row.hand), 1e-12)
        out["rows"].append({"name": row.name, "hand": row.hand, "software": software, "unit": row.unit, "rel_err": rel, "tol": row.tol, "passed": rel <= row.tol})
    out["passed"] = bool(out["rows"]) and all(r["passed"] for r in out["rows"])
    return out


def run_all(ids: list[str] | None = None) -> list[dict]:
    return [run_case(cid) for cid in (ids or list(CASE_BUILDERS))]
