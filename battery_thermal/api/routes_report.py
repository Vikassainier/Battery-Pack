"""Report endpoints (Phase 9): the analysis is recomputed from the posted request so the report always matches the inputs."""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from ..engine.pipeline import run_analysis
from ..engine.schemas import AnalysisRequest
from ..engine.sensitivity import sensitivity_analysis
from ..reporting.excel_report import build_xlsx
from ..reporting.pdf_report import build_pdf
from ..reporting.text import report_meta

router = APIRouter(prefix="/api/report", tags=["report"])

PDF_TYPE = "application/pdf"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ReportRequest(BaseModel):
    request: AnalysisRequest
    include_sensitivity: bool = True


def _filename(req: AnalysisRequest, res: dict, ext: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", req.project.name or "report").strip("_")[:60] or "report"
    return f"{slug}_{report_meta(req, res)['report_id']}.{ext}"


def _analysis_or_422(req: AnalysisRequest, full_series: bool) -> dict:
    res = run_analysis(req, max_series_points=None if full_series else 6000)
    if res.get("status") == "blocked":
        errs = [i["message"] for i in res["issues"] if i["severity"] == "error"]
        raise HTTPException(status_code=422, detail="The analysis is blocked, so no report can be produced. Fix these errors first: " + "; ".join(errs[:6]) + ("; …" if len(errs) > 6 else ""))
    return res


def _sens(req: AnalysisRequest, include: bool):
    return sensitivity_analysis(req) if include else None


@router.post("/pdf")
def report_pdf(body: ReportRequest):
    res = _analysis_or_422(body.request, full_series=False)
    data = build_pdf(body.request, res, _sens(body.request, body.include_sensitivity))
    return Response(data, media_type=PDF_TYPE, headers={"Content-Disposition": f'attachment; filename="{_filename(body.request, res, "pdf")}"'})


@router.post("/xlsx")
def report_xlsx(body: ReportRequest):
    res = _analysis_or_422(body.request, full_series=True)             # the workbook carries every time step
    data = build_xlsx(body.request, res, _sens(body.request, body.include_sensitivity))
    return Response(data, media_type=XLSX_TYPE, headers={"Content-Disposition": f'attachment; filename="{_filename(body.request, res, "xlsx")}"'})
