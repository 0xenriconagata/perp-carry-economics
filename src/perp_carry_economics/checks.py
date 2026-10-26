"""Validation checks of spec 11: D1-D4 on the raw data, E1-E3 on the engine's ledger."""

import math
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Literal

import polars as pl

from perp_carry_economics.carry.book import Mode, run_book
from perp_carry_economics.config import DAY_US, US
from perp_carry_economics.data.rules import Rules
from perp_carry_economics.io import write_parquet_atomic
from perp_carry_economics.params import (
    MARK_GAP_FAIL_MIN,
    MARK_GAP_LIST_MIN,
    NOTIONAL_USD,
    OI_MISSING_DAYS_MAX_SHARE,
    PRINTS_PER_DAY,
    MarginSetting,
)

MIN_US = 60 * US
_EPOCH = date(1970, 1, 1)


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Literal["PASS", "FAIL"]
    detail: str
    numbers: dict[str, float | int]


def _status(ok: bool) -> Literal["PASS", "FAIL"]:
    return "PASS" if ok else "FAIL"


def _us(d: date) -> int:
    return (d - _EPOCH).days * DAY_US


def _rows_per_day(ts: pl.Series, start: date, end: date) -> dict[date, int]:
    """Rows of a ts_us column on each calendar day of [start, end), 0 for a day without any."""
    counts = Counter(t // DAY_US for t in ts.to_list() if _us(start) <= t < _us(end))
    return {
        start + timedelta(days=i): counts[(start - _EPOCH).days + i]
        for i in range((end - start).days)
    }


def check_d1_funding_complete(funding: pl.DataFrame, start: date, end: date) -> CheckResult:
    """Exactly PRINTS_PER_DAY funding prints on every calendar day of [start, end)."""
    per_day = _rows_per_day(funding["ts_us"], start, end)
    bad = {d: n for d, n in per_day.items() if n != PRINTS_PER_DAY}
    detail = (
        f"days without exactly {PRINTS_PER_DAY} prints: "
        + ", ".join(f"{d} ({n})" for d, n in bad.items())
        if bad
        else f"{PRINTS_PER_DAY} prints on each of {len(per_day)} days"
    )
    numbers: dict[str, float | int] = {
        "days": len(per_day),
        "prints": sum(per_day.values()),
        "bad_days": len(bad),
    }
    return CheckResult(
        "D1 funding prints complete", _status(bool(per_day) and not bad), detail, numbers
    )


def check_d2_mark_gaps(mark: pl.DataFrame, start: date, end: date) -> CheckResult:
    """Missing mark minutes in [start, end): FAIL only on a gap over MARK_GAP_FAIL_MIN; every
    gap over MARK_GAP_LIST_MIN is listed by its first missing minute and its length. Shorter
    gaps (Binance maintenance windows) are tolerated; the engine carries the last bar."""
    lo, hi = _us(start), _us(end)
    ts = sorted(t for t in mark["ts_us"].to_list() if lo <= t < hi)
    edges = [lo - MIN_US, *ts, hi]  # a hole at either edge of the window counts too
    gaps = [(a + MIN_US, (b - a) // MIN_US - 1) for a, b in pairwise(edges) if b - a > MIN_US]
    listed = {
        datetime.fromtimestamp(s / US, UTC).strftime("%Y-%m-%dT%H:%M"): n
        for s, n in gaps
        if n > MARK_GAP_LIST_MIN
    }
    longest = max((n for _, n in gaps), default=0)
    detail = (
        f"{len(listed)} gaps over {MARK_GAP_LIST_MIN} min: "
        + ", ".join(f"{s} ({n} min)" for s, n in listed.items())
        + f"; longest {longest} min, fails above {MARK_GAP_FAIL_MIN} min"
    )
    numbers: dict[str, float | int] = {
        "bars": len(ts),
        "missing_minutes": sum(n for _, n in gaps),
        "longest_gap_min": longest,
        "listed_gaps": len(listed),
        **{f"gap_{s}_min": n for s, n in listed.items()},
    }
    return CheckResult(
        "D2 mark klines gaps", _status(longest <= MARK_GAP_FAIL_MIN), detail, numbers
    )


def check_d3_oi_daily(oi: pl.DataFrame, start: date, end: date) -> CheckResult:
    """Open interest present on every day of [start, end); FAIL if the share of days without a
    row exceeds OI_MISSING_DAYS_MAX_SHARE."""
    per_day = _rows_per_day(oi["ts_us"], start, end)
    missing = [d for d, n in per_day.items() if n == 0]
    share = len(missing) / len(per_day) if per_day else 1.0
    detail = f"{len(missing)} of {len(per_day)} days without OI" + (
        ": " + ", ".join(map(str, missing)) if missing else ""
    )
    numbers: dict[str, float | int] = {
        "days": len(per_day),
        "missing_days": len(missing),
        "missing_share": share,
    }
    return CheckResult(
        "D3 open interest daily", _status(share <= OI_MISSING_DAYS_MAX_SHARE), detail, numbers
    )


def check_d4_tbill_covers(tbill: pl.DataFrame, start: date, end: date) -> CheckResult:
    """The DTB3 prints (`day`, no-print days excluded) span [start, end): one on or before
    start, one on or after the last day, so no day is back-filled or carried past the series."""
    days: list[date] = tbill["day"].to_list()
    ok = bool(days) and min(days) <= start and max(days) >= end - timedelta(days=1)
    inside = sum(start <= d < end for d in days)
    span = f"from {min(days)} to {max(days)}" if days else "none"
    detail = f"DTB3 prints {span} for [{start}, {end})"
    return CheckResult("D4 T-bill covers the period", _status(ok), detail, {"prints": inside})


def check_e1_funding_total(ledger: pl.DataFrame, bars: pl.DataFrame) -> CheckResult:
    """Σ funding_usd recomputed as the engine pays it, rate × previous mark × previous
    perp_qty on bars hedged at the previous bar, equals the ledger's total exactly."""
    if ledger["ts_us"].to_list() != bars["ts_us"].to_list():
        return CheckResult("E1 funding total", "FAIL", "ledger and bars differ in ts_us", {})
    prev = ledger.shift(1)
    # same operand order as the engine's `rate * m_prev * perp_qty`, so the same bits
    paid = (bars["funding_rate"] * bars["mark"].shift(1) * prev["perp_qty"]).filter(
        prev["hedged"].fill_null(False) & bars["funding_rate"].is_not_null()
    )
    recomputed = math.fsum(paid.to_list())
    booked = math.fsum(ledger["funding_usd"].to_list())
    diff = booked - recomputed
    numbers: dict[str, float | int] = {
        "prints": paid.len(),
        "ledger_usd": booked,
        "recomputed_usd": recomputed,
        "difference_usd": diff,
    }
    detail = f"ledger {booked!r} vs recomputed {recomputed!r} USD over {paid.len()} prints"
    return CheckResult("E1 funding total", _status(diff == 0.0), detail, numbers)


def check_e2_rerun_identical(
    bars: pl.DataFrame, setting: MarginSetting, rules: Rules
) -> CheckResult:
    """Two hold-through runs on the same bars write byte-identical parquet."""
    with tempfile.TemporaryDirectory() as d:
        paths = [Path(d) / "a.parquet", Path(d) / "b.parquet"]
        for p in paths:
            write_parquet_atomic(run_book(bars, setting, rules, Mode.SEPARATE), p)
        a, b = (p.read_bytes() for p in paths)
    detail = f"two runs over {bars.height} bars: {'identical' if a == b else 'different'} bytes"
    return CheckResult("E2 rerun identical", _status(a == b), detail, {"bytes": len(a)})


def check_e3_cash_conservation(ledger: pl.DataFrame) -> CheckResult:
    """Δequity_usd == funding + tbill − fee − liq_loss − sweep + spot_mtm + perp_mtm on every bar
    after the first, to 1e-6 × NOTIONAL_USD."""
    flows = ledger.select(
        pl.col("funding_usd")
        + pl.col("tbill_usd")
        - pl.col("fee_usd")
        - pl.col("liq_loss_usd")
        - pl.col("sweep_usd")
        + pl.col("spot_mtm_usd")
        + pl.col("perp_mtm_usd")
    ).to_series()
    residual = (ledger["equity_usd"].diff() - flows)[1:].abs()
    worst = float(residual.max() or 0.0)  # type: ignore[arg-type]  # None when empty
    tol = 1e-6 * NOTIONAL_USD
    numbers: dict[str, float | int] = {
        "bars": ledger.height,
        "max_abs_residual_usd": worst,
        "tolerance_usd": tol,
    }
    detail = f"largest |residual| {worst:.3g} USD over {residual.len()} bars (tolerance {tol:g})"
    return CheckResult(
        "E3 cash conservation", _status(residual.len() > 0 and worst < tol), detail, numbers
    )
