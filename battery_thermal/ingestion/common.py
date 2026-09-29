"""Shared file-reading helpers (CSV / Excel -> rows of strings; PDF text extraction)."""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass


class IngestionError(ValueError):
    """Raised when a file cannot be read at all (unsupported type, corrupt, empty)."""


@dataclass
class SheetRows:
    name: str
    rows: list[list[str]]


def file_kind(filename: str) -> str:
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if ext in ("csv", "txt", "tsv"):
        return "csv"
    if ext in ("xlsx", "xlsm"):
        return "xlsx"
    if ext == "xls":
        return "xls"
    if ext == "pdf":
        return "pdf"
    return ext or "unknown"


def decode_text(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise IngestionError("Could not decode the file as text")


def sniff_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample[:4096], delimiters=",;\t|").delimiter
    except csv.Error:
        first = sample.splitlines()[0] if sample.splitlines() else ""
        return max(",;\t|", key=first.count)


def read_csv_rows(data: bytes) -> list[list[str]]:
    text = decode_text(data)
    if not text.strip():
        raise IngestionError("The file is empty")
    delim = sniff_delimiter(text)
    return [[c.strip() for c in row] for row in csv.reader(io.StringIO(text), delimiter=delim)]


def _cell_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return str(v).strip()


def read_xlsx_sheets(data: bytes) -> list[SheetRows]:
    from openpyxl import load_workbook
    try:
        wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    except Exception as exc:                                    # corrupt / not a zip
        raise IngestionError(f"Could not open the Excel file: {exc}") from exc
    sheets: list[SheetRows] = []
    for ws in wb.worksheets:
        rows = []
        for r in ws.iter_rows(values_only=True):
            rows.append([_cell_text(v) for v in r])
        while rows and not any(rows[-1]):
            rows.pop()
        if rows:
            sheets.append(SheetRows(ws.title, rows))
    if not sheets:
        raise IngestionError("The Excel file contains no data")
    return sheets


def read_sheets(data: bytes, filename: str) -> list[SheetRows]:
    kind = file_kind(filename)
    if kind == "csv":
        return [SheetRows(filename, read_csv_rows(data))]
    if kind == "xlsx":
        return read_xlsx_sheets(data)
    if kind == "xls":
        raise IngestionError("Legacy .xls files are not supported - please save as .xlsx or .csv")
    raise IngestionError(f"Unsupported file type '.{kind}' (use CSV, XLSX or PDF)")


# ---------------------------------------------------------------------------------------------
# numbers
# ---------------------------------------------------------------------------------------------
_MINUS = str.maketrans({"−": "-", "–": "-", "—": "-", " ": " "})


def to_float(s) -> float | None:
    """Parse a number from text (handles decimal commas, thousands separators, unicode minus)."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).translate(_MINUS).strip()
    if not t:
        return None
    if re.fullmatch(r"[-+]?\d{1,3}(,\d{3})+(\.\d+)?", t):
        t = t.replace(",", "")
    elif re.fullmatch(r"[-+]?\d+,\d+", t):
        t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None
