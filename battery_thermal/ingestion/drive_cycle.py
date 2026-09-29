"""Driving-cycle parser (CSV / Excel).

Identifies which parameters are present (time, speed, acceleration, motor power, battery power, battery
current, SOC, grade), converts units to the engine's canonical set, and reports data-quality problems:
gaps, duplicate timestamps, non-uniform time step, missing values, out-of-range SOC, missing load data.

Nothing is repaired silently: by default problems are reported as errors. With ``repair=True`` the parser
applies documented repairs (sort, drop duplicates, fill gaps by linear interpolation, interpolate short runs of
missing values) and lists every change.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ..engine.validation import ERROR, INFO, WARNING, Issue
from .common import IngestionError, SheetRows, read_sheets, to_float

CANON = ["time_s", "speed_kmh", "accel_ms2", "motor_power_kw", "battery_power_kw", "battery_current_a", "soc_pct", "grade_pct"]
LABEL = {"time_s": "Time", "speed_kmh": "Vehicle speed", "accel_ms2": "Acceleration", "motor_power_kw": "Motor power",
         "battery_power_kw": "Battery power", "battery_current_a": "Battery current", "soc_pct": "SOC", "grade_pct": "Road grade"}

_PATTERNS: list[tuple[str, str]] = [
    ("motor_power_kw", r"(motor|traction|mech(anical)?|shaft|machine|drive ?unit|axle|wheel)\s*(power|pwr)|^p ?(mot|mech|trac)"),
    ("battery_power_kw", r"(batt(ery)?|pack|hv|dc|bat)\s*(power|pwr)|^p ?(batt|bat|pack|hv)"),
    ("battery_current_a", r"(batt(ery)?|pack|hv|dc|bat)\s*(current|curr|amps?)|^i ?(batt|bat|pack|hv)|^current$|^i$"),
    ("soc_pct", r"\bsoc\b|state of charge"),
    ("accel_ms2", r"accel|^acc$|^a$"),
    ("grade_pct", r"grade|slope|gradient|incline"),
    ("speed_kmh", r"speed|velocity|^v$|^vel\b|^veh"),
    ("time_s", r"^(time|timestamp|elapsed( time)?|t|sec(onds?)?|date ?time)\b|^time"),
    ("battery_power_kw", r"^power$|^pwr$"),                                   # ambiguous lone 'power'
]
_COMP = [(k, re.compile(p, re.I)) for k, p in _PATTERNS]

# unit tables: canonical unit token -> factor to the canonical unit
_UNITS = {
    "time_s": {"s": 1.0, "sec": 1.0, "secs": 1.0, "seconds": 1.0, "ms": 1e-3, "min": 60.0, "mins": 60.0, "h": 3600.0, "hr": 3600.0},
    "speed_kmh": {"km/h": 1.0, "kmh": 1.0, "kph": 1.0, "kmph": 1.0, "m/s": 3.6, "ms": 3.6, "ms-1": 3.6, "mph": 1.609344, "mi/h": 1.609344},
    "accel_ms2": {"m/s2": 1.0, "m/s^2": 1.0, "m/s²": 1.0, "ms2": 1.0, "ms-2": 1.0, "g": 9.80665},
    "motor_power_kw": {"kw": 1.0, "w": 1e-3, "mw": 1e3, "hp": 0.7457, "ps": 0.7355},
    "battery_power_kw": {"kw": 1.0, "w": 1e-3, "mw": 1e3},
    "battery_current_a": {"a": 1.0, "amp": 1.0, "amps": 1.0, "ma": 1e-3, "ka": 1e3},
    "soc_pct": {"%": 1.0, "pct": 1.0, "percent": 1.0, "fraction": 100.0, "-": 100.0, "frac": 100.0, "0-1": 100.0, "p.u.": 100.0, "pu": 100.0},
    "grade_pct": {"%": 1.0, "pct": 1.0, "deg": "deg", "°": "deg", "degree": "deg", "degrees": "deg", "rad": "rad"},
}
_DEFAULT_UNIT = {"time_s": "s", "speed_kmh": "km/h", "accel_ms2": "m/s²", "motor_power_kw": "kW", "battery_power_kw": "kW",
                 "battery_current_a": "A", "soc_pct": "%", "grade_pct": "%"}


@dataclass
class ParsedCycle:
    name: str
    sheet: str
    sheets: list[str]
    mapping: dict[str, str]                     # canonical -> original header
    units: dict[str, str]                       # canonical -> unit interpreted
    cycle: dict[str, Any]                       # DriveCycle-compatible arrays
    available: dict[str, bool]
    sources: list[str]                          # usable load sources, in priority order
    recommended_source: str | None
    stats: dict[str, Any]
    issues: list[Issue] = field(default_factory=list)
    repairs: list[str] = field(default_factory=list)
    headers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name, "sheet": self.sheet, "sheets": self.sheets, "mapping": self.mapping, "units": self.units,
            "cycle": self.cycle, "available": self.available, "sources": self.sources,
            "recommended_source": self.recommended_source, "stats": self.stats, "repairs": self.repairs,
            "headers": self.headers, "issues": [i.to_dict() for i in self.issues],
            "has_errors": any(i.severity == ERROR for i in self.issues),
        }


def _norm_header(h: str) -> str:
    s = re.sub(r"[\[(][^\])]*[\])]", " ", h.lower())
    s = re.sub(r"[_\-]+", " ", s).replace(".", " ")
    return re.sub(r"\s+", " ", s).strip()


def _unit_from_header(h: str) -> str | None:
    m = re.search(r"[\[(]\s*([^\])]+?)\s*[\])]", h)
    if m:
        return m.group(1).strip()
    m = re.search(r"\b(km/h|kmh|kph|mph|m/s2|m/s\^2|m/s²|m/s|kw|mw|ma|a|w|%|deg|fraction|pct)\b\s*$", h.lower())
    return m.group(1) if m else None


def classify_header(h: str) -> str | None:
    n = _norm_header(h)
    if not n:
        return None
    for key, rx in _COMP:
        if rx.search(n):
            return key
    return None


def _canon_unit(u: str) -> str:
    return (u or "").strip().lower().replace(" ", "").replace("²", "2").replace("·", "")


def _detect_header(rows: list[list[str]]) -> tuple[int, dict[str, int]]:
    best = (-1, {})
    for i, r in enumerate(rows[:40]):
        if not any(r) or (r and r[0].startswith("#")):
            continue
        cm: dict[str, int] = {}
        for j, c in enumerate(r):
            k = classify_header(c) if c and to_float(c) is None else None
            if k and k not in cm:
                cm[k] = j
        if len(cm) > len(best[1]):
            best = (i, cm)
    return best


def _count_numeric_rows(rows: list[list[str]], start: int) -> int:
    return sum(1 for r in rows[start:] if r and sum(to_float(c) is not None for c in r) >= 1)


def parse_drive_cycle(data: bytes, filename: str, *, sheet: str | None = None, mapping: dict[str, str] | None = None,
                      repair: bool = False, resample_dt_s: float | None = None, assume_dt_s: float | None = None,
                      gap_factor: float = 5.0) -> ParsedCycle:
    sheets = read_sheets(data, filename)
    names = [s.name for s in sheets]
    chosen: SheetRows | None = None
    if sheet:
        chosen = next((s for s in sheets if s.name == sheet), None)
        if chosen is None:
            raise IngestionError(f"Sheet '{sheet}' not found (available: {', '.join(names)})")
    else:
        scored = []
        for s in sheets:
            hi, cm = _detect_header(s.rows)
            scored.append((len(cm) * 100000 + _count_numeric_rows(s.rows, hi + 1 if hi >= 0 else 0), s))
        chosen = max(scored, key=lambda x: x[0])[1]
    rows = [r for r in chosen.rows if not (r and r[0].startswith("#"))]

    issues: list[Issue] = []
    repairs: list[str] = []
    add = lambda *a, **k: issues.append(Issue(*a, **k))  # noqa: E731

    hi, cm = _detect_header(rows)
    if hi < 0 or not cm:
        raise IngestionError("No recognisable column headers found (expected e.g. 'Time [s]', 'Speed [km/h]', "
                             "'Battery power [kW]', 'Battery current [A]', 'SOC [%]').")
    headers = rows[hi]
    if mapping:                                                     # user override: canonical -> header text
        for canon, hname in mapping.items():
            if canon not in CANON:
                continue
            if not hname:
                cm.pop(canon, None)
            elif hname in headers:
                cm[canon] = headers.index(hname)
    # optional unit row directly below the header ("s", "km/h", ...)
    data_start = hi + 1
    unit_row: list[str] | None = None
    if data_start < len(rows):
        nxt = rows[data_start]
        cells = [nxt[j] for j in cm.values() if j < len(nxt)]
        if cells and all(to_float(c) is None for c in cells if c) and any(cells):
            unit_row = nxt
            data_start += 1

    n_cols = {k: [] for k in cm}
    skipped = 0
    for r in rows[data_start:]:
        if not any(r):
            continue
        vals = {k: (r[j] if j < len(r) else "") for k, j in cm.items()}
        if "time_s" in cm and to_float(vals["time_s"]) is None and not _looks_like_datetime(vals["time_s"]):
            skipped += 1
            continue
        for k, v in vals.items():
            n_cols[k].append(v)
    if skipped:
        add("CYCLE_ROWS_SKIPPED", INFO, "", f"{skipped} non-numeric row(s) were skipped (text/footer lines).")

    def unit_of(k: str) -> str | None:
        u = _unit_from_header(headers[cm[k]])
        if not u and unit_row is not None and cm[k] < len(unit_row):
            u = unit_row[cm[k]] or None
        return u

    # ---- time axis ------------------------------------------------------------------------------
    if "time_s" not in cm:
        if assume_dt_s and assume_dt_s > 0:
            n = len(next(iter(n_cols.values())))
            t = np.arange(n) * float(assume_dt_s)
            add("CYCLE_TIME_ASSUMED", WARNING, "time_s", f"No time column: uniform sampling of {assume_dt_s:g} s was assumed as instructed.")
            cm_time_units = "s"
        else:
            add("CYCLE_TIME_MISSING", ERROR, "time_s", "The file has no time column, so the time step cannot be determined.",
                "Add a 'Time [s]' column, or specify the sampling interval to assume.")
            t = np.arange(len(next(iter(n_cols.values())))) * 1.0
            cm_time_units = "s"
    else:
        raw = n_cols["time_s"]
        tf = np.array([to_float(x) if to_float(x) is not None else np.nan for x in raw], float)
        if np.isnan(tf).mean() > 0.5:                                     # date-time strings
            ts = pd.to_datetime(pd.Series(raw), errors="coerce")
            tf = (ts - ts.iloc[0]).dt.total_seconds().to_numpy(float)
            cm_time_units = "s"
            add("CYCLE_TIME_DATETIME", INFO, "time_s", "Timestamps were parsed as date-times and converted to elapsed seconds.")
        else:
            u = unit_of("time_s")
            fac = 1.0
            cm_time_units = _DEFAULT_UNIT["time_s"]
            if u:
                f = _UNITS["time_s"].get(_canon_unit(u))
                if f is None:
                    add("CYCLE_UNIT_UNKNOWN", WARNING, "time_s", f"Unknown time unit '{u}': assumed seconds.")
                else:
                    fac, cm_time_units = f, u
            tf = tf * fac
        t = tf

    units: dict[str, str] = {"time_s": cm_time_units}
    sig: dict[str, np.ndarray] = {}
    for k in cm:
        if k == "time_s":
            continue
        arr = np.array([to_float(x) if to_float(x) is not None else np.nan for x in n_cols[k]], float)
        sig[k], units[k] = _convert_units(k, arr, unit_of(k), issues)

    if len(t) < 10:
        add("CYCLE_TOO_SHORT", ERROR, "", f"Only {len(t)} samples found - a driving cycle needs at least 10 rows.")

    # ---- time checks / repairs ------------------------------------------------------------------
    finite_t = ~np.isnan(t)
    if (~finite_t).any():
        add("CYCLE_TIME_INVALID", ERROR if not repair else WARNING, "time_s", f"{int((~finite_t).sum())} row(s) have an unreadable timestamp.")
        if repair:
            repairs.append(f"dropped {int((~finite_t).sum())} row(s) with unreadable timestamps")
        t, sig = t[finite_t], {k: v[finite_t] for k, v in sig.items()}

    if len(t) > 1 and np.any(np.diff(t) < 0):
        n_back = int((np.diff(t) < 0).sum())
        if repair:
            o = np.argsort(t, kind="stable")
            t, sig = t[o], {k: v[o] for k, v in sig.items()}
            repairs.append(f"sorted rows by time ({n_back} out-of-order step(s))")
            add("CYCLE_TIME_NOT_MONOTONIC", WARNING, "time_s", f"Time went backwards {n_back} time(s) - rows were sorted (repair).")
        else:
            add("CYCLE_TIME_NOT_MONOTONIC", ERROR, "time_s", f"Time is not monotonic ({n_back} backward step(s)).",
                "Sort the file by time or enable auto-repair.")

    if len(t) > 1:
        dup = np.concatenate([[False], np.diff(t) == 0])
        if dup.any():
            first_dups = t[dup][:3]
            if repair:
                t, sig = t[~dup], {k: v[~dup] for k, v in sig.items()}
                repairs.append(f"dropped {int(dup.sum())} duplicate timestamp row(s) (kept first occurrence)")
                add("CYCLE_DUPLICATE_TIME", WARNING, "time_s", f"{int(dup.sum())} duplicate timestamp(s) removed (repair), e.g. t = {first_dups.tolist()}.")
            else:
                add("CYCLE_DUPLICATE_TIME", ERROR, "time_s", f"{int(dup.sum())} duplicate timestamp(s), e.g. t = {first_dups.tolist()}.",
                    "Duplicate times make integration ambiguous. Remove them or enable auto-repair.")

    shifted = False
    if len(t) and t[0] != 0:
        add("CYCLE_TIME_OFFSET", INFO, "time_s", f"Time starts at {t[0]:g} s; shifted so the cycle starts at 0 s.")
        t = t - t[0]
        shifted = True

    # gaps & step uniformity
    stats: dict[str, Any] = {}
    if len(t) > 2 and not np.any(np.diff(t) <= 0):
        dt = np.diff(t)
        med = float(np.median(dt))
        gaps = np.where(dt > gap_factor * med)[0]
        if len(gaps):
            gsz = dt[gaps]
            desc = ", ".join(f"{t[i]:g}→{t[i + 1]:g} s" for i in gaps[:3])
            if repair:
                new_t = [t[0]]
                for i in range(len(dt)):
                    if dt[i] > gap_factor * med:
                        n_ins = int(round(dt[i] / med)) - 1
                        new_t.extend(t[i] + med * (j + 1) for j in range(n_ins))
                    new_t.append(t[i + 1])
                new_t = np.array(new_t)
                sig = {k: np.interp(new_t, t, v) for k, v in sig.items()}      # NaNs stay NaN and are flagged below
                t = new_t
                repairs.append(f"filled {len(gaps)} gap(s) by linear interpolation at Δt = {med:g} s")
                add("CYCLE_GAP", WARNING, "time_s", f"{len(gaps)} gap(s) filled by linear interpolation (repair): {desc}.")
            else:
                add("CYCLE_GAP", ERROR, "time_s", f"{len(gaps)} gap(s) in the time base (largest {gsz.max():g} s vs. median step {med:g} s): {desc}.",
                    "Held values across a gap would corrupt the heat integral. Fill the gap or enable auto-repair.")
        dt = np.diff(t)
        med = float(np.median(dt))
        if (dt.max() - dt.min()) / med > 0.01 and (not len(gaps) or repair):
            add("CYCLE_NONUNIFORM_DT", WARNING, "time_s",
                f"Non-uniform time step: min {dt.min():g} s, median {med:g} s, max {dt.max():g} s. "
                f"The engine integrates with the actual step, but sub-sampling may hide peaks.",
                "Optionally resample to a uniform step.")
        if resample_dt_s and resample_dt_s > 0:
            new_t = np.arange(0.0, t[-1] + 1e-9, float(resample_dt_s))
            sig = {k: np.interp(new_t, t, v) for k, v in sig.items()}
            t = new_t
            repairs.append(f"resampled to a uniform {resample_dt_s:g} s step by linear interpolation")
            add("CYCLE_RESAMPLED", INFO, "time_s", f"Resampled to Δt = {resample_dt_s:g} s.")

    # ---- missing values ------------------------------------------------------------------------------
    for k, v in list(sig.items()):
        nan = np.isnan(v)
        if not nan.any():
            continue
        runs = _nan_runs(nan)
        longest = max(b - a for a, b in runs)
        edge = nan[0] or nan[-1]
        if repair and longest <= 5 and not edge:
            idx = np.arange(len(v))
            sig[k] = np.interp(idx, idx[~nan], v[~nan])
            repairs.append(f"interpolated {int(nan.sum())} missing value(s) in '{LABEL[k]}'")
            add("CYCLE_MISSING_VALUES", WARNING, k, f"{int(nan.sum())} missing value(s) in {LABEL[k]} interpolated (repair).")
        else:
            add("CYCLE_MISSING_VALUES", ERROR, k, f"{int(nan.sum())} missing/non-numeric value(s) in {LABEL[k]} (longest run {longest} rows"
                f"{', including the first/last row' if edge else ''}).",
                "Fill the gaps in the file" + (" - runs longer than 5 rows cannot be auto-repaired." if repair else " or enable auto-repair for short runs."))

    # ---- physical range checks ---------------------------------------------------------------------------
    if "soc_pct" in sig and not np.isnan(sig["soc_pct"]).all():
        s = sig["soc_pct"]
        lo, hi_ = np.nanmin(s), np.nanmax(s)
        if lo < 0 or hi_ > 100:
            add("CYCLE_SOC_OUT_OF_RANGE", ERROR, "soc_pct", f"SOC values leave the 0-100 % range (min {lo:.3g}, max {hi_:.3g}).",
                "Check the unit (fraction vs %) and sensor offsets.")
    if "speed_kmh" in sig:
        s = sig["speed_kmh"]
        if np.nanmin(s) < -0.5:
            add("CYCLE_NEGATIVE_SPEED", WARNING, "speed_kmh", f"Negative speed found (min {np.nanmin(s):.3g} km/h) - reversing or a sign problem.")
        if np.nanmax(s) > 260:
            add("CYCLE_SPEED_UNREALISTIC", WARNING, "speed_kmh", f"Maximum speed {np.nanmax(s):.0f} km/h looks unrealistic - check the unit (m/s or mph?).")
    if "accel_ms2" in sig and np.nanmax(np.abs(sig["accel_ms2"])) > 12:
        add("CYCLE_ACCEL_UNREALISTIC", WARNING, "accel_ms2", f"|acceleration| up to {np.nanmax(np.abs(sig['accel_ms2'])):.1f} m/s² is unrealistic for a road vehicle - check the unit (g?).")
    for k in ("battery_power_kw", "motor_power_kw"):
        if k in sig and np.nanmax(np.abs(sig[k])) > 2000:
            add("CYCLE_POWER_UNREALISTIC", WARNING, k, f"|{LABEL[k]}| up to {np.nanmax(np.abs(sig[k])):.0f} kW is unrealistic - check the unit (W vs kW).")

    # ---- what is available? ---------------------------------------------------------------------------------
    available = {k: (k in sig and not np.isnan(sig[k]).all()) for k in CANON if k != "time_s"}
    available["time_s"] = "time_s" in cm or bool(assume_dt_s)
    sources = [s for s, k in (("battery_current", "battery_current_a"), ("battery_power", "battery_power_kw"),
                              ("motor_power", "motor_power_kw"), ("vehicle_speed", "speed_kmh")) if available.get(k)]
    if not sources:
        add("CYCLE_NO_LOAD_DATA", ERROR, "", "The file contains no battery current, battery power, motor power or vehicle speed - "
            "there is nothing to derive a heat load from.", "Add at least one of these columns.")
    elif sources == ["vehicle_speed"]:
        add("CYCLE_SPEED_ONLY", INFO, "speed_kmh", "Only vehicle speed is available: battery power will be calculated from the road-load "
            "model, which needs the vehicle parameters (mass, Crr, Cd, frontal area, wheel radius, drivetrain efficiency, auxiliaries, grade).")
    if "battery_current_a" in sig and "battery_power_kw" in sig:
        add("CYCLE_BOTH_CURRENT_POWER", INFO, "", "Both battery current and battery power are present; current is used by default (power is used only if you select it).")

    # sign convention heuristic
    for k in ("battery_current_a", "battery_power_kw"):
        if k in sig and not np.isnan(sig[k]).all() and len(t) > 2 and np.nanmean(sig[k]) < 0 and available.get("speed_kmh"):
            add("CYCLE_SIGN_CHECK", WARNING, k, f"Mean {LABEL[k]} is negative for a driving cycle - the sign convention may be reversed "
                f"(the tool expects positive = discharge). Use the sign option if needed.")

    stats.update(_stats(t, sig, shifted))
    cycle = {"name": filename, "time_s": t.tolist()}
    for k, v in sig.items():
        if available.get(k):
            cycle[k] = np.where(np.isnan(v), 0.0, v).tolist() if np.isnan(v).any() else v.tolist()
    return ParsedCycle(filename, chosen.name, names, {k: headers[j] for k, j in cm.items()}, units, cycle, available, sources,
                       sources[0] if sources else None, stats, issues, repairs, headers)


# ---------------------------------------------------------------------------------------------
def _looks_like_datetime(s: str) -> bool:
    return bool(re.search(r"\d{1,4}[-/:]\d{1,2}[-/:]\d{1,4}", s or ""))


def _nan_runs(nan: np.ndarray) -> list[tuple[int, int]]:
    runs, start = [], None
    for i, x in enumerate(nan):
        if x and start is None:
            start = i
        if not x and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(nan)))
    return runs


def _convert_units(k: str, arr: np.ndarray, unit: str | None, issues: list[Issue]) -> tuple[np.ndarray, str]:
    table = _UNITS[k]
    default = _DEFAULT_UNIT[k]
    finite = arr[~np.isnan(arr)]
    if unit:
        f = table.get(_canon_unit(unit))
        if f is None:
            issues.append(Issue("CYCLE_UNIT_UNKNOWN", WARNING, k, f"{LABEL[k]}: unknown unit '{unit}', assumed {default}."))
        elif f == "deg":
            return np.tan(np.radians(arr)) * 100.0, "% (from deg)"
        elif f == "rad":
            return np.tan(arr) * 100.0, "% (from rad)"
        else:
            if k == "soc_pct" and _canon_unit(unit) in ("-", "0-1", "fraction", "frac", "p.u.", "pu"):
                issues.append(Issue("CYCLE_SOC_FRACTION", INFO, k, "SOC given as a fraction (0-1): converted to %."))
            return arr * f, unit
    # no unit: heuristics, always reported
    if k == "soc_pct" and finite.size and finite.min() >= 0 and finite.max() <= 1.0:
        issues.append(Issue("CYCLE_SOC_FRACTION", WARNING, k, "SOC has no unit and lies within 0-1: interpreted as a fraction and converted to %.",
                            "Confirm - if it is actually % the cycle covers <1 % SOC."))
        return arr * 100.0, "fraction→%"
    if k in ("motor_power_kw", "battery_power_kw") and finite.size and np.abs(finite).max() > 1500:
        issues.append(Issue("CYCLE_UNIT_ASSUMED", WARNING, k, f"{LABEL[k]} has no unit and values up to {np.abs(finite).max():.0f}: interpreted as W and converted to kW.",
                            "Confirm the unit."))
        return arr / 1000.0, "W (assumed)"
    if k not in ("time_s",) and finite.size:
        issues.append(Issue("CYCLE_UNIT_ASSUMED", INFO, k, f"{LABEL[k]}: no unit in the header - assumed {default}."))
    return arr, f"{default} (assumed)"


def _stats(t: np.ndarray, sig: dict[str, np.ndarray], shifted: bool) -> dict[str, Any]:
    st: dict[str, Any] = {"n_samples": int(len(t))}
    if len(t) > 1 and np.all(np.diff(t) > 0):
        dt = np.diff(t)
        st.update(duration_s=float(t[-1] - t[0]), dt_median_s=float(np.median(dt)), dt_min_s=float(dt.min()), dt_max_s=float(dt.max()))
    for k, v in sig.items():
        if not np.isnan(v).all():
            st[k] = {"min": float(np.nanmin(v)), "max": float(np.nanmax(v)), "mean": float(np.nanmean(v))}
    if "battery_power_kw" in sig and len(t) > 1 and np.all(np.diff(t) > 0) and not np.isnan(sig["battery_power_kw"]).any():
        p = sig["battery_power_kw"][:-1]
        dt = np.diff(t)
        st["energy_discharge_kwh"] = float(np.sum(np.clip(p, 0, None) * dt) / 3600.0)
        st["energy_regen_kwh"] = float(-np.sum(np.clip(p, None, 0) * dt) / 3600.0)
    return st
