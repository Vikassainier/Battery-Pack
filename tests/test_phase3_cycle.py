"""Phase 3 - driving-cycle ingestion, data-quality checks, road-load model, load construction.

Road-load hand calculations (m = 1800 kg, Crr = 0.009, Cd = 0.28, A = 2.2 m², ρ = 1.225, η = 0.90, aux = 0.5 kW):
  k_aero = ½ρCdA = 0.5·1.225·0.28·2.2 = 0.37730 kg/m ;  F_roll = 0.009·1800·9.80665 = 158.868 N
  (a) 100 km/h cruise, v = 27.7778 m/s:   F_aero = 0.3773·771.605 = 291.127 N ; F = 449.995 N
      P_wheel = 12.4999 kW ; P_batt = 12.4999/0.9 + 0.5 = 14.3888 kW
  (b) v = 10 m/s, a = +1 m/s²:            F = 1800 + 158.868 + 37.730 = 1996.598 N ; P_wheel = 19.966 kW
      P_batt = 19.966/0.9 + 0.5 = 22.684 kW
  (c) v = 10 m/s, a = −1 m/s²:            F = −1800 + 158.868 + 37.73 = −1603.402 N ; P_wheel = −16.034 kW
      regen (f = 1):  −16.034·0.9 + 0.5 = −13.931 kW ;  f = 0.5:  −8.017·0.9 + 0.5 = −6.715 kW
"""
import io
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from battery_thermal.api.main import app
from battery_thermal.engine.load import LoadError, build_load
from battery_thermal.engine.pack import derive_pack
from battery_thermal.engine.schemas import (
    AnalysisRequest, CRateProfile, CellSpec, CycleOptions, DriveCycle, PackConfig, Segment, VehicleParams,
)
from battery_thermal.engine.validation import validate_load, validate_time_base, validate_vehicle
from battery_thermal.engine.vehicle import motor_to_battery_power, road_load
from battery_thermal.ingestion.common import IngestionError
from battery_thermal.ingestion.drive_cycle import parse_drive_cycle

SAMPLES = Path(__file__).resolve().parents[1] / "sample_data"
client = TestClient(app)


def csv(text: str, **kw):
    return parse_drive_cycle(text.encode(), "c.csv", **kw)


def codes(p, sev=None):
    return {i.code for i in p.issues if sev is None or i.severity == sev}


def ramp_csv(n=30, extra_cols="", header="Time [s],Speed [km/h]"):
    rows = [header] + [f"{i},{i * 2}{extra_cols}" for i in range(n)]
    return "\n".join(rows) + "\n"


VEH = VehicleParams(mass_kg=1800, crr=0.009, cd=0.28, frontal_area_m2=2.2, wheel_radius_m=0.32,
                    drivetrain_eff=0.90, aux_load_kw=0.5)


# ------------------------------------------------------------------------------------ parsing
def test_sample_speed_only_file():
    p = parse_drive_cycle((SAMPLES / "drive_cycle_speed_only.csv").read_bytes(), "speed.csv")
    assert p.mapping == {"time_s": "Time [s]", "speed_kmh": "Vehicle speed [km/h]"}
    assert p.sources == ["vehicle_speed"] and p.recommended_source == "vehicle_speed"
    assert p.stats["duration_s"] == 1200 and p.stats["dt_median_s"] == 1.0
    assert "CYCLE_SPEED_ONLY" in codes(p, "info") and not codes(p, "error")


def test_sample_battery_file_detects_all_parameters():
    p = parse_drive_cycle((SAMPLES / "drive_cycle_battery.csv").read_bytes(), "b.csv")
    assert set(p.mapping) == {"time_s", "speed_kmh", "accel_ms2", "battery_power_kw", "battery_current_a", "soc_pct"}
    assert p.sources == ["battery_current", "battery_power", "vehicle_speed"]
    assert p.available["motor_power_kw"] is False and p.available["soc_pct"] is True
    assert p.stats["energy_regen_kwh"] > 0 and p.stats["energy_discharge_kwh"] > p.stats["energy_regen_kwh"]
    assert not codes(p, "error")


def test_header_styles_and_unit_conversion():
    txt = "t (s);v [m/s];P_batt [W];I_batt [mA];SOC [-]\n" + "\n".join(
        f"{i};{10 + i * 0.1};{20000 + i * 100};{50000 + i * 10};{0.9 - i * 0.001}" for i in range(20)) + "\n"
    p = csv(txt)
    assert p.mapping["speed_kmh"].startswith("v") and p.mapping["battery_power_kw"].startswith("P_batt")
    c = p.cycle
    assert c["speed_kmh"][0] == pytest.approx(36.0)                 # 10 m/s
    assert c["battery_power_kw"][0] == pytest.approx(20.0)          # 20000 W
    assert c["battery_current_a"][0] == pytest.approx(50.0)         # 50000 mA
    assert c["soc_pct"][0] == pytest.approx(90.0)                   # fraction -> %
    assert "CYCLE_SOC_FRACTION" in codes(p)


def test_mph_and_time_in_minutes():
    txt = "Time [min],Speed [mph]\n" + "\n".join(f"{i / 60:.6f},{i}" for i in range(20)) + "\n"
    p = csv(txt)
    assert p.cycle["time_s"][1] == pytest.approx(1.0, rel=1e-4) and p.cycle["speed_kmh"][10] == pytest.approx(16.09344)


def test_unit_row_and_comment_lines():
    txt = "# comment\nTime,Speed,Battery power\ns,km/h,kW\n" + "\n".join(f"{i},{i},{i * 0.5}" for i in range(15)) + "\n"
    p = csv(txt)
    assert p.units["battery_power_kw"] == "kW" and p.cycle["battery_power_kw"][2] == 1.0


def test_watts_without_unit_are_detected_and_flagged():
    txt = "Time,Battery power\n" + "\n".join(f"{i},{15000 + 100 * i}" for i in range(15)) + "\n"
    p = csv(txt)
    assert p.cycle["battery_power_kw"][0] == pytest.approx(15.0) and "CYCLE_UNIT_ASSUMED" in codes(p, "warning")


def test_excel_multi_sheet_and_sheet_selection():
    wb = Workbook()
    ws = wb.active
    ws.title = "Notes"
    ws.append(["Some notes"])
    d = wb.create_sheet("Cycle")
    d.append(["Time [s]", "Battery current [A]", "SOC [%]"])
    for i in range(20):
        d.append([i, 100 + i, 90 - i * 0.01])
    bio = io.BytesIO()
    wb.save(bio)
    p = parse_drive_cycle(bio.getvalue(), "c.xlsx")
    assert p.sheet == "Cycle" and p.sheets == ["Notes", "Cycle"] and p.sources == ["battery_current"]
    with pytest.raises(IngestionError):
        parse_drive_cycle(bio.getvalue(), "c.xlsx", sheet="Missing")


def test_datetime_timestamps():
    txt = "Timestamp,Speed [km/h]\n" + "\n".join(f"2024-01-01 12:00:{i:02d},{i}" for i in range(20)) + "\n"
    p = csv(txt)
    assert p.cycle["time_s"][:3] == [0.0, 1.0, 2.0]


# ------------------------------------------------------------------------------------ data-quality errors
def test_duplicate_timestamps_error_then_repair():
    lines = ["Time [s],Speed [km/h]"] + [f"{i},{i}" for i in range(10)] + ["9,99"] + [f"{i},{i}" for i in range(10, 20)]
    txt = "\n".join(lines) + "\n"
    assert "CYCLE_DUPLICATE_TIME" in codes(csv(txt), "error")
    r = csv(txt, repair=True)
    assert "CYCLE_DUPLICATE_TIME" in codes(r, "warning") and not codes(r, "error")
    assert len(r.cycle["time_s"]) == 20 and any("duplicate" in x for x in r.repairs)


def test_gap_error_then_repair_fills_at_median_step():
    times = list(range(0, 10)) + list(range(30, 40))                # 20 s hole between 9 and 30
    txt = "Time [s],Speed [km/h]\n" + "\n".join(f"{t},{t}" for t in times) + "\n"
    e = csv(txt)
    assert "CYCLE_GAP" in codes(e, "error")
    r = csv(txt, repair=True)
    t = np.array(r.cycle["time_s"])
    assert not codes(r, "error") and np.allclose(np.diff(t), 1.0) and len(t) == 40
    assert r.cycle["speed_kmh"][20] == pytest.approx(9 + (30 - 9) * (20 - 9) / (30 - 9))       # linear fill


def test_non_uniform_step_warning_and_resample():
    times = [0, 1, 2, 3.5, 5, 6, 7, 8, 9, 10, 11, 12]
    txt = "Time [s],Speed [km/h]\n" + "\n".join(f"{t},{2 * t}" for t in times) + "\n"
    p = csv(txt)
    assert "CYCLE_NONUNIFORM_DT" in codes(p, "warning") and not codes(p, "error")
    q = csv(txt, resample_dt_s=1.0)
    assert np.allclose(np.diff(q.cycle["time_s"]), 1.0) and q.cycle["speed_kmh"][4] == pytest.approx(8.0)


def test_unsorted_time_error_then_repair():
    times = [0, 1, 2, 4, 3, 5, 6, 7, 8, 9, 10, 11]
    txt = "Time [s],Speed [km/h]\n" + "\n".join(f"{t},{t}" for t in times) + "\n"
    assert "CYCLE_TIME_NOT_MONOTONIC" in codes(csv(txt), "error")
    assert not codes(csv(txt, repair=True), "error")


def test_missing_values_error_and_short_run_repair():
    lines = ["Time [s],Speed [km/h]"] + [f"{i},{'' if i in (5, 6) else i}" for i in range(20)]
    txt = "\n".join(lines) + "\n"
    assert "CYCLE_MISSING_VALUES" in codes(csv(txt), "error")
    r = csv(txt, repair=True)
    assert not codes(r, "error") and r.cycle["speed_kmh"][5] == pytest.approx(5.0)
    long_run = "\n".join(["Time [s],Speed [km/h]"] + [f"{i},{'' if 3 <= i <= 12 else i}" for i in range(20)]) + "\n"
    assert "CYCLE_MISSING_VALUES" in codes(csv(long_run, repair=True), "error")               # >5 rows: never auto-repaired


def test_soc_out_of_range():
    p = csv("Time [s],Battery current [A],SOC [%]\n" + "\n".join(f"{i},100,{101 + i}" for i in range(15)) + "\n")
    assert "CYCLE_SOC_OUT_OF_RANGE" in codes(p, "error")


def test_no_load_data_and_no_time_column():
    p = csv("Time [s],SOC [%]\n" + "\n".join(f"{i},{90 - i}" for i in range(15)) + "\n")
    assert "CYCLE_NO_LOAD_DATA" in codes(p, "error")
    q = csv("Speed [km/h]\n" + "\n".join(str(i) for i in range(15)) + "\n")
    assert "CYCLE_TIME_MISSING" in codes(q, "error")
    r = csv("Speed [km/h]\n" + "\n".join(str(i) for i in range(15)) + "\n", assume_dt_s=0.5)
    assert "CYCLE_TIME_MISSING" not in codes(r) and r.cycle["time_s"][2] == 1.0 and "CYCLE_TIME_ASSUMED" in codes(r, "warning")


def test_unrealistic_values_flagged():
    p = csv("Time [s],Speed [km/h],Battery power [kW]\n" + "\n".join(f"{i},{300 + i},{5000}" for i in range(15)) + "\n")
    assert {"CYCLE_SPEED_UNREALISTIC", "CYCLE_POWER_UNREALISTIC"} <= codes(p, "warning")


def test_sign_convention_warning_when_mean_negative():
    p = csv("Time [s],Speed [km/h],Battery current [A]\n" + "\n".join(f"{i},50,-100" for i in range(15)) + "\n")
    assert "CYCLE_SIGN_CHECK" in codes(p, "warning")


def test_too_short_and_unreadable_files():
    assert "CYCLE_TOO_SHORT" in codes(csv("Time [s],Speed [km/h]\n0,1\n1,2\n"), "error")
    with pytest.raises(IngestionError):
        csv("foo,bar\n1,2\n3,4\n")                                   # no recognisable headers


def test_column_mapping_override():
    txt = "Time [s],Power\n" + "\n".join(f"{i},{i}" for i in range(15)) + "\n"
    p = csv(txt)
    assert p.mapping["battery_power_kw"] == "Power"                    # lone 'Power' -> battery power (reviewable)
    q = csv(txt, mapping={"battery_power_kw": "", "motor_power_kw": "Power"})
    assert "motor_power_kw" in q.mapping and "battery_power_kw" not in q.mapping


# ------------------------------------------------------------------------------------ road load
def test_road_load_cruise_hand_calc():
    t = np.arange(0, 20.0)
    rl = road_load(t, np.full(20, 100.0), VEH, accel_ms2=np.zeros(20))
    assert rl.f_roll[5] == pytest.approx(158.868, abs=0.01)
    assert rl.f_aero[5] == pytest.approx(291.127, abs=0.01)
    assert rl.f_tractive[5] == pytest.approx(449.995, abs=0.02)
    assert rl.p_wheel_w[5] / 1000 == pytest.approx(12.4999, abs=0.001)
    assert rl.p_traction_batt_w[5] / 1000 == pytest.approx(13.8888, abs=0.001)
    assert rl.p_aux_w[5] == 500.0 and rl.p_batt_w[5] / 1000 == pytest.approx(14.3888, abs=0.001)
    assert rl.wheel_torque_nm[5] == pytest.approx(449.995 * 0.32, abs=0.01)
    assert rl.wheel_rpm[5] == pytest.approx(27.7778 / 0.32 * 60 / (2 * np.pi), abs=0.01)


def test_road_load_acceleration_and_regen_hand_calc():
    t = np.arange(0, 5.0)
    acc = road_load(t, np.full(5, 36.0), VEH, accel_ms2=np.full(5, 1.0))
    assert acc.f_tractive[0] == pytest.approx(1996.598, abs=0.01)
    assert acc.p_wheel_w[0] / 1000 == pytest.approx(19.966, abs=0.001)
    assert acc.p_batt_w[0] / 1000 == pytest.approx(22.684, abs=0.001)
    dec = road_load(t, np.full(5, 36.0), VEH, accel_ms2=np.full(5, -1.0))
    assert dec.p_wheel_w[0] / 1000 == pytest.approx(-16.034, abs=0.001)
    assert dec.p_batt_w[0] / 1000 == pytest.approx(-13.931, abs=0.001)          # regen power is NEGATIVE battery power
    assert dec.p_friction_brake_w[0] == pytest.approx(0.0)
    half = road_load(t, np.full(5, 36.0), VehicleParams(**{**VEH.model_dump(), "regen_fraction": 0.5}), accel_ms2=np.full(5, -1.0))
    assert half.p_batt_w[0] / 1000 == pytest.approx(-6.715, abs=0.001)
    assert half.p_friction_brake_w[0] == pytest.approx(16034.02 * 0.5, abs=1.0)
    cap = road_load(t, np.full(5, 36.0), VehicleParams(**{**VEH.model_dump(), "max_regen_kw": 5.0}), accel_ms2=np.full(5, -1.0))
    assert cap.p_traction_batt_w[0] == pytest.approx(-5000.0, abs=1e-6)          # limited at the battery terminals


def test_standstill_only_auxiliary_power_and_no_rolling_force():
    rl = road_load(np.arange(5.0), np.zeros(5), VEH, accel_ms2=np.zeros(5))
    assert np.all(rl.f_roll == 0) and np.all(rl.p_wheel_w == 0) and np.all(rl.p_batt_w == 500.0)


def test_grade_force_hand_calc():
    v = VehicleParams(**{**VEH.model_dump(), "gradient_pct": 5.0})
    rl = road_load(np.arange(5.0), np.full(5, 36.0), v, accel_ms2=np.zeros(5))
    theta = np.arctan(0.05)
    assert rl.f_grade[0] == pytest.approx(1800 * 9.80665 * np.sin(theta), rel=1e-9) and rl.f_grade[0] == pytest.approx(881.5, abs=0.2)
    assert rl.f_roll[0] == pytest.approx(0.009 * 1800 * 9.80665 * np.cos(theta), rel=1e-9)


def test_acceleration_derived_from_speed_when_missing():
    t = np.arange(0, 11.0)
    rl = road_load(t, t * 3.6, VEH)                                   # v = t m/s -> a = 1 m/s²
    assert rl.a_ms2[5] == pytest.approx(1.0) and rl.a_ms2[0] == pytest.approx(1.0) and "derived" in rl.accel_source


def test_motor_power_conversion():
    trac, aux, batt = motor_to_battery_power([10.0, -10.0], VEH, "mechanical")
    assert batt[0] == pytest.approx(10000 / 0.9 + 500) and batt[1] == pytest.approx(-10000 * 0.9 + 500)
    _, _, batt_e = motor_to_battery_power([10.0, -10.0], VEH, "electrical")
    assert batt_e[0] == pytest.approx(10500) and batt_e[1] == pytest.approx(-9500)


def test_vehicle_validation():
    bad = VehicleParams(mass_kg=-5, crr=0.009, cd=0.28, frontal_area_m2=2.2, wheel_radius_m=0.3, drivetrain_eff=1.2)
    got = {i.code for i in validate_vehicle(bad)}
    assert {"VEHICLE_VALUE_INVALID", "VEHICLE_EFFICIENCY_INVALID"} <= got
    assert not [i for i in validate_vehicle(VEH) if i.severity == "error"]


# ------------------------------------------------------------------------------------ load construction
def base_req(**kw):
    cell = CellSpec(capacity_ah=100, v_nom=3.2, r_dc_mohm=1.0, confirmed=True)
    pack = PackConfig(ns=120, np=1, n_modules=10, cells_per_module=12)
    return AnalysisRequest(cell=cell, pack=pack, **kw), derive_pack(cell, pack)


def cyc(**kw):
    n = 30
    d = dict(time_s=list(range(n)))
    d.update(kw)
    return DriveCycle(**d)


def test_source_priority_current_over_power_over_speed():
    req, pk = base_req(cycle=cyc(battery_current_a=[100.0] * 30, battery_power_kw=[30.0] * 30, speed_kmh=[50.0] * 30))
    lp = build_load(req, pk)
    assert lp.source == "battery_current" and lp.kind == "current" and lp.values[0] == 100.0
    req2, pk2 = base_req(cycle=cyc(battery_power_kw=[30.0] * 30, speed_kmh=[50.0] * 30))
    lp2 = build_load(req2, pk2)
    assert lp2.source == "battery_power" and lp2.kind == "power" and lp2.values[0] == 30000.0


def test_explicit_source_selection_and_sign_options():
    req, pk = base_req(cycle=cyc(battery_current_a=[100.0] * 30, battery_power_kw=[30.0] * 30),
                       cycle_options=CycleOptions(source="battery_power", power_sign=-1))
    lp = build_load(req, pk)
    assert lp.source == "battery_power" and lp.values[0] == -30000.0
    with pytest.raises(LoadError):
        build_load(base_req(cycle=cyc(battery_current_a=[1.0] * 30), cycle_options=CycleOptions(source="motor_power"))[0], pk)


def test_speed_only_needs_vehicle_and_builds_road_load():
    req, pk = base_req(cycle=cyc(speed_kmh=[100.0] * 30, accel_ms2=[0.0] * 30))
    with pytest.raises(LoadError):
        build_load(req, pk)
    assert "VEHICLE_PARAMS_MISSING" in {i.code for i in validate_load(req)}
    req2, pk2 = base_req(cycle=cyc(speed_kmh=[100.0] * 30, accel_ms2=[0.0] * 30), vehicle=VEH)
    lp = build_load(req2, pk2)
    assert lp.source == "vehicle_speed" and lp.kind == "power" and lp.values[3] / 1000 == pytest.approx(14.3888, abs=0.001)
    assert lp.p_aux_w[0] == 500.0 and lp.road is not None


def test_battery_power_without_aux_adds_auxiliary_load():
    req, pk = base_req(cycle=cyc(battery_power_kw=[10.0] * 30), cycle_options=CycleOptions(battery_power_includes_aux=False), vehicle=VEH)
    assert build_load(req, pk).values[0] == pytest.approx(10500.0)


def test_cycle_repeats_tile_time_and_drop_file_soc():
    req, pk = base_req(cycle=cyc(battery_current_a=list(range(30)), soc_pct=[90.0] * 30), cycle_options=CycleOptions(repeats=3))
    lp = build_load(req, pk)
    assert len(lp.t) == 90 and lp.t[30] == pytest.approx(30.0) and lp.t[-1] == pytest.approx(89.0) and lp.soc_file_pct is None
    assert lp.values[30] == 0 and lp.values[59] == 29 and lp.period_s == pytest.approx(30.0)


def test_crate_profile_builds_signed_current():
    prof = CRateProfile(segments=[Segment(kind="discharge", c_rate=2.0, duration_s=10), Segment(kind="rest", duration_s=5),
                                  Segment(kind="charge", c_rate=0.5, duration_s=10)], dt_s=1.0)
    req, pk = base_req(crate_profile=prof)
    lp = build_load(req, pk)
    assert lp.source == "c_rate_profile" and lp.t[-1] == 25.0 and len(lp.t) == 26
    assert lp.values[0] == 200.0 and lp.values[9] == 200.0 and lp.values[10] == 0.0 and lp.values[15] == -50.0
    assert lp.state_override[0] == "discharge" and lp.state_override[12] == "rest" and lp.state_override[20] == "charge"


def test_driving_cycle_takes_precedence_over_crate_profile():
    prof = CRateProfile(segments=[Segment(kind="discharge", c_rate=1.0, duration_s=100)])
    req, pk = base_req(cycle=cyc(battery_current_a=[10.0] * 30), crate_profile=prof)
    assert build_load(req, pk).source == "battery_current"


def test_validate_load_errors():
    req, _ = base_req()
    assert "LOAD_MISSING" in {i.code for i in validate_load(req)}
    req2, _ = base_req(cycle=DriveCycle(time_s=[0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9, 10], battery_current_a=[1.0] * 12))
    assert "CYCLE_DUPLICATE_TIME" in {i.code for i in validate_load(req2)}
    req3, _ = base_req(cycle=DriveCycle(time_s=list(range(10)) + [100, 101, 102], battery_current_a=[1.0] * 13))
    assert "CYCLE_GAP" in {i.code for i in validate_load(req3)}
    req4, _ = base_req(cycle=cyc(battery_current_a=[1.0] * 30, soc_pct=[105.0] * 30))
    assert "CYCLE_SOC_OUT_OF_RANGE" in {i.code for i in validate_load(req4)}
    req5, _ = base_req(cycle=cyc(battery_current_a=[1.0] * 30), crate_limits={"cont_discharge_c": 2.0, "peak_discharge_c": 1.0})
    assert "CRATE_PEAK_BELOW_CONTINUOUS" in {i.code for i in validate_load(req5)}
    req6, _ = base_req(crate_profile=CRateProfile(segments=[Segment(kind="discharge", c_rate=-1, duration_s=10)]))
    assert "CRATE_INVALID" in {i.code for i in validate_load(req6)}
    assert "CYCLE_NONUNIFORM_DT" in {i.code for i in validate_time_base([0, 1, 2, 3.4, 4, 5, 6, 7, 8, 9, 10])}


# ------------------------------------------------------------------------------------ API
def test_api_drivecycle_upload_and_repair_flag():
    data = (SAMPLES / "drive_cycle_battery.csv").read_bytes()
    r = client.post("/api/drivecycle/parse", files={"file": ("c.csv", data, "text/csv")})
    assert r.status_code == 200 and r.json()["recommended_source"] == "battery_current" and r.json()["has_errors"] is False
    bad = b"Time [s],Speed [km/h]\n" + b"\n".join(f"{t},{t}".encode() for t in list(range(10)) + list(range(50, 60))) + b"\n"
    r2 = client.post("/api/drivecycle/parse", files={"file": ("g.csv", bad, "text/csv")})
    assert r2.json()["has_errors"] is True
    r3 = client.post("/api/drivecycle/parse", files={"file": ("g.csv", bad, "text/csv")}, data={"repair": "true"})
    assert r3.json()["has_errors"] is False and r3.json()["repairs"]
    assert client.post("/api/drivecycle/parse", files={"file": ("x.bin", b"junk", "application/octet-stream")}).status_code == 400
    assert client.post("/api/samples/drive_cycle_speed_only.csv/parse-cycle").json()["sources"] == ["vehicle_speed"]
