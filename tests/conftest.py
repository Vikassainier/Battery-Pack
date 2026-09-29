"""Test-suite setup: create the synthetic binary sample files (XLSX / PDF datasheets) if a fresh checkout does not have them."""
from pathlib import Path

import pytest

from battery_thermal.ingestion.sample_files import ensure_sample_files


@pytest.fixture(scope="session", autouse=True)
def _sample_files():
    ensure_sample_files(Path(__file__).resolve().parents[1] / "sample_data")
