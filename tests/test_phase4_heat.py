"""Phase 4 - electrical + heat-generation model, verified against hand calculations.

VALIDATION CASE 1 (constant current)
    cell 100 Ah, 3.2 V, 1 mΩ ; pack 120S1P ; 200 A discharge
    I_cell = 200 A ; C-rate = 2 C
    Q_cell = I²R  = 200² × 0.001 = 40 W
    Q_pack = Ns × Q_cell = 120 × 40 = 4800 W            (module of 12 cells: 480 W)
    Terminal: V_cell = 3.2 − 0.2 = 3.0 V ; V_pack = 360 V ; P = 72 kW ; chemical power = 76.8 kW
    => heat is 6.25 % of the chemical power; electrical output (72 kW) is NOT heat.

VALIDATION CASE 2 (time-varying cycle, piece-wise constant pack current, R = 1 mΩ, 120S1P, 100 Ah)
    0-100 s: 100 A     100-200 s: 300 A     200-250 s: −150 A (regen)     250-300 s: 0 A
    Q_cell(t) = I²R  =  10 W | 90 W | 22.5 W | 0 W
    E_cell = 10·100 + 90·100 + 22.5·50 = 11 125 J ;  E_pack = 120 · 11 125 = 1 335 000 J = 0.370833 kWh
    peak pack heat = 120·90 = 10.8 kW ; average = 1 335 000 / 300 = 4450 W
    ΔAh = (100·100 + 300·100 − 150·50)/3600 = 9.02778 Ah  =>  SOC_end = 90 − 9.02778 = 80.9722 %
"""
import math

import numpy as np
import pytest

from battery_thermal.engine.electrical import OcvModel, cell_current_from_power, soc_step, terminal_voltage
from battery_thermal.engine.heat import (
    EntropicModelError, build_entropic_model, entropic_heat_w, joule_heat_w,
)
from battery_thermal.engine.interp import OutOfRangeError
from battery_thermal.engine.load import LoadProfile
from battery_thermal.engine.pack import derive_pack
from battery_thermal.engine.resistance import ResistanceModelError, available_levels, build_resistance_model
from battery_thermal.engine.schemas import (
    CellSpec, Curve, EntropicSettings, Grid2D, PackConfig, ResistanceSettings,
)
from battery_thermal.engine.simulation import SimConfig, simulate
from battery_thermal.engine.summary import summarize_heat


def cell(**kw):
    base = dict(capacity_ah=100, v_nom=3.2, r_dc_mohm=1.0, confirmed=True)
    base.update(kw)
    return CellSpec(**base)


PACK = PackConfig(ns=120, np=1, n_modules=10, cells_per_module=12)


def run(load_kind, values, t, c=None, pk=PACK, res=None, ent=None, soc0=90.0, t0=25.0, **kw):
    c = c or cell()
    d = derive_pack(c, pk)
    load = LoadProfile(t=np.asarray(t, float), kind=load_kind, values=np.asarray(values, float), source="test",
                       state_override=kw.pop("state_override", None), soc_file_pct=kw.pop("soc_file", None))
    rm = build_resistance_model(c, res or ResistanceSettings())
    ocv = OcvModel(c)
    em = build_entropic_model(c, ent or EntropicSettings(mode="excluded"))
    sim = simulate(load, d, rm, ocv, em, soc0, t0, cfg=kw.pop("cfg", None), cap_vs_temp=kw.pop("cap_vs_temp", None))
    return sim, d


# --------------------------------------------------------------------------------- validation case 1
def test_validation_case_1_constant_current_hand_calc():
    t = np.arange(0, 61.0)
    sim, d = run("current", np.full(61, 200.0), t)
    assert sim.i_cell[10] == pytest.approx(200.0)
    assert sim.c_rate[10] == pytest.approx(2.0)
    assert sim.q_joule_cell[10] == pytest.approx(40.0, abs=1e-12)                    # I²R
    assert sim.q_cell[10] == pytest.approx(40.0, abs=1e-12)                          # entropic explicitly excluded
    assert sim.q_pack[10] == pytest.approx(120 * 40.0, abs=1e-9)                     # Ns × Q_cell
    assert sim.q_module[10] == pytest.approx(480.0, abs=1e-9)
    # closed form of the same number, independent of the engine
    assert sim.q_pack[10] == pytest.approx(d.ns * d.np * 200.0 ** 2 * 1e-3, rel=1e-12)
    s = summarize_heat(sim, d)
    assert s["max_pack_heat_kw"] == pytest.approx(4.8) and s["avg_pack_heat_kw"] == pytest.approx(4.8)
    assert s["total_heat_kwh"] == pytest.approx(4800 * 60 / 3.6e6)                    # 0.08 kWh
    assert sim.soc_pct[-1] == pytest.approx(90 - 200 * 60 / (3600 * 100) * 100)       # 86.6667 %


def test_validation_case_1_electrical_energy_is_not_heat():
    sim, d = run("current", np.full(61, 200.0), np.arange(0, 61.0))
    assert sim.v_cell[5] == pytest.approx(3.0) and sim.v_pack[5] == pytest.approx(360.0)
    assert sim.p_terminal_w[5] == pytest.approx(72000.0)                             # electrical output
    assert sim.p_chem_w[5] == pytest.approx(76800.0)                                 # internal power
    assert sim.q_pack[5] / sim.p_chem_w[5] == pytest.approx(0.0625)                  # 6.25 % ends up as heat
    s = summarize_heat(sim, d)
    assert s["electrical"]["terminal_discharge_kwh"] == pytest.approx(72000 * 60 / 3.6e6)
    assert s["electrical"]["discharge_efficiency_pct"] == pytest.approx(93.75)
    # identity: chemical energy = terminal energy + Joule loss (exact by construction, reversible heat excluded)
    assert s["electrical"]["chemical_discharge_kwh"] == pytest.approx(s["electrical"]["terminal_discharge_kwh"] + s["total_heat_kwh"])


def test_validation_case_1_via_power_input_gives_same_current():
    # 72 kW terminal power -> quadratic  R I² − OCV I + p = 0 has the exact root I = 200 A
    sim, d = run("power", np.full(61, 72000.0), np.arange(0, 61.0))
    assert sim.i_cell[3] == pytest.approx(200.0, rel=1e-12)
    assert sim.q_pack[3] == pytest.approx(4800.0, rel=1e-9)
    assert sim.p_terminal_w[3] == pytest.approx(72000.0, rel=1e-12)
    assert sim.feasible.all()


# --------------------------------------------------------------------------------- validation case 2
CASE2_T = np.arange(0, 301.0)


def case2_current():
    i = np.zeros(301)
    i[0:100] = 100.0
    i[100:200] = 300.0
    i[200:250] = -150.0
    return i


def test_validation_case_2_time_varying_hand_calc():
    sim, d = run("current", case2_current(), CASE2_T)
    s = summarize_heat(sim, d)
    assert sim.q_cell[50] == pytest.approx(10.0) and sim.q_cell[150] == pytest.approx(90.0)
    assert sim.q_cell[225] == pytest.approx(22.5) and sim.q_cell[275] == pytest.approx(0.0)
    assert s["max_pack_heat_kw"] == pytest.approx(10.8) and s["max_cell_heat_w"] == pytest.approx(90.0)
    assert s["max_module_heat_w"] == pytest.approx(1080.0)
    assert s["avg_pack_heat_kw"] == pytest.approx(4.45, rel=1e-12)
    assert s["total_heat_kwh"] == pytest.approx(1_335_000 / 3.6e6, rel=1e-12)          # 0.370833 kWh
    assert s["soc_end_pct"] == pytest.approx(90 - 32500 / 3600, rel=1e-12)             # 80.9722 %
    assert s["max_discharge_c"] == pytest.approx(3.0) and s["max_charge_c"] == pytest.approx(1.5)
    assert sim.state[50] == "discharge" and sim.state[225] == "regen" and sim.state[275] == "rest"


def test_validation_case_2_matches_independent_python_loop_with_soc_dependent_r():
    """Cross-check against a plain-Python re-implementation (power-driven, R = f(SOC), OCV curve)."""
    rs = Curve(x=[0, 50, 100], y=[0.9, 0.5, 0.6])
    oc = Curve(x=[0, 50, 100], y=[3.0, 3.25, 3.45])
    c = cell(r_dc_mohm=None, r_vs_soc=rs, ocv_vs_soc=oc)
    t = np.arange(0, 121.0)
    p = np.where(t < 60, 30e3, np.where(t < 100, -10e3, 0.0))
    sim, d = run("power", p, t, c=c, soc0=80.0)

    soc, N, cap = 80.0, 120, 100.0
    q_ref, i_ref = [], []
    for k in range(121):
        r = np.interp(soc, rs.x, rs.y) * 1e-3
        v = np.interp(soc, oc.x, oc.y)
        pc = p[k] / N
        i = 2 * pc / (v + math.sqrt(v * v - 4 * r * pc))
        q_ref.append(N * i * i * r)
        i_ref.append(i)
        soc -= i * 1.0 / 3600 / cap * 100
    assert np.allclose(sim.q_pack, q_ref, rtol=1e-12) and np.allclose(sim.i_cell, i_ref, rtol=1e-12)
    assert sim.soc_pct[-1] == pytest.approx(soc + i_ref[-1] * 0 - 0, abs=0.01 + 100 * i_ref[-1] / 3600 / cap)


# --------------------------------------------------------------------------------- identities
def test_soc_and_energy_conservation_identities():
    rs = Curve(x=[0, 50, 100], y=[0.9, 0.5, 0.6])
    c = cell(r_dc_mohm=None, r_vs_soc=rs, ocv_vs_soc=Curve(x=[0, 100], y=[3.0, 3.4]))
    t = np.arange(0, 400.0, 2.0)
    p = 20e3 * np.sin(np.linspace(0, 6 * np.pi, len(t)))
    sim, d = run("power", p, t, c=c, soc0=60.0)
    dt = sim.dt
    # coulomb counting: ΔSOC · C = Σ I dt
    assert (sim.soc_pct[0] - sim.soc_pct[-1]) / 100 * 100.0 * 3600 == pytest.approx(np.sum(sim.i_cell[:-1] * dt[:-1]), rel=1e-9)
    # P_chem = P_terminal + N·I²R  at every sample (entropic excluded)
    assert np.allclose(sim.p_chem_w, sim.p_terminal_w + sim.q_pack, rtol=1e-10, atol=1e-9)
    # terminal power equals the request wherever it was feasible
    assert np.allclose(sim.p_terminal_w[sim.feasible], p[sim.feasible], rtol=1e-9, atol=1e-6)


def test_infeasible_power_is_flagged_and_limited():
    # max deliverable power per cell = OCV²/(4R) = 3.2²/(4·0.001) = 2560 W  -> 120 cells: 307.2 kW
    sim, d = run("power", np.full(11, 400e3), np.arange(0, 11.0))
    assert not sim.feasible.any() and sim.flags["n_infeasible"] == 11
    assert sim.i_cell[0] == pytest.approx(3.2 / (2 * 1e-3))                             # 1600 A at the maximum-power point
    assert sim.p_terminal_w[0] == pytest.approx(307200.0, rel=1e-9)
    i, ok = cell_current_from_power(2560.0, 3.2, 1e-3)
    assert ok and i == pytest.approx(1600.0)                                            # discriminant exactly zero


def test_quadratic_current_solution_hand_values():
    # p = 600 W, OCV 3.2, R = 1 mΩ -> I = 200 A ; regen p = −300 W -> I = −2·300/(3.2+√(10.24+1.2)) = −93.8 A? (check by substitution)
    i, ok = cell_current_from_power(600.0, 3.2, 1e-3)
    assert ok and i == pytest.approx(200.0)
    i2, _ = cell_current_from_power(-300.0, 3.2, 1e-3)
    assert terminal_voltage(3.2, i2, 1e-3) * i2 == pytest.approx(-300.0, rel=1e-12)
    assert cell_current_from_power(0.0, 3.2, 1e-3)[0] == 0.0
    assert soc_step(90.0, 100.0, 36.0, 100.0) == pytest.approx(89.0)                     # 100 A · 36 s = 1 Ah of 100 Ah


# --------------------------------------------------------------------------------- resistance models
RS = Curve(x=[0, 50, 100], y=[0.8, 0.5, 0.6])
RT = Curve(x=[0, 25, 50], y=[1.2, 0.5, 0.4])


def test_level_1_constant():
    m = build_resistance_model(cell(r_dc_mohm=0.7), ResistanceSettings())
    assert m.level == 1 and m.r_ohm(10, -5) == pytest.approx(0.7e-3) and "constant" in m.description


def test_level_2_soc_hand_values():
    m = build_resistance_model(cell(r_dc_mohm=None, r_vs_soc=RS), ResistanceSettings())
    assert m.level == 2
    assert m.r_ohm(25, 30) == pytest.approx(0.65e-3) and m.r_ohm(75, 30) == pytest.approx(0.55e-3)
    assert m.r_ohm(25, -40) == pytest.approx(0.65e-3)                                    # no temperature dependence at level 2


def test_level_3_temperature_hand_values():
    m = build_resistance_model(cell(r_dc_mohm=None, r_vs_temp=RT), ResistanceSettings())
    assert m.level == 3
    assert m.r_ohm(99, 10) == pytest.approx((1.2 + (0.5 - 1.2) * 10 / 25) * 1e-3)        # 0.92 mΩ
    assert m.r_ohm(1, 37.5) == pytest.approx(0.45e-3)


def test_level_4_map_bilinear_hand_value():
    grid = Grid2D(x=[0, 100], y=[0, 50], z=[[1.0, 0.6], [0.8, 0.4]])
    m = build_resistance_model(cell(r_dc_mohm=None, r_map=grid), ResistanceSettings())
    assert m.level == 4
    # SOC 25, T 10: on SOC axis 0->1.0/0.6, 100->0.8/0.4 ; at SOC 25: (0.95, 0.55); at T=10 (0.2 of 0..50): 0.95 − 0.2·0.4 = 0.87
    assert m.r_ohm(25, 10) == pytest.approx(0.87e-3)


def test_level_4_separable_from_1d_tables_and_needs_reference_temperature():
    c = cell(r_dc_mohm=0.5, r_vs_soc=RS, r_vs_temp=RT, r_ref_temp_c=25.0)
    m = build_resistance_model(c, ResistanceSettings())
    assert m.level == 4 and m.separable and "separable" in m.name
    # R(50 %, 25 °C) reproduces the table value 0.5 ; R(50 %, 0 °C) = 0.5 · 1.2/0.5 = 1.2 ; R(0 %, 50 °C) = 0.8 · 0.4/0.5 = 0.64
    assert m.r_ohm(50, 25) == pytest.approx(0.5e-3)
    assert m.r_ohm(50, 0) == pytest.approx(1.2e-3) and m.r_ohm(0, 50) == pytest.approx(0.64e-3)
    with pytest.raises(ResistanceModelError):
        build_resistance_model(cell(r_dc_mohm=0.5, r_vs_soc=RS, r_vs_temp=RT), ResistanceSettings())


def test_auto_level_selection_and_explicit_level_errors():
    assert available_levels(cell()) == [1]
    assert build_resistance_model(cell(r_vs_soc=RS), ResistanceSettings()).level == 2
    with pytest.raises(ResistanceModelError) as e:
        build_resistance_model(cell(), ResistanceSettings(level=3))
    assert "supports level(s) [1]" in str(e.value)
    with pytest.raises(ResistanceModelError):
        build_resistance_model(CellSpec(capacity_ah=100, v_nom=3.2), ResistanceSettings())
    assert build_resistance_model(cell(r_vs_soc=RS), ResistanceSettings(level=1)).level == 1     # user may force a lower level


def test_extrapolation_blocked_by_default_and_explicit_opt_in():
    c = cell(r_dc_mohm=None, r_vs_soc=RS)
    m = build_resistance_model(c, ResistanceSettings())
    with pytest.raises(OutOfRangeError):
        m.r_ohm(105, 25)
    mc = build_resistance_model(c, ResistanceSettings(extrapolation="clamp"))
    assert mc.r_ohm(105, 25) == pytest.approx(0.6e-3) and mc.usage()[0]["outside_data_range"] == 1
    ml = build_resistance_model(c, ResistanceSettings(extrapolation="linear"))
    assert ml.r_ohm(150, 25) == pytest.approx(0.7e-3)                                       # slope 0.002 mΩ/% beyond 100 %
    steep = build_resistance_model(cell(r_dc_mohm=None, r_vs_soc=Curve(x=[0, 10], y=[1.0, 0.2])), ResistanceSettings(extrapolation="linear"))
    assert steep.r_ohm(30, 25) > 0 and steep.n_floored == 1                                 # never zero / negative


def test_scale_and_charge_factor_and_usage_report():
    m = build_resistance_model(cell(r_dc_mohm=0.5), ResistanceSettings(scale=1.5, charge_factor=1.2))
    assert m.r_ohm(50, 25) == pytest.approx(0.75e-3) and m.r_ohm(50, 25, charging=True) == pytest.approx(0.9e-3)
    m2 = build_resistance_model(cell(r_dc_mohm=None, r_vs_soc=RS), ResistanceSettings())
    m2.r_ohm(20, 25)
    m2.r_ohm(80, 25)
    u = m2.usage()[0]
    assert u["used_range"] == [20, 80] and u["data_range"] == [0, 100] and u["outside_data_range"] == 0
    assert m2.describe()["level"] == 2 and "SOC" in m2.describe()["depends_on"]


def test_simulation_uses_soc_dependent_resistance_hand_values():
    c = cell(r_dc_mohm=None, r_vs_soc=Curve(x=[0, 100], y=[1.0, 0.5]))
    sim, _ = run("current", np.full(3, 100.0), np.array([0.0, 1.0, 2.0]), c=c, soc0=50.0)
    # SOC 50 %  ->  R = 0.75 mΩ ; Q_cell = 100²·0.75e-3 = 7.5 W
    assert sim.r_cell_ohm[0] == pytest.approx(0.75e-3) and sim.q_cell[0] == pytest.approx(7.5)
    # after 1 s at 100 A: SOC = 50 − 100/(3600·100)·100 = 49.97222 %  -> R = 0.5 + 0.5·0.4997222 ...
    soc1 = 50 - 100 * 1 / 3600 / 100 * 100
    assert sim.soc_pct[1] == pytest.approx(soc1) and sim.r_cell_ohm[1] == pytest.approx((0.5 + 0.5 * (100 - soc1) / 100) * 1e-3)


def test_charge_direction_uses_charge_factor():
    sim, _ = run("current", np.array([100.0, -100.0, 100.0]), np.array([0.0, 1.0, 2.0]),
                 res=ResistanceSettings(charge_factor=1.25))
    assert sim.q_cell[0] == pytest.approx(10.0) and sim.q_cell[1] == pytest.approx(12.5)


# --------------------------------------------------------------------------------- entropic heat
def test_entropic_hand_calc_constant_coefficient():
    # I = 200 A, T = 25 °C, dU/dT = +0.1 mV/K -> Q_rev = −200·298.15·1e-4 = −5.963 W (cooling on discharge)
    assert entropic_heat_w(200.0, 25.0, 1e-4) == pytest.approx(-5.963)
    sim, d = run("current", np.full(5, 200.0), np.arange(5.0), ent=EntropicSettings(mode="constant", constant_mv_per_k=0.1))
    assert sim.q_rev_cell[0] == pytest.approx(-5.963) and sim.q_cell[0] == pytest.approx(40.0 - 5.963)
    assert sim.q_pack[0] == pytest.approx(120 * (40.0 - 5.963))
    # charging reverses the sign: I = −100 A -> +2.9815 W
    assert entropic_heat_w(-100.0, 25.0, 1e-4) == pytest.approx(2.9815)
    assert joule_heat_w(-100.0, 1e-3) == pytest.approx(10.0)                              # Joule heat is always positive


def test_entropic_auto_prefers_table_then_map_then_constant_then_reports_exclusion():
    dtab = Curve(x=[0, 100], y=[0.0, 0.2])                                                # mV/K vs SOC
    m = build_entropic_model(cell(dudt_vs_soc=dtab), EntropicSettings())
    assert m.mode == "table" and m.included and m.dudt_v_per_k(50, 25) == pytest.approx(0.1e-3)
    grid = Grid2D(x=[0, 100], y=[10, 40], z=[[3.0 + 0.0002 * (t - 25) for t in (10, 40)], [3.4 + 0.0002 * (t - 25) for t in (10, 40)]])
    mm = build_entropic_model(cell(ocv_map=grid), EntropicSettings())
    assert mm.mode == "map" and mm.dudt_v_per_k(50, 25) == pytest.approx(0.2e-3)          # finite-difference of the OCV map
    mc = build_entropic_model(cell(), EntropicSettings(constant_mv_per_k=-0.15))
    assert mc.mode == "constant" and mc.dudt_v_per_k(10, 25) == pytest.approx(-0.15e-3) and "USER ESTIMATE" in mc.status
    mx = build_entropic_model(cell(), EntropicSettings())
    assert mx.mode == "excluded" and not mx.included and "NOT included" in mx.status and mx.dudt_v_per_k(50, 25) == 0.0
    assert mx.bound_w_per_cell(200.0, 25.0) == pytest.approx(200 * 298.15 * 0.2e-3)       # what was left out, at ±0.2 mV/K
    assert build_entropic_model(cell(), EntropicSettings(mode="excluded")).status.startswith("EXCLUDED by the user")


def test_entropic_missing_inputs_raise_clear_errors():
    with pytest.raises(EntropicModelError):
        build_entropic_model(cell(), EntropicSettings(mode="table"))
    with pytest.raises(EntropicModelError):
        build_entropic_model(cell(), EntropicSettings(mode="constant"))
    with pytest.raises(EntropicModelError):
        build_entropic_model(cell(), EntropicSettings(mode="map"))


# --------------------------------------------------------------------------------- other model options
def test_pack_heat_uses_correct_series_parallel_current_relation():
    # 96S2P: I_pack = 200 A -> I_cell = 100 A ; Q_cell = 10 W ; Q_pack = 192 cells · 10 W = 1920 W = I_pack²·R·Ns/Np
    pk = PackConfig(ns=96, np=2, n_modules=8, cells_per_module=24)
    sim, d = run("current", np.full(3, 200.0), np.arange(3.0), pk=pk)
    assert sim.i_cell[0] == pytest.approx(100.0) and sim.q_cell[0] == pytest.approx(10.0)
    assert sim.q_pack[0] == pytest.approx(1920.0)
    assert sim.q_pack[0] == pytest.approx(200.0 ** 2 * 1e-3 * 96 / 2)
    assert sim.q_module[0] == pytest.approx(240.0)


def test_capacity_vs_temperature_speeds_up_soc_change_when_cold():
    from battery_thermal.engine.interp import Interp1D
    cap_t = Interp1D([-20, 25], [70.0, 100.0], "capacity vs T", "T [°C]")
    hot, _ = run("current", np.full(3, 100.0), np.array([0.0, 36.0, 72.0]), t0=25.0, cap_vs_temp=cap_t)
    cold, _ = run("current", np.full(3, 100.0), np.array([0.0, 36.0, 72.0]), t0=-20.0, cap_vs_temp=cap_t)
    assert 90 - hot.soc_pct[1] == pytest.approx(1.0) and 90 - cold.soc_pct[1] == pytest.approx(1.0 / 0.7)


def test_soc_from_file_drives_lookups_and_records_source():
    c = cell(r_dc_mohm=None, r_vs_soc=Curve(x=[0, 100], y=[1.0, 0.5]))
    soc = np.array([80.0, 70.0, 60.0])
    sim, _ = run("current", np.full(3, 100.0), np.array([0.0, 1.0, 2.0]), c=c, soc_file=soc, cfg=SimConfig(soc_mode="file"))
    assert list(sim.soc_pct) == [80.0, 70.0, 60.0] and sim.flags["soc_source"] == "file"
    assert sim.r_cell_ohm[2] == pytest.approx((1.0 - 0.5 * 0.6) * 1e-3)


def test_soc_outside_table_range_blocks_and_depletion_is_flagged():
    c = cell(r_dc_mohm=None, r_vs_soc=Curve(x=[5, 95], y=[1.0, 0.5]))
    with pytest.raises(OutOfRangeError):
        run("current", np.full(3, 100.0), np.arange(3.0), c=c, soc0=99.0)                  # start outside the table -> blocked, not extrapolated
    sim, _ = run("current", np.full(40, 300.0), np.arange(40.0) * 60.0, soc0=12.0, cfg=SimConfig(soc_min_pct=10.0))
    assert sim.flags["first_soc_below_min_s"] is not None and sim.soc_pct.min() < 10


def test_summary_average_uses_time_weighting_and_excludes_last_sample_duration():
    t = np.array([0.0, 10.0, 30.0])
    sim, d = run("current", np.array([100.0, 200.0, 999.0]), t)
    s = summarize_heat(sim, d)
    # cell heat: 10 W for 10 s, 40 W for 20 s ; last sample (999 A) has zero duration
    assert s["avg_cell_heat_w"] == pytest.approx((10 * 10 + 40 * 20) / 30)
    assert s["total_heat_kwh"] == pytest.approx(120 * (10 * 10 + 40 * 20) / 3.6e6)
    assert sim.q_cell[2] == pytest.approx(999.0 ** 2 * 1e-3)                                # still reported as an instantaneous value
