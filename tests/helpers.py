"""Shared request builders for the integration tests."""
from __future__ import annotations

from battery_thermal.engine.schemas import (
    AnalysisRequest, ColdPlateSpec, CoolantSpec, CRateLimits, CellSpec, DriveCycle, PackConfig, ThermalSettings,
)

# hand-calculation coolant: ρ = 1070 kg/m³, cp = 3400 J/kgK, k = 0.39, μ = 0.004 Pa·s
HAND_COOLANT = dict(type="custom", density_kg_m3=1070.0, cp_j_kg_k=3400.0, k_w_mk=0.39, mu_pa_s=0.004, inlet_c=25.0, allowable_dt_k=5.0)


def cell_100ah(**kw) -> CellSpec:
    base = dict(capacity_ah=100, v_nom=3.2, v_max=3.65, v_min=2.5, r_dc_mohm=1.0, mass_kg=2.05, cp_j_kg_k=1000.0,
                t_op_min_c=-20, t_op_max_c=60, t_rec_min_c=15, t_rec_max_c=45, max_discharge_c=3.0, max_charge_c=1.0,
                pulse_discharge_c=5.0, pulse_duration_s=30.0, length_mm=174, width_mm=71, height_mm=207,
                form_factor="prismatic", confirmed=True)
    base.update(kw)
    return CellSpec(**base)


def cycle_const_current(i_a: float = 200.0, n: int = 61, dt: float = 1.0) -> DriveCycle:
    return DriveCycle(name="const", time_s=[k * dt for k in range(n)], battery_current_a=[i_a] * n)


def cold_plate(**kw) -> ColdPlateSpec:
    base = dict(material="custom", k_plate_w_mk=200.0, thickness_mm=2.0, channel_width_mm=8.0, channel_height_mm=2.0, n_channels=5,
                channel_length_mm=1000.0, cooling_area_m2=0.3, n_plates=10, plate_arrangement="parallel", tim_thickness_mm=0.5,
                tim_k_w_mk=2.0, contact_resistance_m2k_w=1e-4, cell_contact_area_m2=0.02, external_dp_kpa=30.0)
    base.update(kw)
    return ColdPlateSpec(**base)


def base_request(*, cycle=None, cell=None, pack=None, thermal=None, coolant=None, plate="default", **kw) -> AnalysisRequest:
    """Validation case 1: 100 Ah / 3.2 V / 1 mΩ cell, 120S1P, 200 A discharge for 60 s (peak philosophy, SF 1.2)."""
    return AnalysisRequest(
        cell=cell or cell_100ah(),
        pack=pack or PackConfig(ns=120, np=1, n_modules=10, cells_per_module=12, soc_initial_pct=90, soc_min_pct=10, soc_max_pct=100,
                                t_initial_c=25, t_target_max_c=40, target_delta_t_k=5, t_ambient_c=25),
        cycle=cycle or cycle_const_current(),
        thermal=thermal or ThermalSettings(design_philosophy="peak", safety_factor=1.2),
        coolant=coolant or CoolantSpec(**HAND_COOLANT),
        cold_plate=cold_plate() if plate == "default" else plate,
        crate_limits=kw.pop("crate_limits", CRateLimits(cont_discharge_c=3.0, peak_discharge_c=5.0, peak_discharge_duration_s=30, charge_c=1.0)),
        **kw)
