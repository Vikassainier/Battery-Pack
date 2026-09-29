"""Human-readable labels, units and value texts for request fields (shared by the PDF and Excel reports)."""
from __future__ import annotations

from ..engine.assumptions import CATALOG

# (label, unit) for settings that are not in the assumptions catalogue or whose catalogue label is not a good input-table label
FIELD_LABELS: dict[str, tuple[str, str]] = {
    "resistance.level": ("Resistance model level (auto = highest level the data supports)", "-"),
    "resistance.extrapolation": ("Table extrapolation policy", "-"),
    "resistance.scale": ("Resistance scale factor (BOL→EOL)", "-"),
    "resistance.charge_factor": ("Charge / discharge resistance ratio", "-"),
    "entropic.mode": ("Entropic (reversible) heat source", "-"),
    "entropic.constant_mv_per_k": ("Entropic coefficient dU/dT (user estimate)", "mV/K"),
    "thermal.extra_thermal_mass_j_k": ("Extra thermal mass (housings, busbars, plates)", "J/K"),
    "thermal.ambient_ua_w_k": ("Pack-to-ambient conductance UA (user value)", "W/K"),
    "thermal.ambient_h_w_m2k": ("Pack external heat-transfer coefficient", "W/(m²·K)"),
    "thermal.couple_resistance_to_temperature": ("Couple resistance to the predicted cell temperature", "-"),
    "thermal.design_philosophy": ("Design heat-load philosophy", "-"),
    "thermal.safety_factor": ("Thermal safety factor", "-"),
    "thermal.moving_avg_window_s": ("Moving-average window (blank = thermal time constant)", "s"),
    "thermal.cell_heat_spread_pct": ("Cell-to-cell heat-generation spread", "%"),
    "thermal.flow_maldistribution_pct": ("Flow maldistribution (parallel plates)", "%"),
    "coolant.type": ("Coolant type", "-"),
    "coolant.concentration_pct": ("Glycol concentration", "%"),
    "coolant.concentration_basis": ("Concentration basis", "-"),
    "coolant.inlet_c": ("Coolant inlet (supply) temperature", "°C"),
    "coolant.max_outlet_c": ("Maximum coolant outlet temperature", "°C"),
    "coolant.allowable_dt_k": ("Allowable coolant temperature rise ΔT", "K"),
    "coolant.density_kg_m3": ("Coolant density (user override)", "kg/m³"),
    "coolant.cp_j_kg_k": ("Coolant specific heat (user override)", "J/(kg·K)"),
    "coolant.k_w_mk": ("Coolant thermal conductivity (user override)", "W/(m·K)"),
    "coolant.mu_pa_s": ("Coolant dynamic viscosity (user override)", "Pa·s"),
    "cold_plate.material": ("Plate material", "-"),
    "cold_plate.k_plate_w_mk": ("Plate thermal conductivity", "W/(m·K)"),
    "cold_plate.thickness_mm": ("Plate conduction thickness", "mm"),
    "cold_plate.channel_width_mm": ("Channel width", "mm"),
    "cold_plate.channel_height_mm": ("Channel height", "mm"),
    "cold_plate.n_channels": ("Parallel channels per plate", "-"),
    "cold_plate.channel_length_mm": ("Channel length", "mm"),
    "cold_plate.cooling_area_m2": ("Plate footprint (cooling) area", "m²"),
    "cold_plate.n_plates": ("Number of cold plates", "-"),
    "cold_plate.plate_arrangement": ("Plate hydraulic arrangement", "-"),
    "cold_plate.tim_thickness_mm": ("TIM thickness", "mm"),
    "cold_plate.tim_k_w_mk": ("TIM thermal conductivity", "W/(m·K)"),
    "cold_plate.contact_resistance_m2k_w": ("Cell/TIM contact resistance (area-specific)", "m²K/W"),
    "cold_plate.cell_contact_area_m2": ("Cell contact area (per cell)", "m²"),
    "cold_plate.fin_efficiency": ("Channel-wall (fin) efficiency", "-"),
    "cold_plate.roughness_um": ("Channel wall roughness", "µm"),
    "cold_plate.nu_boundary": ("Nusselt boundary condition (laminar)", "-"),
    "cold_plate.minor_loss_k": ("Minor-loss coefficient (inlet / outlet / bends)", "-"),
    "cold_plate.external_dp_kpa": ("External loop pressure drop (hoses, chiller, radiator)", "kPa"),
    "cold_plate.flow_lpm": ("Actual pack coolant flow", "L/min"),
    "pump.overall_efficiency": ("Pump overall efficiency (hydraulic → electrical)", "-"),
    "radiator.air_dt_k": ("Assumed air-side temperature rise", "K"),
    "limits.cooling_margin_warn_pct": ("Cooling margin: warning below", "%"),
    "limits.cooling_margin_target_pct": ("Cooling margin: engineering target", "%"),
    "limits.thermal_margin_warn_k": ("Thermal margin: warning below", "K"),
    "limits.dt_warn_fraction": ("Cell-to-cell ΔT: warning above this fraction of the target", "-"),
    "limits.max_velocity_warn_m_s": ("Channel velocity: warning above", "m/s"),
    "limits.max_velocity_fail_m_s": ("Channel velocity: fail above", "m/s"),
    "limits.plate_dp_warn_kpa": ("Cold-plate pressure drop: warning above", "kPa"),
    "limits.plate_dp_fail_kpa": ("Cold-plate pressure drop: fail above", "kPa"),
    "limits.loop_dp_warn_kpa": ("Total loop pressure drop: warning above", "kPa"),
    "limits.loop_dp_fail_kpa": ("Total loop pressure drop: fail above", "kPa"),
    "limits.flow_warn_lpm": ("Pack coolant flow: warning above", "L/min"),
    "limits.flow_fail_lpm": ("Pack coolant flow: fail above", "L/min"),
    "limits.flow_min_lpm": ("Minimum practical coolant flow", "L/min"),
}

VALUE_TEXT: dict[str, dict[str, str]] = {
    "resistance.level": {"auto": "auto", "1": "Level 1 (constant)", "2": "Level 2 R(SOC)", "3": "Level 3 R(T)", "4": "Level 4 R(SOC,T)"},
    "resistance.extrapolation": {"block": "block (error outside the tabulated range)", "clamp": "clamp to the table edge", "linear": "linear extrapolation"},
    "entropic.mode": {"auto": "auto (table → map → constant → excluded)", "table": "dU/dT table", "map": "OCV map (finite difference)", "constant": "constant estimate", "excluded": "excluded (reported)"},
    "thermal.design_philosophy": {"peak": "Peak heat load", "moving_average": "Moving-average heat load", "sustained": "Sustained heat load", "drive_cycle": "Drive-cycle thermal load"},
    "coolant.type": {"water": "Water", "eg_water": "Ethylene glycol / water", "pg_water": "Propylene glycol / water", "custom": "Custom"},
    "coolant.concentration_basis": {"volume": "by volume", "mass": "by mass"},
    "cold_plate.plate_arrangement": {"parallel": "parallel (flow split between plates)", "series": "series (flow passes through every plate)"},
    "cold_plate.nu_boundary": {"constant_heat_flux": "constant heat flux (H1)", "constant_wall_temperature": "constant wall temperature (T)"},
}


def field_label(path: str) -> tuple[str, str]:
    """(label, unit) for a request path such as 'cold_plate.n_channels'."""
    if path in FIELD_LABELS:
        return FIELD_LABELS[path]
    meta = CATALOG.get(path)
    if meta is not None:
        return meta.label, meta.unit
    return path.split(".")[-1].replace("_", " ").capitalize(), ""


def value_text(path: str, v) -> object:
    """Readable text for enumerated settings; numbers pass through unchanged."""
    m = VALUE_TEXT.get(path)
    if m is not None and v is not None:
        return m.get(str(v), str(v))
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, str):
        return v.replace("_", " ")
    return v


# ------------------------------------------------------------------------------------------------ shared row builders
def _rng(lo, hi) -> str | None:
    if lo is None and hi is None:
        return None
    f = lambda v: "–" if v is None else f"{v:g}"  # noqa: E731
    return f"{f(lo)} … {f(hi)}"


def cell_spec(c) -> list[tuple[str, object, str, str | None]]:
    """(label, value, unit, provenance path) rows describing the confirmed cell; entries without a value are omitted."""
    dims = f"{c.length_mm:g} × {c.width_mm:g} × {c.height_mm:g}" if None not in (c.length_mm, c.width_mm, c.height_mm) else None
    rows = [("Cell name", c.name, "", None), ("Chemistry", c.chemistry, "", None), ("Cell type", c.form_factor, "", None),
            ("Nominal capacity", c.capacity_ah, "Ah", "cell.capacity_ah"), ("Nominal voltage", c.v_nom, "V", "cell.v_nom"), ("Maximum voltage", c.v_max, "V", "cell.v_max"),
            ("Minimum voltage", c.v_min, "V", "cell.v_min"), ("DC internal resistance", c.r_dc_mohm, "mΩ", "cell.r_dc_mohm"), ("AC impedance (1 kHz)", c.r_ac_mohm, "mΩ", "cell.r_ac_mohm"),
            ("Max continuous discharge", c.max_discharge_c, "C", "cell.max_discharge_c"), ("Max continuous charge", c.max_charge_c, "C", "cell.max_charge_c"),
            ("Pulse discharge", c.pulse_discharge_c, "C", None), ("Pulse charge", c.pulse_charge_c, "C", None), ("Pulse duration", c.pulse_duration_s, "s", None),
            ("Dimensions L × W × H", dims, "mm", None), ("Diameter", c.diameter_mm, "mm", None),
            ("Mass", c.mass_kg, "kg", "cell.mass_kg"), ("Specific heat", c.cp_j_kg_k, "J/(kg·K)", "cell.cp_j_kg_k"),
            ("Operating temperature", _rng(c.t_op_min_c, c.t_op_max_c), "°C", "cell.t_op_max_c"),
            ("Recommended temperature", _rng(c.t_rec_min_c, c.t_rec_max_c), "°C", "cell.t_rec_max_c"),
            ("Charge temperature", _rng(c.t_charge_min_c, c.t_charge_max_c), "°C", None)]
    return [r for r in rows if r[1] is not None]


def cell_tables(c) -> list[str]:
    """Names of the curves / maps present on the cell."""
    return [name for name, obj in (("R vs SOC", c.r_vs_soc), ("R vs temperature", c.r_vs_temp), ("R map", c.r_map), ("OCV vs SOC", c.ocv_vs_soc), ("OCV map", c.ocv_map),
                                   ("dU/dT vs SOC", c.dudt_vs_soc), ("Capacity vs temperature", c.capacity_vs_temp)) if obj is not None]


VEHICLE_FIELDS = (("Mass", "mass_kg", "kg"), ("Rolling resistance Crr", "crr", "-"), ("Drag coefficient Cd", "cd", "-"), ("Frontal area", "frontal_area_m2", "m²"), ("Wheel radius", "wheel_radius_m", "m"),
                  ("Drivetrain efficiency", "drivetrain_eff", "-"), ("Auxiliary load", "aux_load_kw", "kW"), ("Road gradient", "gradient_pct", "%"), ("Air density", "air_density", "kg/m³"),
                  ("Rotational inertia factor ε", "rotational_inertia_factor", "-"), ("Regen fraction", "regen_fraction", "-"), ("Max regen power", "max_regen_kw", "kW"))

CRATE_FIELDS = (("Continuous discharge", "cont_discharge_c", "C"), ("Peak discharge", "peak_discharge_c", "C"), ("Peak discharge duration", "peak_discharge_duration_s", "s"),
                ("Charging", "charge_c", "C"), ("Regenerative", "regen_c", "C"), ("Peak regenerative", "peak_regen_c", "C"), ("Peak regen duration", "peak_regen_duration_s", "s"))


PACK_FIELDS = (("Cells in series Ns", "ns", "-"), ("Cells in parallel Np", "np", "-"), ("Number of modules", "n_modules", "-"), ("Cells per module", "cells_per_module", "-"),
               ("Module arrangement", "module_arrangement", "-"), ("Modules in series", "modules_in_series", "-"), ("Stated pack nominal voltage", "pack_nominal_voltage_v", "V"),
               ("Stated pack capacity", "pack_capacity_ah", "Ah"), ("Stated pack energy", "pack_energy_kwh", "kWh"), ("Initial SOC", "soc_initial_pct", "%"),
               ("Minimum SOC", "soc_min_pct", "%"), ("Maximum SOC", "soc_max_pct", "%"), ("Initial battery temperature", "t_initial_c", "°C"),
               ("Target max cell temperature", "t_target_max_c", "°C"), ("Target cell-to-cell ΔT", "target_delta_t_k", "K"), ("Ambient temperature", "t_ambient_c", "°C"))

_SRC_LABEL = {"user": "User-provided", "datasheet": "Datasheet", "assumed": "Assumed", "calculated": "Calculated"}


def provenance_of(req, path: str) -> tuple[str, str]:
    """(source class label, confidence label) of a request parameter, from the request provenance or the catalogue default."""
    pv = req.provenance.get(path)
    meta = CATALOG.get(path)
    src = pv.source if pv else (meta.default_source if meta else "user")
    conf = (pv.confidence if pv and pv.confidence else (meta.confidence if meta else "high")).capitalize()
    return _SRC_LABEL[src if src in _SRC_LABEL else "user"], conf
