"""Phase 2 - datasheet ingestion.

The parser must (a) extract values with units converted to the canonical set, (b) never invent data,
(c) flag ambiguity / missing / atypical values, and (d) leave every value unconfirmed.
"""
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from battery_thermal.api.main import app
from battery_thermal.engine.schemas import CellSpec
from battery_thermal.engine.validation import validate_cell
from battery_thermal.ingestion.common import IngestionError
from battery_thermal.ingestion.datasheet import (
    datasheet_template_csv, datasheet_template_xlsx, parse_datasheet, parse_value,
)

SAMPLES = Path(__file__).resolve().parents[1] / "sample_data"
client = TestClient(app)


def parse_csv(text: str):
    return parse_datasheet(text.encode(), "t.csv")


def val(res, path):
    return res.fields[path].value


# --------------------------------------------------------------------------------------------------
def test_sample_csv_extracts_all_scalar_fields_with_units():
    r = parse_datasheet((SAMPLES / "cell_datasheet_LFP100Ah.csv").read_bytes(), "cell_datasheet_LFP100Ah.csv")
    assert val(r, "cell.capacity_ah") == 100.0
    assert val(r, "cell.v_nom") == 3.2 and val(r, "cell.v_max") == 3.65 and val(r, "cell.v_min") == 2.5
    assert val(r, "cell.r_dc_mohm") == 0.52 and val(r, "cell.r_ac_mohm") == 0.28
    assert val(r, "cell.r_ref_temp_c") == 25 and val(r, "cell.r_ref_soc_pct") == 50
    assert val(r, "cell.max_discharge_c") == 1.0 and val(r, "cell.max_charge_c") == 0.5
    assert val(r, "cell.pulse_discharge_c") == 2.0 and val(r, "cell.pulse_duration_s") == 10
    assert (val(r, "cell.length_mm"), val(r, "cell.width_mm"), val(r, "cell.height_mm")) == (174, 71, 207)
    assert val(r, "cell.mass_kg") == 2.05
    assert (val(r, "cell.t_op_min_c"), val(r, "cell.t_op_max_c")) == (-20, 60)
    assert (val(r, "cell.t_charge_min_c"), val(r, "cell.t_charge_max_c")) == (0, 55)
    assert (val(r, "cell.t_rec_min_c"), val(r, "cell.t_rec_max_c")) == (15, 35)
    assert val(r, "cell.chemistry") == "LFP" and val(r, "cell.form_factor") == "prismatic"


def test_sample_csv_curves():
    r = parse_datasheet((SAMPLES / "cell_datasheet_LFP100Ah.csv").read_bytes(), "x.csv")
    assert set(r.curves) == {"r_vs_soc", "r_vs_temp", "ocv_vs_soc", "capacity_vs_temp"}
    assert r.curves["r_vs_soc"]["x"][0] == 0 and r.curves["r_vs_soc"]["y"][4] == 0.52
    assert r.curves["r_vs_temp"]["x"] == [-10, 0, 10, 25, 40, 55]
    assert len(r.curves["ocv_vs_soc"]["x"]) == 13


def test_proposal_is_never_confirmed_and_blocks_analysis():
    r = parse_datasheet((SAMPLES / "cell_datasheet_LFP100Ah.csv").read_bytes(), "x.csv")
    prop = r.cell_proposal()
    assert prop["confirmed"] is False
    codes = {i.code for i in validate_cell(CellSpec(**prop))}
    assert "CELL_NOT_CONFIRMED" in codes                     # extraction alone can never start a calculation


def test_xlsx_matches_csv_and_adds_entropic_table_and_map():
    csv_r = parse_datasheet((SAMPLES / "cell_datasheet_LFP100Ah.csv").read_bytes(), "x.csv")
    x_r = parse_datasheet((SAMPLES / "cell_datasheet_LFP100Ah.xlsx").read_bytes(), "x.xlsx")
    for p, f in csv_r.fields.items():
        if p == "cell.name":
            continue
        assert p in x_r.fields and x_r.fields[p].value == f.value, p
    assert "dudt_vs_soc" in x_r.curves and "r_map" in x_r.maps
    m = x_r.maps["r_map"]
    assert len(m["x"]) == 6 and len(m["y"]) == 6 and len(m["z"]) == 6 and len(m["z"][0]) == 6
    # R_map at (SOC=50, T=25) must reproduce the DC resistance of the datasheet (separable synthetic map)
    assert m["z"][m["x"].index(50)][m["y"].index(25)] == pytest.approx(0.52, abs=1e-6) if 50 in m["x"] else True


def test_pdf_extraction_converts_amps_to_c_rate_and_fixes_ohm_glyph():
    r = parse_datasheet((SAMPLES / "cell_datasheet_LFP100Ah.pdf").read_bytes(), "x.pdf")
    assert val(r, "cell.capacity_ah") == 100 and val(r, "cell.v_nom") == 3.2
    assert val(r, "cell.max_discharge_c") == pytest.approx(1.0)          # 100 A / 100 Ah
    assert val(r, "cell.max_charge_c") == pytest.approx(0.5)
    assert val(r, "cell.pulse_discharge_c") == pytest.approx(2.0)
    assert "Converted from 100 A" in r.fields["cell.max_discharge_c"].note
    rdc = r.fields["cell.r_dc_mohm"]
    assert rdc.value == pytest.approx(0.6) and rdc.confidence == "low"        # glyph fallback -> low confidence
    assert "≤" not in str(rdc.value) and "maximum" in rdc.note.lower()         # "≤ 0.6 mΩ" flagged as a maximum
    assert all(f.confidence != "high" for f in r.fields.values())               # PDF text is never 'high'


def test_pdf_without_text_is_reported():
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.rect(50, 50, 200, 100)
    c.save()
    r = parse_datasheet(buf.getvalue(), "scan.pdf")
    assert any(i.code == "DS_PDF_NO_TEXT" and i.severity == "error" for i in r.issues)


# --------------------------------------------------------------------------------------------------
# unit conversions
# --------------------------------------------------------------------------------------------------
def test_unit_conversions():
    r = parse_csv("Parameter,Value,Unit\n"
                  "Nominal capacity,100000,mAh\n"
                  "Nominal voltage,3200,mV\n"
                  "DC internal resistance,0.0005,Ω\n"
                  "Cell mass,2050,g\n"
                  "Length,17.4,cm\n"
                  "Operating temperature,253.15 ~ 333.15,K\n"
                  "Specific heat capacity,1.05,kJ/(kg·K)\n")
    assert val(r, "cell.capacity_ah") == pytest.approx(100)
    assert val(r, "cell.v_nom") == pytest.approx(3.2)
    assert val(r, "cell.r_dc_mohm") == pytest.approx(0.5)
    assert val(r, "cell.mass_kg") == pytest.approx(2.05)
    assert val(r, "cell.length_mm") == pytest.approx(174)
    assert val(r, "cell.t_op_min_c") == pytest.approx(-20) and val(r, "cell.t_op_max_c") == pytest.approx(60)
    assert val(r, "cell.cp_j_kg_k") == pytest.approx(1050)


def test_missing_unit_is_flagged_and_lowers_confidence():
    r = parse_csv("Parameter,Value\nNominal capacity,100\nDC internal resistance,0.5\n")
    assert r.fields["cell.r_dc_mohm"].confidence in ("low", "medium") and "assumed" in r.fields["cell.r_dc_mohm"].note.lower()
    assert any(i.code == "DS_UNIT_ASSUMED" for i in r.issues)


def test_atypical_value_downgraded():
    r = parse_csv("Parameter,Value,Unit\nNominal capacity,100000,Ah\n")        # 100 kAh cell is not credible
    f = r.fields["cell.capacity_ah"]
    assert f.confidence == "low" and any(i.code == "DS_OUT_OF_TYPICAL_RANGE" for i in r.issues)


def test_ambiguous_internal_resistance_is_not_used_as_dc():
    r = parse_csv("Parameter,Value,Unit\nInternal resistance,0.3,mΩ\n")
    assert "cell.r_dc_mohm" not in r.fields and val(r, "cell.r_ac_mohm") == 0.3
    assert any(i.code == "DS_AMBIGUOUS_R" for i in r.issues)


def test_maximum_qualifier_noted():
    r = parse_csv("Parameter,Value,Unit\nDC internal resistance,≤ 0.6,mΩ\n")
    assert val(r, "cell.r_dc_mohm") == 0.6 and "maximum" in r.fields["cell.r_dc_mohm"].note.lower()


def test_operating_voltage_range_is_a_window_not_nominal():
    r = parse_csv("Parameter,Value,Unit\nOperating voltage,2.5 ~ 3.65,V\n")
    assert "cell.v_nom" not in r.fields
    assert val(r, "cell.v_min") == 2.5 and val(r, "cell.v_max") == 3.65


def test_c_rate_from_amps_uses_extracted_capacity():
    r = parse_csv("Parameter,Value,Unit\nNominal capacity,50,Ah\nMax continuous discharge current,100,A\n")
    assert val(r, "cell.max_discharge_c") == pytest.approx(2.0)


def test_parse_value_variants():
    assert parse_value("−20 ~ 60 °C").n1 == -20 and parse_value("−20 ~ 60 °C").n2 == 60
    assert parse_value("0,52 mΩ").n1 == 0.52
    p = parse_value("≤ 0.6 mΩ")
    assert p.qualifier == "≤" and p.n1 == 0.6 and p.unit == "mΩ"


# --------------------------------------------------------------------------------------------------
# missing data / suggestions (never applied silently)
# --------------------------------------------------------------------------------------------------
def test_missing_required_parameters_listed_with_suggestions_not_applied():
    r = parse_csv("Parameter,Value,Unit\nChemistry,LFP,\nNominal capacity,100,Ah\nNominal voltage,3.2,V\n")
    miss = {m["path"]: m for m in r.missing}
    assert miss["cell.r_dc_mohm"]["required"] and miss["cell.r_dc_mohm"]["suggestion"] is None   # no invented resistance
    assert miss["cell.cp_j_kg_k"]["suggestion"]["confidence"] == "low"
    assert miss["cell.mass_kg"]["suggestion"]["value"] == pytest.approx(100 * 3.2 / 160.0, rel=1e-3)
    assert "cell.cp_j_kg_k" not in r.fields and "cell.mass_kg" not in r.fields             # suggestions are NOT values
    assert any(m["path"] == "cell.dudt_vs_soc" for m in r.missing)                            # entropic gap is visible
    assert any(m["path"] == "cell.ocv_vs_soc" for m in r.missing)


def test_resistance_curve_satisfies_resistance_requirement():
    r = parse_csv("Parameter,Value,Unit\nNominal capacity,100,Ah\n\n[R_vs_SOC]\nSOC (%),R (mΩ)\n0,0.8\n50,0.5\n100,0.6\n")
    assert "r_vs_soc" in r.curves
    assert not any(m["path"] == "cell.r_dc_mohm" and m["required"] for m in r.missing)


def test_empty_template_yields_nothing_no_fabrication():
    r = parse_datasheet(datasheet_template_csv().encode(), "template.csv")
    assert r.fields == {} and r.curves == {} and r.maps == {}
    assert any(i.code == "DS_NOTHING_FOUND" for i in r.issues)
    rx = parse_datasheet(datasheet_template_xlsx(), "template.xlsx")
    assert rx.fields == {} and rx.curves == {}


def test_filled_template_round_trip():
    text = datasheet_template_csv().replace("Nominal capacity,,Ah,", "Nominal capacity,280,Ah,") \
        .replace("Nominal voltage,,V,", "Nominal voltage,3.2,V,").replace("Cell mass,,kg,", "Cell mass,5.4,kg,")
    r = parse_csv(text)
    assert val(r, "cell.capacity_ah") == 280 and val(r, "cell.v_nom") == 3.2 and val(r, "cell.mass_kg") == 5.4
    assert "cell.chemistry" not in r.fields                       # empty template notes must not leak into values


# --------------------------------------------------------------------------------------------------
# maps / curves edge cases
# --------------------------------------------------------------------------------------------------
def test_soc_fraction_axis_converted_and_noted():
    r = parse_csv("[R_vs_SOC]\nSOC (-),R (mΩ)\n0,0.8\n0.5,0.5\n1.0,0.6\n")
    assert r.curves["r_vs_soc"]["x"] == [0, 50, 100] and "fraction" in r.curves["r_vs_soc"]["note"]


def test_resistance_in_ohm_converted_to_mohm():
    r = parse_csv("[R_vs_T]\nTemperature (°C),R (Ω)\n0,0.0010\n25,0.0005\n45,0.0004\n")
    assert r.curves["r_vs_temp"]["y"] == pytest.approx([1.0, 0.5, 0.4])


def test_duplicate_x_values_rejected():
    r = parse_csv("[R_vs_SOC]\nSOC (%),R (mΩ)\n0,0.8\n0,0.7\n100,0.6\n")
    assert "r_vs_soc" not in r.curves and any(i.code == "DS_CURVE_INVALID" for i in r.issues)


def test_map_axes_must_be_increasing():
    r = parse_csv("[R_map]\nSOC \\ T,25,10,40\n0,1,2,3\n50,1,2,3\n100,1,2,3\n")
    assert "r_map" not in r.maps and any(i.code == "DS_CURVE_INVALID" for i in r.issues)


# --------------------------------------------------------------------------------------------------
# bad files
# --------------------------------------------------------------------------------------------------
def test_unsupported_and_corrupt_files():
    with pytest.raises(IngestionError):
        parse_datasheet(b"abc", "notes.docx")
    with pytest.raises(IngestionError):
        parse_datasheet(b"legacy", "old.xls")
    with pytest.raises(IngestionError):
        parse_datasheet(b"not a zip", "bad.xlsx")
    with pytest.raises(IngestionError):
        parse_datasheet(b"   \n ", "empty.csv")


def test_semicolon_decimal_comma_csv():
    r = parse_csv("Parameter;Value;Unit\nNominal capacity;100,5;Ah\nNominal voltage;3,2;V\n")
    assert val(r, "cell.capacity_ah") == 100.5 and val(r, "cell.v_nom") == 3.2


# --------------------------------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------------------------------
def test_api_parse_upload_and_templates():
    data = (SAMPLES / "cell_datasheet_LFP100Ah.csv").read_bytes()
    r = client.post("/api/datasheet/parse", files={"file": ("ds.csv", data, "text/csv")})
    assert r.status_code == 200
    j = r.json()
    assert j["cell"]["confirmed"] is False and j["cell"]["capacity_ah"] == 100.0
    assert j["fields"]["cell.capacity_ah"]["snippet"].startswith("Nominal capacity")
    assert any(m["path"] == "cell.cp_j_kg_k" and m["suggestion"] for m in j["missing"])
    assert client.post("/api/datasheet/parse", files={"file": ("x.docx", b"zzz", "application/octet-stream")}).status_code == 400
    assert client.post("/api/datasheet/parse", files={"file": ("e.csv", b"", "text/csv")}).status_code == 400
    assert client.get("/api/templates/datasheet.csv").status_code == 200
    assert client.get("/api/templates/datasheet.xlsx").content[:2] == b"PK"


def test_api_samples_are_confined_to_sample_dir():
    assert "cell_datasheet_LFP100Ah.csv" in client.get("/api/samples").json()
    assert client.get("/api/samples/..%2Fpyproject.toml").status_code in (404, 400)
    assert client.get("/api/samples/nonexistent.csv").status_code == 404
    r = client.post("/api/samples/cell_datasheet_LFP100Ah.xlsx/parse-datasheet")
    assert r.status_code == 200 and r.json()["source_type"] == "xlsx"
