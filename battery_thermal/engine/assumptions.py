"""Parameter catalogue and the *Assumptions & Data Quality* register.

Every parameter that influences a result appears in the register with its value, unit, source class
(user-provided / datasheet / calculated / assumed) and a confidence level. Values that the user never
touched and that are not from a datasheet are *assumed* and listed as such - nothing is silent.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from .schemas import AnalysisRequest


@dataclass(frozen=True)
class ParamMeta:
    label: str
    unit: str
    group: str
    default_source: str = "user"       # class when the user supplied nothing but the engine still has a value
    confidence: str = "high"           # default confidence for that source class
    rationale: str = ""


# default_source == "assumed" -> value is an engineering assumption until the user edits it
CATALOG: dict[str, ParamMeta] = {
    # ---- cell (normally datasheet) ------------------------------------------------------------
    "cell.capacity_ah": ParamMeta("Cell nominal capacity", "Ah", "Cell", "datasheet"),
    "cell.v_nom": ParamMeta("Cell nominal voltage", "V", "Cell", "datasheet"),
    "cell.v_max": ParamMeta("Cell max voltage", "V", "Cell", "datasheet"),
    "cell.v_min": ParamMeta("Cell min voltage", "V", "Cell", "datasheet"),
    "cell.r_dc_mohm": ParamMeta("Cell DC internal resistance", "mΩ", "Cell", "datasheet", "medium",
                                "Depends on SOC, temperature, pulse length and ageing; verify measurement conditions."),
    "cell.r_ac_mohm": ParamMeta("Cell AC impedance (1 kHz)", "mΩ", "Cell", "datasheet", "low",
                                "AC impedance under-estimates DC resistance - not used for heat unless chosen explicitly."),
    "cell.mass_kg": ParamMeta("Cell mass", "kg", "Cell", "datasheet"),
    "cell.cp_j_kg_k": ParamMeta("Cell specific heat capacity", "J/(kg·K)", "Cell", "assumed", "low",
                                "Not normally on datasheets; typical Li-ion 800–1100 J/(kg·K)."),
    "cell.t_op_max_c": ParamMeta("Max operating temperature", "°C", "Cell", "datasheet"),
    "cell.t_op_min_c": ParamMeta("Min operating temperature", "°C", "Cell", "datasheet"),
    "cell.t_rec_max_c": ParamMeta("Recommended max temperature", "°C", "Cell", "datasheet"),
    "cell.t_rec_min_c": ParamMeta("Recommended min temperature", "°C", "Cell", "datasheet"),
    "cell.max_discharge_c": ParamMeta("Max continuous discharge C-rate", "C", "Cell", "datasheet"),
    "cell.max_charge_c": ParamMeta("Max continuous charge C-rate", "C", "Cell", "datasheet"),
    # ---- pack / operating --------------------------------------------------------------------
    "pack.ns": ParamMeta("Cells in series Ns", "-", "Pack"),
    "pack.np": ParamMeta("Cells in parallel Np", "-", "Pack"),
    "pack.soc_initial_pct": ParamMeta("Initial SOC", "%", "Operating"),
    "pack.soc_min_pct": ParamMeta("Minimum SOC", "%", "Operating"),
    "pack.soc_max_pct": ParamMeta("Maximum SOC", "%", "Operating"),
    "pack.t_initial_c": ParamMeta("Initial battery temperature", "°C", "Operating"),
    "pack.t_target_max_c": ParamMeta("Target max cell temperature", "°C", "Operating"),
    "pack.target_delta_t_k": ParamMeta("Target cell-to-cell ΔT", "K", "Operating"),
    "pack.t_ambient_c": ParamMeta("Ambient temperature", "°C", "Operating"),
    # ---- model settings that are engineering assumptions -------------------------------------
    "resistance.scale": ParamMeta("Resistance scale factor (BOL→EOL)", "-", "Heat model", "assumed", "medium",
                                  "1.0 = datasheet (beginning-of-life) resistance. End-of-life is commonly 1.3–2.0."),
    "resistance.charge_factor": ParamMeta("Charge / discharge resistance ratio", "-", "Heat model", "assumed", "low",
                                          "1.0 = same resistance in charge and discharge."),
    "entropic.constant_mv_per_k": ParamMeta("Entropic coefficient dU/dT", "mV/K", "Heat model", "assumed", "low",
                                            "User estimate; NMC/graphite typically ±0.1–0.3 mV/K, LFP ~ ±0.05 mV/K."),
    "thermal.safety_factor": ParamMeta("Thermal safety factor", "-", "Thermal", "assumed", "medium",
                                       "Typical 1.1–1.3 on design heat load."),
    "thermal.extra_thermal_mass_j_k": ParamMeta("Extra thermal mass (housings, busbars, plates)", "J/K", "Thermal", "assumed", "low",
                                                "0 = only cell mass contributes (conservative for temperature rise)."),
    "thermal.ambient_ua_w_k": ParamMeta("Pack-to-ambient conductance UA", "W/K", "Thermal", "assumed", "low",
                                        "Estimated from cell volume and an external h if not supplied."),
    "thermal.ambient_h_w_m2k": ParamMeta("Pack external heat-transfer coefficient", "W/(m²·K)", "Thermal", "assumed", "low",
                                         "Natural convection + enclosure; 3–10 W/(m²K) typical."),
    "thermal.cell_heat_spread_pct": ParamMeta("Cell-to-cell heat generation spread", "%", "Thermal", "assumed", "low",
                                              "Resistance / current sharing variation; typical ±5–15 %."),
    "thermal.flow_maldistribution_pct": ParamMeta("Flow maldistribution (parallel plates)", "%", "Thermal", "assumed", "low",
                                                  "Weakest-branch flow deficit; verify with a 1-D network / CFD model."),
    "coolant.type": ParamMeta("Coolant type", "-", "Coolant"),
    "coolant.concentration_pct": ParamMeta("Coolant glycol concentration", "%", "Coolant"),
    "coolant.inlet_c": ParamMeta("Coolant inlet temperature", "°C", "Coolant"),
    "coolant.allowable_dt_k": ParamMeta("Allowable coolant ΔT", "K", "Coolant", "user"),
    "coolant.density_kg_m3": ParamMeta("Coolant density", "kg/m³", "Coolant", "calculated", "medium",
                                       "From built-in mixture correlation (±2 %); use supplier data for final design."),
    "coolant.cp_j_kg_k": ParamMeta("Coolant specific heat", "J/(kg·K)", "Coolant", "calculated", "medium",
                                   "From built-in mixture correlation (±2 %)."),
    "coolant.k_w_mk": ParamMeta("Coolant thermal conductivity", "W/(m·K)", "Coolant", "calculated", "low",
                                "From built-in mixture correlation (±5–10 %)."),
    "coolant.mu_pa_s": ParamMeta("Coolant dynamic viscosity", "Pa·s", "Coolant", "calculated", "low",
                                 "From built-in mixture correlation (±10 %); strongly temperature dependent."),
    "cold_plate.k_plate_w_mk": ParamMeta("Cold-plate material conductivity", "W/(m·K)", "Cold plate", "calculated", "high",
                                         "Material library value."),
    "cold_plate.thickness_mm": ParamMeta("Plate conduction thickness", "mm", "Cold plate", "assumed", "low",
                                         "Placeholder until the plate design is fixed."),
    "cold_plate.channel_width_mm": ParamMeta("Channel width", "mm", "Cold plate", "assumed", "low", "Placeholder geometry."),
    "cold_plate.channel_height_mm": ParamMeta("Channel height", "mm", "Cold plate", "assumed", "low", "Placeholder geometry."),
    "cold_plate.n_channels": ParamMeta("Number of channels", "-", "Cold plate", "assumed", "low", "Placeholder geometry."),
    "cold_plate.channel_length_mm": ParamMeta("Channel length", "mm", "Cold plate", "assumed", "low", "Placeholder geometry."),
    "cold_plate.cooling_area_m2": ParamMeta("Cooling (plate footprint) area", "m²", "Cold plate", "assumed", "low", "Placeholder geometry."),
    "cold_plate.n_plates": ParamMeta("Number of cold plates", "-", "Cold plate", "assumed", "low", "Placeholder geometry."),
    "cold_plate.tim_thickness_mm": ParamMeta("TIM thickness", "mm", "Cold plate", "assumed", "low",
                                             "Compressed bond-line thickness; verify against the assembly design."),
    "cold_plate.tim_k_w_mk": ParamMeta("TIM thermal conductivity", "W/(m·K)", "Cold plate", "assumed", "medium",
                                       "Use the datasheet value at the actual compression."),
    "cold_plate.contact_resistance_m2k_w": ParamMeta("Cell/TIM contact resistance (area-specific)", "m²K/W", "Cold plate", "assumed", "low",
                                                     "Typical 1e-4…5e-4 m²K/W; strongly dependent on assembly pressure."),
    "cold_plate.cell_contact_area_m2": ParamMeta("Cell contact area (per cell)", "m²", "Cold plate", "assumed", "low",
                                                 "Placeholder; derive from cell dimensions and cooling face."),
    "cold_plate.fin_efficiency": ParamMeta("Channel-wall (fin) efficiency", "-", "Cold plate", "assumed", "medium",
                                           "1.0 = the entire wetted perimeter is fully effective."),
    "cold_plate.roughness_um": ParamMeta("Channel wall roughness", "µm", "Cold plate", "assumed", "low",
                                         "Only matters for turbulent flow."),
    "cold_plate.minor_loss_k": ParamMeta("Minor-loss coefficient (inlet/outlet/bends)", "-", "Cold plate", "assumed", "low",
                                         "Sum of loss coefficients on channel velocity head."),
    "cold_plate.external_dp_kpa": ParamMeta("External loop pressure drop (hoses, chiller, radiator)", "kPa", "Cold plate", "assumed", "low",
                                            "Placeholder until the loop is designed."),
    "cold_plate.flow_lpm": ParamMeta("Actual pack coolant flow", "L/min", "Cold plate", "calculated", "medium",
                                     "If not supplied, the required flow from the sizing calculation is used."),
    "pump.overall_efficiency": ParamMeta("Pump overall efficiency (hydraulic→electrical)", "-", "Pump", "assumed", "medium",
                                         "Automotive electric coolant pumps: 0.3–0.5."),
    "radiator.air_dt_k": ParamMeta("Assumed air-side temperature rise", "K", "Radiator", "assumed", "low",
                                   "Indicative only; final sizing needs air-side data."),
    "vehicle.air_density": ParamMeta("Air density", "kg/m³", "Vehicle", "assumed", "medium", "ISA sea level 15 °C = 1.225."),
    "vehicle.regen_fraction": ParamMeta("Regenerative braking recovery fraction", "-", "Vehicle", "assumed", "low",
                                        "Share of braking energy taken through the motor; the rest goes to friction brakes."),
    "vehicle.rotational_inertia_factor": ParamMeta("Rotational inertia factor ε", "-", "Vehicle", "assumed", "medium",
                                                   "0 = rotating masses neglected; typical 0.03–0.08."),
    "limits.cooling_margin_warn_pct": ParamMeta("Cooling margin: warning below", "%", "Margins", "assumed", "medium", "Configurable engineering limit."),
    "limits.cooling_margin_target_pct": ParamMeta("Cooling margin: engineering target", "%", "Margins", "assumed", "medium", "Configurable engineering limit."),
    "limits.flow_min_lpm": ParamMeta("Minimum practical coolant flow", "L/min", "Margins", "assumed", "low", "Floor applied to the flow when the required flow is very small."),
    "limits.thermal_margin_warn_k": ParamMeta("Thermal margin: warning below", "K", "Margins", "assumed", "medium", "Configurable engineering limit."),
}


def assumed_default_paths() -> list[str]:
    return [p for p, m in CATALOG.items() if m.default_source == "assumed"]


def catalog_dict() -> dict[str, dict]:
    return {p: asdict(m) for p, m in CATALOG.items()}


@dataclass
class RegisterRow:
    path: str
    parameter: str
    value: Any
    unit: str
    source_class: str        # User-provided | Datasheet | Calculated | Assumed
    source: str
    confidence: str          # High | Medium | Low
    group: str
    editable: bool = True
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


_CLASS_LABEL = {"user": "User-provided", "datasheet": "Datasheet", "assumed": "Assumed", "calculated": "Calculated"}


def _lookup(req: AnalysisRequest, path: str):
    obj: Any = req
    for k in path.split("."):
        obj = getattr(obj, k, None)
        if obj is None:
            return None
    return obj


def build_register(req: AnalysisRequest, computed: dict[str, tuple[Any, str]] | None = None) -> list[RegisterRow]:
    """Build the assumptions/data-quality register.

    ``computed`` maps a catalogue path to (value, note) for parameters the engine derived itself
    (e.g. coolant properties from the correlation, estimated ambient UA).
    """
    computed = computed or {}
    rows: list[RegisterRow] = []
    for path, meta in CATALOG.items():
        value = _lookup(req, path)
        pv = req.provenance.get(path)
        note = meta.rationale
        if path in computed:
            value, cnote = computed[path]
            source_class = "calculated"
            source = cnote or "engine correlation"
            conf = meta.confidence if meta.default_source == "calculated" else "medium"
        elif value is None:
            continue
        elif pv is not None:
            source_class = pv.source
            source = pv.note or {"user": "entered by user", "datasheet": "extracted from datasheet (reviewed)",
                                 "assumed": "engineering assumption", "calculated": "derived"}[pv.source]
            conf = pv.confidence or ("high" if pv.source == "user" else meta.confidence)
        else:
            source_class = meta.default_source
            if source_class == "calculated":              # a value present in the request for a normally-calculated parameter is a user override
                source_class = "user"
            source = {"assumed": "engineering default - not confirmed by user", "user": "entered by user",
                      "datasheet": "datasheet / user entry"}[source_class]
            conf = "high" if meta.default_source == "calculated" else meta.confidence
        if hasattr(value, "model_dump"):
            value = "table"
        rows.append(RegisterRow(path, meta.label, value, meta.unit, _CLASS_LABEL[source_class], source,
                                conf.capitalize(), meta.group, True, note))
    return rows
