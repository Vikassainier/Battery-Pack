"""Time-marching electro-thermal simulation (sample-and-hold).

For every sample k (state at the start of the interval [t_k, t_{k+1})):

    R      = R(SOC_k, T_k)                       resistance model (Level 1-4)
    OCV    = OCV(SOC_k, T_k)
    I_cell = solve( P_batt/N = (OCV − I·R)·I )   or   I_pack/Np if the load is a current
    Q_joule = I²R ; Q_rev = −I·T·dU/dT ; Q_cell = Q_joule + Q_rev ; Q_module ; Q_pack = N·Q_cell
    SOC_{k+1} = SOC_k − I·Δt/(3600·C_eff)
    T_{k+1}   = exact lumped-thermal step (Phase 5) - only if a thermal network is supplied

The last sample has zero duration (its state is reported, it adds no energy).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .electrical import OcvModel, cell_current_from_power
from .heat import EntropicModel
from .interp import Interp1D
from .load import LoadProfile
from .pack import PackDerived
from .resistance import ResistanceModel
from .thermal import ThermalNetwork, thermal_step
from .units import KELVIN

REST_EPS_A = 1e-3


@dataclass
class SimConfig:
    couple_temperature: bool = True          # evaluate R/OCV at the simulated temperature (else at t0)
    soc_mode: str = "integrate"              # integrate | file
    soc_min_pct: float = 0.0
    soc_max_pct: float = 100.0


@dataclass
class SimResult:
    t: np.ndarray
    dt: np.ndarray
    soc_pct: np.ndarray
    i_cell: np.ndarray
    i_pack: np.ndarray
    i_module: np.ndarray
    c_rate: np.ndarray
    r_cell_ohm: np.ndarray
    ocv_v: np.ndarray
    v_cell: np.ndarray
    v_pack: np.ndarray
    p_terminal_w: np.ndarray
    p_chem_w: np.ndarray
    q_joule_cell: np.ndarray
    q_rev_cell: np.ndarray
    q_cell: np.ndarray
    q_module: np.ndarray
    q_pack: np.ndarray
    dudt_mv_k: np.ndarray
    t_cell_c: np.ndarray
    t_step_mean_c: np.ndarray
    t_coolant_out_c: np.ndarray
    q_cool_w: np.ndarray
    q_amb_w: np.ndarray
    feasible: np.ndarray
    state: list[str]
    flags: dict = field(default_factory=dict)


def simulate(load: LoadProfile, pack: PackDerived, rmodel: ResistanceModel, ocv: OcvModel, ent: EntropicModel,
             soc0_pct: float, t0_c: float, network: ThermalNetwork | None = None, cfg: SimConfig | None = None,
             cap_vs_temp: Interp1D | None = None) -> SimResult:
    cfg = cfg or SimConfig()
    t = load.t.tolist()
    n = len(t)
    N, Np, cap_nom = pack.n_cells, pack.np, pack.cell_capacity_ah
    vals = load.values.tolist()
    power_mode = load.kind == "power"
    soc_file = load.soc_file_pct.tolist() if (cfg.soc_mode == "file" and load.soc_file_pct is not None) else None

    soc = soc_file[0] if soc_file else float(soc0_pct)
    temp = float(t0_c)
    coupled = cfg.couple_temperature

    arr = {k: [0.0] * n for k in ("soc", "i", "r", "ocv", "qj", "qr", "dudt", "T", "Tm", "Tout", "qcool", "qamb")}
    feas = [True] * n
    soc_lo, soc_hi, first_depleted, first_over = 100.0, 0.0, None, None
    n_infeasible = 0
    r_ohm, ocv_fn, dudt_fn = rmodel.r_ohm, ocv.ocv, ent.dudt_v_per_k
    has_net = network is not None

    for k in range(n):
        dt = t[k + 1] - t[k] if k < n - 1 else 0.0
        soc_eval = 0.0 if soc < 0.0 else (100.0 if soc > 100.0 else soc)
        t_look = temp if coupled else t0_c
        x = vals[k]
        if power_mode:
            r = r_ohm(soc_eval, t_look, x < 0.0)
            v_oc = ocv_fn(soc_eval, t_look)
            i, ok = cell_current_from_power(x / N, v_oc, r)
            if not ok:
                feas[k] = False
                n_infeasible += 1
        else:
            i = x / Np
            r = r_ohm(soc_eval, t_look, i < 0.0)
            v_oc = ocv_fn(soc_eval, t_look)
        dudt = dudt_fn(soc_eval, t_look)
        qj = i * i * r
        qr = -i * (t_look + KELVIN) * dudt
        a = arr
        a["soc"][k], a["i"][k], a["r"][k], a["ocv"][k], a["qj"][k], a["qr"][k], a["dudt"][k] = soc, i, r, v_oc, qj, qr, dudt * 1e3
        a["T"][k] = temp
        if soc < cfg.soc_min_pct - 1e-9 and first_depleted is None:
            first_depleted = t[k]
        if soc > cfg.soc_max_pct + 1e-9 and first_over is None:
            first_over = t[k]
        soc_lo, soc_hi = min(soc_lo, soc), max(soc_hi, soc)

        # ---- state update -------------------------------------------------------------------------------------
        if has_net:
            t_next, t_mean, qc, qa = thermal_step(temp, (qj + qr) * N, dt, network)
            a["Tm"][k], a["qcool"][k], a["qamb"][k] = t_mean, qc, qa
            a["Tout"][k] = network.t_in_c + network.eps * (t_mean - network.t_in_c) if network.m_cp_w_k > 0 else float("nan")
            temp = t_next
        else:
            a["Tm"][k] = temp
            a["Tout"][k] = float("nan")
        if k < n - 1:
            if soc_file:
                soc = soc_file[k + 1]
            else:
                cap = cap_nom
                if cap_vs_temp is not None:
                    cap = cap_nom * cap_vs_temp(t_look) / 100.0
                soc = soc - i * dt / (3600.0 * cap) * 100.0

    t_np = np.asarray(t, float)
    dt_np = np.diff(t_np, append=t_np[-1])
    i_cell = np.asarray(arr["i"])
    i_pack = i_cell * Np
    r_np = np.asarray(arr["r"])
    ocv_np = np.asarray(arr["ocv"])
    v_cell = ocv_np - i_cell * r_np
    qj_np, qr_np = np.asarray(arr["qj"]), np.asarray(arr["qr"])
    q_cell = qj_np + qr_np
    if load.state_override is not None:
        state = list(load.state_override)
    else:
        state = ["discharge" if i > REST_EPS_A else (load.charge_label if i < -REST_EPS_A else "rest") for i in i_cell.tolist()]
    flags = {"n_infeasible": n_infeasible, "soc_min_seen": soc_lo, "soc_max_seen": soc_hi,
             "first_soc_below_min_s": first_depleted, "first_soc_above_max_s": first_over,
             "r_floored": rmodel.n_floored, "soc_source": "file" if soc_file else "integrated"}
    return SimResult(
        t=t_np, dt=dt_np, soc_pct=np.asarray(arr["soc"]), i_cell=i_cell, i_pack=i_pack, i_module=i_pack / pack.modules_in_parallel,
        c_rate=i_cell / cap_nom, r_cell_ohm=r_np, ocv_v=ocv_np, v_cell=v_cell, v_pack=v_cell * pack.ns,
        p_terminal_w=v_cell * pack.ns * i_pack, p_chem_w=ocv_np * i_cell * N, q_joule_cell=qj_np, q_rev_cell=qr_np, q_cell=q_cell,
        q_module=q_cell * pack.cells_per_module, q_pack=q_cell * N, dudt_mv_k=np.asarray(arr["dudt"]),
        t_cell_c=np.asarray(arr["T"]), t_step_mean_c=np.asarray(arr["Tm"]), t_coolant_out_c=np.asarray(arr["Tout"]),
        q_cool_w=np.asarray(arr["qcool"]), q_amb_w=np.asarray(arr["qamb"]), feasible=np.asarray(feas), state=state, flags=flags)
