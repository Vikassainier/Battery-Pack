"""Scalar results derived from a simulation (peaks, averages, energies). All integrals are sample-and-hold."""
from __future__ import annotations

import numpy as np

from .pack import PackDerived
from .simulation import SimResult


def _int(x: np.ndarray, dt: np.ndarray) -> float:
    return float(np.sum(x * dt))


def summarize_heat(sim: SimResult, pack: PackDerived) -> dict:
    dt = sim.dt
    dur = float(sim.t[-1] - sim.t[0])
    n = pack.n_cells
    i_cell_max_idx = int(np.argmax(sim.q_cell))
    i_pk = int(np.argmax(sim.q_pack))
    e_joule_cell = _int(sim.q_joule_cell, dt)
    e_rev_cell = _int(sim.q_rev_cell, dt)
    e_cell = e_joule_cell + e_rev_cell
    p_term, p_chem = sim.p_terminal_w, sim.p_chem_w
    e_term_dis = _int(np.clip(p_term, 0, None), dt) / 3.6e6
    e_term_chg = -_int(np.clip(p_term, None, 0), dt) / 3.6e6
    e_chem_dis = _int(np.clip(p_chem, 0, None), dt) / 3.6e6
    e_chem_chg = -_int(np.clip(p_chem, None, 0), dt) / 3.6e6
    e_heat_pack_kwh = _int(sim.q_pack, dt) / 3.6e6
    e_joule_pack_kwh = e_joule_cell * n / 3.6e6
    c = sim.c_rate
    i_dis = sim.i_cell > 0
    out = {
        "duration_s": dur, "n_samples": int(len(sim.t)),
        # instantaneous
        "max_cell_heat_w": float(sim.q_cell.max()), "t_max_cell_heat_s": float(sim.t[i_cell_max_idx]),
        "max_module_heat_w": float(sim.q_module.max()), "max_pack_heat_kw": float(sim.q_pack.max() / 1000.0),
        "t_max_pack_heat_s": float(sim.t[i_pk]), "i_pk": i_pk,
        "min_pack_heat_kw": float(sim.q_pack.min() / 1000.0),
        # average (time weighted)
        "avg_cell_heat_w": e_cell / dur if dur > 0 else float(sim.q_cell[0]),
        "avg_module_heat_w": e_cell * pack.cells_per_module / dur if dur > 0 else float(sim.q_module[0]),
        "avg_pack_heat_kw": e_cell * n / dur / 1000.0 if dur > 0 else float(sim.q_pack[0] / 1000.0),
        # energy
        "total_heat_kwh": e_heat_pack_kwh, "joule_heat_kwh": e_joule_pack_kwh, "reversible_heat_kwh": e_rev_cell * n / 3.6e6,
        "electrical": {
            "terminal_discharge_kwh": e_term_dis, "terminal_charge_kwh": e_term_chg, "terminal_net_kwh": e_term_dis - e_term_chg,
            "chemical_discharge_kwh": e_chem_dis, "chemical_charge_kwh": e_chem_chg,
            "discharge_efficiency_pct": 100.0 * e_term_dis / e_chem_dis if e_chem_dis > 0 else None,
            "heat_to_throughput_pct": 100.0 * e_heat_pack_kwh / (e_term_dis + e_term_chg) if (e_term_dis + e_term_chg) > 0 else None,
        },
        # C-rate / current
        "max_discharge_c": float(max(c.max(), 0.0)), "max_charge_c": float(-min(c.min(), 0.0)),
        "rms_c_rate": float(np.sqrt(np.sum(c * c * dt) / dur)) if dur > 0 else float(abs(c[0])),
        "max_cell_current_a": float(np.abs(sim.i_cell).max()), "max_pack_current_a": float(np.abs(sim.i_pack).max()),
        "max_module_current_a": float(np.abs(sim.i_module).max()),
        "soc_start_pct": float(sim.soc_pct[0]), "soc_end_pct": float(sim.soc_pct[-1]),
        "soc_min_pct": float(sim.soc_pct.min()), "soc_max_pct": float(sim.soc_pct.max()),
        "r_cell_min_mohm": float(sim.r_cell_ohm.min() * 1e3), "r_cell_max_mohm": float(sim.r_cell_ohm.max() * 1e3),
        "r_cell_mean_mohm": float(sim.r_cell_ohm.mean() * 1e3),
        "v_cell_min": float(sim.v_cell.min()), "v_cell_max": float(sim.v_cell.max()),
        "v_pack_min": float(sim.v_pack.min()), "v_pack_max": float(sim.v_pack.max()),
        "peak_terminal_power_kw": float(p_term.max() / 1000.0), "peak_charge_power_kw": float(-min(p_term.min(), 0.0) / 1000.0),
    }
    return out
