"""Input-data schema (authoritative definition of everything the engine accepts).

Conventions: SI-derived engineering units are encoded in field names (``_mohm``, ``_kw``, ``_c`` ...).
Sign convention: current / battery power **positive = discharge**.
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------------------------------
# generic containers
# --------------------------------------------------------------------------------------------------
class Curve(_Model):
    """1-D table y = f(x)."""
    x: list[float]
    y: list[float]
    x_label: str = ""
    y_label: str = ""

    @model_validator(mode="after")
    def _check(self):
        if len(self.x) != len(self.y):
            raise ValueError("curve x and y must have the same length")
        if len(self.x) < 2:
            raise ValueError("curve needs at least 2 points")
        if any(not math.isfinite(v) for v in self.x + self.y):
            raise ValueError("curve contains non-finite values")
        pairs = sorted(zip(self.x, self.y))
        self.x = [p[0] for p in pairs]
        self.y = [p[1] for p in pairs]
        if any(b <= a for a, b in zip(self.x, self.x[1:])):
            raise ValueError("curve x values must be unique")
        return self


class Grid2D(_Model):
    """2-D table z[i][j] = f(x[i], y[j]); x = SOC [%], y = temperature [degC]."""
    x: list[float]
    y: list[float]
    z: list[list[float]]
    x_label: str = "SOC [%]"
    y_label: str = "T [degC]"
    z_label: str = ""

    @model_validator(mode="after")
    def _check(self):
        if len(self.x) < 2 or len(self.y) < 2:
            raise ValueError("2-D map needs >=2 points on each axis")
        if len(self.z) != len(self.x) or any(len(r) != len(self.y) for r in self.z):
            raise ValueError("2-D map z must be shaped [len(x)][len(y)]")
        if any(b <= a for a, b in zip(self.x, self.x[1:])) or any(b <= a for a, b in zip(self.y, self.y[1:])):
            raise ValueError("2-D map axes must be strictly increasing")
        return self


class ProvEntry(_Model):
    source: Literal["user", "datasheet", "assumed", "calculated"] = "user"
    confidence: Literal["high", "medium", "low"] | None = None
    note: str = ""


# --------------------------------------------------------------------------------------------------
# project / cell / pack
# --------------------------------------------------------------------------------------------------
class ProjectInfo(_Model):
    name: str = "Untitled project"
    customer: str = ""
    project_no: str = ""
    engineer: str = ""
    revision: str = "A"
    notes: str = ""


class CellSpec(_Model):
    name: str | None = None
    chemistry: str | None = None
    form_factor: Literal["prismatic", "cylindrical", "pouch"] | None = None
    capacity_ah: float | None = None
    v_nom: float | None = None
    v_max: float | None = None
    v_min: float | None = None
    r_dc_mohm: float | None = None            # DC internal resistance at the reference condition
    r_ac_mohm: float | None = None            # AC impedance (1 kHz) - information only, underestimates DCIR
    r_ref_soc_pct: float | None = None
    r_ref_temp_c: float | None = None
    r_vs_soc: Curve | None = None             # x = SOC [%], y = R [mohm]
    r_vs_temp: Curve | None = None            # x = T [degC], y = R [mohm]
    r_map: Grid2D | None = None               # z = R [mohm] at (SOC, T)
    ocv_vs_soc: Curve | None = None           # x = SOC [%], y = OCV [V]
    ocv_map: Grid2D | None = None             # z = OCV [V] at (SOC, T)
    dudt_vs_soc: Curve | None = None          # x = SOC [%], y = dU/dT [mV/K]
    capacity_vs_temp: Curve | None = None     # x = T [degC], y = capacity [% of nominal]
    max_charge_c: float | None = None
    max_discharge_c: float | None = None
    pulse_discharge_c: float | None = None
    pulse_charge_c: float | None = None
    pulse_duration_s: float | None = None
    length_mm: float | None = None
    width_mm: float | None = None
    height_mm: float | None = None
    diameter_mm: float | None = None
    mass_kg: float | None = None
    cp_j_kg_k: float | None = None            # cell specific heat - rarely on datasheets
    t_op_min_c: float | None = None
    t_op_max_c: float | None = None
    t_charge_min_c: float | None = None
    t_charge_max_c: float | None = None
    t_rec_min_c: float | None = None
    t_rec_max_c: float | None = None
    confirmed: bool = False


class PackConfig(_Model):
    ns: int
    np: int
    n_modules: int
    cells_per_module: int
    module_arrangement: Literal["series", "parallel", "series_parallel"] = "series"
    modules_in_series: int | None = None      # only for series_parallel
    pack_nominal_voltage_v: float | None = None
    pack_capacity_ah: float | None = None
    pack_energy_kwh: float | None = None
    soc_initial_pct: float = 90.0
    soc_min_pct: float = 10.0
    soc_max_pct: float = 100.0
    t_initial_c: float = 25.0
    t_target_max_c: float = 40.0
    target_delta_t_k: float = 5.0
    t_ambient_c: float = 25.0


# --------------------------------------------------------------------------------------------------
# driving cycle / vehicle / C-rate
# --------------------------------------------------------------------------------------------------
class DriveCycle(_Model):
    name: str = "cycle"
    time_s: list[float]
    speed_kmh: list[float] | None = None
    accel_ms2: list[float] | None = None
    motor_power_kw: list[float] | None = None
    battery_power_kw: list[float] | None = None
    battery_current_a: list[float] | None = None
    soc_pct: list[float] | None = None
    grade_pct: list[float] | None = None

    @model_validator(mode="after")
    def _lengths(self):
        n = len(self.time_s)
        for name in ("speed_kmh", "accel_ms2", "motor_power_kw", "battery_power_kw",
                     "battery_current_a", "soc_pct", "grade_pct"):
            v = getattr(self, name)
            if v is not None and len(v) != n:
                raise ValueError(f"{name} has {len(v)} samples but time_s has {n}")
        return self


class CycleOptions(_Model):
    source: Literal["auto", "battery_current", "battery_power", "motor_power", "vehicle_speed"] = "auto"
    current_sign: Literal[1, -1] = 1          # +1: positive current = discharge
    power_sign: Literal[1, -1] = 1            # +1: positive power = discharge
    motor_power_basis: Literal["mechanical", "electrical"] = "mechanical"
    battery_power_includes_aux: bool = True
    soc_mode: Literal["auto", "integrate", "file"] = "auto"
    repeats: int = 1


class VehicleParams(_Model):
    mass_kg: float
    crr: float
    cd: float
    frontal_area_m2: float
    wheel_radius_m: float
    drivetrain_eff: float
    aux_load_kw: float = 0.0
    gradient_pct: float = 0.0
    air_density: float = 1.225
    rotational_inertia_factor: float = 0.0    # epsilon: m_eff = m(1+eps)
    regen_fraction: float = 1.0               # share of braking energy recovered through the motor
    max_regen_kw: float | None = None


class CRateLimits(_Model):
    cont_discharge_c: float | None = None
    peak_discharge_c: float | None = None
    peak_discharge_duration_s: float | None = None
    charge_c: float | None = None
    regen_c: float | None = None
    peak_regen_c: float | None = None
    peak_regen_duration_s: float | None = None


class Segment(_Model):
    kind: Literal["discharge", "charge", "regen", "rest"]
    c_rate: float = 0.0                       # magnitude (>=0)
    duration_s: float


class CRateProfile(_Model):
    """Constant-C-rate duty profile used only when no driving cycle is supplied."""
    segments: list[Segment]
    dt_s: float = 1.0


# --------------------------------------------------------------------------------------------------
# heat / thermal / cooling settings
# --------------------------------------------------------------------------------------------------
class ResistanceSettings(_Model):
    level: Literal["auto", 1, 2, 3, 4] = "auto"
    extrapolation: Literal["block", "clamp", "linear"] = "block"
    scale: float = 1.0                        # BOL->EOL or margin multiplier
    charge_factor: float = 1.0                # R(charge)/R(discharge)


class EntropicSettings(_Model):
    mode: Literal["auto", "table", "map", "constant", "excluded"] = "auto"
    constant_mv_per_k: float | None = None    # user estimate of dU/dT


class ThermalSettings(_Model):
    extra_thermal_mass_j_k: float = 0.0       # busbars, housings, plates not in cell mass
    ambient_ua_w_k: float | None = None       # pack-to-ambient conductance; None -> estimated
    ambient_h_w_m2k: float = 5.0              # used only for the UA estimate
    couple_resistance_to_temperature: bool = True
    design_philosophy: Literal["peak", "moving_average", "sustained", "drive_cycle"] = "moving_average"
    safety_factor: float = 1.2
    moving_avg_window_s: float | None = None
    cell_heat_spread_pct: float = 10.0        # cell-to-cell heat generation spread (resistance / current sharing)
    flow_maldistribution_pct: float = 10.0    # weakest-branch flow deficit for parallel plates


class CoolantSpec(_Model):
    type: Literal["water", "eg_water", "pg_water", "custom"] = "eg_water"
    concentration_pct: float = 50.0
    concentration_basis: Literal["volume", "mass"] = "volume"
    inlet_c: float = 25.0
    max_outlet_c: float | None = None
    allowable_dt_k: float | None = 5.0
    density_kg_m3: float | None = None
    cp_j_kg_k: float | None = None
    k_w_mk: float | None = None
    mu_pa_s: float | None = None


class ColdPlateSpec(_Model):
    material: Literal["aluminium_6061", "aluminium_3003", "copper", "stainless_304", "custom"] = "aluminium_6061"
    k_plate_w_mk: float | None = None
    thickness_mm: float = 2.0                  # conduction thickness between TIM and coolant
    channel_width_mm: float = 10.0
    channel_height_mm: float = 3.0
    n_channels: int = 4
    channel_length_mm: float = 1000.0
    cooling_area_m2: float = 0.3               # plate footprint per plate
    n_plates: int = 1
    plate_arrangement: Literal["parallel", "series"] = "parallel"
    tim_thickness_mm: float = 0.5
    tim_k_w_mk: float = 3.0
    contact_resistance_m2k_w: float = 2.0e-4   # cell/TIM interface, area-specific
    cell_contact_area_m2: float = 0.02         # per cell, against the plate
    fin_efficiency: float = 1.0
    roughness_um: float = 1.5
    nu_boundary: Literal["constant_heat_flux", "constant_wall_temperature"] = "constant_heat_flux"
    minor_loss_k: float = 1.5                  # sum of inlet/outlet/bend loss coefficients (channel velocity head)
    external_dp_kpa: float = 30.0              # hoses, chiller, radiator, fittings
    flow_lpm: float | None = None              # actual pack flow; None -> use required flow


class PumpSpec(_Model):
    overall_efficiency: float = 0.40


class RadiatorSpec(_Model):
    air_dt_k: float = 10.0                     # assumed air-side temperature rise


class LimitSettings(_Model):
    cooling_margin_warn_pct: float = 10.0
    cooling_margin_target_pct: float = 20.0
    thermal_margin_warn_k: float = 3.0
    dt_warn_fraction: float = 0.8
    max_velocity_warn_m_s: float = 2.0
    max_velocity_fail_m_s: float = 4.0
    plate_dp_warn_kpa: float = 50.0
    plate_dp_fail_kpa: float = 100.0
    loop_dp_warn_kpa: float = 100.0
    loop_dp_fail_kpa: float = 200.0
    flow_warn_lpm: float = 40.0
    flow_fail_lpm: float = 100.0
    flow_min_lpm: float = 0.5


# --------------------------------------------------------------------------------------------------
# top-level request
# --------------------------------------------------------------------------------------------------
class AnalysisRequest(_Model):
    project: ProjectInfo = Field(default_factory=ProjectInfo)
    cell: CellSpec
    pack: PackConfig
    cycle: DriveCycle | None = None
    cycle_options: CycleOptions = Field(default_factory=CycleOptions)
    vehicle: VehicleParams | None = None
    crate_limits: CRateLimits = Field(default_factory=CRateLimits)
    crate_profile: CRateProfile | None = None
    resistance: ResistanceSettings = Field(default_factory=ResistanceSettings)
    entropic: EntropicSettings = Field(default_factory=EntropicSettings)
    thermal: ThermalSettings = Field(default_factory=ThermalSettings)
    coolant: CoolantSpec = Field(default_factory=CoolantSpec)
    cold_plate: ColdPlateSpec | None = None
    pump: PumpSpec = Field(default_factory=PumpSpec)
    radiator: RadiatorSpec = Field(default_factory=RadiatorSpec)
    limits: LimitSettings = Field(default_factory=LimitSettings)
    installed_cooling_capacity_kw: float | None = None
    provenance: dict[str, ProvEntry] = Field(default_factory=dict)
    require_cell_confirmation: bool = True
