"""Engineering report (PDF) - OEM-consultancy style, 17 sections, traceability appendix."""
from __future__ import annotations

import io
import math
import os
import threading
from xml.sax.saxutils import escape

import matplotlib
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, CondPageBreak, Frame, Image, KeepTogether, LongTable, NextPageTemplate, PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

from ..engine.assumptions import CATALOG
from ..engine.schemas import AnalysisRequest
from . import charts
from .labels import CRATE_FIELDS, VEHICLE_FIELDS, cell_spec, cell_tables, field_label, provenance_of, value_text
from .text import LIMITATIONS, METHODOLOGY, recommendations, report_meta

# ------------------------------------------------------------------------------------------------ fonts & styles
_FONT_DIR = os.path.join(os.path.dirname(matplotlib.__file__), "mpl-data", "fonts", "ttf")
for name, fn in (("DV", "DejaVuSans.ttf"), ("DV-B", "DejaVuSans-Bold.ttf"), ("DV-I", "DejaVuSans-Oblique.ttf"), ("DV-BI", "DejaVuSans-BoldOblique.ttf"), ("DV-M", "DejaVuSansMono.ttf")):
    pdfmetrics.registerFont(TTFont(name, os.path.join(_FONT_DIR, fn)))
pdfmetrics.registerFontFamily("DV", normal="DV", bold="DV-B", italic="DV-I", boldItalic="DV-BI")

NAVY, ACCENT, INK, MUTED, LINE, ZEBRA = colors.HexColor("#0f1c2e"), colors.HexColor("#0b6fb8"), colors.HexColor("#14202e"), colors.HexColor("#6b7a8f"), colors.HexColor("#cfd7e1"), colors.HexColor("#f4f7fa")
OKC, WARNC, FAILC, NAC = colors.HexColor("#1a7f4b"), colors.HexColor("#a86400"), colors.HexColor("#b3261e"), colors.HexColor("#5b6675")
OKBG, WARNBG, FAILBG, NABG = colors.HexColor("#e4f5ec"), colors.HexColor("#fff2dc"), colors.HexColor("#fde8e6"), colors.HexColor("#eceff3")
STATUS = {"PASS": (OKC, OKBG), "WARNING": (WARNC, WARNBG), "FAIL": (FAILC, FAILBG), "N/A": (NAC, NABG)}

S = {
    "body": ParagraphStyle("body", fontName="DV", fontSize=8.4, leading=11.6, textColor=INK, spaceAfter=4),
    "small": ParagraphStyle("small", fontName="DV", fontSize=7.2, leading=9.2, textColor=INK, spaceBefore=3, spaceAfter=2),
    "formula": ParagraphStyle("formula", fontName="DV", fontSize=7.8, leading=12.5, textColor=INK, spaceBefore=2, spaceAfter=6),
    "cell": ParagraphStyle("cell", fontName="DV", fontSize=7.3, leading=9.0, textColor=INK),
    "cellb": ParagraphStyle("cellb", fontName="DV-B", fontSize=7.3, leading=9.0, textColor=INK),
    "head": ParagraphStyle("head", fontName="DV-B", fontSize=7.3, leading=9.0, textColor=colors.white),
    "mono": ParagraphStyle("mono", fontName="DV-M", fontSize=6.9, leading=8.6, textColor=INK),
    "caption": ParagraphStyle("caption", fontName="DV-I", fontSize=7.2, leading=9, textColor=MUTED, spaceAfter=8, alignment=TA_CENTER),
    "H1": ParagraphStyle("H1", fontName="DV-B", fontSize=14.5, leading=18, textColor=NAVY, spaceBefore=4, spaceAfter=6),
    "H1c": ParagraphStyle("H1c", fontName="DV-B", fontSize=14.5, leading=18, textColor=NAVY, spaceBefore=4, spaceAfter=6),
    "H2": ParagraphStyle("H2", fontName="DV-B", fontSize=10, leading=13, textColor=ACCENT, spaceBefore=9, spaceAfter=3),
    "note": ParagraphStyle("note", fontName="DV", fontSize=7.6, leading=10, textColor=INK, backColor=colors.HexColor("#eef4fb"), borderPadding=(4, 5, 4, 5), spaceBefore=8, spaceAfter=9),
    "warn": ParagraphStyle("warn", fontName="DV", fontSize=7.6, leading=10, textColor=INK, backColor=WARNBG, borderPadding=(4, 5, 4, 5), spaceBefore=8, spaceAfter=9),
}
FW = 174 * mm          # frame width


def esc(x) -> str:
    return escape(str(x))


def n(v, d=4, unit: str = "") -> str:
    """Compact numeric formatting for tables."""
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "–"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, int):
        return f"{v}{(' ' + unit) if unit else ''}"
    if isinstance(v, str):
        return v
    s = f"{v:.{d}g}"
    return f"{s}{(' ' + unit) if unit else ''}"


def sentence(t) -> str:
    """Escape and terminate with exactly one full stop."""
    t = str(t).strip()
    return esc(t.rstrip(".") + ".") if t else ""


def mathtext(t: str) -> str:
    """Escape plain-text formulas and turn X_sub / X_{sub} into real subscripts."""
    import re
    t = esc(t)
    t = re.sub(r"_\{([^}]+)\}", r"<sub>\1</sub>", t)
    return re.sub(r"\b([A-Za-zΑ-Ωα-ωΔ]{1,3})_([A-Za-z0-9,]+)", r"\1<sub>\2</sub>", t)


def _clip(text: str, limit: int) -> str:
    """Shorten to at most ``limit`` characters at a sentence or word boundary (never mid-word); full text lives in Section 15."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("; "))
    cut = cut[:end + 1] if end > limit * 0.5 else cut[:cut.rfind(" ")].rstrip(",;:")
    return cut + " … (see Section 15)"


def P(text, style="body"):
    return Paragraph(text, S[style])


def cellp(x, style="cell"):
    if isinstance(x, (Paragraph, Image, Table)):
        return x
    return Paragraph(esc(x) if not str(x).startswith("<") else str(x), S[style])


def tbl(rows, widths, header=True, status_cols=(), zebra=True, repeat=True, extra=None, font_rows=None):
    """Styled table. Cells may be plain strings (auto-wrapped) or flowables."""
    data = []
    for ri, r in enumerate(rows):
        data.append([cellp(c, "head" if (header and ri == 0) else "cell") for c in r])
    t = LongTable(data, colWidths=[w * mm if w < 1000 else w for w in widths], repeatRows=1 if (header and repeat) else 0)
    st = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.25, LINE), ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
          ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]
    if header:
        st += [("BACKGROUND", (0, 0), (-1, 0), NAVY)]
    if zebra:
        st += [("ROWBACKGROUNDS", (0, 1 if header else 0), (-1, -1), [colors.white, ZEBRA])]
    for ri, r in enumerate(rows):
        if ri == 0 and header:
            continue
        for ci in status_cols:
            v = str(r[ci]).upper() if ci < len(r) else ""
            if v in STATUS:
                fg, bg = STATUS[v]
                st += [("BACKGROUND", (ci, ri), (ci, ri), bg)]
                data[ri][ci] = Paragraph(f'<font color="{fg.hexval()}"><b>{esc(v)}</b></font>', S["cell"])
    if extra:
        st += extra
    t.setStyle(TableStyle(st))
    return t


def kvt(pairs, widths=(62, 112)):
    return tbl([[f"<b>{esc(k)}</b>", v] for k, v in pairs], widths, header=False)


def fig(png: bytes | None, caption: str, width=FW, max_h=None, heading: str | None = None):
    """Figure + caption (+ optional level-2 heading) kept together on one page."""
    if not png:
        return []
    from PIL import Image as PILImage
    im = PILImage.open(io.BytesIO(png))
    w, h = im.size
    ww = width
    hh = ww * h / w
    if max_h and hh > max_h:
        hh, ww = max_h, max_h * w / h
    items = ([Paragraph(esc(heading), S["H2"])] if heading else []) + [Spacer(1, 3), Image(io.BytesIO(png), width=ww, height=hh), Paragraph(esc(caption), S["caption"])]
    return [KeepTogether(items)]


# ------------------------------------------------------------------------------------------------ document
class ReportDoc(BaseDocTemplate):
    def __init__(self, buf, meta, project, **kw):
        super().__init__(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=22 * mm, bottomMargin=18 * mm,
                         title=f"Thermal analysis report - {project.name}", author=project.engineer or "Battery Thermal Studio", **kw)
        self.meta, self.project = meta, project
        cover = Frame(18 * mm, 18 * mm, FW, A4[1] - 18 * mm - 118 * mm, id="cover", leftPadding=0, rightPadding=0)
        body = Frame(18 * mm, 18 * mm, FW, A4[1] - 40 * mm, id="body", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        self.addPageTemplates([PageTemplate("cover", [cover], onPage=self._cover), PageTemplate("body", [body], onPage=self._page)])
        self._k = 0

    def afterFlowable(self, fl):
        for h in (getattr(fl, "_content", None) or [fl]) if isinstance(fl, KeepTogether) else [fl]:
            if isinstance(h, Paragraph) and h.style.name in ("H1", "H2"):
                level = 0 if h.style.name == "H1" else 1
                text = h.getPlainText()
                self._k += 1
                key = f"h{self._k}"
                self.canv.bookmarkPage(key)
                self.canv.addOutlineEntry(text, key, level=level, closed=level > 0)
                if level == 0:
                    self.notify("TOCEntry", (level, text, self.page))

    def _cover(self, c, doc):
        w, h = A4
        c.saveState()
        c.setFillColor(NAVY)
        c.rect(0, h - 105 * mm, w, 105 * mm, stroke=0, fill=1)
        c.setFillColor(ACCENT)
        c.rect(0, h - 108 * mm, w, 3 * mm, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont("DV", 9)
        c.drawString(18 * mm, h - 20 * mm, "ENGINEERING CONSULTANCY REPORT")
        c.setFont("DV-B", 24)
        c.drawString(18 * mm, h - 45 * mm, "Battery Pack Thermal Analysis")
        c.drawString(18 * mm, h - 56 * mm, "& Cooling-System Sizing")
        c.setFont("DV", 11)
        c.drawString(18 * mm, h - 74 * mm, self.project.name[:90])
        c.setFont("DV", 8.5)
        c.setFillColor(colors.HexColor("#aac4de"))
        c.drawString(18 * mm, h - 88 * mm, f"Report {self.meta['report_id']}  ·  Revision {self.project.revision}  ·  {self.meta['date']}")
        c.setFont("DV", 7)
        c.setFillColor(MUTED)
        c.drawString(18 * mm, 10 * mm, "Confidential - prepared for the named customer. Screening-level analysis; see Section 16 (Limitations).")
        c.restoreState()

    def _page(self, c, doc):
        w, h = A4
        c.saveState()
        c.setStrokeColor(LINE)
        c.setLineWidth(0.5)
        c.line(18 * mm, h - 16 * mm, w - 18 * mm, h - 16 * mm)
        c.line(18 * mm, 14 * mm, w - 18 * mm, 14 * mm)
        c.setFont("DV-B", 7.4)
        c.setFillColor(NAVY)
        c.drawString(18 * mm, h - 13 * mm, self.project.name[:70])
        c.setFont("DV", 7.4)
        c.setFillColor(MUTED)
        c.drawRightString(w - 18 * mm, h - 13 * mm, f"{self.meta['report_id']}  ·  Rev {self.project.revision}")
        c.drawString(18 * mm, 9.5 * mm, "Confidential - Battery pack thermal analysis")
        c.drawRightString(w - 18 * mm, 9.5 * mm, f"Page {doc.page}")
        c.restoreState()


# ------------------------------------------------------------------------------------------------ helpers
_prov = provenance_of


def H1(text):
    return [CondPageBreak(60 * mm), Paragraph(esc(text), S["H1"])]


def H2(text):
    return Paragraph(esc(text), S["H2"])


def _issues_table(issues, codes_prefix=None, only=("error", "warning")):
    rows = [["Severity", "Code", "Message"]]
    for i in issues:
        if i["severity"] in only and (codes_prefix is None or i["code"].startswith(codes_prefix)):
            rows.append([i["severity"].upper(), i["code"], i["message"]])
    if len(rows) == 1:
        return P("No issues.", "small")
    return tbl(rows, [18, 60, 96], status_cols=())


# ------------------------------------------------------------------------------------------------ sections
def _exec_summary(res, req, recs):
    out = [Paragraph("Executive summary", S["H1"])]
    cap, flow = res["sizing"]["capacity"], res["cooling"]["pack"]
    fails = [c for c in res["checks"] if c["status"] == "FAIL"]
    warns = [c for c in res["checks"] if c["status"] == "WARNING" and not c["supplementary"]]
    verdict = ("<b>Design not acceptable as specified</b>: " + f"{len(fails)} check(s) failed." if fails else
               ("<b>Design acceptable with reservations</b>: " + f"{len(warns)} warning(s)." if warns else "<b>Design meets all evaluated checks.</b>"))
    out.append(P(f"{esc(req.project.name)}: {req.pack.ns}S{req.pack.np}P pack of {esc(req.cell.chemistry or 'Li-ion')} cells "
                 f"({res['pack']['n_cells']} cells, {res['pack']['v_nom']:.0f} V, {res['pack']['energy_kwh']:.1f} kWh). "
                 f"Over the analysed duty the pack generates up to {res['heat']['max_pack_heat_kw']:.2f} kW of heat ({res['heat']['avg_pack_heat_kw']:.2f} kW average, "
                 f"{res['heat']['total_heat_kwh']:.3f} kWh in total). Under the <b>{esc(res['design']['label'])}</b> philosophy the cooling system must reject "
                 f"<b>{cap['required_kw']:.2f} kW</b>; with a safety factor of {cap['safety_factor']:g} the recommended capacity is <b>{cap['design_kw']:.2f} kW</b> at "
                 f"<b>{flow['lpm']:.1f} L/min</b> of coolant. {verdict}"))
    kp = []
    for grp, title in (("battery", "Battery"), ("thermal", "Thermal"), ("cooling", "Cooling")):
        for k in res["kpis"][grp]:
            kp.append([title, k["label"], f"{n(k['value'], 4)} {k['unit']}" if k["value"] is not None else "n/a", k.get("sub") or ""])
            title = ""
    out += [H2("Key results"), tbl([["", "Quantity", "Value", "Note"]] + kp, [22, 62, 32, 58])]
    out += [H2("Performance checks"), tbl([["#", "Check", "Status", "Result"]] + [[c["id"], c["name"], c["status"], c["value"]] for c in res["checks"] if not c["supplementary"]],
                                         [10, 84, 22, 58], status_cols=(2,))]
    items = [r for r in recs if r["level"] in ("fail", "warning")][:5]
    if items:
        out += [H2("Principal findings")] + [P(f"• <b>{esc(r['title'])}</b> - {esc(_clip(r['text'], 330))}", "small") for r in items]
    if res["data_quality"]["n_assumed"]:
        out.append(P(f"Data quality: {res['data_quality']['n_assumed']} parameters are unconfirmed engineering assumptions ({res['data_quality']['n_low']} low confidence) - see Section 6.", "warn"))
    return out


def _sec1(req, meta):
    p = req.project
    out = H1("1  Customer & project information")
    out.append(kvt([("Project", p.name), ("Customer", p.customer or "–"), ("Project number", p.project_no or "–"), ("Engineer", p.engineer or "–"),
                    ("Report number", meta["report_id"]), ("Revision", p.revision), ("Date", meta["date"]), ("Tool", "Battery Thermal Studio (calculation engine v0.1)")]))
    if p.notes:
        out.append(P(esc(p.notes), "note"))
    out += [H2("Document control"), tbl([["Rev", "Date", "Description", "Author"], [p.revision, meta["date"], "Automated analysis report", p.engineer or "–"]], [14, 34, 96, 30])]
    return out


def _sec2(req, res):
    c = req.cell
    rows = [["Parameter", "Value", "Unit", "Source", "Confidence"]]
    for label, val, unit, path in cell_spec(c):
        src, conf = _prov(req, path) if path else ("Datasheet / user", "–")
        rows.append([label, n(val, 5), unit, src, conf])
    tables = cell_tables(c)
    out = H1("2  Cell information")
    out.append(P("All values below were reviewed and confirmed by the user before calculation (datasheet extraction is a proposal only). Source and confidence follow the assumptions register (Section 6)."))
    out.append(tbl(rows, [46, 34, 22, 40, 32]))
    out.append(P("Curves / maps available: " + (", ".join(tables) if tables else "none - only scalar data were provided."), "small"))
    m = res["models"]["resistance"]
    out.append(P(f"<b>Resistance model used:</b> {sentence(m['name'])} {sentence(m['description'])} Extrapolation policy: {sentence(m['extrapolation_policy'])} "
                 f"<b>OCV model:</b> {sentence(res['models']['ocv']['description'])} <b>Entropic heat:</b> {sentence(res['models']['entropic']['status'])}", "note"))
    out += fig(charts.fig_cell_curves(c.model_dump(mode="json")), "Figure 2.1 - Cell characteristic curves used by the model")
    return out


def _sec3(req, res):
    pk, d, h = req.pack, res["pack"], res["heat"]
    out = H1("3  Battery configuration")
    out.append(tbl([["Item", "Value"],
                    ["Series × parallel (Ns × Np)", f"{pk.ns} × {pk.np}  →  {d['n_cells']} cells"], ["Modules", f"{d['n_modules']} × {d['cells_per_module']} cells ({d['modules_in_series']} in series × {d['modules_in_parallel']} in parallel)"],
                    ["Per module", f"{d['ns_per_module']}S{d['np_per_module']}P"], ["Nominal / max / min pack voltage", f"{n(d['v_nom'])} V / {n(d['v_max'])} V / {n(d['v_min'])} V"],
                    ["Pack capacity", f"{n(d['capacity_ah'])} Ah"], ["Pack energy", f"{n(d['energy_kwh'])} kWh"],
                    ["SOC initial / window", f"{pk.soc_initial_pct:g} % / {pk.soc_min_pct:g}-{pk.soc_max_pct:g} %"], ["Initial / target max / ambient temperature", f"{pk.t_initial_c:g} / {pk.t_target_max_c:g} / {pk.t_ambient_c:g} °C"],
                    ["Target cell-to-cell ΔT", f"{pk.target_delta_t_k:g} K"],
                    ["Peak currents (pack / module / cell)", f"{n(h['max_pack_current_a'])} A / {n(h['max_module_current_a'])} A / {n(h['max_cell_current_a'])} A"],
                    ["Maximum C-rate (discharge / charge)", f"{n(h['max_discharge_c'], 3)} C / {n(h['max_charge_c'], 3)} C"]], [70, 104]))
    out.append(H2("Configuration validation"))
    out.append(_issues_table(res["issues"], None, ("error", "warning")) if any(i["code"].startswith(("PACK", "SOC", "TEMP", "TARGET")) for i in res["issues"]) else P("The configuration is consistent: cell count, module topology and (where stated) pack voltage, capacity and energy agree with the cell data.", "small"))
    return out


def _sec4(req, res):
    out = H1("4  Driving-cycle information")
    L, m = res.get("load"), res["models"]["load"]
    rows = [["Item", "Value"], ["Load source used", f"{m['source']} ({'battery power' if m['kind'] == 'power' else 'pack current'})"], ["Notes", " ".join(m["notes"])]]
    if req.cycle is not None:
        t = req.cycle.time_s
        rows += [["Samples / duration", f"{len(t)} / {n(t[-1] - t[0], 6)} s"], ["Median time step", f"{n(sorted(b - a for a, b in zip(t, t[1:]))[len(t) // 2 - 1], 4)} s"],
                 ["Signals in file", ", ".join(k for k in ("speed_kmh", "accel_ms2", "motor_power_kw", "battery_power_kw", "battery_current_a", "soc_pct", "grade_pct") if getattr(req.cycle, k) is not None)],
                 ["Cycle repeats", str(m["repeats"])]]
    if L and L.get("energy"):
        e = L["energy"]
        rows.append(["Battery energy (discharge / regen / net)", f"{n(e['discharge_kwh'])} / {n(e['regen_kwh'])} / {n(e['net_kwh'])} kWh"])
        if e.get("aux_kwh") is not None:
            rows.append(["Auxiliary energy", f"{n(e['aux_kwh'])} kWh"])
    out.append(tbl(rows, [50, 124]))
    if req.vehicle is not None and m["source"] == "vehicle_speed":
        v = req.vehicle
        out.append(H2("Vehicle parameters (road-load model)"))
        out.append(P("F<sub>tractive</sub> = F<sub>acc</sub> + F<sub>roll</sub> + F<sub>aero</sub> + F<sub>grade</sub>;  P<sub>wheel</sub> = F·v;  P<sub>batt</sub> = P<sub>wheel</sub>/η (discharge) or P<sub>wheel</sub>·η·f<sub>regen</sub> (braking), plus auxiliaries.", "formula"))
        rows = [["Parameter", "Value", "Source", "Confidence"]]
        for lab, key, unit in VEHICLE_FIELDS:
            if getattr(v, key) is None:
                continue
            s_, cf = _prov(req, f"vehicle.{key}")
            rows.append([lab, f"{n(getattr(v, key), 5)} {unit}", s_, cf])
        out.append(tbl(rows, [60, 40, 40, 34]))
    lim = req.crate_limits
    rows = [["C-rate limit", "Value"]] + [[f"{lab} [{unit}]", n(getattr(lim, k))] for lab, k, unit in CRATE_FIELDS if getattr(lim, k) is not None]
    if len(rows) > 1:
        out += [H2("Charge / discharge C-rate definition"), tbl(rows, [80, 40])]
    out += fig(charts.fig_cycle(L), "Figure 4.1 - Load profile used in the analysis")
    return out


def _sec5(req):
    out = H1("5  Input data")
    out.append(P("Complete list of thermal, cooling and model inputs. Values not entered by the user are engineering defaults and appear in the assumptions register (Section 6)."))

    def block(title, obj, prefix, skip=()):
        rows = [["Parameter", "Value", "Unit", "Source", "Confidence"]]
        for k, v in obj.model_dump(mode="json").items():
            if v is None or k in skip:
                continue
            path = f"{prefix}.{k}"
            src, conf = _prov(req, path)
            label, unit = field_label(path)
            rows.append([label, n(value_text(path, v), 6), unit, src, conf])
        return [H2(title), tbl(rows, [68, 44, 17, 25, 20])] if len(rows) > 1 else []
    out += block("Heat / resistance model", req.resistance, "resistance") + block("Entropic model", req.entropic, "entropic") + block("Thermal model & design philosophy", req.thermal, "thermal")
    out += block("Coolant", req.coolant, "coolant")
    if req.cold_plate is not None:
        out += block("Cold plate", req.cold_plate, "cold_plate")
    out += block("Pump", req.pump, "pump") + block("Radiator assumptions", req.radiator, "radiator") + block("Margins and check limits", req.limits, "limits")
    if req.installed_cooling_capacity_kw is not None:
        out.append(P(f"Installed cooling capacity specified: {req.installed_cooling_capacity_kw:g} kW", "small"))
    return out


def _sec6(res):
    out = H1("6  Assumptions & data quality")
    rows_all = res["assumptions"]
    cnt: dict[str, int] = {}
    for r in rows_all:
        cnt[r["source_class"]] = cnt.get(r["source_class"], 0) + 1
    out.append(P("Every parameter that influences a result is listed with its source class and confidence. <b>Assumed</b> values are engineering defaults that have not been confirmed by the user and must be verified before design release. "
                 + "Counts: " + ", ".join(f"{k} {v}" for k, v in cnt.items()) + f"; low confidence: {sum(1 for r in rows_all if r['confidence'] == 'Low')}."))
    conf_rank = {"Low": 0, "Medium": 1, "High": 2}
    groups = [("6.1  Unconfirmed engineering assumptions - verify before design release", lambda r: r["source_class"] == "Assumed"),
              ("6.2  Customer, datasheet and user-entered inputs", lambda r: r["source_class"] not in ("Assumed", "Calculated")),
              ("6.3  Calculated (derived) parameters", lambda r: r["source_class"] == "Calculated")]
    for title, sel in groups:
        rows_sel = sorted((r for r in rows_all if sel(r)), key=lambda r: (conf_rank.get(r["confidence"], 1), r["group"], r["parameter"]))
        if not rows_sel:
            continue
        rows = [["Parameter", "Value", "Unit", "Source class", "Confidence", "Source / basis"]]
        extra = []
        for i, r in enumerate(rows_sel, start=1):
            rows.append([r["parameter"], n(r["value"], 5), r["unit"], r["source_class"], r["confidence"], r["source"]])
            col = {"Assumed": WARNBG, "Datasheet": colors.HexColor("#e8f0fb"), "User-provided": colors.HexColor("#e8f0fb"), "Calculated": NABG}.get(r["source_class"])
            if col:
                extra.append(("BACKGROUND", (3, i), (3, i), col))
            extra.append(("BACKGROUND", (4, i), (4, i), {"Low": FAILBG, "Medium": WARNBG}.get(r["confidence"], OKBG)))
        out += [H2(title), tbl(rows, [46, 21, 16, 22, 21, 48], zebra=False, extra=extra)]
    return out


def _sec7():
    out = H1("7  Calculation methodology")
    for title, paras in METHODOLOGY:
        out.append(H2(title))
        out += [P(mathtext(t)) for t in paras]
    out.append(P("Sign convention: current and battery power are positive for discharge. Energy integrals use sample-and-hold: the quantity at sample k applies over [t<sub>k</sub>, t<sub>k+1</sub>) and the last sample has zero duration.", "note"))
    return out


def _sec8(res, req):
    out = H1("8  Heat-generation calculation")
    m = res["models"]
    out.append(kvt([("Resistance model", m["resistance"]["name"]), ("Model description", m["resistance"]["description"]), ("Entropic heat", m["entropic"]["status"]),
                    ("OCV model", m["ocv"]["description"]), ("SOC source", m["soc_source"]), ("Time integration", m["time_integration"])]))
    tr = res["trace"]
    steps = ["in.i_pack_pk", "heat.i_cell_pk", "heat.r_pk", "heat.q_joule_pk", "heat.q_rev_pk", "heat.q_cell_pk", "heat.q_module_pk", "heat.q_pack_pk"]
    rows = [["Step", "Formula", "Substitution", "Result"]]
    for sid in steps:
        nd = tr.get(sid)
        if nd:
            rows.append([nd["label"], nd["formula"] or "input", (nd["substitution"] or "").replace("+ -", "− "), f"{n(nd['value'], 5)} {nd['unit']}"])
    out += [H2("Worked example at the instant of maximum pack heat"), tbl(rows, [40, 46, 52, 36]),
            P(f"Electrical energy is not heat: {esc(res['explanations']['electrical_vs_heat'])}", "note")]
    if res["series"].get("t"):
        out += fig(charts.fig_electrical(res), "Figure 8.1 - Electrical quantities over the duty (Graphs 1, 2, 3 and 7)")
        out += fig(charts.fig_heat(res), "Figure 8.2 - Heat generation and thermal accumulation (Graphs 4, 5, 6)")
        s = res["series"]
        nt = len(s["t"])
        pk = max(range(nt), key=lambda i: s["q_pack_kw"][i])
        idx = sorted({0} | set(range(max(0, pk - 4), min(nt, pk + 5))))
        rows = [["t [s]", "SOC [%]", "I_pack [A]", "C-rate [C]", "R [mΩ]", "Q_joule [W]", "Q_rev [W]", "Q_cell [W]", "Q_pack [kW]", "P_batt [kW]", "State"]]
        for i in idx:
            r = [n(s["t"][i], 5), n(s["soc_pct"][i], 4), n(s["i_pack_a"][i], 4), n(s["c_rate"][i], 3), n(s["r_cell_mohm"][i], 4), n(s["q_joule_cell_w"][i], 4), n(s["q_rev_cell_w"][i], 3),
                 n(s["q_cell_w"][i], 4), n(s["q_pack_kw"][i], 4), n(s["p_batt_kw"][i], 4), s["state"][i]]
            rows.append([f"<b>{esc(c)}</b>" for c in r] if i == pk else r)
        out += [KeepTogether([H2("Time-step results (extract - full series in the Excel workbook)"), tbl(rows, [13, 14, 16, 14, 15, 17, 17, 17, 17, 17, 17]),
                              P("The extract shows the first sample and the samples around the instant of maximum pack heat (bold). The complete per-step results (time, SOC, current, C-rate, resistance, "
                                "Joule / entropic / total heat, module and pack heat, battery power, state) are in the Excel report.", "small")])]
    return out


def _sec9(res):
    H, D = res["heat"], res["design"]
    out = H1("9  Thermal-load results")
    out.append(tbl([["", "Per cell", "Per module", "Pack"],
                    ["Maximum (instantaneous)", f"{n(H['max_cell_heat_w'])} W", f"{n(H['max_module_heat_w'])} W", f"{n(H['max_pack_heat_kw'])} kW  (t = {n(H['t_max_pack_heat_s'], 5)} s)"],
                    ["Average (time-weighted)", f"{n(H['avg_cell_heat_w'])} W", f"{n(H['avg_module_heat_w'])} W", f"{n(H['avg_pack_heat_kw'])} kW"],
                    ["Total heat generated over the cycle", "", "", f"{n(H['total_heat_kwh'], 5)} kWh  (Joule {n(H['joule_heat_kwh'], 4)}, reversible {n(H['reversible_heat_kwh'], 3)})"]], [52, 28, 28, 66]))
    out.append(H2("Design heat load"))
    c = D["candidates"]
    names = {"peak": "Peak heat load", "moving_average": "Moving-average heat load", "sustained": "Sustained heat load", "drive_cycle": "Drive-cycle thermal load"}
    rows = [["Philosophy", "Q [kW]", "Basis"]] + [[names[k] + (" ◀ selected" if k == D["philosophy"] else ""), n(c[k]["value_w"] / 1000, 4) if c[k]["available"] else "n/a",
                                                    c[k].get("substitution") or c[k].get("note", "")] for k in names]
    out.append(tbl(rows, [52, 22, 100]))
    out.append(P(f"<b>Q<sub>design</sub> = (Q<sub>relevant</sub> + Q<sub>ambient</sub>) × SF = ({n(D['q_relevant_w'] / 1e3, 4)} + {n(D['q_ambient_gain_w'] / 1e3, 3)}) × {D['safety_factor']:g} = {n(D['q_design_w'] / 1e3, 4)} kW</b>", "formula"))
    out += fig(charts.fig_design_levels(res), "Figure 9.1 - Design heat-load candidates")
    out += [P(f"<b>{esc(D['label'])}:</b> {esc(D['explanation'])}", "note"), H2("Peak thermal load versus sustained cooling requirement"), P(esc(D["peak_vs_sustained"]))]
    return out


def _sec10(res):
    C = res["cooling"]
    p = C["props"]
    out = H1("10  Cooling requirement")
    out.append(P(f"Required coolant flow from ṁ = Q/(c<sub>p</sub>·ΔT) with ΔT = {C['dt_k']:.2f} K ({esc(C['dt_basis'])}); properties at the mean coolant temperature {p['t_eval_c']:.1f} °C: "
                 f"{esc(p['description'])}."))
    out.append(tbl([["Level", "Design heat [W]", "Mass flow [kg/s]", "Volume flow [L/min]"]] + [[l.capitalize(), n(C[l]["q_w"], 5), n(C[l]["m_dot_kg_s"], 5), n(C[l]["lpm"], 5)] for l in ("cell", "module", "pack")], [40, 40, 47, 47]))
    out.append(H2("Coolant properties"))
    out.append(tbl([["Property", "Value", "Basis"], ["Density ρ", f"{n(p['rho'], 5)} kg/m³", "overrides: " + (", ".join(p["overrides"]) or "none")], ["Specific heat cp", f"{n(p['cp'], 5)} J/(kg·K)", ""],
                    ["Conductivity k", f"{n(p['k'], 4)} W/(m·K)", ""], ["Dynamic viscosity μ", f"{n(p['mu'] * 1000, 4)} mPa·s", ""], ["Prandtl number", n(p["pr"], 4), ""]], [50, 50, 74]))
    return out


def _sec11(res, req):
    S_, cap = res["sizing"], res["sizing"]["capacity"]
    out = H1("11  Cooling-system sizing")
    rows = [["Item", "Value"], ["Required cooling capacity", f"{n(cap['required_kw'], 4)} kW  ({cap['philosophy']})"], ["Design capacity = required × SF", f"{n(cap['required_kw'], 4)} × {cap['safety_factor']:g} = {n(cap['design_kw'], 4)} kW"],
            ["Recommended installed capacity", f"≥ {n(cap['recommended_kw'], 3)} kW"], ["Required coolant flow", f"{n(S_['flow']['required_lpm'], 4)} L/min at ΔT = {S_['flow']['dt_cool_k']:.2f} K"]]
    if S_["flow"].get("actual_lpm") is not None:
        rows.append(["Flow used for plate / hydraulics", f"{n(S_['flow']['actual_lpm'], 4)} L/min ({S_['flow']['flow_source']})"])
    if S_.get("inlet_temperature"):
        it = S_["inlet_temperature"]
        rows.append(["Coolant inlet temperature", f"specified {it['specified_c']:g} °C; maximum permissible at the design load {n(it['t_in_max_c'], 4)} °C → recommended ≤ {n(it['t_in_recommended_c'], 3)} °C"])
    out.append(tbl(rows, [62, 112]))
    if S_.get("pump"):
        p, r = S_["pump"], S_["radiator"]
        out += [H2("Pump"), tbl([["Item", "Value"], ["Flow", f"{n(p['flow_lpm'], 4)} L/min"], ["Pressure", f"{n(p['dp_bar'], 3)} bar ({n(p['dp_kpa'], 4)} kPa)"], ["Hydraulic power", f"{n(p['p_hydraulic_w'], 4)} W"],
                                 ["Estimated electrical power", f"{n(p['p_electrical_w'], 4)} W"], ["Suggested duty point", f"{n(p['duty_flow_lpm'], 4)} L/min at {n(p['duty_dp_bar'], 3)} bar"]], [62, 112]),
                P(esc(p["allowances"]), "small")]
        out += [H2("Heat exchanger / radiator (indicative)"),
                tbl([["Item", "Value"], ["Required heat rejection capacity", f"{n(r['q_reject_w'] / 1000, 4)} kW (design load + pump heat)"], ["Coolant-side flow", f"{n(r['coolant_flow_lpm'], 4)} L/min"],
                     ["Coolant into / out of radiator", f"{n(r['coolant_inlet_to_radiator_c'], 3)} / {n(r['coolant_outlet_from_radiator_c'], 3)} °C"], ["Ambient air / ITD", f"{n(r['air_inlet_c'])} °C / {n(r['itd_k'], 3)} K"],
                     ["Required radiator capacity per kelvin of ITD", f"{n(r['ua_required_w_k'], 4)} W/K" if r["ua_required_w_k"] is not None else "n/a (no positive temperature difference)"],
                     ["Air-side assumption", f"air temperature rise {n(r['air_dt_assumed_k'])} K → {n(r['air_volume_flow_m3_s'], 3)} m³/s"]], [62, 112])]
        out += [P(esc(t), "warn") for t in r["notes"]]
    th = res["thermal"]
    if th["has_temperature"]:
        out += [H2("Thermal accumulation"), P(f"Pack thermal capacity C = {n(th['c_pack_j_k'], 5)} J/K; thermal time constant τ = C/(G<sub>c</sub>+G<sub>a</sub>) = {n(th['tau_s'], 4)} s; pack-to-ambient conductance "
                                                f"{n(th['ambient_ua_w_k'], 3)} W/K ({esc(th['ambient_ua_source'])}). Without active cooling the pack would reach {n(th['t_max_uncooled_c'], 4)} °C.")]
    return out


def _sec12(res):
    out = H1("12  Pressure-drop calculation")
    cp, hy = res.get("cold_plate"), res.get("hydraulics")
    if not cp:
        return out + [P("No cold plate was defined, so no channel hydraulics were calculated.", "warn")]
    out.append(P("Channel pressure drop from the Darcy-Weisbach relation with a Shah-London (laminar) or Haaland (turbulent) friction factor, plus minor losses and the external loop; "
                 "the pump duty follows from ΔP·V̇ and the pump efficiency. The thermal side of the same channel calculation (convection coefficient, resistance chain) is in Section 13."))
    out.append(H2("Channel hydraulics"))
    out.append(tbl([["Quantity", "Value"], ["Flow velocity", f"{n(hy['velocity_m_s'], 4)} m/s"], ["Hydraulic diameter", f"{n(hy['dh_m'] * 1000, 4)} mm"], ["Reynolds number / regime", f"{n(hy['reynolds'], 5)} ({hy['regime']})"],
                    ["Darcy friction factor", n(hy["f_darcy"], 4)], ["Channel pressure drop", f"{n(hy['dp_channel_pa'] / 1000, 4)} kPa"], ["Minor losses", f"{n(hy['dp_minor_pa'] / 1000, 4)} kPa"],
                    ["Cold-plate pressure drop", f"{n(hy['dp_plates_total_pa'] / 1000, 4)} kPa"], ["External loop pressure drop", f"{n(hy['dp_external_pa'] / 1000, 4)} kPa"],
                    ["<b>Total pressure drop</b>", f"<b>{n(hy['dp_total_pa'] / 1e5, 4)} bar</b>"], ["Pack flow", f"{n(hy['q_pack_lpm'], 4)} L/min"], ["Hydraulic / electrical pump power", f"{n(hy['p_hyd_w'], 4)} W / {n(hy['p_elec_w'], 4)} W"]], [70, 104]))
    fl = [[f["severity"].upper(), f["code"], f["message"]] for f in hy["flags"]]
    if fl:
        out += [H2("Hydraulic flags"), tbl([["Severity", "Code", "Message"]] + fl, [18, 52, 104])]
    out += [P(esc(w), "warn") for w in cp["warnings"]]
    return out


def _resistance_block(res):
    cp = res.get("cold_plate")
    if not cp:
        return []
    tot = cp["r_total"]
    out = [H2("Cold-plate thermal resistance chain (per cell)")]
    out.append(tbl([["Element", "R [K/W]", "Share"], ["Cell contact interface", n(cp["r_contact"], 4), f"{cp['r_contact'] / tot * 100:.1f} %"], ["TIM", n(cp["r_tim"], 4), f"{cp['r_tim'] / tot * 100:.1f} %"],
                    ["Plate conduction", n(cp["r_plate"], 4), f"{cp['r_plate'] / tot * 100:.1f} %"], ["Coolant convection", n(cp["r_conv"], 4), f"{cp['r_conv'] / tot * 100:.1f} %"],
                    ["<b>Overall R<sub>total</sub></b>", f"<b>{n(tot, 4)}</b>", "100 %"]], [70, 50, 54]))
    out += fig(charts.fig_resistance_chain(res), "Figure 13.1 - Thermal resistance chain")
    out.append(tbl([["Quantity", "Value"], ["Convective coefficient h", f"{n(cp['h_w_m2k'], 4)} W/(m²·K)  (Nu = {n(cp['nusselt'], 4)})"], ["Overall U (cell contact area)", f"{n(cp['u_cell_w_m2k'], 4)} W/(m²·K)"],
                    ["Overall U (plate footprint)", f"{n(cp['u_plate_w_m2k'], 4)} W/(m²·K)"], ["NTU / effectiveness", f"{n(cp['ntu'], 3)} / {n(cp['effectiveness'], 3)}"],
                    ["Cell-to-coolant ΔT at the design heat", f"{n(res['design']['q_design_w'] / res['pack']['n_cells'] * tot, 3)} K"]], [70, 104]))
    out.append(P("<b>Thermal resistance vs overall heat-transfer coefficient.</b> R [K/W] is the absolute temperature rise per watt through a specific part of a specific geometry; resistances in series add. "
                 "U [W/(m²·K)] = 1/(R·A<sub>ref</sub>) normalises the same resistance by a reference area, so it depends on the area chosen (here the cell contact area or the plate footprint) and is used to compare layers and technologies independent of size.", "note"))
    return out


def _sec13(res, req):
    out = H1("13  Thermal performance")
    out.append(P("Each check is reported as PASS / WARNING / FAIL. Failed and not-evaluable checks are never hidden."))
    core = [c for c in res["checks"] if not c["supplementary"]]
    out.append(tbl([["#", "Check", "Status", "Predicted / actual", "Limit", "Assessment"]] + [[c["id"], c["name"], c["status"], c["value"], c["limit"], c["message"]] for c in core], [8, 32, 16, 28, 26, 64], status_cols=(2,)))
    supp = [c for c in res["checks"] if c["supplementary"]]
    if supp:
        out += [H2("Supplementary checks"), tbl([["#", "Check", "Status", "Value", "Assessment"]] + [[c["id"], c["name"], c["status"], c["value"], c["message"]] for c in supp], [8, 38, 16, 30, 82], status_cols=(2,))]
    out += _resistance_block(res)
    out += fig(charts.fig_temperature(res, req.pack.t_target_max_c), "Figure 13.2 - Predicted temperatures", heading="Predicted temperatures")
    m = res["margins"]
    rows = [["Margin", "Value", "Classification"]]
    if m["cooling"].get("available"):
        rows.append(["Cooling margin = installed / required", f"{n(m['cooling']['margin_pct'], 4)} % (ratio {n(m['cooling']['ratio'], 3)})",
                     f"{m['cooling']['class']} (warning < {m['cooling']['warn_pct']:g} %, target ≥ {m['cooling']['target_pct']:g} %)"])
    if m["thermal"].get("available"):
        rows.append(["Thermal margin = allowed − predicted maximum T", f"{n(m['thermal']['margin_k'], 3)} K", f"{m['thermal']['class']} (warning < {m['thermal']['warn_k']:g} K)"])
    if len(rows) > 1:
        out += [H2("Design margin"), tbl(rows, [70, 46, 58])]
    return out


def _sec14(sens):
    out = H1("14  Sensitivity analysis")
    if not sens or not sens.get("ok"):
        return out + [P("The sensitivity analysis was not included in this report." if not sens else "The sensitivity analysis could not be run for this case.", "note")]
    out.append(P("One-at-a-time perturbation of each parameter with the complete analysis re-run. Effects on the maximum heat generation, the required cooling capacity, the required coolant flow and the maximum cell temperature are shown."))
    out += fig(charts.fig_tornado(sens), "Figure 14.1 - Tornado charts (change from the base case)")
    outs = sens["outputs"]
    rows = [["Parameter", "Change"] + [f"{o['label']} [{o['unit']}]" for o in outs]]
    rows.append(["<b>Base case</b>", ""] + [n(sens["base"][o["key"]], 4) for o in outs])
    def cell_text(o, lv):
        v = lv["metrics"].get(o["key"])
        if v is None:
            return "–"
        if o["unit"] == "°C":                      # a percentage of a Celsius temperature is meaningless: show the absolute change
            d = (lv.get("delta") or {}).get(o["key"]) or 0.0
            return f"{n(v, 4)} ({'±0' if abs(d) < 0.005 else format(d, '+.2g')} K)"
        d = lv["delta_pct"].get(o["key"]) or 0.0
        return f"{n(v, 4)} ({'±0' if abs(d) < 0.005 else format(d, '+.2g')} %)"

    for p in sens["params"]:
        if p.get("skipped"):
            rows.append([p["label"], f"skipped: {p['skipped']}", "", "", "", ""])
            continue
        for i, lv in enumerate(p["levels"]):
            cells = [cell_text(o, lv) for o in outs] if lv["status"] == "ok" else [f"{lv['status']}: {lv.get('reason', '')}"[:60], "", "", ""]
            rows.append([p["label"] if i == 0 else "", lv["label"]] + cells)
    out += [H2("Results by parameter"), tbl(rows, [30, 16, 32, 32, 32, 32])]
    return out


def _sec15(recs):
    out = H1("15  Engineering recommendations")
    colr = {"fail": (FAILC, FAILBG, "ACTION REQUIRED"), "warning": (WARNC, WARNBG, "ATTENTION"), "info": (ACCENT, colors.HexColor("#eef4fb"), "NOTE")}
    for r in recs:
        fg, bg, tag = colr[r["level"]]
        out.append(KeepTogether([Table([[Paragraph(f'<font color="{fg.hexval()}"><b>{tag}</b></font>', S["cell"]), Paragraph(f"<b>{esc(r['title'])}</b><br/>{esc(r['text'])}", S["cell"])]],
                                       colWidths=[28 * mm, FW - 28 * mm], style=TableStyle([("BACKGROUND", (0, 0), (0, 0), bg), ("BOX", (0, 0), (-1, -1), 0.25, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                                                                             ("LEFTPADDING", (0, 0), (-1, -1), 4), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)])),
                                 Spacer(1, 3)]))
    return out


def _sec16():
    return H1("16  Limitations") + [P(f"{i}. {esc(t)}") for i, t in enumerate(LIMITATIONS, 1)]


def _sec17(res):
    out = H1("17  Complete calculation traceability")
    out.append(P("Every important output can be traced Input → Formula → Intermediate calculation → Final result. The table lists each recorded quantity with its formula, the numbers substituted and the quantities it depends on (IDs). "
                 "Kinds: INPUT (user / datasheet), ASSUMPTION (unconfirmed engineering value), INTERMEDIATE, RESULT."))
    tr = res["trace"]
    order = {"input": 0, "assumption": 1, "intermediate": 2, "result": 3}
    nodes = sorted(tr.values(), key=lambda x: (order.get(x["kind"], 2)))
    rows = [["ID", "Quantity", "Value", "Kind / source", "Formula & substitution", "Depends on"]]
    for nd in nodes:
        val = "–" if nd["value"] is None else f"{n(nd['value'], 5)} {nd['unit'] if nd['unit'] != '-' else ''}".strip()
        fs = (f"<font name='DV-M'>{esc(nd['formula'])}</font>" if nd["formula"] else "") + (f"<br/>= {esc(nd['substitution'])}" if nd["substitution"] else "")
        src = f"<b>{esc(nd['kind'].upper())}</b>" + (f"<br/>{esc(nd['source'])}" if nd.get("source") else "")
        rows.append([Paragraph(f"<font name='DV-M' size='6.2'>{esc(nd['id'])}</font>", S["cell"]), nd["label"], val, Paragraph(src, S["cell"]), Paragraph(fs or "", S["cell"]),
                     Paragraph(f"<font size='6.2'>{esc(', '.join(nd['inputs']))}</font>", S["cell"])])
    out.append(tbl(rows, [30, 30, 22, 25, 42, 25]))
    return out


# ------------------------------------------------------------------------------------------------ entry point
def _protect_headings(story: list) -> list:
    """A level-2 heading is never left alone at the bottom of a page: require room for the heading plus a few table rows."""
    out: list = []
    for f in story:
        if isinstance(f, Paragraph) and f.style.name == "H2":
            out.append(CondPageBreak(34 * mm))
        out.append(f)
    return out


_BUILD_LOCK = threading.Lock()          # matplotlib's pyplot state is process-global: one report at a time


def build_pdf(req: AnalysisRequest, res: dict, sens: dict | None = None) -> bytes:
    if res.get("status") == "blocked":
        raise ValueError("Cannot build a report for a blocked analysis")
    with _BUILD_LOCK:
        return _build_pdf(req, res, sens)


def _build_pdf(req: AnalysisRequest, res: dict, sens: dict | None) -> bytes:
    meta = report_meta(req, res)
    recs = recommendations(res, req)
    buf = io.BytesIO()
    doc = ReportDoc(buf, meta, req.project)
    p = req.project
    story: list = [Spacer(1, 4 * mm)]
    story.append(tbl([["Customer", p.customer or "–"], ["Project number", p.project_no or "–"], ["Prepared by", p.engineer or "–"], ["Cell", f"{req.cell.name or ''} {req.cell.chemistry or ''} {req.cell.capacity_ah or ''} Ah".strip()],
                      ["Pack", f"{req.pack.ns}S{req.pack.np}P - {res['pack']['v_nom']:.0f} V, {res['pack']['energy_kwh']:.1f} kWh"], ["Design philosophy", res["design"]["label"]]],
                     [40, 134], header=False))
    story += [Spacer(1, 6 * mm), P("This report documents the heat-generation, transient thermal and cooling-system sizing analysis of the battery pack described above. "
                                   "All results are traceable to their inputs (Section 17); unconfirmed engineering assumptions are listed in Section 6.", "small")]
    story += [NextPageTemplate("body"), PageBreak()]
    toc = TableOfContents()
    toc.levelStyles = [ParagraphStyle("toc0", fontName="DV", fontSize=9, leading=15, leftIndent=0, textColor=INK)]
    story += [Paragraph("Contents", S["H1c"]), toc, PageBreak()]
    story += _exec_summary(res, req, recs) + [PageBreak()]
    story += _sec1(req, meta) + _sec2(req, res) + _sec3(req, res) + _sec4(req, res) + _sec5(req) + _sec6(res) + _sec7() + _sec8(res, req) + _sec9(res) + _sec10(res) + _sec11(res, req)
    story += _sec12(res) + _sec13(res, req) + _sec14(sens) + _sec15(recs) + _sec16() + _sec17(res)
    doc.multiBuild(_protect_headings(story))
    return buf.getvalue()
