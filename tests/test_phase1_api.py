"""Phase 1 API: configuration endpoint, defaults, static UI."""
from fastapi.testclient import TestClient

from battery_thermal.api.main import app

client = TestClient(app)

CELL = dict(capacity_ah=100, v_nom=3.2, v_max=3.65, v_min=2.5, r_dc_mohm=1.0, confirmed=True)
PACK = dict(ns=120, np=1, n_modules=10, cells_per_module=12)


def test_health_and_index():
    assert client.get("/api/health").json()["status"] == "ok"
    r = client.get("/")
    assert r.status_code == 200 and "EV Battery Thermal Studio" in r.text
    assert client.get("/static/js/app.js").status_code == 200
    assert client.get("/vendor/plotly.min.js").status_code == 200


def test_config_validate_returns_derived_values_and_probe():
    r = client.post("/api/config/validate", json={"cell": CELL, "pack": PACK, "probe_pack_current_a": 200}).json()
    assert r["ok"] is True
    d = r["derived"]
    assert d["v_nom"] == 384.0 and d["capacity_ah"] == 100.0 and abs(d["energy_kwh"] - 38.4) < 1e-9
    assert r["probe"] == {"i_pack_a": 200.0, "i_module_a": 200.0, "i_cell_a": 200.0, "c_rate": 2.0}


def test_config_validate_probe_from_c_rate():
    r = client.post("/api/config/validate", json={"cell": CELL, "pack": dict(PACK, ns=96, np=2, n_modules=8,
                                                                            cells_per_module=24), "probe_c_rate": 1.5}).json()
    assert r["probe"]["i_cell_a"] == 150.0 and r["probe"]["i_pack_a"] == 300.0


def test_config_validate_flags_inconsistency():
    r = client.post("/api/config/validate", json={"cell": CELL, "pack": dict(PACK, cells_per_module=10)}).json()
    assert r["ok"] is False
    assert any(i["code"] == "PACK_CELL_COUNT_MISMATCH" for i in r["issues"])
    assert r["derived"] is None


def test_defaults_contain_assumed_paths_and_no_cell_data():
    d = client.get("/api/defaults").json()
    assert "thermal.safety_factor" in d["assumed_paths"]
    assert "cell" not in d["groups"]                       # never fabricate cell data
    assert d["groups"]["thermal"]["safety_factor"] == 1.2
    assert d["cold_plate"]["n_channels"] >= 1


def test_invalid_payload_is_422():
    r = client.post("/api/config/validate", json={"cell": CELL, "pack": {"ns": 1}})
    assert r.status_code == 422
