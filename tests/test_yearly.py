import math
from datetime import date

import polars as pl
from bars_factory import flat, frame, ts_of, with_funding
from test_run import RULES

from perp_carry_economics.carry.book import Mode, run_book
from perp_carry_economics.carry.yields import decompose, monthly, yearly
from perp_carry_economics.params import NOTIONAL_USD as N
from perp_carry_economics.params import PRIMARY


def run(days):
    bars = with_funding(frame(flat(days), start_us=ts_of(2023, 12, 20)), [1e-4])
    ledger = run_book(bars, PRIMARY, RULES, Mode.SEPARATE)
    return ledger, bars, yearly(ledger, bars, PRIMARY, N)


def test_month_start_sweep_belongs_to_previous_year():
    # 2023-12-20 to 2024-01-10: the 2024-01-01 sweep carries December's funding
    ledger, _, y = run(22)
    assert y["year"].to_list() == [2023, 2024]
    assert y.filter(pl.col("year") == 2023)["net_usd"][0] > 0
    in_2024 = ledger.filter(pl.col("ts_us") >= ts_of(2024, 1, 1))["sweep_usd"].sum()
    jan_first = ledger.filter(pl.col("ts_us") == ts_of(2024, 1, 1))["sweep_usd"][0]
    assert jan_first > 0
    assert math.isclose(y.filter(pl.col("year") == 2024)["net_usd"][0], in_2024 - jan_first)


def test_final_sweep_belongs_to_its_own_year():
    ledger, bars, y = run(22)
    assert ledger["sweep_usd"][-1] != 0
    total = decompose(ledger, bars, PRIMARY, N).net_usd
    assert math.isclose(y["net_usd"].sum(), total, rel_tol=1e-12)


def test_partial_flag_days_and_day_range():
    _, _, y = run(22)
    assert y.dtypes[:5] == [pl.Int32, pl.Int64, pl.Boolean, pl.Date, pl.Date]
    assert set(y.dtypes[5:]) == {pl.Float64}
    row = y.filter(pl.col("year") == 2024).select("partial", "days", "first_day", "last_day")
    assert row.row(0) == (True, 10, date(2024, 1, 1), date(2024, 1, 10))


def test_monthly_month_start_sweep_belongs_to_previous_month():
    ledger, bars, _ = run(22)
    m = monthly(ledger, bars)
    assert m.schema == pl.Schema({"month": pl.Date, "sweep_usd": pl.Float64})
    assert m["month"].to_list() == [date(2023, 12, 1), date(2024, 1, 1)]
    jan_first = ledger.filter(pl.col("ts_us") == ts_of(2024, 1, 1))["sweep_usd"][0]
    in_dec = ledger.filter(pl.col("ts_us") < ts_of(2024, 1, 1))["sweep_usd"].sum()
    assert math.isclose(m["sweep_usd"][0], in_dec + jan_first)


def test_monthly_sums_to_the_ledger_total_one_row_per_month_touched():
    ledger, bars, _ = run(45)  # 2023-12-20 to 2024-02-02: three months, two month-start sweeps
    m = monthly(ledger, bars)
    touched = ledger.select(pl.from_epoch("ts_us", "us").dt.strftime("%Y%m").n_unique()).item()
    assert m.height == touched == 3
    assert ledger["sweep_usd"][-1] != 0  # the final partial month's sweep stays in February
    assert math.isclose(m["sweep_usd"].sum(), ledger["sweep_usd"].sum(), rel_tol=1e-12)


def test_spread_is_net_yield_minus_tbill_mean():
    _, _, y = run(22)
    row = y.row(0, named=True)
    assert math.isclose(row["tbill_mean"], 0.05)
    assert math.isclose(row["spread"], row["net_yield"] - row["tbill_mean"], abs_tol=1e-12)
