"""Validation engine: turns bad / missing / inconsistent inputs into clear engineering messages.

Severity: ``error`` blocks the analysis, ``warning`` is reported prominently, ``info`` is context.
Every issue has a stable ``code`` so the UI/tests can react to it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Iterable

from .schemas import AnalysisRequest, CellSpec, PackConfig

ERROR, WARNING, INFO = "error", "warning", "info"


@dataclass
class Issue:
    code: str
    severity: str
    field: str
    message: str
    hint: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def has_errors(issues: Iterable[Issue]) -> bool:
    return any(i.severity == ERROR for i in issues)


def _num(v) -> bool:
    return v is not None and isinstance(v, (int, float)) and math.isfinite(v)


# --------------------------------------------------------------------------------------------------
# cell
# --------------------------------------------------------------------------------------------------
def validate_cell(cell: CellSpec, require_confirmation: bool = True) -> list[Issue]:
    out: list[Issue] = []
    add = lambda *a, **k: out.append(Issue(*a, **k))  # noqa: E731

    if require_confirmation and not cell.confirmed:
        add("CELL_NOT_CONFIRMED", ERROR, "cell.confirmed",
            "Cell parameters have not been reviewed and confirmed.",
            "Review every extracted/entered value on the 'Confirm cell parameters' step and confirm.")

    # required
    if not _num(cell.capacity_ah):
        add("CELL_CAPACITY_MISSING", ERROR, "cell.capacity_ah",
            "Cell nominal capacity [Ah] is missing.", "Enter it from the datasheet - C-rate, SOC and pack energy depend on it.")
    elif cell.capacity_ah <= 0:
        add("CELL_CAPACITY_INVALID", ERROR, "cell.capacity_ah", f"Cell capacity must be > 0 Ah (got {cell.capacity_ah}).")

    if not _num(cell.v_nom):
        add("CELL_VOLTAGE_MISSING", ERROR, "cell.v_nom",
            "Cell nominal voltage [V] is missing.", "Enter the nominal voltage; pack voltage and energy depend on it.")
    elif cell.v_nom <= 0:
        add("CELL_VOLTAGE_INVALID", ERROR, "cell.v_nom", f"Cell nominal voltage must be > 0 V (got {cell.v_nom}).")

    has_r = (_num(cell.r_dc_mohm) or cell.r_vs_soc is not None or cell.r_vs_temp is not None
             or cell.r_map is not None)
    if not has_r:
        add("CELL_RESISTANCE_MISSING", ERROR, "cell.r_dc_mohm",
            "No cell internal resistance is available (no DC value, no R-vs-SOC/T table, no map).",
            "Joule heat = I²R cannot be calculated without it. Enter the DC resistance from the datasheet or test data. "
            "The tool will not invent a value.")
    if _num(cell.r_dc_mohm) and cell.r_dc_mohm <= 0:
        add("CELL_RESISTANCE_INVALID", ERROR, "cell.r_dc_mohm", f"Cell resistance must be > 0 mΩ (got {cell.r_dc_mohm}).")
    if _num(cell.r_ac_mohm) and not _num(cell.r_dc_mohm) and cell.r_vs_soc is None \
            and cell.r_vs_temp is None and cell.r_map is None:
        add("CELL_RESISTANCE_AC_ONLY", WARNING, "cell.r_ac_mohm",
            "Only AC (1 kHz) impedance is available. It typically under-estimates the DC resistance seen "
            "by the load, so Joule heat will be under-predicted.",
            "Provide DCIR (e.g. HPPC 10 s) or apply a documented scale factor.")

    # voltages
    if _num(cell.v_max) and _num(cell.v_min) and cell.v_max <= cell.v_min:
        add("CELL_VOLTAGE_LIMITS", ERROR, "cell.v_max", "Max cell voltage must exceed min cell voltage.")
    if _num(cell.v_nom) and _num(cell.v_max) and _num(cell.v_min) and not (cell.v_min <= cell.v_nom <= cell.v_max):
        add("CELL_VOLTAGE_ORDER", ERROR, "cell.v_nom",
            f"Nominal voltage {cell.v_nom} V is outside [{cell.v_min}, {cell.v_max}] V.")
    if not _num(cell.v_max) or not _num(cell.v_min):
        add("CELL_VOLTAGE_WINDOW_MISSING", WARNING, "cell.v_max",
            "Max/min cell voltage not provided - pack voltage window cannot be checked.")

    # C-rates
    for f in ("max_charge_c", "max_discharge_c", "pulse_discharge_c", "pulse_charge_c"):
        v = getattr(cell, f)
        if v is not None and (not _num(v) or v <= 0):
            add("CELL_CRATE_INVALID", ERROR, f"cell.{f}", f"{f} must be a positive C-rate (got {v}).")
        elif v is not None and v > 20:
            add("CELL_CRATE_UNREALISTIC", WARNING, f"cell.{f}", f"{f} = {v} C is unusually high - check units (A vs C).")

    # geometry / mass
    for f in ("length_mm", "width_mm", "height_mm", "diameter_mm", "mass_kg", "cp_j_kg_k"):
        v = getattr(cell, f)
        if v is not None and (not _num(v) or v <= 0):
            add("CELL_VALUE_INVALID", ERROR, f"cell.{f}", f"{f} must be > 0 (got {v}).")
    if not _num(cell.mass_kg):
        add("CELL_MASS_MISSING", WARNING, "cell.mass_kg",
            "Cell mass is missing: thermal mass cannot be calculated, so transient temperature prediction and the "
            "drive-cycle sizing philosophy are unavailable.", "Enter the cell mass [kg].")
    if not _num(cell.cp_j_kg_k):
        add("CELL_CP_MISSING", WARNING, "cell.cp_j_kg_k",
            "Cell specific heat is not on the datasheet: transient temperature prediction is unavailable until you "
            "enter a value or accept an engineering assumption.",
            "Typical Li-ion cells: 800-1100 J/(kg·K). Confirm/measure for the actual cell.")

    # temperature limits
    if _num(cell.t_op_min_c) and _num(cell.t_op_max_c) and cell.t_op_min_c >= cell.t_op_max_c:
        add("CELL_TEMP_RANGE", ERROR, "cell.t_op_min_c", "Operating temperature range is inverted (min >= max).")
    if _num(cell.t_rec_min_c) and _num(cell.t_rec_max_c) and cell.t_rec_min_c >= cell.t_rec_max_c:
        add("CELL_TEMP_RANGE", ERROR, "cell.t_rec_min_c", "Recommended temperature range is inverted (min >= max).")
    if not _num(cell.t_op_max_c):
        add("CELL_TOP_MISSING", WARNING, "cell.t_op_max_c",
            "Maximum operating temperature not provided - absolute temperature limit check (Check 6) is incomplete.")

    # OCV / entropic data status (informational, never silent)
    if cell.ocv_vs_soc is None and cell.ocv_map is None:
        add("CELL_OCV_MISSING", WARNING, "cell.ocv_vs_soc",
            "No OCV-vs-SOC curve: OCV is held at the nominal voltage (a constant), so terminal power/current "
            "conversion and voltage sag are approximate.")
    return out


# --------------------------------------------------------------------------------------------------
# pack configuration
# --------------------------------------------------------------------------------------------------
def _pct_diff(a: float, b: float) -> float:
    return abs(a - b) / max(abs(b), 1e-12) * 100.0


def validate_pack(cell: CellSpec, pack: PackConfig) -> list[Issue]:
    out: list[Issue] = []
    add = lambda *a, **k: out.append(Issue(*a, **k))  # noqa: E731

    for f in ("ns", "np", "n_modules", "cells_per_module"):
        v = getattr(pack, f)
        if v < 1:
            add("PACK_COUNT_INVALID", ERROR, f"pack.{f}", f"{f} must be an integer >= 1 (got {v}).")
    if has_errors(out):
        return out

    n_cells = pack.ns * pack.np
    if n_cells != pack.n_modules * pack.cells_per_module:
        add("PACK_CELL_COUNT_MISMATCH", ERROR, "pack.cells_per_module",
            f"Ns×Np = {pack.ns}×{pack.np} = {n_cells} cells, but modules × cells/module = "
            f"{pack.n_modules}×{pack.cells_per_module} = {pack.n_modules * pack.cells_per_module}.",
            "Correct the series/parallel counts or the module definition so the cell count matches.")

    arr = pack.module_arrangement
    ms = mp = None
    if arr == "series":
        ms, mp = pack.n_modules, 1
    elif arr == "parallel":
        ms, mp = 1, pack.n_modules
    else:
        if not pack.modules_in_series or pack.modules_in_series < 1:
            add("PACK_TOPOLOGY", ERROR, "pack.modules_in_series",
                "Series-parallel module arrangement requires 'modules in series'.")
        elif pack.n_modules % pack.modules_in_series:
            add("PACK_TOPOLOGY", ERROR, "pack.modules_in_series",
                f"{pack.n_modules} modules cannot be split into groups of {pack.modules_in_series} in series.")
        else:
            ms, mp = pack.modules_in_series, pack.n_modules // pack.modules_in_series
    if ms is not None:
        if pack.ns % ms:
            add("PACK_TOPOLOGY", ERROR, "pack.ns",
                f"Ns = {pack.ns} is not divisible by modules-in-series = {ms}: series strings cannot be split evenly.")
        if pack.np % mp:
            add("PACK_TOPOLOGY", ERROR, "pack.np",
                f"Np = {pack.np} is not divisible by modules-in-parallel = {mp}.")

    # user stated vs computed
    if _num(cell.v_nom) and _num(pack.pack_nominal_voltage_v):
        calc = pack.ns * cell.v_nom
        d = _pct_diff(pack.pack_nominal_voltage_v, calc)
        if d > 5:
            add("PACK_VOLTAGE_INCONSISTENT", ERROR, "pack.pack_nominal_voltage_v",
                f"Stated pack voltage {pack.pack_nominal_voltage_v:.1f} V differs from Ns×V_cell = {calc:.1f} V by {d:.1f} %.",
                "Check Ns and the cell nominal voltage.")
        elif d > 1:
            add("PACK_VOLTAGE_INCONSISTENT", WARNING, "pack.pack_nominal_voltage_v",
                f"Stated pack voltage {pack.pack_nominal_voltage_v:.1f} V differs from Ns×V_cell = {calc:.1f} V by {d:.1f} %.")
    if _num(cell.capacity_ah) and _num(pack.pack_capacity_ah):
        calc = pack.np * cell.capacity_ah
        d = _pct_diff(pack.pack_capacity_ah, calc)
        if d > 5:
            add("PACK_CAPACITY_INCONSISTENT", ERROR, "pack.pack_capacity_ah",
                f"Stated pack capacity {pack.pack_capacity_ah:.1f} Ah differs from Np×C_cell = {calc:.1f} Ah by {d:.1f} %.")
        elif d > 1:
            add("PACK_CAPACITY_INCONSISTENT", WARNING, "pack.pack_capacity_ah",
                f"Stated pack capacity {pack.pack_capacity_ah:.1f} Ah differs from Np×C_cell = {calc:.1f} Ah by {d:.1f} %.")
    if _num(cell.capacity_ah) and _num(cell.v_nom) and _num(pack.pack_energy_kwh):
        calc = pack.ns * pack.np * cell.capacity_ah * cell.v_nom / 1000
        d = _pct_diff(pack.pack_energy_kwh, calc)
        if d > 5:
            add("PACK_ENERGY_INCONSISTENT", ERROR, "pack.pack_energy_kwh",
                f"Stated pack energy {pack.pack_energy_kwh:.2f} kWh differs from Ns·Np·Ah·V = {calc:.2f} kWh by {d:.1f} %.")
        elif d > 1:
            add("PACK_ENERGY_INCONSISTENT", WARNING, "pack.pack_energy_kwh",
                f"Stated pack energy {pack.pack_energy_kwh:.2f} kWh differs from Ns·Np·Ah·V = {calc:.2f} kWh by {d:.1f} %.")

    # SOC
    for f in ("soc_initial_pct", "soc_min_pct", "soc_max_pct"):
        v = getattr(pack, f)
        if not (0 <= v <= 100):
            add("SOC_OUT_OF_RANGE", ERROR, f"pack.{f}", f"{f} = {v} is outside 0-100 %.")
    if pack.soc_min_pct >= pack.soc_max_pct:
        add("SOC_WINDOW_INVALID", ERROR, "pack.soc_min_pct", "Minimum SOC must be lower than maximum SOC.")
    elif not (pack.soc_min_pct <= pack.soc_initial_pct <= pack.soc_max_pct):
        add("SOC_INITIAL_OUTSIDE_WINDOW", WARNING, "pack.soc_initial_pct",
            f"Initial SOC {pack.soc_initial_pct} % is outside the operating window "
            f"[{pack.soc_min_pct}, {pack.soc_max_pct}] %.")

    # temperatures
    if pack.t_target_max_c <= pack.t_initial_c:
        add("TEMP_TARGET_BELOW_INITIAL", ERROR, "pack.t_target_max_c",
            f"Target max cell temperature ({pack.t_target_max_c} °C) must exceed the initial temperature ({pack.t_initial_c} °C).")
    if pack.target_delta_t_k <= 0:
        add("TARGET_DT_INVALID", ERROR, "pack.target_delta_t_k", "Target cell-to-cell ΔT must be > 0 K.")
    if not (-60 <= pack.t_ambient_c <= 90):
        add("AMBIENT_UNREALISTIC", WARNING, "pack.t_ambient_c", f"Ambient temperature {pack.t_ambient_c} °C looks unrealistic.")
    if _num(cell.t_op_max_c) and pack.t_target_max_c > cell.t_op_max_c:
        add("TARGET_ABOVE_CELL_LIMIT", ERROR, "pack.t_target_max_c",
            f"Target max cell temperature {pack.t_target_max_c} °C exceeds the cell's maximum operating temperature {cell.t_op_max_c} °C.")
    elif _num(cell.t_rec_max_c) and pack.t_target_max_c > cell.t_rec_max_c:
        add("TARGET_ABOVE_RECOMMENDED", WARNING, "pack.t_target_max_c",
            f"Target max cell temperature {pack.t_target_max_c} °C is above the recommended maximum {cell.t_rec_max_c} °C "
            f"(accelerated ageing).")
    return out


def validate_config(req: AnalysisRequest) -> list[Issue]:
    """Phase-1 subset: cell + pack only (used by the live configuration endpoint)."""
    return validate_cell(req.cell, req.require_cell_confirmation) + validate_pack(req.cell, req.pack)


# --------------------------------------------------------------------------------------------------
# vehicle / driving cycle / C-rate
# --------------------------------------------------------------------------------------------------
def validate_vehicle(v) -> list[Issue]:
    out: list[Issue] = []
    add = lambda *a, **k: out.append(Issue(*a, **k))  # noqa: E731
    checks = [("mass_kg", 300, 60000, "vehicle mass"), ("crr", 0.002, 0.05, "rolling-resistance coefficient"),
              ("cd", 0.1, 1.5, "drag coefficient"), ("frontal_area_m2", 0.5, 12, "frontal area"),
              ("wheel_radius_m", 0.15, 0.7, "wheel radius")]
    for f, lo, hi, label in checks:
        x = getattr(v, f)
        if not _num(x) or x <= 0:
            add("VEHICLE_VALUE_INVALID", ERROR, f"vehicle.{f}", f"{label} must be > 0 (got {x}).")
        elif not (lo <= x <= hi):
            add("VEHICLE_VALUE_ATYPICAL", WARNING, f"vehicle.{f}", f"{label} = {x:g} is outside the usual range {lo:g}-{hi:g} - check units.")
    if not (0 < v.drivetrain_eff <= 1):
        add("VEHICLE_EFFICIENCY_INVALID", ERROR, "vehicle.drivetrain_eff", f"Drivetrain efficiency must be in (0, 1] (got {v.drivetrain_eff}).")
    elif v.drivetrain_eff < 0.6:
        add("VEHICLE_EFFICIENCY_LOW", WARNING, "vehicle.drivetrain_eff", f"Drivetrain efficiency {v.drivetrain_eff:g} is unusually low.")
    if v.aux_load_kw < 0:
        add("VEHICLE_VALUE_INVALID", ERROR, "vehicle.aux_load_kw", "Auxiliary load cannot be negative.")
    if not (0 <= v.regen_fraction <= 1):
        add("VEHICLE_REGEN_INVALID", ERROR, "vehicle.regen_fraction", "Regenerative fraction must be within 0-1.")
    if abs(v.gradient_pct) > 40:
        add("VEHICLE_GRADE_UNREALISTIC", WARNING, "vehicle.gradient_pct", f"Road gradient {v.gradient_pct:g} % is unrealistic.")
    if v.max_regen_kw is not None and v.max_regen_kw < 0:
        add("VEHICLE_VALUE_INVALID", ERROR, "vehicle.max_regen_kw", "Max regen power must be >= 0.")
    return out


def validate_time_base(t, gap_factor: float = 5.0) -> list[Issue]:
    """Engine-side re-check of a time vector (defence in depth - ingestion normally catches these first)."""
    import numpy as np
    out: list[Issue] = []
    t = np.asarray(t, float)
    if t.size < 10:
        out.append(Issue("CYCLE_TOO_SHORT", ERROR, "cycle.time_s", f"Only {t.size} samples - need at least 10."))
        return out
    if not np.all(np.isfinite(t)):
        out.append(Issue("CYCLE_TIME_INVALID", ERROR, "cycle.time_s", "Time contains non-numeric values."))
        return out
    dt = np.diff(t)
    if np.any(dt < 0):
        out.append(Issue("CYCLE_TIME_NOT_MONOTONIC", ERROR, "cycle.time_s", "Time is not monotonically increasing."))
    if np.any(dt == 0):
        out.append(Issue("CYCLE_DUPLICATE_TIME", ERROR, "cycle.time_s", f"{int((dt == 0).sum())} duplicate timestamp(s)."))
    if has_errors(out):
        return out
    med = float(np.median(dt))
    big = dt > gap_factor * med
    if big.any():
        out.append(Issue("CYCLE_GAP", ERROR, "cycle.time_s", f"{int(big.sum())} gap(s) in the time base (largest {dt.max():g} s vs. median {med:g} s)."))
    elif (dt.max() - dt.min()) / med > 0.01:
        out.append(Issue("CYCLE_NONUNIFORM_DT", WARNING, "cycle.time_s",
                         f"Non-uniform time step (min {dt.min():g}, median {med:g}, max {dt.max():g} s)."))
    return out


def validate_load(req: AnalysisRequest) -> list[Issue]:
    import numpy as np
    out: list[Issue] = []
    add = lambda *a, **k: out.append(Issue(*a, **k))  # noqa: E731
    c, opt = req.cycle, req.cycle_options
    if c is None and req.crate_profile is None:
        add("LOAD_MISSING", ERROR, "cycle", "No load defined.", "Upload a driving cycle or define a charge/discharge C-rate duty profile.")
    if c is not None:
        out += validate_time_base(c.time_s)
        avail = {"battery_current": c.battery_current_a is not None, "battery_power": c.battery_power_kw is not None,
                 "motor_power": c.motor_power_kw is not None, "vehicle_speed": c.speed_kmh is not None}
        if not any(avail.values()):
            add("CYCLE_NO_LOAD_DATA", ERROR, "cycle", "The driving cycle has no battery current, battery power, motor power or speed.")
        elif opt.source != "auto" and not avail[opt.source]:
            add("CYCLE_SOURCE_UNAVAILABLE", ERROR, "cycle_options.source", f"Selected load source '{opt.source}' is not in the driving cycle.")
        else:
            src = opt.source if opt.source != "auto" else next(k for k in ("battery_current", "battery_power", "motor_power", "vehicle_speed") if avail[k])
            if src == "vehicle_speed" and req.vehicle is None:
                add("VEHICLE_PARAMS_MISSING", ERROR, "vehicle", "Only vehicle speed is available - vehicle parameters are required to calculate battery power.",
                    "Enter mass, Crr, Cd, frontal area, wheel radius, drivetrain efficiency, auxiliary load and gradient.")
            if src == "motor_power" and opt.motor_power_basis == "mechanical" and req.vehicle is None:
                add("VEHICLE_PARAMS_MISSING", ERROR, "vehicle", "Motor power is mechanical - the drivetrain efficiency is needed to obtain battery power.")
            if src == "battery_power" and not opt.battery_power_includes_aux and req.vehicle is None:
                add("VEHICLE_PARAMS_MISSING", ERROR, "vehicle", "Battery power excludes auxiliaries but no auxiliary load was provided.")
        if c.soc_pct is not None:
            s = np.asarray(c.soc_pct, float)
            if np.nanmin(s) < 0 or np.nanmax(s) > 100:
                add("CYCLE_SOC_OUT_OF_RANGE", ERROR, "cycle.soc_pct", f"SOC in the cycle leaves 0-100 % (min {np.nanmin(s):.3g}, max {np.nanmax(s):.3g}).")
        for name in ("speed_kmh", "accel_ms2", "motor_power_kw", "battery_power_kw", "battery_current_a", "soc_pct", "grade_pct"):
            arr = getattr(c, name)
            if arr is not None and not np.all(np.isfinite(np.asarray(arr, float))):
                add("CYCLE_MISSING_VALUES", ERROR, f"cycle.{name}", f"{name} contains missing / non-finite values.")
        if not (1 <= opt.repeats <= 200):
            add("CYCLE_REPEATS_INVALID", ERROR, "cycle_options.repeats", "Cycle repeats must be between 1 and 200.")
    if req.vehicle is not None:
        out += validate_vehicle(req.vehicle)
    p = req.crate_profile
    if p is not None and c is None:
        if p.dt_s <= 0:
            add("CRATE_PROFILE_INVALID", ERROR, "crate_profile.dt_s", "Profile time step must be > 0 s.")
        if not p.segments:
            add("CRATE_PROFILE_INVALID", ERROR, "crate_profile.segments", "The C-rate profile has no segments.")
        for i, s in enumerate(p.segments):
            if s.c_rate < 0 or s.duration_s <= 0:
                add("CRATE_INVALID", ERROR, f"crate_profile.segments[{i}]", f"Segment {i + 1}: C-rate must be >= 0 and duration > 0 s.")
            elif s.c_rate > 20:
                add("CRATE_UNREALISTIC", WARNING, f"crate_profile.segments[{i}]", f"Segment {i + 1}: {s.c_rate:g} C is unrealistic - check A vs C.")
    lim = req.crate_limits
    for f in ("cont_discharge_c", "peak_discharge_c", "charge_c", "regen_c", "peak_regen_c"):
        v = getattr(lim, f)
        if v is not None and (not _num(v) or v <= 0):
            add("CRATE_INVALID", ERROR, f"crate_limits.{f}", f"{f} must be a positive C-rate (got {v}).")
        elif v is not None and v > 20:
            add("CRATE_UNREALISTIC", WARNING, f"crate_limits.{f}", f"{f} = {v:g} C is unrealistic.")
    for a, b in (("cont_discharge_c", "peak_discharge_c"), ("regen_c", "peak_regen_c")):
        va, vb = getattr(lim, a), getattr(lim, b)
        if va is not None and vb is not None and vb < va:
            add("CRATE_PEAK_BELOW_CONTINUOUS", ERROR, f"crate_limits.{b}", f"Peak C-rate ({vb:g}) is below the continuous C-rate ({va:g}).")
    for f in ("peak_discharge_duration_s", "peak_regen_duration_s"):
        v = getattr(lim, f)
        if v is not None and v <= 0:
            add("CRATE_INVALID", ERROR, f"crate_limits.{f}", f"{f} must be > 0 s.")
    return out


# --------------------------------------------------------------------------------------------------
# thermal / cooling / resistance range
# --------------------------------------------------------------------------------------------------
def validate_thermal_cooling(req: AnalysisRequest) -> list[Issue]:
    out: list[Issue] = []
    add = lambda *a, **k: out.append(Issue(*a, **k))  # noqa: E731
    th, co, cell, pack = req.thermal, req.coolant, req.cell, req.pack
    if th.safety_factor < 1.0:
        add("SAFETY_FACTOR_INVALID", ERROR, "thermal.safety_factor", f"Thermal safety factor {th.safety_factor:g} is below 1.0 - the design load would be under-sized.")
    elif th.safety_factor > 3.0:
        add("SAFETY_FACTOR_HIGH", WARNING, "thermal.safety_factor", f"Thermal safety factor {th.safety_factor:g} is very high - check for double counting of margins.")
    if th.moving_avg_window_s is not None and th.moving_avg_window_s <= 0:
        add("WINDOW_INVALID", ERROR, "thermal.moving_avg_window_s", "Moving-average window must be > 0 s.")
    for f in ("cell_heat_spread_pct", "flow_maldistribution_pct"):
        v = getattr(th, f)
        if not (0 <= v < 100):
            add("THERMAL_SETTING_INVALID", ERROR, f"thermal.{f}", f"{f} must be within 0-100 % (got {v}).")
    if th.extra_thermal_mass_j_k < 0 or (th.ambient_ua_w_k is not None and th.ambient_ua_w_k < 0):
        add("THERMAL_SETTING_INVALID", ERROR, "thermal", "Extra thermal mass and ambient conductance cannot be negative.")
    if req.resistance.scale <= 0 or req.resistance.charge_factor <= 0:
        add("RESISTANCE_SETTING_INVALID", ERROR, "resistance.scale", "Resistance scale factor and charge factor must be > 0.")

    # coolant
    from .cooling import CoolingError, effective_coolant_dt
    try:
        dt, _ = effective_coolant_dt(co)
        if dt < 0.5:
            add("COOLANT_DT_TINY", WARNING, "coolant.allowable_dt_k", f"Allowable coolant ΔT of {dt:g} K implies a very large flow rate.")
        elif dt > 15:
            add("COOLANT_DT_LARGE", WARNING, "coolant.allowable_dt_k", f"Allowable coolant ΔT of {dt:g} K will give poor cell-to-cell temperature uniformity.")
    except CoolingError as exc:
        add("COOLANT_DT_INVALID", ERROR, "coolant.allowable_dt_k", str(exc))
    if co.inlet_c >= pack.t_target_max_c:
        add("COOLANT_INLET_TOO_WARM", ERROR, "coolant.inlet_c",
            f"Coolant inlet temperature {co.inlet_c:g} °C is not below the target maximum cell temperature {pack.t_target_max_c:g} °C - no heat can be removed.")
    if co.max_outlet_c is not None and co.max_outlet_c > pack.t_target_max_c:
        add("COOLANT_OUTLET_ABOVE_TARGET", WARNING, "coolant.max_outlet_c",
            f"Maximum coolant outlet {co.max_outlet_c:g} °C is above the target cell temperature {pack.t_target_max_c:g} °C.")
    if co.type == "custom":
        miss = [n for n, v in (("density", co.density_kg_m3), ("specific heat", co.cp_j_kg_k), ("conductivity", co.k_w_mk), ("viscosity", co.mu_pa_s)) if v is None]
        if miss:
            add("COOLANT_PROPS_MISSING", ERROR, "coolant", f"Custom coolant needs all properties; missing: {', '.join(miss)}.")
    for f, lo, hi in (("density_kg_m3", 500, 2000), ("cp_j_kg_k", 1000, 6000), ("k_w_mk", 0.05, 2.0), ("mu_pa_s", 1e-4, 0.5)):
        v = getattr(co, f)
        if v is not None and not (lo <= v <= hi):
            add("COOLANT_PROP_UNREALISTIC", WARNING, f"coolant.{f}", f"Coolant {f} = {v:g} is outside the plausible range {lo:g}-{hi:g}.")

    # cold plate
    cp = req.cold_plate
    if cp is not None:
        for f in ("thickness_mm", "channel_width_mm", "channel_height_mm", "channel_length_mm", "cooling_area_m2", "tim_thickness_mm",
                  "tim_k_w_mk", "cell_contact_area_m2", "contact_resistance_m2k_w"):
            v = getattr(cp, f)
            if v is None or v <= 0:
                add("COLDPLATE_VALUE_INVALID", ERROR, f"cold_plate.{f}", f"{f} must be > 0 (got {v}).")
        for f in ("n_channels", "n_plates"):
            if getattr(cp, f) < 1:
                add("COLDPLATE_VALUE_INVALID", ERROR, f"cold_plate.{f}", f"{f} must be >= 1.")
        if not (0 < cp.fin_efficiency <= 1):
            add("COLDPLATE_VALUE_INVALID", ERROR, "cold_plate.fin_efficiency", "Fin efficiency must be in (0, 1].")
        if cp.flow_lpm is not None and cp.flow_lpm <= 0:
            add("FLOW_INVALID", ERROR, "cold_plate.flow_lpm", "Specified coolant flow must be > 0 L/min.")
        if cp.external_dp_kpa < 0 or cp.minor_loss_k < 0 or cp.roughness_um < 0:
            add("COLDPLATE_VALUE_INVALID", ERROR, "cold_plate", "Loss coefficients, roughness and external pressure drop cannot be negative.")
        if cp.material == "custom" and cp.k_plate_w_mk is None:
            add("COLDPLATE_K_MISSING", ERROR, "cold_plate.k_plate_w_mk", "Custom plate material needs a thermal conductivity.")
        if cp.n_plates and cp.plate_arrangement in ("series", "parallel") and pack.n_modules and cp.n_plates != pack.n_modules:
            add("COLDPLATE_PLATE_COUNT", INFO, "cold_plate.n_plates",
                f"{cp.n_plates} cold plate(s) for {pack.n_modules} module(s): module ΔT is estimated per plate flow path.")
        if not _num(cell.mass_kg) or not _num(cell.cp_j_kg_k):
            add("THERMAL_MASS_MISSING", WARNING, "cell.mass_kg",
                "Cell mass and/or specific heat missing: the temperature prediction and Checks 1-4/6 cannot be evaluated (shown as N/A).")
    if req.pump.overall_efficiency <= 0 or req.pump.overall_efficiency > 1:
        add("PUMP_EFFICIENCY_INVALID", ERROR, "pump.overall_efficiency", "Pump overall efficiency must be in (0, 1].")
    lim = req.limits
    for a, b in (("cooling_margin_warn_pct", "cooling_margin_target_pct"), ("max_velocity_warn_m_s", "max_velocity_fail_m_s"),
                 ("plate_dp_warn_kpa", "plate_dp_fail_kpa"), ("loop_dp_warn_kpa", "loop_dp_fail_kpa"), ("flow_warn_lpm", "flow_fail_lpm")):
        if getattr(lim, a) > getattr(lim, b):
            add("LIMITS_INCONSISTENT", ERROR, f"limits.{a}", f"{a} ({getattr(lim, a):g}) must not exceed {b} ({getattr(lim, b):g}).")
    if req.installed_cooling_capacity_kw is not None and req.installed_cooling_capacity_kw < 0:
        add("CAPACITY_INVALID", ERROR, "installed_cooling_capacity_kw", "Installed cooling capacity cannot be negative.")

    # philosophy prerequisites
    ph = th.design_philosophy
    if ph == "sustained" and not any(x is not None for x in (req.crate_limits.cont_discharge_c, req.crate_limits.charge_c,
                                                              cell.max_discharge_c, cell.max_charge_c)):
        add("PHILOSOPHY_NEEDS_CRATE", ERROR, "thermal.design_philosophy",
            "The 'sustained' philosophy needs a continuous discharge and/or charge C-rate (step 5 or the cell datasheet).")
    if ph == "drive_cycle" and (not _num(cell.mass_kg) or not _num(cell.cp_j_kg_k)):
        add("PHILOSOPHY_NEEDS_THERMAL_MASS", ERROR, "thermal.design_philosophy",
            "The 'drive-cycle' philosophy needs the cell mass and specific heat (thermal mass).")

    # entropic
    if req.entropic.mode == "constant" and req.entropic.constant_mv_per_k is None:
        add("ENTROPIC_CONSTANT_MISSING", ERROR, "entropic.constant_mv_per_k", "Entropic mode 'constant' needs a dU/dT estimate [mV/K].")
    if req.entropic.mode == "table" and cell.dudt_vs_soc is None:
        add("ENTROPIC_TABLE_MISSING", ERROR, "entropic.mode", "Entropic mode 'table' needs a dU/dT-vs-SOC table on the cell.")
    if req.entropic.mode == "auto" and cell.dudt_vs_soc is None and cell.ocv_map is None and req.entropic.constant_mv_per_k is None:
        add("ENTROPIC_DATA_MISSING", WARNING, "entropic",
            "No dU/dT data and no estimate: reversible (entropic) heat CANNOT be calculated accurately and is left out of the result. "
            "Enter an estimated coefficient (Thermal & cooling step) to include it, or set the mode to 'excluded' to acknowledge the omission.")
    return out


def validate_resistance_range(req: AnalysisRequest) -> list[Issue]:
    """Resistance/OCV data must cover the operating window (SOC window, start temperature ... target temperature)."""
    from .resistance import available_levels
    out: list[Issue] = []
    cell, pack = req.cell, req.pack
    pol = req.resistance.extrapolation
    sev = ERROR if pol == "block" else WARNING
    t_lo = min(pack.t_initial_c, req.coolant.inlet_c)
    t_hi = pack.t_target_max_c
    soc_lo, soc_hi = min(pack.soc_min_pct, pack.soc_initial_pct), max(pack.soc_max_pct, pack.soc_initial_pct)
    tail = ("Extrapolation is blocked - provide data for the full range or explicitly enable clamp/linear extrapolation." if pol == "block"
            else f"Values outside will be {'held constant' if pol == 'clamp' else 'linearly extrapolated'} (explicitly enabled).")

    def soc_check(name, xs):
        if min(xs) > soc_lo or max(xs) < soc_hi:
            out.append(Issue("RESISTANCE_RANGE", sev, f"cell.{name}",
                             f"{name}: SOC data cover {min(xs):g}-{max(xs):g} % but the operating window is {soc_lo:g}-{soc_hi:g} %. {tail}"))

    def t_check(name, ys):
        if min(ys) > t_lo or max(ys) < t_hi:
            out.append(Issue("RESISTANCE_RANGE", sev, f"cell.{name}",
                             f"{name}: temperature data cover {min(ys):g}-{max(ys):g} °C but the pack operates from {t_lo:g} to {t_hi:g} °C. {tail}"))

    avail = available_levels(cell)
    if avail:
        lvl = max(avail) if req.resistance.level == "auto" else int(req.resistance.level)
        uses_soc, uses_t = lvl in (2, 4), lvl in (3, 4)
        if cell.r_map is not None and lvl > 1:
            if uses_soc:
                soc_check("r_map", cell.r_map.x)
            if uses_t:
                t_check("r_map", cell.r_map.y)
        else:
            if uses_soc and cell.r_vs_soc is not None:
                soc_check("r_vs_soc", cell.r_vs_soc.x)
            if uses_t and cell.r_vs_temp is not None:
                t_check("r_vs_temp", cell.r_vs_temp.x)
    if cell.ocv_vs_soc is not None and cell.ocv_map is None:
        soc_check("ocv_vs_soc", cell.ocv_vs_soc.x)
    if cell.ocv_map is not None:
        soc_check("ocv_map", cell.ocv_map.x)
    if cell.capacity_vs_temp is not None:
        t_check("capacity_vs_temp", cell.capacity_vs_temp.x)
    return out


def validate_request(req: AnalysisRequest) -> list[Issue]:
    """Everything that can be checked before a single time step is computed."""
    out = validate_cell(req.cell, req.require_cell_confirmation) + validate_pack(req.cell, req.pack)
    out += validate_load(req) + validate_thermal_cooling(req) + validate_resistance_range(req)
    return out
