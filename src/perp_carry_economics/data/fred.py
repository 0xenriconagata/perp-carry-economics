"""FRED DTB3 (3-month Treasury bill, secondary market) as the daily risk-free benchmark.

DTB3 is a percent per year on a discount basis. `load_tbill` divides by 100 and uses the result
as-is as a fraction per year, with no conversion to a bond-equivalent or continuously
compounded yield.

FRED's CSV header is `observation_date,DTB3` today and was `DATE,DTB3`, so the two columns are
read by position. Days without a print (weekends, holidays) are absent or the literal `.`.
"""

from datetime import date, timedelta
from pathlib import Path

import httpx
import polars as pl

from perp_carry_economics.io import file_hash, write_text_atomic

DTB3_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTB3"


def _csv_path(raw_dir: Path) -> Path:
    return raw_dir / "fred" / "DTB3.csv"


def fetch_dtb3(raw_dir: Path) -> Path:
    """Download the full DTB3 history to raw_dir/fred/DTB3.csv and record its SHA-256 next to it.

    FRED publishes no checksum, so the hash is recorded, not verified. The file is replaced on
    every call: the series is revised and extended daily.
    """
    dest = _csv_path(raw_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        tmp.write_bytes(client.get(DTB3_URL).raise_for_status().content)
    tmp.replace(dest)
    write_text_atomic(dest.with_name(dest.name + ".sha256"), file_hash(dest) + "\n")
    return dest


def load_tbill(raw_dir: Path, start: date, end: date) -> pl.DataFrame:
    """One row per calendar day in [start, end): `day` (Date) and `rate` (Float64, fraction/year).

    A `.` print is no print: it takes the previous value, as do weekends and holidays. Days
    before the first print take the first print. No nulls remain.
    """
    path = _csv_path(raw_dir)
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; run fetch_dtb3")
    raw = pl.read_csv(path, infer_schema=False)
    prints = (
        raw.select(day=raw.columns[0], rate=raw.columns[1])
        .filter(pl.col("rate") != ".")
        .select(pl.col("day").str.to_date("%Y-%m-%d"), pl.col("rate").cast(pl.Float64) / 100)
        .sort("day")
    )
    days = pl.DataFrame({"day": pl.date_range(start, end - timedelta(days=1), eager=True)})
    out = days.join_asof(prints, on="day").with_columns(
        pl.col("rate").fill_null(strategy="backward")
    )
    if out["rate"].null_count():
        raise ValueError(f"no DTB3 prints in {path}")
    return out
