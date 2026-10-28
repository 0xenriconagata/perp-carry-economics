"""A results directory for the figure tests: the fixture world run end to end, plus yearly.csv
and monthly.csv."""

from pathlib import Path

import polars as pl
import test_run

from perp_carry_economics import io, run
from perp_carry_economics.carry.yields import monthly, yearly
from perp_carry_economics.params import PRIMARY


def results_dir(tmp_path: Path) -> Path:
    """Run `run_all` on the fixture world into `tmp_path / "results"`, add yearly and monthly.csv.

    `calibration` is False: the fixture stands in for the frozen run."""
    out = tmp_path / "results"
    bars, oi, depth = test_run.world()
    info = {**test_run.INFO, "calibration": False}
    run.run_all(bars, test_run.RULES, oi, depth, out, test_run.SETTINGS, 1e7, info)
    ledger = pl.read_parquet(out / "ledger_B_primary.parquet")
    io.write_text_atomic(out / "yearly.csv", yearly(ledger, bars, PRIMARY, 1e7).write_csv())
    io.write_text_atomic(out / "monthly.csv", monthly(ledger, bars).write_csv())
    return out
