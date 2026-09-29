"""Phase 1 - pack configuration, validation engine, interpolation guard, traceability.

Hand calculations (documented in docs/01_PHASE_LOG.md):
  120S1P, 100 Ah, 3.2 V  ->  V = 120*3.2 = 384 V, C = 100 Ah, E = 384*100/1000 = 38.4 kWh
  96S2P, 50 Ah, 3.65 V   ->  V = 350.4 V, C = 100 Ah, E = 96*2*50*3.65/1000 = 35.04 kWh
  I_pack = 200 A on 96S2P -> I_cell = 100 A -> 2C ; module (series) current = 200 A
"""
import pytest

from battery_thermal.engine.interp import Interp1D, Interp2D, OutOfRangeError
from battery_thermal.engine.pack import ConfigError, derive_pack
from battery_thermal.engine.schemas import CellSpec, PackConfig, Curve
from battery_thermal.engine.trace import TraceLog
from battery_thermal.engine.validation import validate_cell, validate_pack, has_errors


def cell100(**kw):
    base = dict(capacity_ah=100, v_nom=3.2, v_max=3.65, v_min=2.5, r_dc_mohm=1.0, mass_kg=2.0, cp_j_kg_k=1000,
                t_op_max_c=60, confirmed=True)
    base.update(kw)
    return CellSpec(**base)


def pack120s1p(**kw):
    base = dict(ns=120, np=1, n_modules=10, cells_per_module=12)
    base.update(kw)
    return PackConfig(**base)


def codes(issues):
    return {i.code for i in issues}


def test_120s1p_derived_quantities_match_hand_calc():
    d = derive_pack(cell100(), pack120s1p())
    assert d.n_cells == 120
    assert d.v_nom == pytest.approx(384.0)
    assert d.capacity_ah == pytest.approx(100.0)
    assert d.energy_kwh == pytest.approx(38.4)
    assert d.v_max == pytest.approx(120 * 3.65)
    assert (d.modules_in_series, d.modules_in_parallel) == (10, 1)
    assert (d.ns_per_module, d.np_per_module) == (12, 1)


def test_96s2p_current_split_and_crate():
    c = cell100(capacity_ah=50, v_nom=3.65)
    p = PackConfig(ns=96, np=2, n_modules=8, cells_per_module=24, module_arrangement="series")
    d = derive_pack(c, p)
    assert d.v_nom == pytest.approx(350.4)
    assert d.capacity_ah == pytest.approx(100.0)
    assert d.energy_kwh == pytest.approx(35.04)
    assert d.cell_current(200.0) == pytest.approx(100.0)
    assert d.module_current(200.0) == pytest.approx(200.0)      # series modules carry the string current
    assert d.c_rate(d.cell_current(200.0)) == pytest.approx(2.0)


def test_parallel_module_arrangement_splits_module_current():
    c = cell100()
    p = PackConfig(ns=100, np=4, n_modules=4, cells_per_module=100, module_arrangement="parallel")
    d = derive_pack(c, p)
    assert (d.modules_in_series, d.modules_in_parallel) == (1, 4)
    assert d.module_current(400.0) == pytest.approx(100.0)
    assert d.cell_current(400.0) == pytest.approx(100.0)


def test_series_parallel_arrangement():
    c = cell100()
    p = PackConfig(ns=96, np=2, n_modules=4, cells_per_module=48, module_arrangement="series_parallel",
                   modules_in_series=2)
    d = derive_pack(c, p)
    assert (d.modules_in_series, d.modules_in_parallel) == (2, 2)
    assert (d.ns_per_module, d.np_per_module) == (48, 1)


def test_valid_configuration_has_no_errors():
    assert not has_errors(validate_cell(cell100()) + validate_pack(cell100(), pack120s1p()))


def test_cell_count_mismatch_flagged():
    iss = validate_pack(cell100(), pack120s1p(cells_per_module=10))
    assert "PACK_CELL_COUNT_MISMATCH" in codes(iss)
    with pytest.raises(ConfigError):
        derive_pack(cell100(), pack120s1p(cells_per_module=10))


def test_inconsistent_pack_voltage_flagged_error_and_warning():
    # 450 V vs 384 V = 17 % -> error ; 390 V = 1.6 % -> warning ; 400 V = 4.2 % -> warning
    err = [i for i in validate_pack(cell100(), pack120s1p(pack_nominal_voltage_v=450)) if i.code == "PACK_VOLTAGE_INCONSISTENT"]
    assert err and err[0].severity == "error"
    warn = [i for i in validate_pack(cell100(), pack120s1p(pack_nominal_voltage_v=390)) if i.code == "PACK_VOLTAGE_INCONSISTENT"]
    assert warn and warn[0].severity == "warning"


def test_topology_not_divisible():
    p = PackConfig(ns=100, np=1, n_modules=8, cells_per_module=12)          # 100 % 8 != 0 and 100 != 96
    iss = validate_pack(cell100(), p)
    assert "PACK_CELL_COUNT_MISMATCH" in codes(iss) or "PACK_TOPOLOGY" in codes(iss)
    p2 = PackConfig(ns=100, np=1, n_modules=8, cells_per_module=100 // 8 * 1)   # 12*8 = 96 != 100
    assert has_errors(validate_pack(cell100(), p2))


def test_missing_required_cell_data():
    c = CellSpec(confirmed=True)
    got = codes(validate_cell(c))
    assert {"CELL_CAPACITY_MISSING", "CELL_VOLTAGE_MISSING", "CELL_RESISTANCE_MISSING"} <= got


def test_negative_and_invalid_values():
    c = cell100(capacity_ah=-5, r_dc_mohm=-1, max_discharge_c=-2)
    got = codes(validate_cell(c))
    assert {"CELL_CAPACITY_INVALID", "CELL_RESISTANCE_INVALID", "CELL_CRATE_INVALID"} <= got


def test_unconfirmed_cell_blocks():
    assert "CELL_NOT_CONFIRMED" in codes(validate_cell(cell100(confirmed=False)))
    assert "CELL_NOT_CONFIRMED" not in codes(validate_cell(cell100(confirmed=False), require_confirmation=False))


def test_ac_only_resistance_warning():
    c = CellSpec(capacity_ah=100, v_nom=3.2, r_ac_mohm=0.3, confirmed=True)
    got = codes(validate_cell(c))
    assert "CELL_RESISTANCE_MISSING" in got and "CELL_RESISTANCE_AC_ONLY" in got


def test_soc_out_of_range_and_window():
    assert "SOC_OUT_OF_RANGE" in codes(validate_pack(cell100(), pack120s1p(soc_initial_pct=120)))
    assert "SOC_WINDOW_INVALID" in codes(validate_pack(cell100(), pack120s1p(soc_min_pct=80, soc_max_pct=50)))


def test_target_temperature_checks():
    assert "TEMP_TARGET_BELOW_INITIAL" in codes(validate_pack(cell100(), pack120s1p(t_initial_c=45, t_target_max_c=40)))
    assert "TARGET_ABOVE_CELL_LIMIT" in codes(validate_pack(cell100(), pack120s1p(t_target_max_c=70)))


def test_trace_records_pack_derivation():
    tr = TraceLog()
    derive_pack(cell100(), pack120s1p(), tr)
    node = tr.get("pack.energy")
    assert node.value == pytest.approx(38.4) and "Ns" in node.formula
    chain = [n.id for n in tr.chain("pack.energy")]
    assert "in.ns" in chain and "in.cell_vnom" in chain and chain[-1] == "pack.energy"


# --- interpolation guard --------------------------------------------------------------------
def test_interp1d_inside_and_block_outside():
    f = Interp1D([0, 50, 100], [2.0, 1.0, 1.5], "R(SOC)", "SOC")
    assert f(25) == pytest.approx(1.5)
    assert f(75) == pytest.approx(1.25)
    with pytest.raises(OutOfRangeError) as e:
        f(105)
    assert "outside the available data range" in str(e.value)


def test_interp1d_clamp_and_linear_are_explicit():
    x, y = [0, 10], [1.0, 2.0]
    assert Interp1D(x, y, policy="clamp")(20) == pytest.approx(2.0)
    assert Interp1D(x, y, policy="linear")(20) == pytest.approx(3.0)
    g = Interp1D(x, y, policy="clamp")
    g(-5)
    assert g.use.n_outside == 1


def test_interp2d_bilinear_hand_calc():
    # z = 1 + 0.01*soc + 0.1*T  is exactly reproduced by bilinear interpolation
    xs, ys = [0, 50, 100], [0, 25, 50]
    z = [[1 + 0.01 * s + 0.1 * t for t in ys] for s in xs]
    f = Interp2D(xs, ys, z)
    assert f(30, 10) == pytest.approx(1 + 0.3 + 1.0)
    assert f(100, 50) == pytest.approx(1 + 1 + 5)
    with pytest.raises(OutOfRangeError):
        f(30, 60)


def test_curve_schema_sorts_and_rejects_duplicates():
    c = Curve(x=[50, 0, 100], y=[1, 2, 3])
    assert c.x == [0, 50, 100] and c.y == [2, 1, 3]
    with pytest.raises(ValueError):
        Curve(x=[0, 0, 1], y=[1, 2, 3])
