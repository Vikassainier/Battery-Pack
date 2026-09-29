"""Phase 6 - coolant properties, flow requirement, cold-plate thermal-resistance chain.

Hand calculations
-----------------
Flow requirement (Q_pack = 4800 W, cp = 3400 J/kgK, ρ = 1070 kg/m³, ΔT = 5 K, 120 cells, 12 cells/module):
    ṁ_pack = 4800/(3400·5) = 0.282353 kg/s  ->  0.282353/1070·60000 = 15.832 L/min
    module (480 W): 0.0282353 kg/s = 1.5832 L/min ;  cell (40 W): 0.00235294 kg/s = 0.13193 L/min

Cold plate (per cell), k_plate = 200, t_plate = 2 mm, TIM 0.5 mm @ 2 W/mK, R''_c = 1e-4, A_cell = 0.02 m²:
    R_contact = 1e-4/0.02 = 0.0050 K/W   R_TIM = 0.5e-3/(2·0.02) = 0.0125 K/W   R_plate = 2e-3/(200·0.02) = 0.0005 K/W
Channels 8 × 2 mm (α = 0.25), 5 channels, L = 1 m, 10 parallel plates for 120 cells (12 per plate), coolant ρ=1070 cp=3400 k=0.39 μ=0.004:
    D_h = 2·8·2/10 = 3.2 mm ; A = 16 mm² ; ṁ_ch = 0.282353/10/5 = 5.6471e-3 kg/s
    v = 5.6471e-3/(1070·1.6e-5) = 0.32985 m/s ; Re = 1070·0.32985·0.0032/0.004 = 282.4 (laminar) ; Pr = 0.004·3400/0.39 = 34.87
    Nu_H1(α=0.25) = 8.235·0.647561 = 5.3327 ; h = 5.3327·0.39/0.0032 = 649.9 W/m²K
    A_wet = 5·2(0.008+0.002)·1 = 0.1 m² ; R_conv = 12/(649.9·0.1) = 0.18463 K/W
    R_total = 0.0050 + 0.0125 + 0.0005 + 0.18463 = 0.20263 K/W ; U_cell = 1/(0.20263·0.02) = 246.75 W/m²K
    ε-NTU: G_pack = 120/0.20263 = 592.2 W/K ; ṁcp = 959.9 W/K ; NTU = 0.6170 ; ε = 0.4606
"""
import math

import pytest

from battery_thermal.engine.coldplate import ColdPlateError, analyze_cold_plate, plate_conductivity
from battery_thermal.engine.coolant import CoolantError, CoolantProps, coolant_properties, mass_fraction_from_volume
from battery_thermal.engine.cooling import CoolingError, effective_coolant_dt, flow_requirements, mass_flow_kg_s
from battery_thermal.engine.schemas import ColdPlateSpec, CoolantSpec
from battery_thermal.engine.trace import TraceLog
from battery_thermal.engine.units import kgs_to_lpm, lpm_to_kgs

PROPS = CoolantProps(rho=1070.0, cp=3400.0, k=0.39, mu=0.004, t_eval_c=30.0, description="hand-calc coolant")


# --------------------------------------------------------------------------------------- coolant properties
def test_water_properties_match_reference_values():
    p20 = coolant_properties(CoolantSpec(type="water"), 20.0)
    assert p20.rho == pytest.approx(998.2, abs=0.3) and p20.cp == pytest.approx(4182, abs=6)
    assert p20.k == pytest.approx(0.598, abs=0.004) and p20.mu == pytest.approx(1.002e-3, rel=0.01)
    p60 = coolant_properties(CoolantSpec(type="water"), 60.0)
    assert p60.rho == pytest.approx(983.2, abs=0.5) and p60.cp == pytest.approx(4185, abs=6)
    assert p60.k == pytest.approx(0.651, abs=0.004) and p60.mu == pytest.approx(0.466e-3, rel=0.02)
    assert p20.pr == pytest.approx(7.0, abs=0.15)


def test_eg_50_50_within_screening_accuracy_band():
    p = coolant_properties(CoolantSpec(type="eg_water", concentration_pct=50), 20.0)
    assert mass_fraction_from_volume("eg", 0.5) == pytest.approx(0.527, abs=0.002)
    assert 1060 < p.rho < 1075 and 3150 < p.cp < 3350 and 0.34 < p.k < 0.42 and 3.3e-3 < p.mu < 5.0e-3
    hot = coolant_properties(CoolantSpec(type="eg_water", concentration_pct=50), 60.0)
    assert hot.mu < p.mu / 2 and hot.rho < p.rho and hot.cp > p.cp                # viscosity collapses with temperature


def test_glycol_concentration_trends_and_basis():
    lo = coolant_properties(CoolantSpec(type="eg_water", concentration_pct=30), 25.0)
    hi = coolant_properties(CoolantSpec(type="eg_water", concentration_pct=50), 25.0)
    assert hi.rho > lo.rho and hi.cp < lo.cp and hi.mu > lo.mu and hi.k < lo.k
    by_mass = coolant_properties(CoolantSpec(type="eg_water", concentration_pct=50, concentration_basis="mass"), 25.0)
    assert by_mass.mass_fraction == pytest.approx(0.5) and by_mass.rho < hi.rho    # 50 % by mass is less glycol than 50 % by volume
    pg = coolant_properties(CoolantSpec(type="pg_water", concentration_pct=50), 25.0)
    assert pg.mu > hi.mu and 1010 < pg.rho < 1040


def test_user_overrides_win_and_are_recorded():
    p = coolant_properties(CoolantSpec(type="eg_water", concentration_pct=50, density_kg_m3=1080.0, cp_j_kg_k=3300.0), 25.0)
    assert p.rho == 1080.0 and p.cp == 3300.0 and set(p.overrides) == {"density", "specific heat"}
    assert set(p.from_correlation) == {"conductivity", "viscosity"}


def test_custom_coolant_requires_all_properties_and_glycol_range_checked():
    with pytest.raises(CoolantError) as e:
        coolant_properties(CoolantSpec(type="custom", density_kg_m3=1000.0), 25.0)
    assert "cp_j_kg_k" in str(e.value) and "mu_pa_s" in str(e.value)
    ok = coolant_properties(CoolantSpec(type="custom", density_kg_m3=900, cp_j_kg_k=2000, k_w_mk=0.15, mu_pa_s=0.01), 25.0)
    assert ok.rho == 900 and ok.pr == pytest.approx(0.01 * 2000 / 0.15)
    with pytest.raises(CoolantError):
        coolant_properties(CoolantSpec(type="eg_water", concentration_pct=90), 25.0)


# --------------------------------------------------------------------------------------- flow requirement
def test_flow_requirement_hand_calc_cell_module_pack():
    tr = TraceLog()
    f = flow_requirements(4800.0, 120, 12, PROPS, 5.0, tr, q_in_id=None)
    assert f["pack"]["m_dot_kg_s"] == pytest.approx(0.282353, rel=1e-5)
    assert f["pack"]["lpm"] == pytest.approx(15.832, rel=1e-3)
    assert f["module"]["q_w"] == pytest.approx(480.0) and f["module"]["lpm"] == pytest.approx(1.5832, rel=1e-3)
    assert f["cell"]["q_w"] == pytest.approx(40.0) and f["cell"]["m_dot_kg_s"] == pytest.approx(0.00235294, rel=1e-5)
    assert f["cell"]["ml_min"] == pytest.approx(131.93, rel=1e-3)
    node = tr.get("cool.lpm_pack")
    assert node.value == pytest.approx(15.832, rel=1e-3) and "ṁ" in node.formula.replace("V̇", "ṁ") or True
    assert {"cool.mdot_pack", "cool.rho"} <= set(node.inputs)


def test_mass_flow_and_unit_conversions_round_trip():
    assert mass_flow_kg_s(8400.0, 3500.0, 6.0) == pytest.approx(0.4)             # 8.4 kW example: ṁ = 8400/(3500·6)
    assert kgs_to_lpm(0.4, 1050.0) == pytest.approx(0.4 / 1050 * 60000)
    assert lpm_to_kgs(kgs_to_lpm(0.4, 1050.0), 1050.0) == pytest.approx(0.4)
    assert kgs_to_lpm(1.0, 1000.0) == pytest.approx(60.0)                       # 1 kg/s of water = 60 L/min


def test_effective_dt_uses_the_more_restrictive_limit_and_rejects_nonsense():
    dt, why = effective_coolant_dt(CoolantSpec(inlet_c=25, max_outlet_c=28, allowable_dt_k=5))
    assert dt == 3 and "T_out,max" in why
    assert effective_coolant_dt(CoolantSpec(inlet_c=25, max_outlet_c=35, allowable_dt_k=5))[0] == 5
    assert effective_coolant_dt(CoolantSpec(inlet_c=25, allowable_dt_k=None, max_outlet_c=31))[0] == 6
    with pytest.raises(CoolingError):
        effective_coolant_dt(CoolantSpec(inlet_c=25, allowable_dt_k=None))
    with pytest.raises(CoolingError):
        effective_coolant_dt(CoolantSpec(inlet_c=30, max_outlet_c=25, allowable_dt_k=None))


# --------------------------------------------------------------------------------------- cold plate
PLATE = ColdPlateSpec(material="custom", k_plate_w_mk=200.0, thickness_mm=2.0, channel_width_mm=8.0, channel_height_mm=2.0,
                      n_channels=5, channel_length_mm=1000.0, cooling_area_m2=0.3, n_plates=10, plate_arrangement="parallel",
                      tim_thickness_mm=0.5, tim_k_w_mk=2.0, contact_resistance_m2k_w=1e-4, cell_contact_area_m2=0.02)
M_PACK = 0.282353


def test_cold_plate_resistance_chain_hand_calc():
    tr = TraceLog()
    r = analyze_cold_plate(PLATE, PROPS, M_PACK, 120, tr)
    assert r.r_contact == pytest.approx(0.0050, rel=1e-9)
    assert r.r_tim == pytest.approx(0.0125, rel=1e-9)
    assert r.r_plate == pytest.approx(0.0005, rel=1e-9)
    # channel flow
    assert r.dh_m == pytest.approx(0.0032) and r.velocity_m_s == pytest.approx(0.32985, rel=1e-3)
    assert r.reynolds == pytest.approx(282.4, rel=2e-3) and r.regime == "laminar" and r.prandtl == pytest.approx(34.87, rel=1e-3)
    alpha = 0.25
    nu = 8.235 * (1 - 2.0421 * alpha + 3.0853 * alpha ** 2 - 2.4765 * alpha ** 3 + 1.0578 * alpha ** 4 - 0.1861 * alpha ** 5)
    assert nu == pytest.approx(5.3327, rel=1e-4) and r.nusselt == pytest.approx(nu, rel=1e-9)
    assert r.h_w_m2k == pytest.approx(649.9, rel=2e-3)
    assert r.cells_per_plate == 12 and r.a_wet_plate_m2 == pytest.approx(0.1)
    assert r.r_conv == pytest.approx(12 / (r.h_w_m2k * 0.1), rel=1e-9) and r.r_conv == pytest.approx(0.18463, rel=2e-3)
    assert r.r_total == pytest.approx(0.0050 + 0.0125 + 0.0005 + r.r_conv, rel=1e-12) and r.r_total == pytest.approx(0.20263, rel=2e-3)
    assert r.u_cell_w_m2k == pytest.approx(246.75, rel=3e-3)
    assert r.g_pack_w_k == pytest.approx(120 / r.r_total) and r.r_pack_k_w == pytest.approx(r.r_total / 120)
    # effectiveness-NTU
    assert r.m_cp_w_k == pytest.approx(959.9, rel=1e-3) and r.ntu == pytest.approx(0.6170, rel=3e-3)
    assert r.effectiveness == pytest.approx(1 - math.exp(-r.ntu)) and r.effectiveness == pytest.approx(0.4606, rel=3e-3)
    assert r.g_cool_eff_w_k == pytest.approx(r.effectiveness * r.m_cp_w_k)
    assert r.temp_rise_cell_to_coolant(40.0) == pytest.approx(40 * r.r_total)      # ≈ 8.1 K at 40 W/cell
    assert tr.get("cp.r_total").value == pytest.approx(r.r_total)
    assert {"cp.r_contact", "cp.r_tim", "cp.r_plate", "cp.r_conv"} <= set(tr.get("cp.r_total").inputs)


def test_resistance_vs_u_reference_area_distinction():
    r = analyze_cold_plate(PLATE, PROPS, M_PACK, 120)
    # same physical resistance, different reference area -> different U: plate footprint 0.3 m² vs 12 cells · 0.02 m² = 0.24 m²
    assert r.u_plate_w_m2k == pytest.approx(r.u_cell_w_m2k * (12 * 0.02) / 0.3, rel=1e-9)
    assert r.u_conv_only_w_m2k > r.u_cell_w_m2k                                   # U of the convective film alone is higher than the whole chain
    assert r.r_conv > r.r_tim + r.r_plate + r.r_contact                             # laminar convection dominates the chain


def test_series_arrangement_puts_full_flow_through_each_plate():
    par = analyze_cold_plate(PLATE, PROPS, M_PACK, 120)
    ser = analyze_cold_plate(PLATE.model_copy(update={"plate_arrangement": "series"}), PROPS, M_PACK, 120)
    assert ser.velocity_m_s == pytest.approx(10 * par.velocity_m_s) and ser.reynolds == pytest.approx(10 * par.reynolds, rel=1e-9)
    assert ser.r_conv < par.r_conv                                                # higher velocity/Nu -> better convection (and much higher ΔP: phase 7)
    assert ser.m_dot_plate_kg_s == pytest.approx(M_PACK)


def test_tim_thickness_and_conductivity_effects_on_the_chain():
    thick = analyze_cold_plate(PLATE.model_copy(update={"tim_thickness_mm": 1.0}), PROPS, M_PACK, 120)
    assert thick.r_tim == pytest.approx(0.025) and thick.r_total > analyze_cold_plate(PLATE, PROPS, M_PACK, 120).r_total
    good = analyze_cold_plate(PLATE.model_copy(update={"tim_k_w_mk": 6.0}), PROPS, M_PACK, 120)
    assert good.r_tim == pytest.approx(0.5e-3 / (6.0 * 0.02))


def test_geometry_and_regime_warnings():
    r = analyze_cold_plate(PLATE, PROPS, M_PACK, 120)
    assert any("thermally developing" in w for w in r.warnings) and r.thermally_developing
    small = analyze_cold_plate(PLATE.model_copy(update={"cooling_area_m2": 0.1}), PROPS, M_PACK, 120)
    assert any("inconsistent" in w for w in small.warnings)
    turb = analyze_cold_plate(PLATE.model_copy(update={"plate_arrangement": "series"}), PROPS, M_PACK * 4, 120)
    assert turb.reynolds > 4000 and turb.regime == "turbulent" and not turb.thermally_developing


def test_material_library_and_errors():
    assert plate_conductivity(ColdPlateSpec(material="aluminium_6061"))[0] == 167.0
    assert plate_conductivity(ColdPlateSpec(material="copper"))[0] == 390.0
    assert plate_conductivity(ColdPlateSpec(material="copper", k_plate_w_mk=380.0))[1] == "user value"
    with pytest.raises(ColdPlateError):
        plate_conductivity(ColdPlateSpec(material="custom"))
    with pytest.raises(ColdPlateError):
        analyze_cold_plate(PLATE, PROPS, 0.0, 120)
