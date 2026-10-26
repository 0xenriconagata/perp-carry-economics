"""Project-wide constants and settings loaded from .env."""

import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

SYMBOL = "BTCUSDT"
PERIOD_START = date(2020, 9, 1)
CALIBRATION_END = date(2020, 12, 1)

# Time units: timestamps are integer UTC microseconds since the epoch.
US = 1_000_000  # microseconds per second
HOUR_US = 3600 * US
DAY_US = 24 * HOUR_US

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("DATA_DIR") or ROOT / "data")
RAW_DIR = DATA_DIR / "raw"
INTERIM_DIR = DATA_DIR / "interim"
RESULTS_DIR = ROOT / "results"
