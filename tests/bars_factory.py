"""Synthetic 1-minute bar frames with the exact `build_bars` schema, for the engine tests.

Prices are built as plain lists (`flat`, `rally`, concatenated with +) and turned into a frame
by `frame`; `with_funding` adds the 8-hourly prints. Spot equals mark unless a test changes it.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import polars as pl

from perp_carry_economics.config import DAY_US, HOUR_US, US

MIN_US = 60 * US
T0 = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp()) * US
P0 = 10_000.0
TBILL = 0.05


def ts_of(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=UTC).timestamp()) * US


def flat(days: float, price: float = P0) -> list[float]:
    """`days × 1440` bars at `price`."""
    return [price] * round(days * 1440)


def rally(pct: float, minutes: int, price: float = P0) -> list[float]:
    """`minutes` bars moving linearly from `price` (exclusive) to `price × (1 + pct)`."""
    return [price * (1 + pct * k / minutes) for k in range(1, minutes + 1)]


def frame(
    marks: Sequence[float],
    start_us: int = T0,
    highs: Sequence[float] | None = None,
    tbill_rate: float = TBILL,
) -> pl.DataFrame:
    """One bar per minute from `start_us`; `mark_high` defaults to the mark, no funding."""
    month = pl.from_epoch("ts_us", "us").dt.strftime("%Y%m")
    day = pl.col("ts_us") // DAY_US
    return (
        pl.DataFrame(
            {
                "ts_us": [start_us + i * MIN_US for i in range(len(marks))],
                "mark": list(marks),
                "mark_high": list(marks if highs is None else highs),
            },
            schema={"ts_us": pl.Int64, "mark": pl.Float64, "mark_high": pl.Float64},
        )
        .with_columns(
            spot=pl.col("mark"),
            funding_rate=pl.lit(None, pl.Float64),
            tbill_rate=pl.lit(tbill_rate, pl.Float64),
            month_start=(month != month.shift(1)).fill_null(True),
            day_start=(day != day.shift(1)).fill_null(True),
        )
        .select(
            "ts_us",
            "mark",
            "mark_high",
            "spot",
            "funding_rate",
            "tbill_rate",
            "month_start",
            "day_start",
        )
    )


def with_funding(bars: pl.DataFrame, rates: Sequence[float]) -> pl.DataFrame:
    """A print on every bar at 00:00, 08:00 and 16:00 UTC, cycling through `rates`."""
    out: list[float | None] = []
    k = 0
    for t in bars["ts_us"]:
        if t % (8 * HOUR_US) == 0:
            out.append(rates[k % len(rates)])
            k += 1
        else:
            out.append(None)
    return bars.with_columns(funding_rate=pl.Series(out, dtype=pl.Float64))
