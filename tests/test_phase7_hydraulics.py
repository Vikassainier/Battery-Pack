"""Phase 7 - channel correlations and pressure-drop model.

Literature anchors
    square duct (α = 1):      f·Re = 56.91 ,  Nu_H1 = 3.61 ,  Nu_T = 2.98
    parallel plates (α → 0):  f·Re = 96    ,  Nu_H1 = 8.235 ,  Nu_T = 7.541
    smooth pipe Re = 1e5:     f ≈ 0.0180 (Petukhov) / 0.0178 (Haaland)
    Re = 1e4, Pr = 7:         Nu ≈ 79.5 (Gnielinski) vs 79.4 (Dittus-Boelter)

Hand calculation of the cold-plate hydraulics (same case as phase 6: 8×2 mm channels, 5 per plate, L = 1 m, 10 parallel plates,
ṁ_pack = 0.282353 kg/s, ρ = 1070, μ = 0.004, K = 1.5, external 30 kPa, η_pump = 0.4):
    v = 0.32985 m/s ; Re = 282.4 ; f·Re = 96·(1 − 1.3553·0.25 + 1.9467·0.25² − 1.7012·0.25³ + 0.9564·0.25⁴ − 0.2537·0.25⁵) = 72.936
    f = 72.936/282.4 = 0.25828 ; ½ρv² = 58.21 Pa
    ΔP_channel = 0.25828·(1/0.0032)·58.21 = 4698 Pa ; ΔP_minor = 1.5·58.21 = 87.3 Pa ; ΔP_plate = 4.785 kPa
    ΔP_total = 4.785 + 30 = 34.785 kPa = 0.348 bar ; V̇ = 15.832 L/min = 2.6386e-4 m³/s
    P_hyd = 34785·2.6386e-4 = 9.18 W ; P_el = 9.18/0.4 = 22.9 W
"""
import math

import pytest

from battery_thermal.engine.channel import (
    RectChannel, f_haaland, f_petukhov, flow_regime, fre_laminar, friction_factor, nu_gnielinski, nu_laminar, nusselt,
    reynolds, thermal_entry_length,
)
from battery_thermal.engine.coolant import CoolantProps
from battery_thermal.engine.pressure_drop import hydraulics
from battery_thermal.engine.schemas import ColdPlateSpec, LimitSettings
from battery_thermal.engine.trace import TraceLog

PROPS = CoolantProps(rho=1070.0, cp=3400.0, k=0.39, mu=0.004, t_eval_c=30.0, description="hand-calc coolant")
PLATE = ColdPlateSpec(material="custom", k_plate_w_mk=200.0, channel_width_mm=8.0, channel_height_mm=2.0, n_channels=5,
                      channel_length_mm=1000.0, cooling_area_m2=0.3, n_plates=10, plate_arrangement="parallel",
                      cell_contact_area_m2=0.02, minor_loss_k=1.5, external_dp_kpa=30.0)
M_PACK = 0.282353
LIM = LimitSettings()


# ------------------------------------------------------------------------ correlations vs literature
def test_laminar_anchors_square_duct_and_parallel_plates():
    assert fre_laminar(1.0) == pytest.approx(56.91, abs=0.05)
    assert nu_laminar(1.0) == pytest.approx(3.61, abs=0.01)
    assert nu_laminar(1.0, "constant_wall_temperature") == pytest.approx(2.98, abs=0.01)
    assert fre_laminar(0.0) == pytest.approx(96.0) and nu_laminar(0.0) == pytest.approx(8.235)
    assert nu_laminar(0.0, "constant_wall_temperature") == pytest.approx(7.541)
    assert fre_laminar(0.5) == pytest.approx(62.2, abs=0.3) and nu_laminar(0.5) == pytest.approx(4.12, abs=0.05)     # aspect ratio 2:1 duct


def test_turbulent_anchors():
    assert f_petukhov(1e5) == pytest.approx(0.0180, abs=2e-4)
    assert f_haaland(1e5, 0.0) == pytest.approx(0.0178, abs=3e-4)
    f = (0.79 * math.log(1e4) - 1.64) ** -2
    hand = (f / 8) * (1e4 - 1000) * 7 / (1 + 12.7 * math.sqrt(f / 8) * (7 ** (2 / 3) - 1))
    assert nu_gnielinski(1e4, 7.0) == pytest.approx(hand, rel=1e-12) and hand == pytest.approx(79.5, abs=0.5)
    assert nu_gnielinski(1e4, 7.0) == pytest.approx(0.023 * 1e4 ** 0.8 * 7 ** 0.4, rel=0.02)          # agrees with Dittus-Boelter
    assert f_haaland(1e5, 1e-3) > f_haaland(1e5, 0.0)                                                # roughness raises friction


def test_regime_boundaries_and_continuity_across_transition():
    assert flow_regime(2299) == "laminar" and flow_regime(2300) == "transitional" and flow_regime(4000) == "turbulent"
    a = 0.25
    assert friction_factor(2299.999, a) == pytest.approx(friction_factor(2300.001, a), rel=1e-4)
    assert friction_factor(3999.999, a) == pytest.approx(friction_factor(4000.001, a), rel=1e-4)
    assert nusselt(2299.999, 30.0, a) == pytest.approx(nusselt(2300.001, 30.0, a), rel=1e-4)
    assert nusselt(3999.999, 30.0, a) == pytest.approx(nusselt(4000.001, 30.0, a), rel=1e-4)
    mid = friction_factor(3150, a)
    assert min(friction_factor(2300, a), friction_factor(4000, a)) <= mid <= max(friction_factor(2300, a), friction_factor(4000, a))


def test_geometry_helpers():
    ch = RectChannel(0.008, 0.002, 1.0)
    assert ch.dh == pytest.approx(0.0032) and ch.alpha == pytest.approx(0.25) and ch.area == pytest.approx(1.6e-5)
    assert ch.wetted_area == pytest.approx(0.02) and reynolds(1000, 1, 0.01, 1e-3) == pytest.approx(1e4)
    assert thermal_entry_length(282.4, 34.87, 0.0032) == pytest.approx(0.05 * 282.4 * 34.87 * 0.0032)


# ------------------------------------------------------------------------ pressure drop
def test_pressure_drop_hand_calc():
    tr = TraceLog()
    h = hydraulics(PLATE, PROPS, M_PACK, 0.4, LIM, tr)
    v = M_PACK / 10 / 5 / (1070 * 1.6e-5)
    assert h.velocity_m_s == pytest.approx(v) and h.velocity_m_s == pytest.approx(0.32985, rel=1e-3)
    assert h.reynolds == pytest.approx(282.4, rel=2e-3) and h.regime == "laminar"
    poly = 1 - 1.3553 * 0.25 + 1.9467 * 0.25 ** 2 - 1.7012 * 0.25 ** 3 + 0.9564 * 0.25 ** 4 - 0.2537 * 0.25 ** 5
    assert 96 * poly == pytest.approx(72.936, abs=0.01)
    assert h.f_darcy == pytest.approx(72.936 / h.reynolds, rel=1e-4) and h.f_darcy == pytest.approx(0.25828, rel=3e-3)
    q_dyn = 0.5 * 1070 * v * v
    assert q_dyn == pytest.approx(58.21, rel=2e-3)
    assert h.dp_channel_pa == pytest.approx(h.f_darcy * (1 / 0.0032) * q_dyn) and h.dp_channel_pa == pytest.approx(4698, rel=5e-3)
    assert h.dp_minor_pa == pytest.approx(1.5 * q_dyn) and h.dp_minor_pa == pytest.approx(87.3, rel=3e-3)
    assert h.dp_plate_pa == pytest.approx(4785, rel=5e-3) and h.dp_plates_total_pa == pytest.approx(h.dp_plate_pa)   # parallel: one plate drop
    assert h.dp_total_pa == pytest.approx(h.dp_plate_pa + 30000.0) and h.dp_total_pa / 1e5 == pytest.approx(0.348, abs=0.002)
    assert h.q_pack_lpm == pytest.approx(15.832, rel=1e-3) and h.q_plate_lpm == pytest.approx(1.5832, rel=1e-3)
    assert h.p_hyd_w == pytest.approx(h.dp_total_pa * 15.832 / 60000, rel=1e-3) and h.p_hyd_w == pytest.approx(9.18, rel=5e-3)
    assert h.p_elec_w == pytest.approx(h.p_hyd_w / 0.4) and h.p_elec_w == pytest.approx(22.9, rel=5e-3)
    assert h.head_m == pytest.approx(h.dp_total_pa / (1070 * 9.80665))
    assert tr.get("hyd.p_el").value == pytest.approx(h.p_elec_w) and "hyd.p_hyd" in tr.get("hyd.p_el").inputs
    assert h.to_dict()["dp_total_bar"] == pytest.approx(h.dp_total_pa / 1e5)


def test_pressure_drop_scaling_laws():
    base = hydraulics(PLATE.model_copy(update={"external_dp_kpa": 0.0, "minor_loss_k": 0.0}), PROPS, M_PACK, 0.4, LIM)
    dbl = hydraulics(PLATE.model_copy(update={"external_dp_kpa": 0.0, "minor_loss_k": 0.0}), PROPS, 2 * M_PACK, 0.4, LIM)
    assert dbl.dp_plate_pa == pytest.approx(2 * base.dp_plate_pa, rel=1e-9)               # laminar: ΔP ∝ flow (Hagen-Poiseuille)
    long = hydraulics(PLATE.model_copy(update={"external_dp_kpa": 0.0, "minor_loss_k": 0.0, "channel_length_mm": 2000.0}), PROPS, M_PACK, 0.4, LIM)
    assert long.dp_plate_pa == pytest.approx(2 * base.dp_plate_pa, rel=1e-9)               # ΔP ∝ length
    # series plates: the whole flow passes through every plate. At ṁ/10 the per-plate flow equals the parallel case,
    # so the pack drop is exactly 10 × one plate's drop
    ser = hydraulics(PLATE.model_copy(update={"external_dp_kpa": 0.0, "minor_loss_k": 0.0, "plate_arrangement": "series"}), PROPS, M_PACK / 10, 0.4, LIM)
    assert ser.velocity_m_s == pytest.approx(base.velocity_m_s)
    assert ser.dp_plates_total_pa == pytest.approx(10 * base.dp_plate_pa, rel=1e-9)
    full = hydraulics(PLATE.model_copy(update={"external_dp_kpa": 0.0, "minor_loss_k": 0.0, "plate_arrangement": "series"}), PROPS, M_PACK, 0.4, LIM)
    assert full.velocity_m_s == pytest.approx(10 * base.velocity_m_s) and full.regime == "transitional"     # 10× velocity crosses into transition
    assert full.dp_plates_total_pa > 100 * base.dp_plate_pa                                                # more than the laminar 100× extrapolation
    deep = hydraulics(PLATE.model_copy(update={"external_dp_kpa": 0.0, "minor_loss_k": 0.0, "channel_height_mm": 4.0}), PROPS, M_PACK, 0.4, LIM)
    assert deep.dp_plate_pa < base.dp_plate_pa / 4                                         # deeper channel: far lower drop


def test_turbulent_friction_and_dp_use_haaland():
    h = hydraulics(PLATE.model_copy(update={"n_plates": 1, "n_channels": 4, "channel_width_mm": 12, "channel_height_mm": 6,
                                            "external_dp_kpa": 0.0}), CoolantProps(1000, 4180, 0.6, 0.001, 30, "water"), 1.5, 0.4, LIM)
    assert h.regime == "turbulent" and h.reynolds > 4000
    rel = 1.5e-6 / h.dh_m
    assert h.f_darcy == pytest.approx(f_haaland(h.reynolds, rel), rel=1e-9)


# ------------------------------------------------------------------------ flags (nothing hidden)
def codes(h):
    return {f["code"]: f["severity"] for f in h.flags}


def test_velocity_flags_warning_then_fail():
    warn = hydraulics(PLATE.model_copy(update={"n_channels": 2}), PROPS, 0.9, 0.4, LIM)                  # ≈ 2.5 m/s
    assert 2.0 < warn.velocity_m_s < 4.0 and codes(warn)["HYD_VELOCITY_HIGH"] == "warning"
    fail = hydraulics(PLATE.model_copy(update={"n_channels": 1}), PROPS, 1.5, 0.4, LIM)
    assert fail.velocity_m_s > 4.0 and codes(fail)["HYD_VELOCITY_EXCESSIVE"] == "fail"


def test_pressure_drop_flags():
    h = hydraulics(PLATE.model_copy(update={"external_dp_kpa": 120.0}), PROPS, M_PACK, 0.4, LIM)
    assert codes(h)["HYD_LOOP_DP_HIGH"] == "warning"
    h2 = hydraulics(PLATE.model_copy(update={"external_dp_kpa": 250.0}), PROPS, M_PACK, 0.4, LIM)
    assert codes(h2)["HYD_LOOP_DP_EXCESSIVE"] == "fail"
    h3 = hydraulics(PLATE.model_copy(update={"channel_length_mm": 15000.0, "external_dp_kpa": 0.0}), PROPS, M_PACK, 0.4, LIM)
    assert h3.dp_plate_pa / 1e3 > 50 and "HYD_PLATE_DP_HIGH" in codes(h3) or "HYD_PLATE_DP_EXCESSIVE" in codes(h3)


def test_flow_flags_and_custom_limits_are_respected():
    big = hydraulics(PLATE.model_copy(update={"external_dp_kpa": 0.0}), PROPS, 1.0, 0.4, LIM)              # 56 L/min
    assert codes(big)["HYD_FLOW_HIGH"] == "warning"
    huge = hydraulics(PLATE.model_copy(update={"external_dp_kpa": 0.0}), PROPS, 2.0, 0.4, LIM)             # 112 L/min
    assert codes(huge)["HYD_FLOW_EXCESSIVE"] == "fail"
    tiny = hydraulics(PLATE.model_copy(update={"external_dp_kpa": 0.0}), PROPS, 0.005, 0.4, LIM)
    assert "HYD_FLOW_LOW" in codes(tiny)
    relaxed = hydraulics(PLATE.model_copy(update={"external_dp_kpa": 0.0}), PROPS, 1.0, 0.4, LimitSettings(flow_warn_lpm=80.0, flow_fail_lpm=200.0))
    assert "HYD_FLOW_HIGH" not in codes(relaxed)                                                          # limits are configurable, not hard-coded


def test_maldistribution_and_transition_flags():
    many = hydraulics(PLATE.model_copy(update={"n_channels": 12}), PROPS, M_PACK, 0.4, LIM)
    assert "HYD_MALDISTRIBUTION_RISK" in codes(many)                                                       # laminar + many parallel channels
    few = hydraulics(PLATE, PROPS, M_PACK, 0.4, LIM)
    assert "HYD_MALDISTRIBUTION_RISK" not in codes(few)
    trans = hydraulics(PLATE.model_copy(update={"plate_arrangement": "series", "n_plates": 2}), PROPS, 0.1, 0.4, LIM)
    assert trans.regime in ("transitional", "laminar", "turbulent")
    tr2 = hydraulics(PLATE.model_copy(update={"n_plates": 1, "n_channels": 4, "channel_width_mm": 10, "channel_height_mm": 4}),
                     CoolantProps(1000, 4180, 0.6, 0.001, 30, "water"), 0.11, 0.4, LIM)
    assert tr2.regime == "transitional" and "HYD_TRANSITION" in codes(tr2)
    assert codes(few).get("HYD_PLATE_BALANCE") == "info"
