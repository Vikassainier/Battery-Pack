"""Lumped transient thermal model of the pack (Phase 5).

    C · dT/dt = Q_gen − G_c·(T − T_in) − G_a·(T − T_amb)

    C   = N·m_cell·cp + C_extra                thermal capacity of the pack (uniform temperature)
    G_c = ε·ṁ·cp,cool                          effective coolant conductance (ε-NTU with an isothermal wall)
    G_a = UA to ambient

With sample-and-hold heat over a step the equation is linear with constant coefficients, so it is integrated
*exactly*:   T(t+Δt) = T_eq + (T − T_eq)·exp(−Δt/τ),   T_eq = (Q + G_c·T_in + G_a·T_amb)/(G_c + G_a),   τ = C/(G_c+G_a)
and the step-mean temperature (used for removed-heat bookkeeping, so energy closes to round-off) is
    T̄ = T_eq + (T − T_eq)·(τ/Δt)·(1 − exp(−Δt/τ))
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class ThermalNetwork:
    c_pack_j_k: float                       # total thermal capacity [J/K]
    g_cool_w_k: float = 0.0                 # ε·ṁ·cp (effective coolant conductance) [W/K]
    t_in_c: float = 25.0                    # coolant inlet temperature
    g_amb_w_k: float = 0.0                  # pack-to-ambient conductance [W/K]
    t_amb_c: float = 25.0
    eps: float = 0.0                        # effectiveness (for the coolant outlet temperature)
    m_cp_w_k: float = 0.0                   # ṁ·cp,cool [W/K]

    @property
    def g_total(self) -> float:
        return self.g_cool_w_k + self.g_amb_w_k

    @property
    def tau_s(self) -> float:
        return self.c_pack_j_k / self.g_total if self.g_total > 0 else math.inf


def thermal_step(t_c: float, q_w: float, dt: float, net: ThermalNetwork) -> tuple[float, float, float, float]:
    """Advance the pack temperature one step (exact for constant heat over the step).

    Returns (T_next, T_mean_over_step, mean coolant removal [W], mean ambient loss [W]).
    """
    g = net.g_total
    if dt <= 0.0:
        return t_c, t_c, net.g_cool_w_k * (t_c - net.t_in_c), net.g_amb_w_k * (t_c - net.t_amb_c)
    if g <= 0.0:
        t_next = t_c + q_w * dt / net.c_pack_j_k
        return t_next, 0.5 * (t_c + t_next), 0.0, 0.0
    t_eq = (q_w + net.g_cool_w_k * net.t_in_c + net.g_amb_w_k * net.t_amb_c) / g
    tau = net.c_pack_j_k / g
    x = dt / tau
    e = math.exp(-x)
    t_next = t_eq + (t_c - t_eq) * e
    t_mean = t_eq + (t_c - t_eq) * ((1.0 - e) / x if x > 1e-12 else 1.0)
    return t_next, t_mean, net.g_cool_w_k * (t_mean - net.t_in_c), net.g_amb_w_k * (t_mean - net.t_amb_c)


# ==================================================================================================
# pack thermal capacity / ambient coupling
# ==================================================================================================
PACK_FILL_FRACTION = 0.4        # cell volume / pack envelope volume (assumption used only for the ambient-UA estimate)
PACK_SHAPE_FACTOR = 7.0         # A_ext = 7·V^(2/3): flat pack with aspect ratio ~4:2:1


def pack_thermal_capacity(mass_kg: float, cp_j_kg_k: float, n_cells: int, extra_j_k: float = 0.0) -> float:
    """C_pack = N·m_cell·cp + C_extra  [J/K]."""
    return n_cells * mass_kg * cp_j_kg_k + extra_j_k


def cell_volume_m3(cell) -> float | None:
    if cell.form_factor == "cylindrical" and cell.diameter_mm and cell.height_mm:
        return math.pi / 4.0 * (cell.diameter_mm * 1e-3) ** 2 * cell.height_mm * 1e-3
    if cell.length_mm and cell.width_mm and cell.height_mm:
        return cell.length_mm * cell.width_mm * cell.height_mm * 1e-9
    if cell.diameter_mm and cell.height_mm:
        return math.pi / 4.0 * (cell.diameter_mm * 1e-3) ** 2 * cell.height_mm * 1e-3
    return None


def estimate_ambient_ua(cell, n_cells: int, h_w_m2k: float) -> tuple[float | None, dict]:
    """UA = h·A_ext with A_ext from the cell volume, an assumed fill fraction and a flat-pack shape factor."""
    v_cell = cell_volume_m3(cell)
    if v_cell is None:
        return None, {"reason": "cell dimensions unavailable"}
    v_pack = n_cells * v_cell / PACK_FILL_FRACTION
    area = PACK_SHAPE_FACTOR * v_pack ** (2.0 / 3.0)
    return h_w_m2k * area, {"cell_volume_l": v_cell * 1e3, "pack_volume_l": v_pack * 1e3, "external_area_m2": area, "h_w_m2k": h_w_m2k,
                            "fill_fraction": PACK_FILL_FRACTION, "shape_factor": PACK_SHAPE_FACTOR}


# ==================================================================================================
# design heat-load philosophies
# ==================================================================================================
def cumulative_energy_j(t: np.ndarray, q: np.ndarray) -> np.ndarray:
    """E(t_k) = ∫0^{t_k} q dt for sample-and-hold q (piece-wise linear in time)."""
    dt = np.diff(t)
    return np.concatenate([[0.0], np.cumsum(q[:-1] * dt)])


def moving_average_peak(t: np.ndarray, q: np.ndarray, window_s: float) -> tuple[float, float, float]:
    """Maximum *trailing* moving average of q over ``window_s``. Returns (value, time at which it occurs, window used).

    The window is shortened to the cycle duration if the cycle is shorter than the window.
    """
    dur = float(t[-1] - t[0])
    if dur <= 0:
        return float(q[0]), float(t[0]), window_s
    w = min(window_s, dur)
    e = cumulative_energy_j(t, q)
    mask = t - t[0] >= w - 1e-9
    e_back = np.interp(t[mask] - w, t, e)
    avg = (e[mask] - e_back) / w
    k = int(np.argmax(avg))
    return float(avg[k]), float(t[mask][k]), w


def sustained_heat(q_cell_fn, n_cells: int, soc_grid: list[float], t_design_c: float) -> dict:
    """Steady-state heat at a constant current: worst case over the SOC grid at the design temperature.

    ``q_cell_fn(soc, t_c) -> W/cell`` evaluates Joule + reversible heat at the rated continuous current.
    """
    vals = [(q_cell_fn(s, t_design_c), s) for s in soc_grid]
    q, s = max(vals)
    return {"q_cell_w": q, "q_pack_w": q * n_cells, "soc_pct": s}


def _removal_to_reach(t_c: float, q_gen: float, dt: float, target: float, c: float, g_a: float, t_amb: float) -> float:
    """Constant removal rate over the step that ends exactly at ``target`` (may be negative = heating needed)."""
    if dt <= 0:
        return 0.0
    if g_a <= 0:
        return q_gen - c * (target - t_c) / dt
    x = dt / (c / g_a)
    e = math.exp(-x)
    u = (target - t_c * e) / (1.0 - e) if (1.0 - e) > 1e-15 else target
    return q_gen + g_a * t_amb - g_a * u


def _run_capacity(t, q, c, t0, t_target, t_floor, g_a, t_amb, cap_w):
    """Constant removal ``cap_w`` whenever T > T_floor (never cooled below the floor). Returns (T_max, T array, removal array)."""
    n = len(t)
    T = [t0] * n
    rem = [0.0] * n
    temp = t0
    tmax = t0
    net = ThermalNetwork(c, 0.0, t_floor, g_a, t_amb)
    for k in range(n - 1):
        dt = t[k + 1] - t[k]
        q_r = cap_w if temp >= t_floor - 1e-9 else 0.0
        t_next, _, _, _ = thermal_step(temp, q[k] - q_r, dt, net)
        if q_r > 0 and t_next < t_floor:                               # do not overshoot below the floor
            q_r = max(0.0, min(cap_w, _removal_to_reach(temp, q[k], dt, t_floor, c, g_a, t_amb)))
            t_next, _, _, _ = thermal_step(temp, q[k] - q_r, dt, net)
        rem[k] = q_r
        temp = t_next
        T[k + 1] = temp
        tmax = max(tmax, temp)
    return tmax, np.asarray(T), np.asarray(rem)


def required_capacity_drive_cycle(t: np.ndarray, q_pack_w: np.ndarray, c_pack_j_k: float, t0_c: float, t_target_c: float,
                                  t_floor_c: float, g_amb_w_k: float = 0.0, t_amb_c: float = 25.0, tol_w: float = 0.5) -> dict:
    """Smallest constant heat-removal capacity that keeps the (lumped) pack below ``t_target_c`` over the cycle.

    Cooling removes ``Q_cap`` whenever the pack is warmer than ``t_floor_c`` (the coolant cannot cool below its own
    temperature); thermal mass buffers the peaks, so Q_average ≲ Q_cap ≲ Q_peak. Bisection on Q_cap. The heat series is
    held fixed (temperature feedback on resistance is neglected in this sizing step).
    """
    t = np.asarray(t, float)
    q = np.asarray(q_pack_w, float)
    tmax0, _, _ = _run_capacity(t, q, c_pack_j_k, t0_c, t_target_c, t_floor_c, g_amb_w_k, t_amb_c, 0.0)
    res = {"t_max_no_cooling_c": float(tmax0), "iterations": 0}
    if tmax0 <= t_target_c + 1e-9:
        res.update(q_cap_w=0.0, t_max_at_cap_c=float(tmax0), feasible=True,
                   note="The thermal mass absorbs the heat of this cycle: no active cooling is needed for a single pass "
                        "(repeat the cycle to check thermal soak).")
        return res
    lo, hi = 0.0, max(float(np.max(q)), 1.0)
    tmax_hi, _, _ = _run_capacity(t, q, c_pack_j_k, t0_c, t_target_c, t_floor_c, g_amb_w_k, t_amb_c, hi)
    n_it = 0
    while tmax_hi > t_target_c + 1e-6 and n_it < 6:                      # target below the start/floor: widen the bracket
        hi *= 2.0
        tmax_hi, _, _ = _run_capacity(t, q, c_pack_j_k, t0_c, t_target_c, t_floor_c, g_amb_w_k, t_amb_c, hi)
        n_it += 1
    if tmax_hi > t_target_c + 1e-6:
        res.update(q_cap_w=float("inf"), t_max_at_cap_c=float(tmax_hi), feasible=False,
                   note="The target temperature cannot be held even with cooling equal to several times the peak heat (target too close to the start temperature).")
        return res
    while hi - lo > tol_w and res["iterations"] < 60:
        mid = 0.5 * (lo + hi)
        tm, _, _ = _run_capacity(t, q, c_pack_j_k, t0_c, t_target_c, t_floor_c, g_amb_w_k, t_amb_c, mid)
        if tm > t_target_c + 1e-9:
            lo = mid
        else:
            hi = mid
        res["iterations"] += 1
    tm, T, rem = _run_capacity(t, q, c_pack_j_k, t0_c, t_target_c, t_floor_c, g_amb_w_k, t_amb_c, hi)
    res.update(q_cap_w=float(hi), t_max_at_cap_c=float(tm), feasible=True, removal_w=rem, t_c=T,
               note="Minimum constant cooling capacity for the transient (thermal mass considered).")
    return res


# ==================================================================================================
# non-uniformity (screening estimate)
# ==================================================================================================
def cell_to_cell_dt(q_removed_w: float, m_cp_w_k: float, q_cell_w: float, r_total_cell_k_w: float, n_plates: int,
                    series_flow: bool, heat_spread_frac: float, maldist_frac: float) -> dict:
    """Screening estimate of temperature non-uniformity.

    Coolant rise along the flow path  ΔT_path = Q_removed / (ṁ·cp)  (energy balance).
    Series plates: the whole pack sees the full rise, one plate sees ΔT_path / n_plates.
    Parallel plates: each plate sees the full ΔT_path; the weakest branch gets (1−δ_flow) of the flow -> rise / (1−δ_flow).
    Heat spread between cells (resistance / current sharing) adds  2·δ_Q·Q_cell·R_total,cell  (hot vs cold cell).
    """
    d_path = q_removed_w / m_cp_w_k if m_cp_w_k > 0 else 0.0
    d_gen = 2.0 * heat_spread_frac * q_cell_w * r_total_cell_k_w
    if series_flow:
        d_pack_cool, d_mod_cool = d_path, d_path / max(n_plates, 1)
    else:
        d_pack_cool, d_mod_cool = d_path / max(1.0 - maldist_frac, 1e-3), d_path
    return {"dt_coolant_path_k": d_path, "dt_generation_spread_k": d_gen, "dt_module_k": d_mod_cool + d_gen,
            "dt_pack_k": d_pack_cool + d_gen, "hot_cell_offset_k": 0.5 * (d_pack_cool + d_gen)}
