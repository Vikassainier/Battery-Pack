"""A complete, ready-to-run EXAMPLE project (UI state format) built from the synthetic sample files.

Every value that is not from the (synthetic) datasheet is flagged as an *assumed example value* in the provenance so the
assumptions register shows it. Nothing here is real customer or product data.
"""
from __future__ import annotations

import os
from pathlib import Path

from ..ingestion.datasheet import parse_datasheet
from ..ingestion.drive_cycle import parse_drive_cycle

SAMPLE_DIR = Path(os.environ.get("BATTERY_THERMAL_SAMPLES", Path(__file__).resolve().parents[2] / "sample_data"))


def sample_project_state() -> dict:
    ds = parse_datasheet((SAMPLE_DIR / "cell_datasheet_LFP100Ah.xlsx").read_bytes(), "cell_datasheet_LFP100Ah.xlsx")
    cell = ds.cell_proposal()
    prov: dict[str, dict] = {}
    for path, f in ds.fields.items():
        if path.startswith("cell."):
            prov[path] = {"source": "datasheet", "confidence": f.confidence, "note": "synthetic sample datasheet"}
    for key in list(ds.curves) + list(ds.maps):
        prov[f"cell.{key}"] = {"source": "datasheet", "confidence": "high", "note": "synthetic sample datasheet"}
    cell["cp_j_kg_k"] = 1000.0
    prov["cell.cp_j_kg_k"] = {"source": "assumed", "confidence": "low", "note": "engineering assumption (accepted suggestion)"}
    cell["confirmed"] = True

    cyc = parse_drive_cycle((SAMPLE_DIR / "drive_cycle_speed_only.csv").read_bytes(), "drive_cycle_speed_only.csv")
    cycle = dict(cyc.cycle)
    cycle["name"] = "drive_cycle_speed_only.csv (synthetic)"

    vehicle = {"mass_kg": 3500.0, "crr": 0.010, "cd": 0.35, "frontal_area_m2": 3.2, "wheel_radius_m": 0.34, "drivetrain_eff": 0.90,
               "aux_load_kw": 1.5, "gradient_pct": 0.0, "air_density": 1.225, "rotational_inertia_factor": 0.0, "regen_fraction": 0.8}
    for k in vehicle:
        prov[f"vehicle.{k}"] = {"source": "assumed", "confidence": "low", "note": "example light commercial van - replace with customer data"}

    plate = {"material": "aluminium_6061", "thickness_mm": 2.0, "channel_width_mm": 10.0, "channel_height_mm": 3.0, "n_channels": 4,
             "channel_length_mm": 1200.0, "cooling_area_m2": 0.32, "n_plates": 10, "plate_arrangement": "parallel", "tim_thickness_mm": 0.5,
             "tim_k_w_mk": 3.0, "contact_resistance_m2k_w": 2.0e-4, "cell_contact_area_m2": 0.01235, "fin_efficiency": 1.0, "roughness_um": 1.5,
             "nu_boundary": "constant_heat_flux", "minor_loss_k": 1.5, "external_dp_kpa": 30.0, "flow_lpm": 12.0}
    for k in plate:
        prov[f"cold_plate.{k}"] = {"source": "assumed", "confidence": "low", "note": "example plate geometry - replace with the design"}
    prov["cold_plate.cell_contact_area_m2"] = {"source": "assumed", "confidence": "low", "note": "derived from cell dimensions: bottom face L×W"}

    return {
        "project": {"name": "Sample project - LFP 120S2P light commercial van (synthetic data)", "customer": "Example customer", "project_no": "DEMO-001",
                    "engineer": "", "revision": "A", "notes": "Illustrative example built from synthetic sample data - not a real product or customer."},
        "cell": cell,
        "pack": {"ns": 120, "np": 2, "n_modules": 10, "cells_per_module": 24, "module_arrangement": "series", "pack_nominal_voltage_v": 384.0,
                 "pack_capacity_ah": 200.0, "pack_energy_kwh": 76.8, "soc_initial_pct": 90.0, "soc_min_pct": 10.0, "soc_max_pct": 100.0,
                 "t_initial_c": 27.0, "t_target_max_c": 40.0, "target_delta_t_k": 5.0, "t_ambient_c": 35.0},
        "cycle": cycle,
        "cycle_options": {"source": "auto", "current_sign": 1, "power_sign": 1, "motor_power_basis": "mechanical", "battery_power_includes_aux": True,
                          "soc_mode": "auto", "repeats": 3, "load_scale": 1.0},
        "vehicle": vehicle,
        "crate_limits": {"cont_discharge_c": 1.0, "peak_discharge_c": 2.0, "peak_discharge_duration_s": 10.0, "charge_c": 0.5, "regen_c": 0.5,
                         "peak_regen_c": 1.0, "peak_regen_duration_s": 10.0},
        "crate_profile": None,
        "resistance": {"level": "auto", "extrapolation": "block", "scale": 1.0, "charge_factor": 1.0},
        "entropic": {"mode": "auto", "constant_mv_per_k": None},
        "thermal": {"design_philosophy": "moving_average", "safety_factor": 1.2, "extra_thermal_mass_j_k": 0.0, "ambient_ua_w_k": None,
                    "ambient_h_w_m2k": 5.0, "couple_resistance_to_temperature": True, "moving_avg_window_s": None,
                    "cell_heat_spread_pct": 10.0, "flow_maldistribution_pct": 10.0},
        "coolant": {"type": "eg_water", "concentration_pct": 50.0, "concentration_basis": "volume", "inlet_c": 25.0, "max_outlet_c": None, "allowable_dt_k": 5.0},
        "cold_plate": plate,
        "pump": {"overall_efficiency": 0.4}, "radiator": {"air_dt_k": 10.0},
        "limits": {},
        "installed_cooling_capacity_kw": None,
        "provenance": prov,
        "require_cell_confirmation": True,
    }
