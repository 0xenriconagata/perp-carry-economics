"""The two README weeks, picked from the ledger by a frozen rule and never by hand (spec 8)."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import polars as pl

from perp_carry_economics.data.rules import Rules
from perp_carry_economics.params import FILTER_PRINTS, MarginSetting


@dataclass(frozen=True)
class Episode:
    kind: str
    week_start: date  # Monday 00:00 UTC
    week_end: date  # exclusive
    anchor_us: int  # ts_us of the bar that selected the week
    note: str


def _episode(kind: str, anchor_us: int, note: str) -> Episode:
    day = (datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=anchor_us)).date()
    start = day - timedelta(days=day.weekday())
    return Episode(kind, start, start + timedelta(days=7), anchor_us, note)


def liquidation_week(ledger: pl.DataFrame, setting: MarginSetting, rules: Rules) -> Episode:
    """The week of the first liquidation; with none, the week of the closest call."""
    liq = ledger.filter(pl.col("liquidated"))
    if liq.height:
        return _episode("liquidation", liq["ts_us"][0], "first liquidation")
    # closest call: bars with a top-up pending (the landing bar after each run included), scored
    # (trigger - frac) / (trigger - maintenance_frac) with maintenance read at that bar
    pending = pl.col("topup_pending")
    near = ledger.filter(pending | pending.shift(1, fill_value=False)).drop_nulls("margin_frac")
    note = "no liquidation; closest call"
    if near.is_empty():
        near = ledger.drop_nulls("margin_frac")
        note = "no liquidation; no top-up pending; lowest margin fraction"
    if near.is_empty():
        raise ValueError("no hedged bar in the ledger")

    def score(r: dict[str, Any]) -> float:
        notional = r["mark"] * r["perp_qty"]
        maintenance_frac = rules.maintenance(notional) / notional
        return float((setting.trigger - r["margin_frac"]) / (setting.trigger - maintenance_frac))

    row = max(near.iter_rows(named=True), key=score)  # first bar on a tie
    return _episode("liquidation", row["ts_us"], note)


def inversion_week(bars: pl.DataFrame) -> Episode:
    """The week of the print with the lowest trailing mean over FILTER_PRINTS prints."""
    prints = bars.filter(pl.col("funding_rate").is_not_null()).select(
        "ts_us", pl.col("funding_rate").rolling_mean(FILTER_PRINTS).alias("trailing")
    )
    prints = prints.drop_nulls("trailing")  # the first FILTER_PRINTS - 1 windows are incomplete
    if prints.is_empty():
        raise ValueError(f"fewer than {FILTER_PRINTS} funding prints")
    low = prints.row(prints["trailing"].arg_min(), named=True)
    return _episode("inversion", low["ts_us"], f"lowest trailing {FILTER_PRINTS}-print funding")


def week_slice(ledger: pl.DataFrame, episode: Episode) -> pl.DataFrame:
    """The ledger rows of the episode's week, all columns."""
    day = pl.col("ts_us").cast(pl.Datetime("us", "UTC")).dt.date()
    return ledger.filter((day >= episode.week_start) & (day < episode.week_end))
