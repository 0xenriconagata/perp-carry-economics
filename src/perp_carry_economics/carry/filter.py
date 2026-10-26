"""The cut-filter comparison row (spec 6) and the negative-funding stretches that test its prior.

The filter exits both legs when the trailing `FILTER_PRINTS` prints (7 days), annualised, fall
below the T-bill rate and re-enters when they climb back above it plus a fee-recovery hurdle.
Between the two bars the state holds, so single negative prints do not churn it.
"""

from collections import deque

import polars as pl

from perp_carry_economics.data.rules import Rules
from perp_carry_economics.params import FILTER_PRINTS, HURDLE_AMORTISE_DAYS, PRINTS_PER_YEAR


def filter_signal(bars: pl.DataFrame, rules: Rules) -> pl.Series:
    """`in_position` per bar: evaluated on print bars, held between them, True until
    `FILTER_PRINTS` prints exist. `run_book` acts on it at the next bar."""
    hurdle = rules.round_trip_fee() * 365 / HURDLE_AMORTISE_DAYS  # a round trip over 30 days
    window: deque[float] = deque(maxlen=FILTER_PRINTS)
    state, out = True, []
    for rate, tbill in zip(bars["funding_rate"], bars["tbill_rate"], strict=True):
        if rate is not None:
            window.append(rate)
            if len(window) == FILTER_PRINTS:
                annual = sum(window) / FILTER_PRINTS * PRINTS_PER_YEAR
                if state and annual < tbill:
                    state = False
                elif not state and annual > tbill + hurdle:
                    state = True
        out.append(state)
    return pl.Series("in_position", out, dtype=pl.Boolean)


def negative_stretches(funding: pl.DataFrame) -> pl.DataFrame:
    """Maximal runs of consecutive negative prints in a `ts_us, rate` frame."""
    neg = pl.col("rate") < 0
    return (
        funding.sort("ts_us")
        .with_columns(run=neg.rle_id())
        .filter(neg)
        .group_by("run", maintain_order=True)
        .agg(
            start_us=pl.col("ts_us").first(),
            end_us=pl.col("ts_us").last(),
            prints=pl.len(),
            mean_rate=pl.col("rate").mean(),
        )
        .drop("run")
    )
