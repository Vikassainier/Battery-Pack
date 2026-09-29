"""Engineering workbook (Excel).

Sheets: Summary · Inputs · Assumptions · Heat results · Timeseries · Charts · Cooling · Checks · Sensitivity · Traceability ·
Hand calcs · Notes.

* Every per-time-step result is included (the dashboard series is thinned; the workbook is built from a full-resolution run).
* The *Traceability* sheet links every quantity to the quantities it depends on (clickable).
* The *Hand calcs* sheet re-derives the key numbers with **live Excel formulas** next to the engine value, so a reviewer can change an
  input and watch the chain recompute - and the built-in validation case (120S1P, 200 A → 40 W / 4.8 kW) is there in every workbook.
* Text taken from user data is always stored as text (never interpreted as a formula).
"""
from __future__ import annotations

import io
import math
from datetime import datetime

from openpyxl import Workbook
from openpyxl.chart import Reference, ScatterChart, Series
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from ..engine.schemas import AnalysisRequest
from .labels import CRATE_FIELDS, PACK_FIELDS, VEHICLE_FIELDS, cell_spec, cell_tables, field_label, provenance_of, value_text
from .text import LIMITATIONS, METHODOLOGY, recommendations, report_meta, verdict

# ------------------------------------------------------------------------------------------------ styles
FONT = "Calibri"
NAVY, ACCENT, INK, MUTED, LINE = "0F1C2E", "0B6FB8", "14202E", "6B7A8F", "CFD7E1"
ZEBRA = "F4F7FA"
STATUS_STYLE = {"PASS": ("1A7F4B", "E4F5EC"), "WARNING": ("A86400", "FFF2DC"), "FAIL": ("B3261E", "FDE8E6"), "N/A": ("5B6675", "ECEFF3")}
CLASS_FILL = {"Assumed": "FFF2DC", "Datasheet": "E8F0FB", "User-provided": "E8F0FB", "Calculated": "ECEFF3"}
CONF_FILL = {"Low": "FDE8E6", "Medium": "FFF2DC", "High": "E4F5EC"}
INPUT_FILL = "E8F4FD"
THIN = Side(style="thin", color=LINE)
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
MAX_TS_ROWS = 250_000


def _fill(hex_):
    return PatternFill("solid", start_color=hex_, end_color=hex_)


def _clean(v):
    """Excel-safe value: no NaN/inf, numpy scalars to Python."""
    if v is None:
        return None
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):
        try:
            v = v.item()
        except Exception:                                       # noqa: BLE001
            pass
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def put(ws, r, c, v, fmt=None, bold=False, color=INK, fill=None, wrap=False, size=10, align=None, border=True, italic=False):
    cell = ws.cell(row=r, column=c)
    v = _clean(v)
    cell.value = v
    if isinstance(v, str):
        cell.data_type = "s"                                    # text from user data must never become a formula
    cell.font = Font(name=FONT, size=size, bold=bold, italic=italic, color=color)
    if fill:
        cell.fill = _fill(fill)
    if fmt:
        cell.number_format = fmt
    cell.alignment = Alignment(wrap_text=wrap, vertical="top", horizontal=align)
    if border:
        cell.border = BOX
    return cell


def put_formula(ws, r, c, formula: str, fmt=None, bold=False, fill=None):
    cell = ws.cell(row=r, column=c)
    cell.value = formula                                         # starts with '=' -> formula
    cell.font = Font(name=FONT, size=10, bold=bold, color=INK)
    if fill:
        cell.fill = _fill(fill)
    if fmt:
        cell.number_format = fmt
    cell.alignment = Alignment(vertical="top")
    cell.border = BOX
    return cell


class Sh:
    """Small helper for laying out titled blocks on a sheet."""

    def __init__(self, wb: Workbook, title: str, widths: list[float], tab: str | None = None, landscape: bool = True):
        self.ws = wb.create_sheet(title)
        self.r = 1
        self.ncols = len(widths)
        for i, w in enumerate(widths, 1):
            self.ws.column_dimensions[get_column_letter(i)].width = w
        if tab:
            self.ws.sheet_properties.tabColor = tab
        self.ws.sheet_view.showGridLines = False
        self.ws.page_setup.orientation = "landscape" if landscape else "portrait"
        self.ws.page_setup.fitToWidth = 1
        self.ws.page_setup.fitToHeight = 0
        self.ws.sheet_properties.pageSetUpPr.fitToPage = True

    def _text(self, text: str, font: Font):
        c = self.ws.cell(row=self.r, column=1)
        c.value = text
        c.data_type = "s"                                        # user-derived text (project name, ...) is never a formula
        c.font = font
        self.r += 1

    def title(self, text: str, sub: str | None = None):
        self._text(text, Font(name=FONT, size=16, bold=True, color=NAVY))
        if sub:
            self._text(sub, Font(name=FONT, size=10, italic=True, color=MUTED))
        self.r += 1

    def h2(self, text: str):
        self._text(text, Font(name=FONT, size=11, bold=True, color=ACCENT))

    def gap(self, n: int = 1):
        self.r += n

    def header(self, labels: list[str]):
        for i, lab in enumerate(labels, 1):
            put(self.ws, self.r, i, lab, bold=True, color="FFFFFF", fill=NAVY, wrap=True)
        self.r += 1

    def row(self, values: list, fmts: list | None = None, wrap_cols: tuple = (), bold: bool = False, zebra: bool = False, fills: dict | None = None):
        for i, v in enumerate(values, 1):
            fill = (fills or {}).get(i) or (ZEBRA if zebra else None)
            put(self.ws, self.r, i, v, fmt=(fmts[i - 1] if fmts else None), wrap=(i in wrap_cols), bold=bold, fill=fill)
        self.r += 1
        return self.r - 1

    def table(self, header: list[str], rows: list[list], fmts: list | None = None, wrap_cols: tuple = (), status_col: int | None = None,
              class_col: int | None = None, conf_col: int | None = None, autofilter: bool = False) -> tuple[int, int]:
        self.header(header)
        first = self.r
        for k, rw in enumerate(rows):
            fills = {}
            if status_col and str(rw[status_col - 1]).upper() in STATUS_STYLE:
                fills[status_col] = STATUS_STYLE[str(rw[status_col - 1]).upper()][1]
            if class_col and rw[class_col - 1] in CLASS_FILL:
                fills[class_col] = CLASS_FILL[rw[class_col - 1]]
            if conf_col and rw[conf_col - 1] in CONF_FILL:
                fills[conf_col] = CONF_FILL[rw[conf_col - 1]]
            r = self.row(rw, fmts, wrap_cols, zebra=(k % 2 == 1), fills=fills)
            if status_col and str(rw[status_col - 1]).upper() in STATUS_STYLE:
                cell = self.ws.cell(row=r, column=status_col)
                cell.font = Font(name=FONT, size=10, bold=True, color=STATUS_STYLE[str(rw[status_col - 1]).upper()][0])
        last = self.r - 1
        if autofilter and rows:
            self.ws.auto_filter.ref = f"A{first - 1}:{get_column_letter(len(header))}{last}"
        self.gap()
        return first, last

    def kv(self, pairs: list[tuple], value_fmt: str | None = None, wrap_value: bool = True):
        """Two/three-column blocks: (label, value[, unit[, note]])."""
        for p in pairs:
            label, value = p[0], p[1]
            unit = p[2] if len(p) > 2 else ""
            note = p[3] if len(p) > 3 else ""
            put(self.ws, self.r, 1, label, bold=True, fill=ZEBRA, wrap=True)
            put(self.ws, self.r, 2, value, fmt=value_fmt if isinstance(value, (int, float)) and not isinstance(value, bool) else None, wrap=wrap_value, align="left")
            if self.ncols >= 3:
                put(self.ws, self.r, 3, unit)
            if self.ncols >= 4:
                put(self.ws, self.r, 4, note, wrap=True, color=MUTED)
            self.r += 1
        self.gap()

    def kvw(self, pairs: list[tuple[str, str]]):
        """Label in column A, long text value merged across the remaining columns (wraps)."""
        width = sum((self.ws.column_dimensions[get_column_letter(i)].width or 10) for i in range(2, self.ncols + 1))
        for label, value in pairs:
            put(self.ws, self.r, 1, label, bold=True, fill=ZEBRA, wrap=True)
            self.ws.merge_cells(start_row=self.r, start_column=2, end_row=self.r, end_column=self.ncols)
            put(self.ws, self.r, 2, value, wrap=True)
            for c in range(3, self.ncols + 1):
                self.ws.cell(row=self.r, column=c).border = BOX
            self.ws.row_dimensions[self.r].height = max(15.0, 13.5 * math.ceil(len(str(value)) * 1.05 / max(width, 20)))
            self.r += 1
        self.gap()

    def note(self, text: str, span: int | None = None, level: str = "info"):
        span = span or self.ncols
        self.ws.merge_cells(start_row=self.r, start_column=1, end_row=self.r, end_column=span)
        c = self.ws.cell(row=self.r, column=1, value=text)
        c.data_type = "s"
        c.font = Font(name=FONT, size=10, color=INK)
        c.fill = _fill("FFF2DC" if level == "warn" else "EEF4FB")
        c.alignment = Alignment(wrap_text=True, vertical="top")
        width = sum((self.ws.column_dimensions[get_column_letter(i)].width or 10) for i in range(1, span + 1))
        self.ws.row_dimensions[self.r].height = max(15.0, 14.0 * math.ceil(len(text) * 1.05 / max(width, 20)))
        self.r += 2


def _num(v, d=4):
    return None if v is None else round(v, d) if isinstance(v, float) else v


# ------------------------------------------------------------------------------------------------ sheets
def _summary(wb, req, res, meta, recs):
    sh = Sh(wb, "Summary", [38, 32, 12, 72], tab=NAVY)
    ws = sh.ws
    sh.title("Battery Pack Thermal Analysis & Cooling-System Sizing", req.project.name)
    p = req.project
    sh.kvw([("Report number", meta["report_id"]), ("Date", meta["date"]), ("Customer", p.customer or "–"), ("Project number", p.project_no or "–"), ("Engineer", p.engineer or "–"),
            ("Revision", p.revision), ("Cell", f"{req.cell.name or ''} {req.cell.chemistry or ''} {req.cell.capacity_ah or ''} Ah".strip()),
            ("Pack", f"{req.pack.ns}S{req.pack.np}P - {res['pack']['v_nom']:.0f} V nominal, {res['pack']['energy_kwh']:.1f} kWh"),
            ("Design philosophy", res["design"]["label"]), ("Tool", "Battery Thermal Studio - calculation engine v0.1")])
    level, text = verdict(res)
    sh.note(text, level="warn" if level != "pass" else "info")
    sh.h2("Key results")
    sh.header(["Quantity", "Value", "Unit", "Note"])
    for grp, title in (("battery", "Battery"), ("thermal", "Thermal"), ("cooling", "Cooling")):
        put(ws, sh.r, 1, title, bold=True, color="FFFFFF", fill=ACCENT)
        for c in (2, 3, 4):
            put(ws, sh.r, c, None, fill=ACCENT)
        sh.r += 1
        for k, kp in enumerate(res["kpis"][grp]):
            sh.row([kp["label"], _num(kp["value"], 5), kp["unit"], kp.get("sub") or ""], zebra=k % 2 == 1)
    sh.gap()
    sh.h2("Design heat-load philosophies")
    D = res["design"]
    names = {"peak": "Peak heat load", "moving_average": "Moving-average heat load", "sustained": "Sustained heat load", "drive_cycle": "Drive-cycle thermal load"}
    c = D["candidates"]
    rows = [[names[k] + ("  ◀ selected" if k == D["philosophy"] else ""), _num(c[k]["value_w"] / 1000, 5) if c[k]["available"] else None, "kW",
             c[k].get("substitution") or c[k].get("note", "")] for k in names]
    sh.table(["Philosophy", "Q", "Unit", "Basis"], rows, wrap_cols=(4,))
    sh.h2("Cooling requirement and sizing")
    cap, S_ = res["sizing"]["capacity"], res["sizing"]
    rows = [("Relevant heat load", cap["required_kw"] - D["q_ambient_gain_w"] / 1e3, "kW", D["label"]), ("Ambient heat gain", D["q_ambient_gain_w"] / 1e3, "kW", "not added for the drive-cycle philosophy"),
            ("Required cooling capacity", cap["required_kw"], "kW", "Q_required = Q_relevant + Q_ambient"), ("Safety factor", cap["safety_factor"], "-", ""),
            ("Design cooling capacity", cap["design_kw"], "kW", "Q_design = Q_required × SF"), ("Recommended installed capacity", cap["recommended_kw"], "kW", "rounded up"),
            ("Required coolant flow (pack)", S_["flow"]["required_lpm"], "L/min", f"ΔT = {S_['flow']['dt_cool_k']:.2f} K ({S_['flow']['dt_basis']})")]
    if S_["flow"].get("actual_lpm") is not None:
        rows.append(("Flow used for plate / hydraulics", S_["flow"]["actual_lpm"], "L/min", S_["flow"]["flow_source"]))
    if S_.get("inlet_temperature"):
        it = S_["inlet_temperature"]
        rows += [("Coolant inlet temperature (specified)", it["specified_c"], "°C", ""), ("Maximum permissible inlet temperature", it["t_in_max_c"], "°C", "hottest cell held at the target at the design load"),
                 ("Recommended inlet temperature", it["t_in_recommended_c"], "°C", "chiller likely required" if it["chiller_required"] else "")]
    if S_.get("pump"):
        rows += [("Pump: flow", S_["pump"]["flow_lpm"], "L/min", ""), ("Pump: pressure", S_["pump"]["dp_bar"], "bar", ""), ("Pump: electrical power", S_["pump"]["p_electrical_w"], "W", "")]
    sh.table(["Item", "Value", "Unit", "Note"], [[a, _num(b, 5), c_, d] for a, b, c_, d in rows], wrap_cols=(4,))
    sh.h2("Performance checks")
    core = [[f"{x['id']} · {x['name']}", x["value"], x["status"], f"Limit {x['limit']}. {x['message']}"] for x in res["checks"] if not x["supplementary"]]
    sh.table(["Check", "Predicted / actual", "Status", "Assessment"], core, wrap_cols=(1, 2, 4), status_col=3)
    dq = res["data_quality"]
    if dq["n_assumed"]:
        sh.note(f"Data quality: {dq['n_assumed']} parameters are unconfirmed engineering assumptions ({dq['n_low']} low confidence) - see the Assumptions sheet. "
                "Results are only as good as these inputs; confirm them before design release.", level="warn")
    ws.freeze_panes = "A4"
    return sh


def _inputs(wb, req, res):
    sh = Sh(wb, "Inputs", [50, 34, 14, 18, 14], tab=ACCENT)
    sh.title("Inputs", "All values used by the calculation (cell data were reviewed and confirmed before the run).")
    head = ["Parameter", "Value", "Unit", "Source", "Confidence"]
    p = req.project
    sh.h2("Project")
    sh.table(head, [["Project", p.name, "", "User-provided", ""], ["Customer", p.customer or "–", "", "User-provided", ""], ["Project number", p.project_no or "–", "", "User-provided", ""],
                    ["Engineer", p.engineer or "–", "", "User-provided", ""], ["Revision", p.revision, "", "User-provided", ""], ["Notes", p.notes or "", "", "User-provided", ""]], wrap_cols=(1, 2))
    sh.h2("Cell")
    rows = []
    for label, val, unit, path in cell_spec(req.cell):
        src, conf = provenance_of(req, path) if path else ("Datasheet / user", "–")
        rows.append([label, _num(val, 6), unit, src, conf])
    tabs = cell_tables(req.cell)
    rows.append(["Curves / maps provided", ", ".join(tabs) if tabs else "none - scalar data only", "", "Datasheet / user", ""])
    sh.table(head, rows, conf_col=5, wrap_cols=(1, 2))
    sh.h2("Pack configuration and operating conditions")
    rows = []
    for label, key, unit in PACK_FIELDS:
        v = getattr(req.pack, key)
        if v is None:
            continue
        s_, c_ = provenance_of(req, f"pack.{key}")
        rows.append([label, v, unit, s_, c_])
    if req.installed_cooling_capacity_kw is not None:
        rows.append(["Installed cooling capacity", req.installed_cooling_capacity_kw, "kW", "User-provided", "High"])
    sh.table(head, rows, conf_col=5)
    if req.vehicle is not None:
        sh.h2("Vehicle (road-load model)")
        rows = []
        for label, key, unit in VEHICLE_FIELDS:
            v = getattr(req.vehicle, key)
            if v is None:
                continue
            s_, c_ = provenance_of(req, f"vehicle.{key}")
            rows.append([label, v, unit, s_, c_])
        sh.table(head, rows, conf_col=5)
    L = res.get("load") or {}
    m = res["models"]["load"]
    sh.h2("Driving cycle / load")
    rows = [["Load source used", m["source"], "", "", ""], ["Load kind", "battery power" if m["kind"] == "power" else "pack current", "", "", ""], ["Cycle repeats", m["repeats"], "", "", ""]]
    if req.cycle is not None:
        t = req.cycle.time_s
        rows += [["Samples in file", len(t), "", "", ""], ["Duration of the file", t[-1] - t[0], "s", "", ""],
                 ["Signals in file", ", ".join(k for k in ("speed_kmh", "accel_ms2", "motor_power_kw", "battery_power_kw", "battery_current_a", "soc_pct", "grade_pct") if getattr(req.cycle, k) is not None), "", "", ""]]
    e = L.get("energy")
    if e:
        rows += [["Battery energy - discharge", _num(e["discharge_kwh"], 5), "kWh", "Calculated", ""], ["Battery energy - regen", _num(e["regen_kwh"], 5), "kWh", "Calculated", ""],
                 ["Battery energy - net", _num(e["net_kwh"], 5), "kWh", "Calculated", ""]]
    rows.append(["Notes", " ".join(m["notes"]), "", "", ""])
    sh.table(head, rows, wrap_cols=(1, 2))
    lim = req.crate_limits
    rows = [[lab, getattr(lim, k), unit, "User-provided", "High"] for lab, k, unit in CRATE_FIELDS if getattr(lim, k) is not None]
    if rows:
        sh.h2("C-rate limits")
        sh.table(head, rows)
    blocks = [("Heat / resistance model", req.resistance, "resistance"), ("Entropic model", req.entropic, "entropic"), ("Thermal model & design philosophy", req.thermal, "thermal"),
              ("Coolant", req.coolant, "coolant"), ("Cold plate", req.cold_plate, "cold_plate"), ("Pump", req.pump, "pump"), ("Radiator assumptions", req.radiator, "radiator"),
              ("Margins and check limits", req.limits, "limits")]
    for title, obj, prefix in blocks:
        if obj is None:
            continue
        rows = []
        for k, v in obj.model_dump(mode="json").items():
            if v is None:
                continue
            path = f"{prefix}.{k}"
            label, unit = field_label(path)
            s_, c_ = provenance_of(req, path)
            rows.append([label, value_text(path, v), unit, s_, c_])
        if rows:
            sh.h2(title)
            sh.table(head, rows, conf_col=5, wrap_cols=(1, 2))
    sh.ws.freeze_panes = "A4"


def _assumptions(wb, res):
    sh = Sh(wb, "Assumptions", [16, 46, 16, 12, 16, 12, 46, 60], tab="A86400")
    sh.title("Assumptions & data quality", "Every parameter that influences a result, with its source class and confidence. Assumed values are engineering defaults not confirmed by the customer.")
    rank = {"Assumed": 0, "Calculated": 1, "Datasheet": 2, "User-provided": 3}
    crank = {"Low": 0, "Medium": 1, "High": 2}
    rows_all = sorted(res["assumptions"], key=lambda r: (rank.get(r["source_class"], 4), crank.get(r["confidence"], 1), r["group"], r["parameter"]))
    rows = [[r["group"], r["parameter"], _num(r["value"], 6) if not isinstance(r["value"], (list, dict)) else str(r["value"]), r["unit"], r["source_class"], r["confidence"], r["source"], r["note"]]
            for r in rows_all]
    sh.table(["Group", "Parameter", "Value", "Unit", "Source class", "Confidence", "Source / basis", "Engineering note"], rows, class_col=5, conf_col=6, wrap_cols=(2, 7, 8), autofilter=True)
    sh.ws.freeze_panes = "A5"


def _heat_results(wb, req, res):
    sh = Sh(wb, "Heat results", [46, 18, 18, 22, 60], tab="B3261E")
    H, D = res["heat"], res["design"]
    sh.title("Heat generation and thermal load", "Joule heat I²R plus reversible (entropic) heat -I·T·dU/dT; electrical energy is not heat.")
    sh.h2("Heat generation")
    sh.table(["Quantity", "Per cell [W]", "Per module [W]", "Pack", "Note"],
             [["Maximum (instantaneous)", _num(H["max_cell_heat_w"], 5), _num(H["max_module_heat_w"], 5), f"{H['max_pack_heat_kw']:.5g} kW", f"at t = {H['t_max_pack_heat_s']:.0f} s"],
              ["Average (time-weighted)", _num(H["avg_cell_heat_w"], 5), _num(H["avg_module_heat_w"], 5), f"{H['avg_pack_heat_kw']:.5g} kW", ""],
              ["Minimum pack heat (negative = net reversible cooling)", None, None, f"{H['min_pack_heat_kw']:.5g} kW", ""]])
    sh.h2("Energy over the analysed duty")
    E = H["electrical"]
    sh.ncols = 4
    sh.kv([("Total heat generated", _num(H["total_heat_kwh"], 6), "kWh", ""), ("  of which Joule (I²R)", _num(H["joule_heat_kwh"], 6), "kWh", ""), ("  of which reversible (entropic)", _num(H["reversible_heat_kwh"], 6), "kWh", ""),
           ("Terminal energy - discharge", _num(E["terminal_discharge_kwh"], 6), "kWh", "NOT heat"), ("Terminal energy - charge / regen", _num(E["terminal_charge_kwh"], 6), "kWh", "NOT heat"),
           ("Terminal energy - net", _num(E["terminal_net_kwh"], 6), "kWh", ""), ("Discharge efficiency", _num(E["discharge_efficiency_pct"], 4), "%", ""),
           ("Heat as share of electrical throughput", _num(E["heat_to_throughput_pct"], 4), "%", "")])
    sh.h2("Electrical summary")
    sh.kv([("Duration", _num(H["duration_s"], 6), "s", f"{H['n_samples']} samples"), ("Peak pack current", _num(H["max_pack_current_a"], 5), "A", ""), ("Peak module current", _num(H["max_module_current_a"], 5), "A", ""),
           ("Peak cell current", _num(H["max_cell_current_a"], 5), "A", ""), ("Maximum discharge C-rate", _num(H["max_discharge_c"], 4), "C", ""), ("Maximum charge C-rate", _num(H["max_charge_c"], 4), "C", ""),
           ("RMS C-rate", _num(H["rms_c_rate"], 4), "C", ""), ("SOC start / end", f"{H['soc_start_pct']:.2f} / {H['soc_end_pct']:.2f}", "%", ""),
           ("SOC min / max", f"{H['soc_min_pct']:.2f} / {H['soc_max_pct']:.2f}", "%", ""), ("Cell resistance min / mean / max", f"{H['r_cell_min_mohm']:.4f} / {H['r_cell_mean_mohm']:.4f} / {H['r_cell_max_mohm']:.4f}", "mΩ", ""),
           ("Cell voltage min / max", f"{H['v_cell_min']:.3f} / {H['v_cell_max']:.3f}", "V", ""), ("Peak terminal power (discharge)", _num(H["peak_terminal_power_kw"], 5), "kW", ""),
           ("Infeasible power samples", H["n_infeasible"], "", "requested power beyond what the cells can deliver")])
    sh.ncols = 5
    sh.h2("Design heat-load candidates")
    names = {"peak": "Peak heat load", "moving_average": "Moving-average heat load", "sustained": "Sustained heat load", "drive_cycle": "Drive-cycle thermal load"}
    c = D["candidates"]
    sh.table(["Philosophy", "Q [kW]", "Available", "Selected", "Basis"],
             [[names[k], _num(c[k]["value_w"] / 1000, 5) if c[k]["available"] else None, "yes" if c[k]["available"] else "no", "◀ selected" if k == D["philosophy"] else "", c[k].get("substitution") or c[k].get("note", "")]
              for k in names], wrap_cols=(5,))
    sh.ncols = 4
    sh.kv([("Relevant load", _num(D["q_relevant_w"] / 1e3, 5), "kW", D["label"]), ("Ambient heat gain", _num(D["q_ambient_gain_w"] / 1e3, 5), "kW", ""), ("Required cooling capacity", _num(D["q_required_w"] / 1e3, 5), "kW", ""),
           ("Safety factor", D["safety_factor"], "-", ""), ("Design cooling capacity", _num(D["q_design_w"] / 1e3, 5), "kW", "")])
    sh.ncols = 5
    sh.note(f"{D['label']}: {D['explanation']}", span=5)
    sh.note("Peak vs sustained: " + D["peak_vs_sustained"], span=5)
    sh.h2("Models used")
    m = res["models"]
    sh.ncols = 5
    sh.kvw([("Resistance model", f"{m['resistance']['name']} - {m['resistance']['description']}"), ("Extrapolation policy", m["resistance"]["extrapolation_policy"]), ("OCV model", m["ocv"]["description"]),
            ("Entropic heat", m["entropic"]["status"]), ("SOC source", m["soc_source"]), ("Time integration", m["time_integration"])])
    sh.ws.freeze_panes = "A4"


TS_COLUMNS = [  # (header, series key, number format, width)
    ("t [s]", "t", "0.###", 10), ("State", "state", None, 11), ("Battery power [kW]", "p_batt_kw", "0.000", 14), ("Aux power [kW]", "p_aux_kw", "0.000", 12), ("Speed [km/h]", "speed_kmh", "0.0", 11),
    ("I pack [A]", "i_pack_a", "0.00", 11), ("I module [A]", "i_module_a", "0.00", 11), ("I cell [A]", "i_cell_a", "0.000", 11), ("C-rate [C]", "c_rate", "0.0000", 11),
    ("SOC [%]", "soc_pct", "0.000", 10), ("OCV [V]", "ocv_v", "0.0000", 10), ("V cell [V]", "v_cell_v", "0.0000", 10), ("V pack [V]", "v_pack_v", "0.00", 11),
    ("R cell [mΩ]", "r_cell_mohm", "0.0000", 11), ("Q Joule / cell [W]", "q_joule_cell_w", "0.0000", 14), ("Q reversible / cell [W]", "q_rev_cell_w", "0.0000", 16),
    ("Q cell [W]", "q_cell_w", "0.0000", 11), ("Q module [W]", "q_module_w", "0.000", 12), ("Q pack [kW]", "q_pack_kw", "0.00000", 12),
    ("Cumulative heat [kWh]", "cum_heat_kwh", "0.000000", 16), ("Cumulative Joule [kWh]", "cum_joule_kwh", "0.000000", 16),
    ("T avg cell [°C]", "t_cell_c", "0.00", 12), ("T hottest cell [°C]", "t_hot_c", "0.00", 13), ("T coolant out [°C]", "t_coolant_out_c", "0.00", 13),
    ("Q removed by coolant [kW]", "q_cool_kw", "0.0000", 16), ("Q to ambient [kW]", "q_amb_kw", "0.0000", 13), ("ΔT pack [K]", "dt_pack_k", "0.00", 10),
    ("ΔT module [K]", "dt_module_k", "0.00", 11), ("T without cooling [°C]", "t_uncooled_c", "0.00", 15),
]


def _timeseries(wb, res):
    s = res["series"]
    cols = [c for c in TS_COLUMNS if c[1] in s and s[c[1]] is not None]
    n = len(s["t"])
    step = max(1, math.ceil(n / MAX_TS_ROWS))
    sh = Sh(wb, "Timeseries", [c[3] for c in cols], tab="1A7F4B")
    ws = sh.ws
    ws.sheet_view.showGridLines = True
    ws.cell(row=1, column=1, value="Per-time-step results (sample-and-hold: each row applies over [t_k, t_k+1); the last row has zero duration)").font = Font(name=FONT, size=11, bold=True, color=NAVY)
    if step > 1:
        ws.cell(row=2, column=1, value=f"Note: cycle has {n} samples; every {step}th sample is listed to keep the workbook size manageable.").font = Font(name=FONT, size=10, italic=True, color="A86400")
    hr = 3
    for i, c in enumerate(cols, 1):
        put(ws, hr, i, c[0], bold=True, color="FFFFFF", fill=NAVY, wrap=True)
    ws.row_dimensions[hr].height = 30
    fonts = Font(name=FONT, size=10, color=INK)
    for r_i, k in enumerate(range(0, n, step)):
        r = hr + 1 + r_i
        for ci, c in enumerate(cols, 1):
            v = _clean(s[c[1]][k])
            cell = ws.cell(row=r, column=ci, value=v)
            if isinstance(v, str):
                cell.data_type = "s"
            cell.font = fonts
            if c[2]:
                cell.number_format = c[2]
    last = hr + (n + step - 1) // step
    ws.freeze_panes = ws.cell(row=hr + 1, column=2)
    ws.auto_filter.ref = f"A{hr}:{get_column_letter(len(cols))}{last}"
    return {c[1]: (i, c[0]) for i, c in enumerate(cols, 1)}, hr, last


CHARTS = [
    ("Graph 1 - Battery current vs time", "Current [A]", ["i_pack_a", "i_module_a", "i_cell_a"]),
    ("Graph 2 - SOC vs time", "SOC [%]", ["soc_pct"]),
    ("Graph 3 - C-rate vs time", "C-rate [C]", ["c_rate"]),
    ("Graph 4 - Cell heat generation vs time", "Heat per cell [W]", ["q_joule_cell_w", "q_rev_cell_w", "q_cell_w"]),
    ("Graph 5 - Pack heat generation vs time", "Pack heat [kW]", ["q_pack_kw"]),
    ("Graph 6 - Cumulative heat generation", "Energy [kWh]", ["cum_heat_kwh", "cum_joule_kwh"]),
    ("Graph 7 - Battery power vs time", "Power [kW]", ["p_batt_kw", "p_aux_kw"]),
    ("Predicted temperatures", "Temperature [°C]", ["t_hot_c", "t_cell_c", "t_coolant_out_c", "t_uncooled_c"]),
    ("Generation vs removal", "Power [kW]", ["q_pack_kw", "q_cool_kw", "q_amb_kw"]),
]


def _charts(wb, colmap, first_row, last_row):
    ts = wb["Timeseries"]
    sh = Sh(wb, "Charts", [12] * 24, tab="0B6FB8")
    ws = sh.ws
    ws.cell(row=1, column=1, value="Charts (native Excel charts on the Timeseries sheet)").font = Font(name=FONT, size=14, bold=True, color=NAVY)
    xref = Reference(ts, min_col=colmap["t"][0], min_row=first_row + 1, max_row=last_row)
    pos = 0
    for title, ylab, keys in CHARTS:
        keys = [k for k in keys if k in colmap]
        if not keys:
            continue
        ch = ScatterChart()
        ch.title = title
        ch.style = 2
        ch.height, ch.width = 7.6, 15.5
        ch.x_axis.title = "time [s]"
        ch.y_axis.title = ylab
        ch.x_axis.delete = False
        ch.y_axis.delete = False
        for k in keys:
            col, head = colmap[k]
            ser = Series(Reference(ts, min_col=col, min_row=first_row + 1, max_row=last_row), xref, title=head)
            ser.marker.symbol = "none"
            ser.smooth = False
            ser.graphicalProperties.line.width = 15000
            ch.series.append(ser)
        ch.legend.position = "b"
        ws.add_chart(ch, f"{'A' if pos % 2 == 0 else 'M'}{3 + (pos // 2) * 17}")
        pos += 1


def _cooling(wb, res):
    sh = Sh(wb, "Cooling", [48, 22, 14, 62], tab="0B6FB8")
    C, S_ = res["cooling"], res["sizing"]
    sh.title("Cooling requirement, sizing and hydraulics", f"Coolant: {C['props']['description']}")
    sh.h2("Coolant flow requirement  ṁ = Q / (cp·ΔT)")
    sh.ncols = 4
    sh.header(["Level", "Design heat [W]", "Mass flow [kg/s]", "Volume flow [L/min]"])
    for l in ("cell", "module", "pack"):
        sh.row([l.capitalize(), _num(C[l]["q_w"], 6), _num(C[l]["m_dot_kg_s"], 6), _num(C[l]["lpm"], 6)])
    sh.gap()
    p = C["props"]
    sh.h2("Coolant properties (at the mean coolant temperature)")
    sh.kv([("Evaluation temperature", _num(p["t_eval_c"], 4), "°C", ""), ("Density ρ", _num(p["rho"], 6), "kg/m³", "overrides: " + (", ".join(p["overrides"]) or "none")),
           ("Specific heat cp", _num(p["cp"], 6), "J/(kg·K)", ""), ("Conductivity k", _num(p["k"], 5), "W/(m·K)", ""), ("Dynamic viscosity μ", _num(p["mu"], 6), "Pa·s", ""), ("Prandtl number", _num(p["pr"], 4), "-", ""),
           ("Coolant temperature rise used", _num(C["dt_k"], 4), "K", C["dt_basis"])])
    cap = S_["capacity"]
    sh.h2("Cooling-system sizing")
    rows = [("Required cooling capacity", cap["required_kw"], "kW", cap["philosophy"]), ("Design capacity (× SF)", cap["design_kw"], "kW", f"safety factor {cap['safety_factor']:g}"),
            ("Recommended installed capacity", cap["recommended_kw"], "kW", ""), ("Required coolant flow", S_["flow"]["required_lpm"], "L/min", f"ΔT = {S_['flow']['dt_cool_k']:.2f} K")]
    if S_["flow"].get("actual_lpm") is not None:
        rows.append(("Flow used for plate / hydraulics", S_["flow"]["actual_lpm"], "L/min", S_["flow"]["flow_source"]))
    if S_.get("plate_capability_w") is not None:
        rows.append(("Cold-plate heat-removal capability at the target temperature", S_["plate_capability_w"] / 1e3, "kW", "ε·ṁ·cp·(T_target − T_in)"))
    if S_.get("inlet_temperature"):
        it = S_["inlet_temperature"]
        rows += [("Coolant inlet temperature - specified", it["specified_c"], "°C", ""), ("Maximum permissible inlet temperature", it["t_in_max_c"], "°C", "hottest cell held at target at the design load"),
                 ("Recommended inlet temperature", it["t_in_recommended_c"], "°C", "an active chiller is likely required" if it["chiller_required"] else "")]
    sh.kv([(a, _num(b, 6), c, d) for a, b, c, d in rows])
    if S_.get("pump"):
        pu, ra = S_["pump"], S_["radiator"]
        sh.h2("Pump")
        sh.kv([("Flow", _num(pu["flow_lpm"], 5), "L/min", ""), ("Pressure", _num(pu["dp_bar"], 5), "bar", f"{pu['dp_kpa']:.2f} kPa"), ("Head", _num(pu["head_m"], 4), "m", "coolant column"),
               ("Hydraulic power", _num(pu["p_hydraulic_w"], 5), "W", ""), ("Estimated electrical power", _num(pu["p_electrical_w"], 5), "W", ""),
               ("Suggested duty point", f"{pu['duty_flow_lpm']:.4g} L/min at {pu['duty_dp_bar']:.4g} bar", "", pu["allowances"])])
        sh.h2("Heat exchanger / radiator (indicative - final sizing needs air-side data)")
        sh.kv([("Required heat rejection", _num(ra["q_reject_w"] / 1e3, 5), "kW", "design load + pump heat"), ("Coolant-side flow", _num(ra["coolant_flow_lpm"], 5), "L/min", ""),
               ("Coolant into radiator", _num(ra["coolant_inlet_to_radiator_c"], 4), "°C", ""), ("Coolant out of radiator", _num(ra["coolant_outlet_from_radiator_c"], 4), "°C", ""),
               ("Ambient air", _num(ra["air_inlet_c"], 4), "°C", ""), ("Inlet temperature difference (ITD)", _num(ra["itd_k"], 4), "K", ""),
               ("Required capacity per kelvin of ITD", _num(ra["ua_required_w_k"], 5) if ra["ua_required_w_k"] is not None else "n/a", "W/K", ""),
               ("Air volume flow (assumed ΔT_air)", _num(ra["air_volume_flow_m3_s"], 4), "m³/s", f"air temperature rise {ra['air_dt_assumed_k']:g} K")])
        for t in ra["notes"]:
            sh.note(t, span=4, level="warn")
    th = res["thermal"]
    if th["has_temperature"]:
        sh.h2("Thermal accumulation")
        sh.kv([("Pack thermal capacity C", _num(th["c_pack_j_k"], 6), "J/K", ""), ("Thermal time constant τ", _num(th["tau_s"], 5), "s", "C / (G_c + G_a)"),
               ("Pack-to-ambient conductance", _num(th["ambient_ua_w_k"], 5), "W/K", th["ambient_ua_source"]), ("Maximum temperature without active cooling", _num(th["t_max_uncooled_c"], 5), "°C", "")])
    cp, hy = res.get("cold_plate"), res.get("hydraulics")
    if cp:
        sh.h2("Cold-plate thermal resistance chain (per cell)  R_total = R_contact + R_TIM + R_plate + R_conv")
        sh.header(["Element", "R [K/W]", "Share [%]", ""])
        for lab, key in (("Cell contact interface", "r_contact"), ("TIM", "r_tim"), ("Plate conduction", "r_plate"), ("Coolant convection", "r_conv")):
            sh.row([lab, _num(cp[key], 6), _num(cp[key] / cp["r_total"] * 100, 4), ""])
        sh.row(["Overall R_total", _num(cp["r_total"], 6), 100.0, ""], bold=True)
        sh.gap()
        sh.kv([("Convective coefficient h", _num(cp["h_w_m2k"], 5), "W/(m²·K)", f"Nu = {cp['nusselt']:.4g}"), ("Overall U (cell contact area)", _num(cp["u_cell_w_m2k"], 5), "W/(m²·K)", "U = 1/(R_total·A_cell)"),
               ("Overall U (plate footprint)", _num(cp["u_plate_w_m2k"], 5), "W/(m²·K)", ""), ("NTU", _num(cp["ntu"], 5), "-", ""), ("Effectiveness ε", _num(cp["effectiveness"], 5), "-", "ε = 1 − exp(−NTU)"),
               ("Cell-to-coolant ΔT at the design heat", _num(res["design"]["q_design_w"] / res["pack"]["n_cells"] * cp["r_total"], 5), "K", "Q_cell,design · R_total")])
        sh.note("Thermal resistance vs overall heat-transfer coefficient: R [K/W] is the absolute temperature rise per watt through a specific part of a specific geometry and resistances in series add. "
                "U [W/(m²·K)] = 1/(R·A_ref) normalises it by a reference area, so it depends on the area chosen (cell contact area or plate footprint) and is used to compare layers and technologies independent of size.", span=4)
        for w in cp["warnings"]:
            sh.note(w, span=4, level="warn")
    if hy:
        sh.h2("Channel hydraulics and pressure drop")
        sh.kv([("Flow velocity", _num(hy["velocity_m_s"], 5), "m/s", ""), ("Hydraulic diameter", _num(hy["dh_m"] * 1000, 5), "mm", ""), ("Reynolds number", _num(hy["reynolds"], 5), "-", hy["regime"]),
               ("Darcy friction factor", _num(hy["f_darcy"], 5), "-", ""), ("Channel pressure drop", _num(hy["dp_channel_pa"] / 1e3, 5), "kPa", ""), ("Minor losses", _num(hy["dp_minor_pa"] / 1e3, 5), "kPa", ""),
               ("Cold-plate pressure drop (all plates)", _num(hy["dp_plates_total_pa"] / 1e3, 5), "kPa", ""), ("External loop pressure drop", _num(hy["dp_external_pa"] / 1e3, 5), "kPa", ""),
               ("Total pressure drop", _num(hy["dp_total_pa"] / 1e5, 5), "bar", ""), ("Pack flow", _num(hy["q_pack_lpm"], 5), "L/min", ""), ("Hydraulic power", _num(hy["p_hyd_w"], 5), "W", ""),
               ("Electrical pump power", _num(hy["p_elec_w"], 5), "W", "")])
        if hy["flags"]:
            sh.header(["Severity", "Code", "", "Message"])
            for f in hy["flags"]:
                sh.row([f["severity"].upper(), f["code"], "", f["message"]], wrap_cols=(4,))
            sh.gap()
    sh.ws.freeze_panes = "A4"


def _checks(wb, res):
    sh = Sh(wb, "Checks", [7, 40, 12, 26, 28, 80, 20], tab="1A7F4B")
    sh.title("Performance checks and margins", "Each check is PASS / WARNING / FAIL; not-evaluable checks are N/A with the reason - never hidden.")
    core = [[c["id"], c["name"], c["status"], c["value"], c["limit"], c["message"], c.get("trace_id") or ""] for c in res["checks"] if not c["supplementary"]]
    sh.h2("Core checks 1-8")
    sh.table(["#", "Check", "Status", "Predicted / actual", "Limit", "Assessment", "Trace ID"], core, wrap_cols=(2, 4, 5, 6), status_col=3)
    supp = [[c["id"], c["name"], c["status"], c["value"], c["limit"], c["message"], c.get("trace_id") or ""] for c in res["checks"] if c["supplementary"]]
    if supp:
        sh.h2("Supplementary checks")
        sh.table(["#", "Check", "Status", "Value", "Limit", "Assessment", "Trace ID"], supp, wrap_cols=(2, 4, 5, 6), status_col=3)
    m = res["margins"]
    rows = []
    cls_status = {"ADEQUATE": "PASS", "MODERATE": "PASS", "WARNING": "WARNING", "INSUFFICIENT": "FAIL"}
    if m["cooling"].get("available"):
        mc = m["cooling"]
        rows.append(["M1", "Cooling margin = installed / required − 1", cls_status.get(mc["class"], "N/A"), f"{mc['margin_pct']:.4g} %  ({mc['class']})",
                     f"warning < {mc['warn_pct']:g} %, target ≥ {mc['target_pct']:g} %", f"installed {mc['installed_w'] / 1e3:.4g} kW / required {mc['required_w'] / 1e3:.4g} kW", ""])
    if m["thermal"].get("available"):
        mt = m["thermal"]
        rows.append(["M2", "Thermal margin = allowed − predicted maximum T", cls_status.get(mt["class"], "N/A"), f"{mt['margin_k']:.4g} K  ({mt['class']})",
                     f"warning < {mt['warn_k']:g} K", f"allowed {mt['t_allow_c']:.4g} °C, predicted {mt['t_pred_c']:.4g} °C", ""])
    if rows:
        sh.h2("Design margins")
        sh.table(["#", "Margin", "Status", "Value / class", "Thresholds", "Basis", ""], rows, wrap_cols=(2, 4, 5, 6), status_col=3)
    if res["issues"]:
        sh.h2("Input validation messages")
        sh.table(["", "Code", "Severity", "Field", "", "Message", ""], [["", i["code"], i["severity"].upper(), i.get("field", ""), "", i["message"], ""] for i in res["issues"]], wrap_cols=(2, 4, 6))
    sh.ws.freeze_panes = "A4"


def _sensitivity(wb, sens):
    sh = Sh(wb, "Sensitivity", [30, 16, 16, 12, 18, 12, 18, 12, 18, 12], tab="8E44AD")
    sh.title("Sensitivity analysis", "One-at-a-time perturbation of each parameter with the complete analysis re-run.")
    if not sens or not sens.get("ok"):
        sh.note("The sensitivity analysis was not included in this workbook." if not sens else "The sensitivity analysis could not be run for this case.", span=6)
        return
    outs = sens["outputs"]
    head = ["Parameter", "Change"]
    for o in outs:
        head += [f"{o['label']} [{o['unit']}]", "Δ [K]" if o["unit"] == "°C" else "Δ [%]"]
    rows = [["Base case", ""] + sum(([_num(sens["base"][o["key"]], 6), None] for o in outs), [])]
    for p in sens["params"]:
        if p.get("skipped"):
            rows.append([p["label"], f"skipped: {p['skipped']}"] + [None] * (2 * len(outs)))
            continue
        for i, lv in enumerate(p["levels"]):
            if lv["status"] != "ok":
                rows.append([p["label"] if i == 0 else "", lv["label"]] + [f"{lv['status']}: {lv.get('reason', '')}"] + [None] * (2 * len(outs) - 1))
                continue
            cells = []
            for o in outs:
                d = (lv.get("delta") or {}).get(o["key"]) if o["unit"] == "°C" else lv["delta_pct"].get(o["key"])
                cells += [_num(lv["metrics"].get(o["key"]), 6), _num(d, 4)]
            rows.append([p["label"] if i == 0 else "", lv["label"]] + cells)
    sh.ncols = len(head)
    sh.table(head, rows, wrap_cols=(1,), autofilter=False)
    sh.ws.freeze_panes = "C5"


def _traceability(wb, res):
    sh = Sh(wb, "Traceability", [26, 36, 16, 10, 14, 14, 44, 48, 40, 30, 22, 22, 22, 22, 22, 22], tab="6B7A8F")
    sh.title("Complete calculation traceability", "Input → formula → intermediate → result. Click a dependency (columns K-P) to jump to the row of that quantity.")
    tr = res["trace"]
    order = {"input": 0, "assumption": 1, "intermediate": 2, "result": 3}
    nodes = sorted(tr.values(), key=lambda x: order.get(x["kind"], 2))
    head = ["ID", "Quantity", "Value", "Unit", "Kind", "Source", "Formula", "Substitution", "Note", "Depends on"] + [f"→ dependency {i}" for i in range(1, 7)]
    sh.header(head)
    first = sh.r
    row_of = {nd["id"]: first + i for i, nd in enumerate(nodes)}
    link_font = Font(name=FONT, size=10, color="0B6FB8", underline="single")
    for i, nd in enumerate(nodes):
        r = first + i
        vals = [nd["id"], nd["label"], _num(nd["value"], 8) if isinstance(nd["value"], float) else nd["value"], nd["unit"], nd["kind"].upper(), nd.get("source", ""), nd.get("formula", ""), nd.get("substitution", ""),
                nd.get("note", ""), ", ".join(nd["inputs"])]
        for ci, v in enumerate(vals, 1):
            put(sh.ws, r, ci, v, wrap=ci in (2, 7, 8, 9, 10), fill=ZEBRA if i % 2 else None)
        for j, dep in enumerate(nd["inputs"][:6]):
            c = put(sh.ws, r, 11 + j, dep, fill=ZEBRA if i % 2 else None)
            if dep in row_of:
                c.hyperlink = f"#'Traceability'!A{row_of[dep]}"
                c.font = link_font
    sh.ws.auto_filter.ref = f"A{first - 1}:{get_column_letter(len(head))}{first + len(nodes) - 1}"
    sh.ws.freeze_panes = sh.ws.cell(row=first, column=3)


# ------------------------------------------------------------------------------------------------ hand-calc sheet (live formulas)
def _hand_calcs(wb, req, res):
    """Live-formula cross-check. Inputs (blue) are typed values taken from the analysis; results are Excel formulas; the engine value
    is shown beside each so that the two can be compared - change an input and the chain recomputes."""
    sh = Sh(wb, "Hand calcs", [46, 18, 12, 52, 18, 14, 10], tab="A86400")
    ws = sh.ws
    sh.title("Hand calculations with live formulas", "Blue cells are inputs (editable). White cells are Excel formulas. 'Engine' is the value calculated by the software; the check passes within 0.1 %.")
    tr = res["trace"]

    def tv(key):
        nd = tr.get(key)
        return None if nd is None else nd["value"]

    def inp(label, value, unit, note=""):
        r = sh.r
        put(ws, r, 1, label, bold=True, fill=ZEBRA, wrap=True)
        put(ws, r, 2, value, fill=INPUT_FILL, fmt="General")
        put(ws, r, 3, unit)
        put(ws, r, 4, note, color=MUTED, wrap=True)
        for c in (5, 6, 7):
            put(ws, r, c, None)
        sh.r += 1
        return r

    def calc(label, formula, unit, text, engine=None, fmt="General"):
        r = sh.r
        put(ws, r, 1, label, bold=True, fill=ZEBRA, wrap=True)
        put_formula(ws, r, 2, formula, fmt=fmt, bold=True)
        put(ws, r, 3, unit)
        put(ws, r, 4, text, color=MUTED, wrap=True)
        if engine is not None:
            put(ws, r, 5, engine, fmt="General")
            put_formula(ws, r, 6, f"=IF(E{r}=0,B{r}-E{r},(B{r}-E{r})/ABS(E{r}))", fmt="0.000%")
            put_formula(ws, r, 7, f'=IF(ABS(F{r})<=0.001,"OK","CHECK")', bold=True)
        else:
            for c in (5, 6, 7):
                put(ws, r, c, None)
        sh.r += 1
        return r

    def head(t):
        sh.h2(t)
        sh.header(["Quantity", "Value", "Unit", "Formula / source", "Engine value", "Rel. difference", "Check"])

    # ---- A. heat chain at the instant of maximum pack heat
    head("A. Heat chain at the instant of maximum pack heat")
    r_i = inp("Pack current I_pack", tv("in.i_pack_pk"), "A", "from the load profile at the peak instant")
    r_np = inp("Cells in parallel Np", req.pack.np, "-", "pack configuration")
    r_ns = inp("Cells in series Ns", req.pack.ns, "-", "pack configuration")
    r_mod = inp("Cells per module", res["pack"]["cells_per_module"], "-", "pack configuration")
    r_c = inp("Cell capacity C_cell", res["pack"]["cell_capacity_ah"], "Ah", "cell datasheet (confirmed)")
    r_r = inp("Cell resistance R at that SOC / temperature", tv("heat.r_pk"), "mΩ", "interpolated from the resistance data (level used by the engine)")
    r_t = inp("Cell temperature T", tv("in.t_cell"), "°C", "temperature at the peak instant")
    r_d = inp("Entropic coefficient dU/dT", tv("in.dudt_pk"), "mV/K", "from the datasheet dU/dT table (0 if excluded)")
    ic = calc("Cell current  I_cell = I_pack / Np", f"=B{r_i}/B{r_np}", "A", "current sharing between parallel cells", tv("heat.i_cell_pk"), "0.0000")
    calc("Cell C-rate  = I_cell / C_cell", f"=B{ic}/B{r_c}", "C", "positive = discharge")
    qj = calc("Joule heat  Q_joule = I_cell² · R", f"=B{ic}^2*B{r_r}/1000", "W", "R in mΩ → Ω", tv("heat.q_joule_pk"), "0.0000")
    qr = calc("Reversible heat  Q_rev = −I_cell · T[K] · dU/dT", f"=-B{ic}*(B{r_t}+273.15)*B{r_d}/1000", "W", "dU/dT in mV/K → V/K; T in kelvin", tv("heat.q_rev_pk"), "0.0000")
    qc = calc("Cell heat  Q_cell = Q_joule + Q_rev", f"=B{qj}+B{qr}", "W", "", tv("heat.q_cell_pk"), "0.0000")
    calc("Module heat  Q_module = cells_per_module · Q_cell", f"=B{r_mod}*B{qc}", "W", "", tv("heat.q_module_pk"), "0.000")
    calc("Pack heat  Q_pack = Ns · Np · Q_cell", f"=B{r_ns}*B{r_np}*B{qc}/1000", "kW", "", tv("heat.q_pack_pk"), "0.00000")
    sh.gap()

    # ---- B. design load and flow
    head("B. Design cooling load and coolant flow")
    D, C = res["design"], res["cooling"]
    q_rel = inp("Relevant heat load Q_relevant", D["q_relevant_w"] / 1e3, "kW", D["label"])
    q_amb = inp("Ambient heat gain", D["q_ambient_gain_w"] / 1e3, "kW", "0 for the drive-cycle philosophy")
    sf = inp("Safety factor SF", D["safety_factor"], "-", "")
    qreq = calc("Required capacity  Q_required = Q_relevant + Q_ambient", f"=B{q_rel}+B{q_amb}", "kW", "", tv("design.q_required"), "0.00000")
    qdes = calc("Design capacity  Q_design = Q_required × SF", f"=B{qreq}*B{sf}", "kW", "", tv("design.q_design"), "0.00000")
    cpc = inp("Coolant specific heat cp", C["cp"], "J/(kg·K)", "at the mean coolant temperature")
    dtc = inp("Coolant temperature rise ΔT", C["dt_k"], "K", C["dt_basis"])
    rho = inp("Coolant density ρ", C["rho"], "kg/m³", "")
    md = calc("Mass flow  ṁ = Q_design / (cp · ΔT)", f"=B{qdes}*1000/(B{cpc}*B{dtc})", "kg/s", "pack level", tv("cool.mdot_pack"), "0.000000")
    calc("Volume flow  V̇ = ṁ / ρ", f"=B{md}/B{rho}*60000", "L/min", "", tv("cool.lpm_pack"), "0.0000")
    sh.gap()

    # ---- C. cold plate and hydraulics
    cp, hy = res.get("cold_plate"), res.get("hydraulics")
    if cp and hy:
        head("C. Cold plate and hydraulics")
        rc = inp("R_contact", cp["r_contact"], "K/W", "cell-to-plate interface")
        rt = inp("R_TIM", cp["r_tim"], "K/W", "t / (k·A)")
        rp = inp("R_plate", cp["r_plate"], "K/W", "conduction through the plate")
        rv = inp("R_conv", cp["r_conv"], "K/W", "coolant convection")
        rtot = calc("R_total = R_contact + R_TIM + R_plate + R_conv", f"=B{rc}+B{rt}+B{rp}+B{rv}", "K/W", "series resistances add", tv("cp.r_total"), "0.000000")
        ac = inp("Cell contact area A_cell", req.cold_plate.cell_contact_area_m2 or tv("cp.a_cell"), "m²", "reference area for U")
        calc("Overall U = 1 / (R_total · A_cell)", f"=1/(B{rtot}*B{ac})", "W/(m²·K)", "U normalises R by the reference area", tv("cp.u_cell"), "0.000")
        ntu = inp("NTU", cp["ntu"], "-", "G_plate / (ṁ·cp)")
        calc("Effectiveness  ε = 1 − exp(−NTU)", f"=1-EXP(-B{ntu})", "-", "", tv("cp.eps"), "0.00000")
        dpp = inp("Cold-plate pressure drop ΔP_plates", hy["dp_plates_total_pa"] / 1e3, "kPa", "channel + minor losses")
        dpe = inp("External loop pressure drop ΔP_ext", hy["dp_external_pa"] / 1e3, "kPa", "hoses, chiller, radiator")
        dpt = calc("ΔP_total = ΔP_plates + ΔP_ext", f"=(B{dpp}+B{dpe})/100", "bar", "1 bar = 100 kPa", tv("hyd.dp_total"), "0.00000")
        qf = inp("Pack flow V̇", hy["q_pack_lpm"], "L/min", "")
        ph = calc("Hydraulic power  P_hyd = ΔP · V̇", f"=B{dpt}*100000*B{qf}/60000", "W", "Pa · m³/s", tv("hyd.p_hyd"), "0.0000")
        eta = inp("Pump overall efficiency", req.pump.overall_efficiency, "-", "")
        calc("Electrical pump power  P_el = P_hyd / η", f"=B{ph}/B{eta}", "W", "", tv("hyd.p_el"), "0.0000")
        sh.gap()

    # ---- D. built-in validation case 1 (from the specification)
    sh.h2("D. Built-in validation case 1 - constant current (independent of this project)")
    sh.header(["Quantity", "Value", "Unit", "Formula / source", "Expected", "Rel. difference", "Check"])
    v_c = inp("Cell capacity", 100, "Ah", "specified validation input")
    v_v = inp("Cell nominal voltage", 3.2, "V", "specified validation input")
    v_r = inp("Cell resistance", 1, "mΩ", "specified validation input")
    v_ns = inp("Series cells Ns", 120, "-", "120S1P")
    v_np = inp("Parallel cells Np", 1, "-", "120S1P")
    v_i = inp("Pack current", 200, "A", "constant discharge")

    def expect(label, formula, unit, text, expected, fmt="General"):
        r = calc(label, formula, unit, text, expected, fmt)
        return r
    vi = expect("Cell current  I_cell = I_pack / Np", f"=B{v_i}/B{v_np}", "A", "", 200, "0.00")
    expect("C-rate  = I_cell / C_cell", f"=B{vi}/B{v_c}", "C", "2C discharge", 2, "0.00")
    vq = expect("Cell heat  Q_cell = I² · R", f"=B{vi}^2*B{v_r}/1000", "W", "200² × 0.001 Ω", 40, "0.00")
    vp = expect("Pack heat  Q_pack = Ns · Np · Q_cell", f"=B{v_ns}*B{v_np}*B{vq}", "W", "120 × 40 W", 4800, "0.0")
    vv = expect("Pack voltage  V = Ns · V_cell", f"=B{v_ns}*B{v_v}", "V", "", 384, "0.0")
    vpw = expect("Electrical terminal power  P = V · I", f"=B{vv}*B{v_i}/1000", "kW", "electrical power - NOT heat", 76.8, "0.0")
    calc("Heat as share of electrical power", f"=B{vp}/1000/B{vpw}", "-", "the heat is only the resistive loss", 0.0625, "0.00%")
    ws.freeze_panes = "A4"


def _notes(wb, res, recs):
    sh = Sh(wb, "Notes", [120], tab="6B7A8F", landscape=False)
    ws = sh.ws
    sh.title("Methodology, engineering notes and limitations")

    def para(text, bold=False, color=INK, size=10):
        c = ws.cell(row=sh.r, column=1, value=text)
        c.data_type = "s"
        c.font = Font(name=FONT, size=size, bold=bold, color=color)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[sh.r].height = max(15.0, 13.5 * math.ceil(len(text) / 118.0))
        sh.r += 1
    sh.h2("Recommendations")
    for r in recs:
        tag = {"fail": "ACTION REQUIRED", "warning": "ATTENTION", "info": "NOTE"}[r["level"]]
        para(f"[{tag}] {r['title']}: {r['text']}")
    sh.gap()
    for title, paras in METHODOLOGY:
        sh.h2(title)
        for t in paras:
            para(t)
        sh.gap()
    sh.h2("Electrical energy is not heat")
    para(res["explanations"]["electrical_vs_heat"])
    sh.gap()
    sh.h2("Limitations")
    for i, t in enumerate(LIMITATIONS, 1):
        para(f"{i}. {t}")


def _no_stray_formulas(wb: Workbook) -> None:
    """Belt and braces against formula injection: the only formula cells are the ones the 'Hand calcs' sheet writes deliberately
    (value, relative-difference and check columns); anything else that openpyxl inferred to be a formula is turned back into text."""
    for ws in wb:
        allowed = {2, 6, 7} if ws.title == "Hand calcs" else set()
        for row in ws.iter_rows():
            for c in row:
                if c.data_type == "f" and c.column not in allowed:
                    c.data_type = "s"


# ------------------------------------------------------------------------------------------------ entry point
def build_xlsx(req: AnalysisRequest, res: dict, sens: dict | None = None) -> bytes:
    if res.get("status") == "blocked":
        raise ValueError("Cannot build a report for a blocked analysis")
    meta = report_meta(req, res)
    recs = recommendations(res, req)
    wb = Workbook()
    wb.remove(wb.active)
    _summary(wb, req, res, meta, recs)
    _inputs(wb, req, res)
    _assumptions(wb, res)
    _heat_results(wb, req, res)
    colmap, hr, last = _timeseries(wb, res)
    _charts(wb, colmap, hr, last)
    _cooling(wb, res)
    _checks(wb, res)
    _sensitivity(wb, sens)
    _traceability(wb, res)
    _hand_calcs(wb, req, res)
    _notes(wb, res, recs)
    # sheet order: put the analysis sheets first, the bulky data sheets after
    order = ["Summary", "Inputs", "Assumptions", "Heat results", "Cooling", "Checks", "Sensitivity", "Hand calcs", "Traceability", "Charts", "Timeseries", "Notes"]
    wb._sheets = [wb[n] for n in order if n in wb.sheetnames]
    wb.active = 0
    _no_stray_formulas(wb)
    wb.properties.title = f"Thermal analysis - {req.project.name}"
    wb.properties.creator = req.project.engineer or "Battery Thermal Studio"
    wb.properties.subject = f"Report {meta['report_id']}"
    wb.properties.created = datetime.now()
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
