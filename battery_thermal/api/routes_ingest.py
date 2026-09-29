"""File-ingestion endpoints (datasheet, driving cycle, templates, sample data)."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from ..ingestion.common import IngestionError
from ..ingestion.datasheet import datasheet_template_csv, datasheet_template_xlsx, parse_datasheet

router = APIRouter(prefix="/api", tags=["ingestion"])

SAMPLE_DIR = Path(os.environ.get("BATTERY_THERMAL_SAMPLES", Path(__file__).resolve().parents[2] / "sample_data"))
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File too large (>{MAX_UPLOAD_BYTES // 1024 // 1024} MB)")
    if not data:
        raise HTTPException(400, "The uploaded file is empty")
    return data


@router.post("/datasheet/parse")
async def datasheet_parse(file: UploadFile = File(...)):
    """Parse a cell datasheet (CSV / XLSX / PDF). Returns *proposed* values with provenance - never confirmed."""
    data = await _read_upload(file)
    try:
        return parse_datasheet(data, file.filename or "upload").to_dict()
    except IngestionError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/samples")
def list_samples():
    if not SAMPLE_DIR.exists():
        return []
    return sorted(p.name for p in SAMPLE_DIR.iterdir() if p.is_file())


@router.get("/samples/{name}")
def get_sample(name: str):
    p = (SAMPLE_DIR / name).resolve()
    if SAMPLE_DIR.resolve() not in p.parents or not p.is_file():
        raise HTTPException(404, "Unknown sample file")
    return FileResponse(p, filename=p.name)


@router.post("/samples/{name}/parse-datasheet")
def parse_sample_datasheet(name: str):
    p = (SAMPLE_DIR / name).resolve()
    if SAMPLE_DIR.resolve() not in p.parents or not p.is_file():
        raise HTTPException(404, "Unknown sample file")
    try:
        return parse_datasheet(p.read_bytes(), p.name).to_dict()
    except IngestionError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/templates/datasheet.csv")
def template_csv():
    return Response(datasheet_template_csv(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="cell_datasheet_template.csv"'})


@router.get("/templates/datasheet.xlsx")
def template_xlsx():
    return Response(datasheet_template_xlsx(),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="cell_datasheet_template.xlsx"'})
