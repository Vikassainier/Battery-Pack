"""(Re)generate the sample data files (XLSX / PDF datasheets, driving cycles) - overwrites existing files.

Everything generated here is SYNTHETIC test data and is labelled as such in the files themselves.
Run from the repository root:  python scripts/make_sample_data.py

The application itself creates any *missing* binary sample file on first use (``ensure_sample_files``), so this script is only needed
to refresh the files after changing the generator.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "sample_data"

from battery_thermal.ingestion.sample_files import make_cell_pdf, make_cell_xlsx, make_drive_cycles   # noqa: E402

if __name__ == "__main__":
    print(make_cell_xlsx(OUT))
    print(make_cell_pdf(OUT))
    for p in make_drive_cycles(OUT):
        print(p)
