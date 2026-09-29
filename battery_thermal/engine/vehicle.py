"""Vehicle road-load model: speed profile -> wheel power -> battery power.

Engineering logic
-----------------
    F_tractive = F_acceleration + F_rolling + F_aerodynamic + F_grade
    F_acc  = m(1+ε)·a          F_roll = Crr·m·g·cosθ  (only while moving)
    F_aero = ½·ρ·Cd·A·v|v|     F_grade = m·g·sinθ
    P_wheel = F_tractive · v
    P_traction,batt = P_wheel / η                       (P_wheel ≥ 0, discharge)
                    = P_wheel · η · f_regen             (P_wheel < 0, regenerative braking)
    P_batt = P_traction,batt + P_aux                    (auxiliaries are always drawn from the battery)

Three quantities are kept separate on purpose: battery *discharge* power, battery *charge / regenerative*
power and *auxiliary* power - the cooling load depends on the current the cells actually carry.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .schemas import VehicleParams
from .trace import TraceLog, fmt
from .units import G

V_STOP = 0.05     # m/s: below this the vehicle is treated as stationary (no rolling resistance)


@dataclass
class RoadLoad:
    t: np.ndarray
    v_ms: np.ndarray
    a_ms2: np.ndarray
    accel_source: str
    grade_pct: np.ndarray
    f_accel: np.ndarray
    f_roll: np.ndarray
    f_aero: np.ndarray
    f_grade: np.ndarray
    f_tractive: np.ndarray
    p_wheel_w: np.ndarray
    wheel_torque_nm: np.ndarray
    wheel_rpm: np.ndarray
    p_traction_batt_w: np.ndarray       # + discharge / - regen (traction only)
    p_aux_w: np.ndarray
    p_batt_w: np.ndarray                # traction + auxiliaries
    p_friction_brake_w: np.ndarray      # braking power dissipated in friction brakes (>= 0)


def derive_acceleration(t: np.ndarray, v_ms: np.ndarray) -> np.ndarray:
    """Central-difference acceleration on the actual (possibly non-uniform) time base."""
    return np.gradient(v_ms, t) if len(t) > 1 else np.zeros_like(v_ms)


def road_load(t, speed_kmh, veh: VehicleParams, accel_ms2=None, grade_pct=None) -> RoadLoad:
    t = np.asarray(t, float)
    v = np.asarray(speed_kmh, float) / 3.6
    if accel_ms2 is None:
        a, a_src = derive_acceleration(t, v), "derived from speed (central difference)"
    else:
        a, a_src = np.asarray(accel_ms2, float), "from file"
    grade = np.full_like(t, veh.gradient_pct) if grade_pct is None else np.asarray(grade_pct, float)
    theta = np.arctan(grade / 100.0)
    m = veh.mass_kg
    moving = np.abs(v) > V_STOP
    f_acc = m * (1.0 + veh.rotational_inertia_factor) * a
    f_roll = np.where(moving, veh.crr * m * G * np.cos(theta), 0.0)
    f_aero = 0.5 * veh.air_density * veh.cd * veh.frontal_area_m2 * v * np.abs(v)
    f_grade = m * G * np.sin(theta)
    f_tr = f_acc + f_roll + f_aero + f_grade
    p_wheel = f_tr * v
    eta = veh.drivetrain_eff

    p_batt_trac = np.zeros_like(p_wheel)
    p_fric = np.zeros_like(p_wheel)
    pos = p_wheel >= 0
    p_batt_trac[pos] = p_wheel[pos] / eta
    neg = ~pos
    if neg.any():
        wheel_regen = np.abs(p_wheel[neg]) * veh.regen_fraction              # wheel-side power taken by the motor
        if veh.max_regen_kw is not None:
            wheel_regen = np.minimum(wheel_regen, veh.max_regen_kw * 1000.0 / eta)
        p_batt_trac[neg] = -wheel_regen * eta
        p_fric[neg] = np.abs(p_wheel[neg]) - wheel_regen
    p_aux = np.full_like(p_wheel, veh.aux_load_kw * 1000.0)
    omega = v / veh.wheel_radius_m
    return RoadLoad(t, v, a, a_src, grade, f_acc, f_roll, f_aero, f_grade, f_tr, p_wheel,
                    f_tr * veh.wheel_radius_m, omega * 60.0 / (2 * np.pi), p_batt_trac, p_aux, p_batt_trac + p_aux, p_fric)


def motor_to_battery_power(p_motor_kw, veh: VehicleParams | None, basis: str = "mechanical"):
    """Motor power column -> battery power. Returns (traction_w, aux_w, battery_w)."""
    p = np.asarray(p_motor_kw, float) * 1000.0
    aux = np.full_like(p, (veh.aux_load_kw if veh else 0.0) * 1000.0)
    if basis == "electrical":
        trac = p
    else:
        eta = veh.drivetrain_eff
        trac = np.where(p >= 0, p / eta, p * eta)
    return trac, aux, trac + aux


def register_trace(tr: TraceLog, rl: RoadLoad, veh: VehicleParams, idx: int | None = None) -> None:
    """Trace the road-load chain at the instant of peak battery power."""
    i = int(np.argmax(rl.p_batt_w)) if idx is None else idx
    tr.input("veh.mass", "Vehicle mass", veh.mass_kg, "kg")
    tr.input("veh.crr", "Rolling-resistance coefficient", veh.crr, "-")
    tr.input("veh.cd", "Drag coefficient", veh.cd, "-")
    tr.input("veh.area", "Frontal area", veh.frontal_area_m2, "m²")
    tr.input("veh.eta", "Drivetrain efficiency", veh.drivetrain_eff, "-")
    tr.input("veh.aux", "Auxiliary load", veh.aux_load_kw, "kW")
    tr.input("veh.v_peak", f"Speed at t = {rl.t[i]:.0f} s (peak battery power)", rl.v_ms[i], "m/s")
    tr.input("veh.a_peak", "Acceleration at that instant", rl.a_ms2[i], "m/s²", note=rl.accel_source)
    tr.calc("veh.f_acc", "Acceleration force", rl.f_accel[i], "N", "F_acc = m(1+ε)·a", f"{fmt(veh.mass_kg)} × (1+{fmt(veh.rotational_inertia_factor)}) × {fmt(rl.a_ms2[i])}", ["veh.mass", "veh.a_peak"])
    tr.calc("veh.f_roll", "Rolling force", rl.f_roll[i], "N", "F_roll = Crr·m·g·cosθ", f"{fmt(veh.crr)} × {fmt(veh.mass_kg)} × {G:.5f} × cosθ", ["veh.crr", "veh.mass"])
    tr.calc("veh.f_aero", "Aerodynamic force", rl.f_aero[i], "N", "F_aero = ½·ρ·Cd·A·v²", f"0.5 × {fmt(veh.air_density)} × {fmt(veh.cd)} × {fmt(veh.frontal_area_m2)} × {fmt(rl.v_ms[i])}²", ["veh.cd", "veh.area", "veh.v_peak"])
    tr.calc("veh.f_grade", "Grade force", rl.f_grade[i], "N", "F_grade = m·g·sinθ", f"{fmt(veh.mass_kg)} × {G:.5f} × sin(atan({fmt(rl.grade_pct[i])}%))", ["veh.mass"])
    tr.calc("veh.f_trac", "Tractive force", rl.f_tractive[i], "N", "F = F_acc + F_roll + F_aero + F_grade",
            f"{fmt(rl.f_accel[i])} + {fmt(rl.f_roll[i])} + {fmt(rl.f_aero[i])} + {fmt(rl.f_grade[i])}", ["veh.f_acc", "veh.f_roll", "veh.f_aero", "veh.f_grade"])
    tr.calc("veh.p_wheel", "Wheel power", rl.p_wheel_w[i] / 1000, "kW", "P_wheel = F·v", f"{fmt(rl.f_tractive[i])} × {fmt(rl.v_ms[i])} / 1000", ["veh.f_trac", "veh.v_peak"])
    tr.calc("veh.p_trac_batt", "Traction battery power", rl.p_traction_batt_w[i] / 1000, "kW", "P = P_wheel / η (discharge)",
            f"{fmt(rl.p_wheel_w[i] / 1000)} / {fmt(veh.drivetrain_eff)}", ["veh.p_wheel", "veh.eta"])
    tr.result("veh.p_batt_peak", "Peak battery power (traction + auxiliaries)", rl.p_batt_w[i] / 1000, "kW", "P_batt = P_traction,batt + P_aux",
              f"{fmt(rl.p_traction_batt_w[i] / 1000)} + {fmt(veh.aux_load_kw)}", ["veh.p_trac_batt", "veh.aux"])
