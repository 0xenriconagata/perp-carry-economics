"""Yield decomposition of a book ledger (spec 7): gross vs net yield and the spread over T-bill.

Gross yield is the pitch-deck number, positive funding on the spot notional. Net yield and the
spread use the capital deployed for the setting the ledger ran with, `(1 + m0 + reserve_frac) x
notional`, so every row of a table shares one denominator.
"""

import math
from dataclasses import asdict, dataclass

import polars as pl

from perp_carry_economics.config import DAY_US
from perp_carry_economics.io import ordered_group_by
from perp_carry_economics.params import WORTH_IT_SPREAD, MarginSetting


@dataclass(frozen=True)
class Decomposition:
    """net = gross + negative - fees - liq_loss + unhedged_pnl + tbill_on_idle + basis.

    `basis_usd` is the residual: spot-vs-mark convergence realised at resets and closes. On an
    engine ledger it equals sum(spot_mtm_usd + perp_mtm_usd) - sum(unhedged_pnl_usd).
    `missed_funding_usd` never moved cash, so it sits beside the identity, not in it."""

    years: float
    gross_funding_usd: float
    negative_funding_usd: float
    fees_usd: float
    liq_loss_usd: float
    missed_funding_usd: float
    unhedged_pnl_usd: float
    tbill_on_idle_usd: float
    basis_usd: float
    net_usd: float
    capital_usd: float
    gross_yield: float
    net_yield: float
    tbill_mean: float
    spread: float

    def row(self, label: str) -> dict[str, float | str]:
        return {"label": label, **asdict(self)}


def _sum(ledger: pl.DataFrame, col: str) -> float:
    return math.fsum(ledger[col].to_list())


def decompose(
    ledger: pl.DataFrame, bars: pl.DataFrame, setting: MarginSetting, notional: float
) -> Decomposition:
    funding = ledger["funding_usd"].to_list()
    gross = math.fsum(f for f in funding if f > 0)
    negative = math.fsum(f for f in funding if f < 0)
    fees, liq = _sum(ledger, "fee_usd"), _sum(ledger, "liq_loss_usd")
    unhedged, tbill = _sum(ledger, "unhedged_pnl_usd"), _sum(ledger, "tbill_usd")
    net = _sum(ledger, "sweep_usd")
    basis = net - (gross + negative - fees - liq + unhedged + tbill)
    capital = (1 + setting.m0 + setting.reserve_frac) * notional
    ts = ledger["ts_us"]
    years = (ts[-1] - ts[0]) / (365.25 * DAY_US)
    daily = bars.filter(pl.col("day_start"))["tbill_rate"].to_list()
    tbill_mean = math.fsum(daily) / len(daily)
    net_yield = net / capital / years
    return Decomposition(
        years=years,
        gross_funding_usd=gross,
        negative_funding_usd=negative,
        fees_usd=fees,
        liq_loss_usd=liq,
        missed_funding_usd=_sum(ledger, "missed_funding_usd"),
        unhedged_pnl_usd=unhedged,
        tbill_on_idle_usd=tbill,
        basis_usd=basis,
        net_usd=net,
        capital_usd=capital,
        gross_yield=gross / notional / years,
        net_yield=net_yield,
        tbill_mean=tbill_mean,
        spread=net_yield - tbill_mean,
    )


def yearly(
    ledger: pl.DataFrame, bars: pl.DataFrame, setting: MarginSetting, notional: float
) -> pl.DataFrame:
    """`decompose`'s headline numbers per calendar year, for the yearly spread figure.

    A bar's cash flows belong to its own year, except a sweep on a `month_start` bar: it pays
    out the month before, so it belongs to the year of the previous day. The nets therefore
    sum to `decompose(...).net_usd`. `ledger` and `bars` are row-aligned."""
    ts = pl.col("ts_us")
    year = pl.from_epoch(ts, "us").dt.year().cast(pl.Int32)
    prev_year = pl.from_epoch(ts - DAY_US, "us").dt.year().cast(pl.Int32)
    df = ledger.select(
        "ts_us",
        "funding_usd",
        "sweep_usd",
        day=pl.from_epoch(ts, "us").dt.date(),
        year=year,
        sweep_year=pl.when(bars["month_start"]).then(prev_year).otherwise(year),
        tbill=pl.when(bars["day_start"]).then(bars["tbill_rate"]),
    )
    by_bar = ordered_group_by(df, "year").agg(
        pl.col("day").n_unique().cast(pl.Int64).alias("days"),
        pl.col("day").min().alias("first_day"),
        pl.col("day").max().alias("last_day"),
        pl.col("funding_usd").filter(pl.col("funding_usd") > 0).sum().alias("gross_funding_usd"),
        pl.col("tbill").mean().alias("tbill_mean"),
    )
    net = ordered_group_by(df, "sweep_year").agg(pl.col("sweep_usd").sum().alias("net_usd"))
    capital = (1 + setting.m0 + setting.reserve_frac) * notional
    years = pl.col("days") / 365.25
    return (
        by_bar.join(net, left_on="year", right_on="sweep_year", how="left")
        .with_columns(pl.col("net_usd").fill_null(0.0))
        .with_columns(
            partial=pl.col("days") < 365,
            gross_yield=pl.col("gross_funding_usd") / notional / years,
            net_yield=pl.col("net_usd") / capital / years,
        )
        .with_columns(spread=pl.col("net_yield") - pl.col("tbill_mean"))
        .select(
            "year",
            "days",
            "partial",
            "first_day",
            "last_day",
            "gross_funding_usd",
            "net_usd",
            "gross_yield",
            "net_yield",
            "tbill_mean",
            "spread",
        )
        .sort("year")
    )


def monthly(ledger: pl.DataFrame, bars: pl.DataFrame) -> pl.DataFrame:
    """Net sweep per calendar month (`month` is its first day), attributed as in `yearly`: a
    sweep on a `month_start` bar belongs to the month of the day before. One row per month the
    ledger touches; like `yearly`'s join, a nonzero sweep on the first bar would be dropped.
    `ledger` and `bars` are row-aligned."""
    ts = pl.col("ts_us")
    month = pl.from_epoch(ts, "us").dt.month_start().dt.date()
    prev_month = pl.from_epoch(ts - DAY_US, "us").dt.month_start().dt.date()
    df = ledger.select(
        "sweep_usd",
        month=month,
        sweep_month=pl.when(bars["month_start"]).then(prev_month).otherwise(month),
    )
    months = df.select(pl.col("month").unique().sort())
    net = ordered_group_by(df, "sweep_month").agg(pl.col("sweep_usd").sum())
    return (
        months.join(net, left_on="month", right_on="sweep_month", how="left")
        .with_columns(pl.col("sweep_usd").fill_null(0.0))
        .sort("month")
    )


def worth_it(d: Decomposition) -> bool:
    return d.spread >= WORTH_IT_SPREAD
