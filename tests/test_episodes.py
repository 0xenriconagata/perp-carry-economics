import math
from datetime import UTC, date, datetime

import polars as pl
import pytest

from perp_carry_economics.data.rules import Rules
from perp_carry_economics.episodes import Episode, inversion_week, liquidation_week, week_slice
from perp_carry_economics.params import PRIMARY

RULES = Rules(
    spot_taker=0.001,
    perp_taker=0.0005,
    liquidation_fee=0.0125,
    btc_collateral_haircut=0.05,
    tiers=((math.inf, 0.005, 0.0),),
)
HOUR = 3_600_000_000


def us(y, m, d, h=0):
    return int(datetime(y, m, d, h, tzinfo=UTC).timestamp()) * 1_000_000


def ledger(rows):
    """rows: (ts_us, margin_frac, topup_pending, liquidated); mark 100, perp_qty 1."""
    return pl.DataFrame(
        {
            "ts_us": [r[0] for r in rows],
            "mark": [100.0] * len(rows),
            "perp_qty": [1.0] * len(rows),
            "margin_frac": [r[1] for r in rows],
            "topup_pending": [r[2] for r in rows],
            "liquidated": [r[3] for r in rows],
        },
        schema_overrides={"margin_frac": pl.Float64},
    )


def test_liquidation_week_is_first_liquidation():
    led = ledger(
        [
            (us(2021, 3, 1), 0.25, False, False),
            (us(2021, 3, 10, 5), 0.01, False, True),  # Wednesday: week of Mar 8
            (us(2021, 3, 20), 0.01, False, True),  # a later liquidation is ignored
        ]
    )
    ep = liquidation_week(led, PRIMARY, RULES)
    assert ep == Episode(
        "liquidation", date(2021, 3, 8), date(2021, 3, 15), us(2021, 3, 10, 5), ep.note
    )


def test_closest_call_when_no_liquidation():
    led = ledger(
        [
            (us(2021, 3, 1), 0.25, False, False),
            (us(2021, 3, 2), 0.14, True, False),  # breach, week of Mar 1
            (us(2021, 3, 2, 4), 0.25, False, False),  # landing
            (us(2021, 3, 10), 0.08, True, False),  # deeper breach, week of Mar 8
            (us(2021, 3, 10, 1), 0.06, True, False),  # lowest while pending
            (us(2021, 3, 10, 4), 0.25, False, False),  # landing
            (us(2021, 3, 20), 0.01, False, False),  # lower but never pending: ignored
        ]
    )
    ep = liquidation_week(led, PRIMARY, RULES)
    assert (ep.kind, ep.note) == ("liquidation", "no liquidation; closest call")
    assert (ep.week_start, ep.week_end, ep.anchor_us) == (
        date(2021, 3, 8),
        date(2021, 3, 15),
        us(2021, 3, 10, 1),
    )


def test_closest_call_is_max_score_not_lowest_margin_when_tiers_differ():
    # continuous two-tier schedule: maintenance_frac is 0.005 below 1e6 notional, 0.0275 at 2e6
    rules = Rules(0.001, 0.0005, 0.0125, 0.05, ((1e6, 0.005, 0.0), (math.inf, 0.05, 45_000.0)))
    led = ledger(
        [
            (us(2021, 3, 2), 0.06, True, False),  # A: lowest fraction, notional 5e5 -> 0.6207
            (us(2021, 3, 10), 0.07, True, False),  # B: notional 2e6 -> 0.08 / 0.1225 = 0.653
        ]
    ).with_columns(perp_qty=pl.Series([5_000.0, 20_000.0]))
    ep = liquidation_week(led, PRIMARY, rules)
    assert ep.anchor_us == us(2021, 3, 10)
    assert ep.week_start == date(2021, 3, 8)


def test_closest_call_without_pending_bars_falls_back_to_lowest_margin():
    led = ledger(
        [
            (us(2021, 3, 1), None, False, False),  # unhedged: null fraction
            (us(2021, 3, 2), 0.20, False, False),
            (us(2021, 3, 17), 0.16, False, False),
        ]
    )
    ep = liquidation_week(led, PRIMARY, RULES)
    assert ep.week_start == date(2021, 3, 15)
    assert ep.anchor_us == us(2021, 3, 17)
    assert "no top-up pending" in ep.note


def print_bars(rates, start_us):
    """One print per 8 h, each followed by a bar without a print."""
    ts = [start_us + k * 8 * HOUR + j * 60_000_000 for k in range(len(rates)) for j in (0, 1)]
    rate = [x for r in rates for x in (r, None)]
    return pl.DataFrame(
        {"ts_us": ts, "funding_rate": rate}, schema_overrides={"funding_rate": pl.Float64}
    )


def test_inversion_week_is_lowest_trailing_funding():
    # 90 prints from Mon 2021-03-01, 3 a day
    rates = [1e-4] * 90
    for k in range(60, 69):
        rates[k] = -1e-3
    ep = inversion_week(print_bars(rates, us(2021, 3, 1)))
    assert ep.kind == "inversion"
    # the trailing mean is flat over prints 68-80 (all nine dip prints in the window), first wins:
    # print 68 = 2021-03-23 16:00
    assert ep.anchor_us == us(2021, 3, 23, 16)
    assert (ep.week_start, ep.week_end) == (date(2021, 3, 22), date(2021, 3, 29))


def test_inversion_week_skips_the_incomplete_window():
    # the lowest raw prints come first; the first eligible window ends at print 20 = Sun 16:00
    ep = inversion_week(print_bars([-1e-2] * 20 + [1e-4] * 40, us(2021, 3, 1)))
    assert ep.anchor_us == us(2021, 3, 7, 16)
    assert ep.week_start == date(2021, 3, 1)


def test_inversion_week_needs_a_full_window():
    with pytest.raises(ValueError):
        inversion_week(print_bars([1e-4] * 20, us(2021, 3, 1)))


@pytest.mark.parametrize(
    ("ts", "start"),
    [
        (us(2021, 3, 8), date(2021, 3, 8)),  # Monday 00:00 starts its own week
        (us(2021, 3, 7, 23), date(2021, 3, 1)),  # Sunday 23:00 belongs to the week before
        (us(2020, 9, 1), date(2020, 8, 31)),  # across a month boundary
        (us(2021, 1, 1), date(2020, 12, 28)),  # across a year boundary
    ],
)
def test_week_starts_monday_utc(ts, start):
    ep = liquidation_week(ledger([(ts, 0.0, False, True)]), PRIMARY, RULES)
    assert ep.week_start == start
    assert ep.week_start.weekday() == 0
    assert (ep.week_end - ep.week_start).days == 7


def test_week_slice_is_the_weeks_rows_end_exclusive():
    led = ledger(
        [
            (us(2021, 3, 7, 23), 0.2, False, False),
            (us(2021, 3, 8), 0.2, False, False),
            (us(2021, 3, 14, 23), 0.2, False, False),
            (us(2021, 3, 15), 0.2, False, False),
        ]
    )
    ep = Episode("x", date(2021, 3, 8), date(2021, 3, 15), 0, "")
    got = week_slice(led, ep)
    assert got["ts_us"].to_list() == [us(2021, 3, 8), us(2021, 3, 14, 23)]
    assert got.columns == led.columns
