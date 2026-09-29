"""Synthetic sample files are rebuilt on demand, so a checkout that only has the text sources still works."""
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from battery_thermal.api import routes_ingest
from battery_thermal.api.main import app
from battery_thermal.ingestion.datasheet import parse_datasheet
from battery_thermal.ingestion.drive_cycle import parse_drive_cycle
from battery_thermal.ingestion.sample_files import CYCLE_BATTERY, CYCLE_SPEED, DATASHEET_CSV, DATASHEET_PDF, DATASHEET_XLSX, ensure_sample_files

REPO_SAMPLES = Path(__file__).resolve().parents[1] / "sample_data"


def test_missing_binary_samples_are_created_from_the_csv_and_never_overwritten(tmp_path):
    shutil.copy(REPO_SAMPLES / DATASHEET_CSV, tmp_path / DATASHEET_CSV)
    made = {p.name for p in ensure_sample_files(tmp_path)}
    assert made == {DATASHEET_XLSX, DATASHEET_PDF, CYCLE_SPEED, CYCLE_BATTERY}
    marker = b"keep me"
    (tmp_path / DATASHEET_PDF).write_bytes(marker)                           # an existing file is left alone, even if it is not a valid PDF
    assert ensure_sample_files(tmp_path) == [] and (tmp_path / DATASHEET_PDF).read_bytes() == marker


def test_without_the_csv_source_only_the_independent_files_are_created(tmp_path):
    made = {p.name for p in ensure_sample_files(tmp_path)}
    assert made == {DATASHEET_PDF, CYCLE_SPEED, CYCLE_BATTERY}               # the XLSX is derived from the CSV, which is absent


def test_regenerated_files_parse_to_the_same_cell_as_the_committed_ones(tmp_path):
    shutil.copy(REPO_SAMPLES / DATASHEET_CSV, tmp_path / DATASHEET_CSV)
    ensure_sample_files(tmp_path)
    for name in (DATASHEET_CSV, DATASHEET_XLSX, DATASHEET_PDF):
        cell = parse_datasheet((tmp_path / name).read_bytes(), name).to_dict()["cell"]
        assert cell["capacity_ah"] == pytest.approx(100.0) and cell["v_nom"] == pytest.approx(3.2), name
        assert cell["mass_kg"] == pytest.approx(2.05), name
    xl = parse_datasheet((tmp_path / DATASHEET_XLSX).read_bytes(), DATASHEET_XLSX).to_dict()
    assert "r_map" in xl["maps"] and "dudt_vs_soc" in xl["curves"]              # the XLSX carries the 2-D resistance map and the entropic table
    for name in (CYCLE_SPEED, CYCLE_BATTERY):
        assert len(parse_drive_cycle((tmp_path / name).read_bytes(), name).to_dict()["cycle"]["time_s"]) == 1201


def test_api_creates_missing_samples_on_first_use(tmp_path, monkeypatch):
    shutil.copy(REPO_SAMPLES / DATASHEET_CSV, tmp_path / DATASHEET_CSV)
    monkeypatch.setattr(routes_ingest, "SAMPLE_DIR", tmp_path)
    monkeypatch.setattr(routes_ingest, "_samples_checked", False)
    client = TestClient(app)
    names = client.get("/api/samples").json()
    assert {DATASHEET_XLSX, DATASHEET_PDF, CYCLE_SPEED, CYCLE_BATTERY} <= set(names)
    r = client.post(f"/api/samples/{DATASHEET_PDF}/parse-datasheet")
    assert r.status_code == 200
    assert client.post(f"/api/samples/{CYCLE_BATTERY}/parse-cycle").status_code == 200
    assert client.get("/api/samples/nope.txt").status_code == 404


def test_read_only_sample_directory_is_tolerated(tmp_path, monkeypatch):
    ro = tmp_path / "does" / "not" / "exist"
    monkeypatch.setattr(routes_ingest, "SAMPLE_DIR", ro)
    monkeypatch.setattr(routes_ingest, "_samples_checked", False)
    monkeypatch.setattr(routes_ingest, "ensure_sample_files", lambda d: (_ for _ in ()).throw(PermissionError("read-only file system")))
    assert TestClient(app).get("/api/samples").json() == []
