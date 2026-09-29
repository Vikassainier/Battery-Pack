"""Electrical load construction: turns a driving cycle (or a C-rate duty profile) into the pack-level
power / current series that drives the electrical and heat model.

Priority when ``source == 'auto'``:
    battery current  >  battery power  >  motor power  >  vehicle speed (road-load model)
A driving cycle always takes precedence over a constant C-rate profile.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, replace

import numpy as np

from .pack import PackDerived
from .schemas import AnalysisRequest, CRateProfile, VehicleParams
from .vehicle import RoadLoad, motor_to_battery_power, road_load


class LoadError(ValueError):
    pass


@dataclass
class LoadProfile:
    t: np.ndarray
    kind: str                       # 'power' [W, + discharge] | 'current' [A pack, + discharge]
    values: np.ndarray
    source: str                     # battery_current | battery_power | motor_power | vehicle_speed | c_rate_profile
    charge_label: str = "regen"     # label used for negative samples
    state_override: list[str] | None = None
    soc_file_pct: np.ndarray | None = None
    speed_kmh: np.ndarray | None = None
    p_traction_w: np.ndarray | None = None
    p_aux_w: np.ndarray | None = None
    road: RoadLoad | None = None
    period_s: float = 0.0           # duration of one pass of the cycle (incl. last-sample duration)
    repeats: int = 1
    notes: list[str] = field(default_factory=list)


def _tile(t: np.ndarray, arrays: dict[str, np.ndarray | None], repeats: int):
    if repeats <= 1:
        return t, arrays, float(t[-1] - t[0])
    dt_last = float(np.median(np.diff(t))) if len(t) > 1 else 1.0
    period = float(t[-1] - t[0]) + dt_last
    tt = np.concatenate([t + k * period for k in range(repeats)])
    out = {k: (None if v is None else np.tile(v, repeats)) for k, v in arrays.items()}
    return tt, out, period


def _choose_source(req: AnalysisRequest, avail: dict[str, bool]) -> str:
    want = req.cycle_options.source
    order = ["battery_current", "battery_power", "motor_power", "vehicle_speed"]
    key = {"battery_current": "battery_current_a", "battery_power": "battery_power_kw", "motor_power": "motor_power_kw",
           "vehicle_speed": "speed_kmh"}
    if want != "auto":
        if not avail.get(key[want]):
            raise LoadError(f"The selected load source '{want}' is not present in the driving cycle.")
        return want
    for s in order:
        if avail.get(key[s]):
            return s
    raise LoadError("The driving cycle has no battery current, battery power, motor power or speed data.")


def build_load(req: AnalysisRequest, pack: PackDerived) -> LoadProfile:
    if req.cycle is not None:
        lp = _from_cycle(req, pack)
    elif req.crate_profile is not None:
        lp = from_crate_profile(req.crate_profile, pack)
    else:
        raise LoadError("No load defined: provide a driving cycle or a charge/discharge C-rate duty profile.")
    k = req.cycle_options.load_scale
    if k != 1.0:
        if k <= 0:
            raise LoadError("Load scale must be > 0.")
        lp.values = lp.values * k
        lp.notes.append(f"Whole load scaled by ×{k:g} (what-if / sensitivity option).")
    return lp


def _from_cycle(req: AnalysisRequest, pack: PackDerived) -> LoadProfile:
    c, opt = req.cycle, req.cycle_options
    t = np.asarray(c.time_s, float)
    avail = {k: getattr(c, k) is not None for k in ("battery_current_a", "battery_power_kw", "motor_power_kw", "speed_kmh")}
    src = _choose_source(req, avail)
    veh: VehicleParams | None = req.vehicle
    notes: list[str] = []
    speed = np.asarray(c.speed_kmh, float) if c.speed_kmh is not None else None
    soc = np.asarray(c.soc_pct, float) if c.soc_pct is not None else None
    road = None
    p_trac = p_aux = None

    if src == "battery_current":
        kind, vals = "current", np.asarray(c.battery_current_a, float) * opt.current_sign
        notes.append(f"Pack current taken from the file (sign factor {opt.current_sign:+d}; positive = discharge).")
    elif src == "battery_power":
        vals = np.asarray(c.battery_power_kw, float) * 1000.0 * opt.power_sign
        kind = "power"
        if not opt.battery_power_includes_aux:
            if veh is None:
                raise LoadError("Battery power excludes auxiliaries but no vehicle/auxiliary load was provided.")
            vals = vals + veh.aux_load_kw * 1000.0
            p_aux = np.full_like(vals, veh.aux_load_kw * 1000.0)
            notes.append(f"Auxiliary load {veh.aux_load_kw:g} kW added to the battery power from the file.")
        else:
            notes.append("Battery power from the file is taken as terminal power INCLUDING auxiliaries.")
    elif src == "motor_power":
        if opt.motor_power_basis == "mechanical" and veh is None:
            raise LoadError("Motor power is mechanical: vehicle parameters (drivetrain efficiency) are required.")
        p_trac, p_aux, vals = motor_to_battery_power(c.motor_power_kw, veh, opt.motor_power_basis)
        vals = vals * opt.power_sign
        kind = "power"
        notes.append(f"Battery power calculated from {opt.motor_power_basis} motor power"
                     + (f" with drivetrain efficiency {veh.drivetrain_eff:g}" if opt.motor_power_basis == "mechanical" else "")
                     + (f" plus {veh.aux_load_kw:g} kW auxiliaries." if veh else "."))
    else:                                                                    # vehicle_speed
        if veh is None:
            raise LoadError("Only vehicle speed is available: enter the vehicle parameters to calculate battery power.")
        road = road_load(t, c.speed_kmh, veh, c.accel_ms2, c.grade_pct)
        vals, kind = road.p_batt_w * opt.power_sign, "power"
        p_trac, p_aux = road.p_traction_batt_w, road.p_aux_w
        notes.append("Battery power calculated from the road-load model (F = F_acc + F_roll + F_aero + F_grade; P = F·v; "
                     f"acceleration {road.accel_source}).")

    if speed is not None:
        charge_label = "regen"
    else:
        charge_label = "charge" if np.mean(vals) < 0 else "regen"

    soc_arr = soc
    period = float(t[-1] - t[0]) + (float(np.median(np.diff(t))) if len(t) > 1 else 0.0)
    reps = max(1, int(opt.repeats))
    arrays = {"vals": vals, "soc": soc_arr, "speed": speed, "p_trac": p_trac, "p_aux": p_aux}
    if reps > 1:
        t2, arr, period = _tile(t, arrays, reps)
        if road is not None:                                       # keep the road-load traces aligned with the tiled time base
            road = replace(road, t=t2, **{f.name: np.tile(getattr(road, f.name), reps) for f in fields(road)
                                          if f.name not in ("t", "accel_source")})
        t, vals, soc_arr, speed, p_trac, p_aux = t2, arr["vals"], None, arr["speed"], arr["p_trac"], arr["p_aux"]
        notes.append(f"Cycle repeated {reps}× (period {period:.0f} s) to represent consecutive driving; SOC is integrated, not read from file.")
    return LoadProfile(t=t - t[0], kind=kind, values=vals, source=src, charge_label=charge_label, soc_file_pct=soc_arr,
                       speed_kmh=speed, p_traction_w=p_trac, p_aux_w=p_aux, road=road, period_s=period, repeats=reps, notes=notes)


def from_crate_profile(profile: CRateProfile, pack: PackDerived) -> LoadProfile:
    """Constant-C-rate duty profile: each segment holds a signed cell C-rate for its duration."""
    dt = float(profile.dt_s)
    if dt <= 0:
        raise LoadError("C-rate profile time step must be > 0 s")
    cap = pack.cell_capacity_ah
    seg_end = np.cumsum([s.duration_s for s in profile.segments])
    total = float(seg_end[-1])
    t = np.arange(0.0, total + 1e-9, dt)
    cur = np.zeros_like(t)
    labels: list[str] = []
    j = 0
    for k, tk in enumerate(t):
        while j < len(profile.segments) - 1 and tk >= seg_end[j] - 1e-9:
            j += 1
        seg = profile.segments[j]
        sign = {"discharge": 1.0, "charge": -1.0, "regen": -1.0, "rest": 0.0}[seg.kind]
        cur[k] = sign * seg.c_rate * cap * pack.np          # pack current = cell current × Np
        labels.append(seg.kind)
    return LoadProfile(t=t, kind="current", values=cur, source="c_rate_profile", state_override=labels, period_s=total,
                       notes=[f"Constant-C-rate duty profile with {len(profile.segments)} segment(s), {total:.0f} s total (no driving cycle supplied)."])


def load_summary(lp: LoadProfile, decimate_to: int = 4000) -> dict:
    """JSON-ready description of the constructed load (used by the UI preview and the report)."""
    t = lp.t
    dt = np.diff(t, append=t[-1])                       # sample-and-hold: last sample has zero duration
    out: dict = {"source": lp.source, "kind": lp.kind, "notes": lp.notes, "n": int(len(t)), "duration_s": float(t[-1] - t[0]),
                 "repeats": lp.repeats}
    step = max(1, len(t) // decimate_to)
    sl = slice(None, None, step)
    out["t"] = t[sl].tolist()
    if lp.kind == "power":
        p = lp.values / 1000.0
        out["p_batt_kw"] = p[sl].tolist()
        out["p_discharge_kw"] = np.clip(p, 0, None)[sl].tolist()
        out["p_regen_kw"] = np.clip(p, None, 0)[sl].tolist()
        e = {"discharge_kwh": float(np.sum(np.clip(p, 0, None) * dt) / 3600), "regen_kwh": float(-np.sum(np.clip(p, None, 0) * dt) / 3600)}
        e["net_kwh"] = e["discharge_kwh"] - e["regen_kwh"]
        out["peak_discharge_kw"], out["peak_regen_kw"] = float(p.max()), float(-min(p.min(), 0.0))
        if lp.p_aux_w is not None:
            out["p_aux_kw"] = (lp.p_aux_w / 1000.0)[sl].tolist()
            e["aux_kwh"] = float(np.sum(lp.p_aux_w / 1000.0 * dt) / 3600)
        if lp.p_traction_w is not None:
            out["p_traction_kw"] = (lp.p_traction_w / 1000.0)[sl].tolist()
            e["traction_net_kwh"] = float(np.sum(lp.p_traction_w / 1000.0 * dt) / 3600)
        out["energy"] = e
    else:
        i = lp.values
        out["i_pack_a"] = i[sl].tolist()
        out["peak_discharge_a"], out["peak_charge_a"] = float(i.max()), float(-min(i.min(), 0.0))
        out["charge_throughput_ah"] = {"discharge": float(np.sum(np.clip(i, 0, None) * dt) / 3600), "charge": float(-np.sum(np.clip(i, None, 0) * dt) / 3600)}
    if lp.speed_kmh is not None:
        out["speed_kmh"] = lp.speed_kmh[sl].tolist()
    if lp.road is not None:
        r = lp.road
        out["road"] = {"f_accel_n": r.f_accel[sl].tolist(), "f_roll_n": r.f_roll[sl].tolist(), "f_aero_n": r.f_aero[sl].tolist(),
                       "f_grade_n": r.f_grade[sl].tolist(), "f_tractive_n": r.f_tractive[sl].tolist(), "p_wheel_kw": (r.p_wheel_w / 1000)[sl].tolist(),
                       "wheel_torque_nm": r.wheel_torque_nm[sl].tolist(), "wheel_rpm": r.wheel_rpm[sl].tolist(),
                       "p_friction_brake_kw": (r.p_friction_brake_w / 1000)[sl].tolist(), "accel_source": r.accel_source,
                       "wheel_energy_kwh": float(np.sum(np.clip(r.p_wheel_w, 0, None) * dt) / 3.6e6)}
    return out
