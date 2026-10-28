import dataclasses
import math

import numpy as np
import polars as pl
import pytest

from perp_carry_economics.capacity import (
    capacity_curve,
    capacity_usd,
    depth_cost,
    fit_kappa,
    funding_haircut_yield,
    slippage_yield,
)
from perp_carry_economics.carry.book import LEDGER_SCHEMA
from perp_carry_economics.carry.yields import Decomposition
from perp_carry_economics.config import DAY_US, HOUR_US, US
from perp_carry_economics.params import NOTIONAL_USD, SIZES

ZERO = Decomposition(*[0.0] * len(dataclasses.fields(Decomposition)))


def base(spread: float, capital_usd: float = 1.5e7, years: float = 6.0) -> Decomposition:
    return dataclasses.replace(ZERO, spread=spread, capital_usd=capital_usd, years=years)


def book(ts_us: int, levels: list[tuple[float, float]]) -> pl.DataFrame:
    """One snapshot: (signed pct, cumulative notional) levels, plus an ask side to ignore."""
    rows = levels + [(-p, n) for p, n in levels]
    return pl.DataFrame(rows, schema=["pct", "notional_usd"], orient="row").select(
        ts_us=pl.lit(ts_us, pl.Int64), pct="pct", notional_usd="notional_usd"
    )


def ledger(rows: list[tuple[int, float, float]]) -> pl.DataFrame:
    """(ts_us, perp_qty, mark) rows."""
    cols = ["ts_us", "perp_qty", "mark"]
    data = {c: [r[k] for r in rows] for k, c in enumerate(cols)}
    return pl.DataFrame(data, schema={c: LEDGER_SCHEMA[c] for c in cols})


def test_sizes_grid():
    assert SIZES == (1e6, 3e6, 1e7, 3e7, 1e8, 3e8, 1e9)
    assert NOTIONAL_USD in SIZES  # the capacity reference is a grid point


N_PRINTS, KAPPA = 500, 2e-4


def synthetic() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(print ts, rate, metrics ts, ratio): funding change = drift + kappa dlog(ratio) + noise;
    metrics a minute before each print, and a junk row a second after it that as-of backward
    must never see."""
    rng = np.random.default_rng(0)
    ts = np.arange(N_PRINTS) * 8 * HOUR_US
    dx = rng.normal(0, 0.05, N_PRINTS)
    rate = np.cumsum(1e-6 + KAPPA * dx + rng.normal(0, 2e-6, N_PRINTS))
    oi_ts = np.concatenate([ts - 60 * US, ts + US])
    ratio = np.concatenate([np.exp(np.cumsum(dx)), np.full(N_PRINTS, 9.0)])
    return ts, rate, oi_ts, ratio


def fit(ts, rate, oi_ts, ratio) -> tuple[float, float, int]:
    funding = pl.DataFrame({"ts_us": ts, "rate": rate})
    oi = pl.DataFrame(
        {"ts_us": oi_ts, "oi_btc": 1.0, "oi_usd": 1e9, "top_ls_position_ratio": ratio}
    ).sort("ts_us")
    return fit_kappa(funding, oi)


def test_fit_kappa_recovers_slope_on_synthetic():
    k, se, used = fit(*synthetic())
    assert used == N_PRINTS - 1
    assert 0 < se < KAPPA / 10
    assert abs(k - KAPPA) < 2 * se


def test_fit_kappa_drops_stale_sample_and_never_diffs_across_the_gap():
    ts, rate, oi_ts, ratio = synthetic()
    oi_ts[100] = ts[100] - 20 * 60 * US  # print 100's sample is 20 minutes old
    ratio[100] = 9.0
    k, se, used = fit(ts, rate, oi_ts, ratio)
    # print 100 goes, and with it the diffs 99->100 and 100->101; no 16h diff 99->101
    assert used == N_PRINTS - 1 - 2
    assert abs(k - KAPPA) < 2 * se


def test_fit_kappa_drops_null_ratio_prints_and_never_diffs_across_them():
    ts, rate, oi_ts, ratio = synthetic()
    ratio = ratio.tolist()
    ratio[200:210] = [None] * 10  # prints 200-209 sample a blank ratio
    k, se, used = fit(ts, rate, oi_ts, ratio)
    # diffs 199->200 .. 209->210 go (11); no 16h+ diff across the stretch
    assert used == N_PRINTS - 1 - 11
    assert abs(k - KAPPA) < 2 * se


def test_fit_kappa_drops_nonfinite_rows():
    ts, rate, oi_ts, ratio = synthetic()
    rate[100] = np.nan
    k, se, used = fit(ts, rate, oi_ts, ratio)
    assert used == N_PRINTS - 1 - 2
    assert math.isfinite(k) and abs(k - KAPPA) < 2 * se


def test_depth_cost_within_band_is_uniform():
    # band 1: 0..1% holding 100; band 2: 1..2% holding 200 (cumulative 300)
    snap = book(0, [(-1.0, 100.0), (-2.0, 300.0)])
    assert depth_cost(50.0, snap) == pytest.approx(0.25 * 1.0 / 100, rel=1e-12)
    # all of band 1 at a mean 0.5%, then a quarter of band 2 (50) at 1% + 0.25 x 1% / 2
    expected = (100 * 0.5 + 50 * (1.0 + 0.25 * 1.0 / 2)) / 150 / 100
    assert depth_cost(150.0, snap) == pytest.approx(expected, rel=1e-12)


def test_depth_cost_raises_beyond_book():
    snap = book(0, [(-1.0, 100.0), (-2.0, 300.0)])
    assert depth_cost(300.0, snap) == pytest.approx((100 * 0.5 + 200 * 1.5) / 300 / 100)
    with pytest.raises(ValueError):
        depth_cost(300.01, snap)


BAND = 1e12  # one 0..1% band so deep that a fill f costs f / BAND x 0.5%


def cost_usd(f: float) -> float:
    return f * (f / BAND * 0.01 / 2)


def test_slippage_yield_uses_turnover_not_full_round_trips():
    notional, capital, depth_years, base_years = 1e7, 1.5e7, 2.0, 5.0
    depth = book(10 * DAY_US, [(-1.0, BAND)])
    led = ledger(
        [
            (0, 200.0, 5e4),  # seed, before the depth period: not priced
            (20 * DAY_US, 190.0, 5e4),  # first bar in the depth period: full open
            (30 * DAY_US, 190.0, 5e4),  # no perp trade: nothing to price
            (40 * DAY_US, 180.0, 6e4),  # top-up buy-back, not a reset: |dq| x mark = 6e5
            (50 * DAY_US, 200.0, 5e4),  # reset: |dq| x mark = 20 x 5e4 = 1e6
            (60 * DAY_US, 0.0, 5e4),  # last bar: full close
        ]
    )
    sizes = [notional, 2 * notional]
    got = slippage_yield(sizes, depth, led, depth_years, base_years, capital, notional)
    for size, y in zip(sizes, got, strict=True):
        k = size / notional  # the turnover scales with the position
        once = cost_usd(size) + cost_usd(size)
        recurring = cost_usd(k * 6e5) + cost_usd(k * 1e6)
        both_legs = 2 * (once / base_years + recurring / depth_years)
        assert y == pytest.approx(both_legs / (k * capital), rel=1e-9)
        full_round_trips = 2 * 3 * 2 * cost_usd(size) / depth_years / (k * capital)
        assert y < full_round_trips / 2


def test_slippage_one_off_fills_use_base_years_turnover_depth_years():
    depth = book(0, [(-1.0, BAND)])
    led = ledger([(DAY_US, 200.0, 5e4), (2 * DAY_US, 100.0, 5e4), (3 * DAY_US, 0.0, 5e4)])
    once, recurring = 2 * 2 * cost_usd(1e7) / 1.5e7, 2 * cost_usd(5e6) / 1.5e7
    for depth_years, base_years in [(1.0, 1.0), (1.0, 4.0), (3.0, 4.0)]:
        (got,) = slippage_yield([1e7], depth, led, depth_years, base_years, 1.5e7)
        assert got == pytest.approx(once / base_years + recurring / depth_years, rel=1e-9)


def test_slippage_yield_is_none_when_the_book_is_exhausted():
    depth = book(0, [(-1.0, 1e8)])
    led = ledger([(DAY_US, 200.0, 5e4), (3 * DAY_US, 0.0, 5e4)])
    small, big = slippage_yield([1e7, 1e9], depth, led, 1.0, 1.0, 1.5e7)
    assert small is not None and small > 0
    assert big is None


def test_depth_snapshot_is_asof_backward():
    notional, capital, years = 1e7, 1.5e7, 1.0
    # a shallow book at t=10 s and a deep one at t=40 s; bars at t=35 s and t=70 s
    depth = pl.concat([book(10 * US, [(-1.0, 1e9)]), book(40 * US, [(-1.0, 1e12)])])
    led = ledger([(35 * US, 200.0, 5e4), (70 * US, 0.0, 5e4)])
    (got,) = slippage_yield([notional], depth, led, years, years, capital, notional)
    shallow = notional * notional / 1e9 * 0.005
    deep = notional * notional / 1e12 * 0.005
    assert got == pytest.approx(2 * (shallow + deep) / years / capital, rel=1e-9)


OI = pl.DataFrame({"ts_us": [0, 1], "oi_btc": 1.0, "oi_usd": [1e9, 1e9]})


def test_units_are_fractions_of_capital():
    # s = 1e7 / 1e9 = 0.01; the haircut is a per-print rate on the notional
    kappa, notional, capital = 1e-4, 1e7, 1.5e7
    got = funding_haircut_yield(1e7, kappa, OI, capital, notional)
    assert got == pytest.approx(kappa * math.log(1.02) * 1095 * notional / capital, rel=1e-12)
    # the share of OI sets the haircut, not the base desk's notional
    assert funding_haircut_yield(1e7, kappa, OI, 2 * capital, 2 * notional) == pytest.approx(got)


def test_nonpositive_kappa_gives_no_haircut():
    assert funding_haircut_yield(1e7, -1e-4, OI, 1.5e7) == 0.0
    assert funding_haircut_yield(1e7, 0.0, OI, 1.5e7) == 0.0


def test_capacity_curve_subtracts_both_terms():
    oi = pl.DataFrame({"ts_us": [0], "oi_btc": 1.0, "oi_usd": [1e10]})
    depth = book(0, [(-1.0, 1e11)])
    led = ledger([(DAY_US, 200.0, 5e4), (100 * DAY_US, 100.0, 5e4), (366 * DAY_US, 0.0, 5e4)])
    b = base(0.05)
    c = capacity_curve((1e7, 1e8), b, 1e-4, oi, depth, led)
    assert c.columns == [
        "size_usd",
        "oi_share",
        "funding_haircut_yield",
        "slippage_yield",
        "modelled_spread",
        "book_exhausted",
    ]
    assert c["oi_share"].to_list() == pytest.approx([1e-3, 1e-2])
    assert c["book_exhausted"].to_list() == [False, False]
    hair = c["funding_haircut_yield"].to_list()
    slip = slippage_yield([1e7, 1e8], depth, led, 365 / 365.25, b.years, 1.5e7)
    assert c["slippage_yield"].to_list() == pytest.approx(slip)
    assert c["modelled_spread"].to_list() == pytest.approx(
        [0.05 - h - s for h, s in zip(hair, slip, strict=True)]
    )


def test_capacity_curve_marks_exhausted_book_and_capacity_is_that_size():
    oi = pl.DataFrame({"ts_us": [0], "oi_btc": 1.0, "oi_usd": [1e12]})
    depth = book(0, [(-1.0, 1e8)])
    led = ledger([(DAY_US, 200.0, 5e4), (366 * DAY_US, 0.0, 5e4)])
    c = capacity_curve((1e7, 1e9), base(0.05), 1e-4, oi, depth, led)
    assert c["book_exhausted"].to_list() == [False, True]
    assert c["slippage_yield"][1] is None and c["modelled_spread"][1] is None
    assert c["modelled_spread"][0] > 0.025  # the half-spread is never crossed
    assert capacity_usd(c) == 1e9


def curve(modelled: list[float | None], exhausted: list[bool] | None = None) -> pl.DataFrame:
    """Grid 1e6, 1e7 (the default notional, so the reference), 1e8, 1e9."""
    return pl.DataFrame(
        {
            "size_usd": [1e6, 1e7, 1e8, 1e9],
            "modelled_spread": modelled,
            "book_exhausted": exhausted or [False] * 4,
        }
    )


def test_capacity_is_none_when_reference_spread_nonpositive():
    assert capacity_usd(curve([0.01, -0.03, -0.04, -0.05])) is None
    assert capacity_usd(curve([0.01, 0.0, -0.01, None], [False, False, False, True])) is None


def test_capacity_is_none_when_curve_flat():
    assert capacity_usd(curve([0.04, 0.04, 0.04, 0.04])) is None


def test_capacity_target_is_half_the_modelled_spread_at_notional():
    # reference 0.04 at 1e7 (not the 0.05 at 1e6): target 0.02, 2/3 of the way to 0.01 at 1e8
    got = capacity_usd(curve([0.05, 0.04, 0.01, 0.0]))
    assert got == pytest.approx(1e7 + (0.04 - 0.02) / (0.04 - 0.01) * 9e7)
    # at or below half already at the first grid point: that point
    assert capacity_usd(curve([0.02, 0.04, 0.0, 0.0])) == pytest.approx(1e6)
    # the reference follows notional
    assert capacity_usd(curve([0.05, 0.04, 0.03, 0.0]), notional=1e8) == pytest.approx(
        1e8 + (0.03 - 0.015) / 0.03 * 9e8
    )


def test_capacity_crossing_before_exhausted_book_wins():
    got = capacity_usd(curve([0.05, 0.04, 0.01, None], [False, False, False, True]))
    assert got == pytest.approx(1e7 + (0.04 - 0.02) / (0.04 - 0.01) * 9e7)


def test_capacity_is_capped_by_the_smallest_exhausted_size():
    got = capacity_usd(curve([0.05, 0.04, None, None], [False, False, True, True]))
    assert got == 1e8
    # a reference the book cannot fill leaves only the cap
    assert capacity_usd(curve([0.05, None, None, None], [False, True, True, True])) == 1e7


def test_capacity_refuses_a_notional_off_the_grid():
    with pytest.raises(ValueError, match="not a capacity grid size"):
        capacity_usd(curve([0.05, 0.04, 0.01, 0.0]), notional=5e6)
