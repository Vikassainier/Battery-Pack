"""Phase 5 - lumped transient thermal model, design heat-load philosophies, capacity sizing.

Hand calculations (120 cells × 2.05 kg × 1000 J/kgK  ->  C = 246 000 J/K ; Q_pack = 4800 W from 200 A · 1 mΩ):
  adiabatic:              T(600 s) = 25 + 4800·600/246000                       = 36.7073 °C
  coolant G_c = 200 W/K:  T_eq = 25 + 4800/200 = 49 °C ; τ = 246000/200 = 1230 s
                          T(600 s) = 49 − 24·exp(−600/1230)                       = 34.2646 °C
  min. constant cooling for T ≤ 35 °C over 600 s (adiabatic, floor 25 °C, T0 = 25 °C):
                          T_end = 25 + (4800 − Q_c)·600/246000 = 35   ->   Q_c = 700 W
  pulse 4800 W for 300 s, T_target = 28 °C:  25 + (4800 − Q_c)·300/246000 = 28 ->  Q_c = 2340 W
  moving average of a 10 kW × 100 s pulse: window 100 s -> 10 kW ; 200 s -> 5 kW ; 400 s (whole cycle) -> 2.5 kW
"""
import math

import numpy as np
import pytest

from battery_thermal.engine.electrical import OcvModel
from battery_thermal.engine.heat import build_entropic_model
from battery_thermal.engine.interp import Interp1D
from battery_thermal.engine.load import LoadProfile
from battery_thermal.engine.pack import derive_pack
from battery_thermal.engine.resistance import build_resistance_model
from battery_thermal.engine.schemas import CellSpec, Curve, EntropicSettings, PackConfig, ResistanceSettings
from battery_thermal.engine.simulation import SimConfig, simulate
from battery_thermal.engine.thermal import (
    ThermalNetwork, cell_to_cell_dt, estimate_ambient_ua, moving_average_peak, pack_thermal_capacity,
    required_capacity_drive_cycle, sustained_heat, thermal_step,
)

C_PACK = 246_000.0
PACK = PackConfig(ns=120, np=1, n_modules=10, cells_per_module=12)


def cell(**kw):
    base = dict(capacity_ah=100, v_nom=3.2, r_dc_mohm=1.0, mass_kg=2.05, cp_j_kg_k=1000.0, confirmed=True)
    base.update(kw)
    return CellSpec(**base)


def run(values, t, net=None, c=None, res=None, coupled=True, t0=25.0):
    c = c or cell()
    d = derive_pack(c, PACK)
    load = LoadProfile(t=np.asarray(t, float), kind="current", values=np.asarray(values, float), source="test")
    sim = simulate(load, d, build_resistance_model(c, res or ResistanceSettings()), OcvModel(c),
                   build_entropic_model(c, EntropicSettings(mode="excluded")), 90.0, t0, network=net,
                   cfg=SimConfig(couple_temperature=coupled))
    return sim


def test_thermal_capacity_hand_calc():
    assert pack_thermal_capacity(2.05, 1000.0, 120) == pytest.approx(246_000.0)
    assert pack_thermal_capacity(2.05, 1000.0, 120, extra_j_k=50_000) == pytest.approx(296_000.0)


def test_adiabatic_temperature_rise_hand_calc():
    t = np.arange(0, 601.0)
    sim = run(np.full(601, 200.0), t, ThermalNetwork(C_PACK))
    assert sim.t_cell_c[-1] == pytest.approx(25 + 4800 * 600 / 246000, abs=1e-9)         # 36.7073 °C
    assert sim.t_cell_c[-1] == pytest.approx(36.7073, abs=1e-3)


def test_coolant_step_response_matches_analytic_solution():
    net = ThermalNetwork(C_PACK, g_cool_w_k=200.0, t_in_c=25.0)
    t = np.arange(0, 601.0)
    sim = run(np.full(601, 200.0), t, net)
    tau, teq = 246000 / 200, 25 + 4800 / 200
    assert tau == pytest.approx(1230.0) and teq == pytest.approx(49.0)
    analytic = teq + (25 - teq) * np.exp(-t / tau)
    assert np.allclose(sim.t_cell_c, analytic, rtol=0, atol=1e-9)                        # exact integrator
    assert sim.t_cell_c[-1] == pytest.approx(34.2646, abs=1e-3)


def test_step_size_independence_non_uniform_time_base():
    net = ThermalNetwork(C_PACK, g_cool_w_k=200.0, t_in_c=25.0, g_amb_w_k=20.0, t_amb_c=30.0)
    t = np.array([0.0, 1.0, 7.0, 8.0, 60.0, 200.0, 201.0, 600.0])
    sim = run(np.full(len(t), 200.0), t, net)
    g = 220.0
    teq = (4800 + 200 * 25 + 20 * 30) / g
    tau = C_PACK / g
    assert np.allclose(sim.t_cell_c, teq + (25 - teq) * np.exp(-t / tau), atol=1e-9)


def test_piecewise_heat_decay_after_load_removed():
    net = ThermalNetwork(C_PACK, g_cool_w_k=200.0, t_in_c=25.0)
    t = np.arange(0, 1201.0)
    i = np.where(t < 600, 200.0, 0.0)
    sim = run(i, t, net)
    t600 = 49 - 24 * math.exp(-600 / 1230)
    assert sim.t_cell_c[600] == pytest.approx(t600, abs=1e-9)
    assert sim.t_cell_c[1200] == pytest.approx(25 + (t600 - 25) * math.exp(-600 / 1230), abs=1e-9)


def test_energy_closure_generated_equals_stored_plus_removed_plus_ambient():
    net = ThermalNetwork(C_PACK, g_cool_w_k=150.0, t_in_c=22.0, g_amb_w_k=25.0, t_amb_c=32.0, eps=0.6, m_cp_w_k=250.0)
    t = np.arange(0, 1500.0, 5.0)
    i = 150 + 120 * np.sin(t / 90.0) - 80 * (t > 800)
    sim = run(i, t, net)
    e_gen = np.sum(sim.q_pack * sim.dt)
    e_stored = C_PACK * (sim.t_cell_c[-1] - sim.t_cell_c[0])
    e_removed = np.sum(sim.q_cool_w * sim.dt) + np.sum(sim.q_amb_w * sim.dt)
    assert e_gen == pytest.approx(e_stored + e_removed, rel=1e-12)


def test_coolant_energy_balance_and_outlet_temperature():
    net = ThermalNetwork(C_PACK, g_cool_w_k=200.0, t_in_c=25.0, eps=0.5, m_cp_w_k=400.0)     # G_c = ε·ṁcp = 200
    sim = run(np.full(301, 200.0), np.arange(301.0), net)
    k = 100
    assert sim.t_coolant_out_c[k] == pytest.approx(25 + 0.5 * (sim.t_step_mean_c[k] - 25))
    assert sim.q_cool_w[k] == pytest.approx(400.0 * (sim.t_coolant_out_c[k] - 25.0), rel=1e-12)   # Q = ṁ·cp·(T_out − T_in)


def test_step_mean_temperature_and_zero_step():
    net = ThermalNetwork(C_PACK, g_cool_w_k=200.0, t_in_c=25.0)
    t_next, t_mean, qc, qa = thermal_step(30.0, 4800.0, 0.0, net)
    assert t_next == 30.0 and qc == pytest.approx(200 * 5.0) and qa == 0.0
    tn, tm, _, _ = thermal_step(25.0, 4800.0, 10.0, net)
    assert 25.0 < tm < tn                                                                 # mean lies between start and end


def test_time_constant():
    assert ThermalNetwork(C_PACK, g_cool_w_k=200.0).tau_s == pytest.approx(1230.0)
    assert ThermalNetwork(C_PACK, g_cool_w_k=200.0, g_amb_w_k=30.0).tau_s == pytest.approx(246000 / 230)
    assert math.isinf(ThermalNetwork(C_PACK).tau_s)


# --------------------------------------------------------------------------------- resistance-temperature coupling
def test_temperature_coupling_lowers_heat_as_the_pack_warms():
    c = cell(r_dc_mohm=None, r_vs_temp=Curve(x=[0, 25, 50, 75], y=[1.2, 0.5, 0.4, 0.35]))
    t = np.arange(0, 1201.0)
    net = ThermalNetwork(C_PACK, g_cool_w_k=100.0, t_in_c=25.0)
    hot = run(np.full(1201, 200.0), t, net, c=c, coupled=True)
    fixed = run(np.full(1201, 200.0), t, net, c=c, coupled=False)
    assert hot.t_cell_c[-1] > 30
    assert hot.q_pack[-1] < hot.q_pack[0]                                                 # warmer -> lower R -> less heat
    assert fixed.q_pack[-1] == pytest.approx(fixed.q_pack[0])                             # decoupled: R frozen at the start temperature
    tab = Interp1D([0, 25, 50, 75], [1.2, 0.5, 0.4, 0.35])
    for k in (0, 400, 1200):                                                              # per-sample identity with the table
        assert hot.q_cell[k] == pytest.approx(200.0 ** 2 * tab(hot.t_cell_c[k]) * 1e-3, rel=1e-12)
    assert fixed.t_cell_c[-1] < hot.t_cell_c[-1] + 5                                       # thermal state still evolves when decoupled


# --------------------------------------------------------------------------------- design heat-load philosophies
def pulse_series():
    t = np.arange(0, 401.0)
    q = np.where((t >= 100) & (t < 200), 10_000.0, 0.0)
    return t, q


def test_moving_average_peak_hand_calc():
    t, q = pulse_series()
    assert moving_average_peak(t, q, 50)[0] == pytest.approx(10_000.0)
    assert moving_average_peak(t, q, 100)[0] == pytest.approx(10_000.0)
    v, tt, w = moving_average_peak(t, q, 200)
    assert v == pytest.approx(5_000.0) and w == 200
    v2, _, w2 = moving_average_peak(t, q, 1000)                                            # window longer than the cycle
    assert v2 == pytest.approx(1e6 / 400) and w2 == pytest.approx(400)
    assert moving_average_peak(t, q, 100)[1] >= 199                                        # trailing window ends after the pulse starts


def test_peak_vs_moving_average_vs_average_ordering():
    rng = np.random.default_rng(1)
    t = np.arange(0, 1800.0)
    q = np.abs(rng.normal(3000, 2500, len(t)))
    peak = q.max()
    ma = moving_average_peak(t, q, 300)[0]
    avg = np.sum(q[:-1]) / (len(t) - 1)
    assert avg <= ma <= peak


def test_sustained_heat_takes_worst_soc_at_design_temperature():
    f = lambda soc, t: (100.0 ** 2) * (1.0 + 0.002 * abs(soc - 50)) * 1e-3          # 10 W/cell at 50 % SOC, higher at the ends
    s = sustained_heat(f, 120, [10, 50, 90, 100], 40.0)
    assert s["soc_pct"] == 100 and s["q_cell_w"] == pytest.approx(10 * 1.10) and s["q_pack_w"] == pytest.approx(120 * 11.0)   # |100−50|·0.002 = +10 %
    assert sustained_heat(lambda soc, t: 10.0, 120, [50], 40.0)["q_pack_w"] == pytest.approx(1200.0)


# --------------------------------------------------------------------------------- drive-cycle sizing (thermal mass considered)
def test_required_capacity_hand_calc_constant_load():
    t = np.arange(0, 601.0)
    q = np.full(601, 4800.0)
    r = required_capacity_drive_cycle(t, q, C_PACK, 25.0, 35.0, 25.0)
    assert r["feasible"] and r["q_cap_w"] == pytest.approx(700.0, abs=1.0)
    assert r["t_max_at_cap_c"] <= 35.0 + 1e-6 and r["t_max_no_cooling_c"] == pytest.approx(36.7073, abs=1e-3)
    assert r["q_cap_w"] < q.max()                                                          # NOT the peak heat: thermal mass buffers it


def test_no_active_cooling_needed_when_thermal_mass_absorbs_the_cycle():
    t = np.arange(0, 601.0)
    r = required_capacity_drive_cycle(t, np.full(601, 4800.0), C_PACK, 25.0, 40.0, 25.0)
    assert r["q_cap_w"] == 0.0 and "thermal mass absorbs" in r["note"]


def test_pulse_capacity_hand_calc():
    t = np.arange(0, 601.0)
    q = np.where(t < 300, 4800.0, 0.0)
    r = required_capacity_drive_cycle(t, q, C_PACK, 25.0, 28.0, 25.0)
    assert r["q_cap_w"] == pytest.approx(2340.0, abs=1.0)


def test_capacity_approaches_average_heat_for_long_duty():
    t = np.arange(0, 40_001.0, 100.0)
    r = required_capacity_drive_cycle(t, np.full(len(t), 4800.0), C_PACK, 25.0, 35.0, 25.0)
    assert r["q_cap_w"] == pytest.approx(4800 - 10 * 246000 / 40000, abs=1.5)              # 4738.5 W -> tends to the average heat
    short = required_capacity_drive_cycle(t[:7], np.full(7, 4800.0), C_PACK, 25.0, 35.0, 25.0)
    assert short["q_cap_w"] < r["q_cap_w"]                                                 # longer duty needs more capacity


def test_capacity_accounts_for_ambient_exchange_and_start_temperature():
    t = np.arange(0, 601.0)
    q = np.full(601, 4800.0)
    base = required_capacity_drive_cycle(t, q, C_PACK, 25.0, 35.0, 25.0)["q_cap_w"]
    warm = required_capacity_drive_cycle(t, q, C_PACK, 30.0, 35.0, 25.0)["q_cap_w"]
    cool_amb = required_capacity_drive_cycle(t, q, C_PACK, 25.0, 35.0, 25.0, g_amb_w_k=50.0, t_amb_c=15.0)["q_cap_w"]
    assert warm > base > cool_amb                                                          # hotter start needs more; cold ambient helps


def test_capacity_infeasible_when_target_below_start():
    t = np.arange(0, 101.0)
    r = required_capacity_drive_cycle(t, np.full(101, 1000.0), C_PACK, 30.0, 29.0, 25.0)
    assert r["feasible"] is False or r["q_cap_w"] > 0                                      # cannot start above the target; never silently 0


# --------------------------------------------------------------------------------- ambient coupling & uniformity
def test_ambient_ua_estimate_hand_calc():
    c = cell(length_mm=174, width_mm=71, height_mm=207, form_factor="prismatic")
    ua, info = estimate_ambient_ua(c, 120, 5.0)
    v_pack = 120 * 174 * 71 * 207e-9 / 0.4
    assert info["pack_volume_l"] == pytest.approx(v_pack * 1e3)
    assert ua == pytest.approx(5.0 * 7.0 * v_pack ** (2 / 3)) and ua == pytest.approx(29.3, abs=0.1)
    cyl = cell(form_factor="cylindrical", diameter_mm=21, height_mm=70, length_mm=None)
    assert estimate_ambient_ua(cyl, 100, 5.0)[1]["cell_volume_l"] == pytest.approx(math.pi / 4 * 0.021 ** 2 * 0.070 * 1e3)
    assert estimate_ambient_ua(cell(), 120, 5.0)[0] is None                                # no dimensions -> no silent guess


def test_ambient_loss_reduces_temperature_rise():
    t = np.arange(0, 601.0)
    warm = run(np.full(601, 200.0), t, ThermalNetwork(C_PACK, g_amb_w_k=30.0, t_amb_c=25.0), t0=25.0)
    adiabatic = run(np.full(601, 200.0), t, ThermalNetwork(C_PACK), t0=25.0)
    assert warm.t_cell_c[-1] < adiabatic.t_cell_c[-1]
    assert warm.q_amb_w[-1] > 0


def test_cell_to_cell_dt_hand_calc():
    # ṁcp = 800 W/K, Q_removed = 4800 W -> ΔT_path = 6 K ; heat spread 10 %, Q_cell = 40 W, R_total = 0.5 K/W -> 2·0.1·40·0.5 = 4 K
    series = cell_to_cell_dt(4800.0, 800.0, 40.0, 0.5, 10, True, 0.10, 0.10)
    assert series["dt_coolant_path_k"] == pytest.approx(6.0) and series["dt_generation_spread_k"] == pytest.approx(4.0)
    assert series["dt_pack_k"] == pytest.approx(10.0) and series["dt_module_k"] == pytest.approx(0.6 + 4.0)
    par = cell_to_cell_dt(4800.0, 800.0, 40.0, 0.5, 10, False, 0.10, 0.10)
    assert par["dt_module_k"] == pytest.approx(10.0) and par["dt_pack_k"] == pytest.approx(6.0 / 0.9 + 4.0)
    assert par["hot_cell_offset_k"] == pytest.approx(par["dt_pack_k"] / 2)
    assert cell_to_cell_dt(0.0, 0.0, 0.0, 0.5, 1, False, 0.1, 0.1)["dt_pack_k"] == 0.0
