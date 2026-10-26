import dataclasses
import math

import numpy as np
import polars as pl
import pytest

from perp_carry_economics.capacity import (
    SIZES,
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

ZERO = Decomposition(*[0.0] * len(dataclasses.fields(Decomposition)))


def base(spread: float, capital_usd: float = 1.5e7) -> Decomposition:
    return dataclasses.replace(ZERO, spread=spread, capital_usd=capital_usd)


def book(ts_us: int, levels: list[tuple[float, float]]) -> pl.DataFrame:
    """One snapshot: (signed pct, cumulative notional) levels, plus an ask side to ignore."""
    rows = levels + [(-p, n) for p, n in levels]
    return pl.DataFrame(rows, schema=["pct", "notional_usd"], orient="row").select(
        ts_us=pl.lit(ts_us, pl.Int64), pct="pct", notional_usd="notional_usd"
    )


def ledger(rows: list[tuple[int, float, float, float]]) -> pl.DataFrame:
    """(ts_us, perp_qty, mark, sweep_usd) rows."""
    cols = ["ts_us", "perp_qty", "mark", "sweep_usd"]
    data = {c: [r[k] for r in rows] for k, c in enumerate(cols)}
    return pl.DataFrame(data, schema={c: LEDGER_SCHEMA[c] for c in cols})


def test_sizes_grid():
    assert SIZES == (1e6, 3e6, 1e7, 3e7, 1e8, 3e8, 1e9)


def test_fit_kappa_recovers_slope_on_synthetic():
    rng = np.random.default_rng(0)
    n, kappa = 500, 2e-4
    ts = np.arange(n) * 8 * HOUR_US
    dx = rng.normal(0, 0.05, n)
    log_ratio = np.cumsum(dx)
    # funding change = drift + kappa * dlog(ratio) + noise; the intercept takes the drift
    rate = np.cumsum(1e-6 + kappa * dx + rng.normal(0, 2e-6, n))
    funding = pl.DataFrame({"ts_us": ts, "rate": rate})
    # metrics a minute before each print, and a junk row a second after it that as-of
    # backward must never see
    oi = pl.DataFrame(
        {
            "ts_us": np.concatenate([ts - 60 * US, ts + US]),
            "oi_btc": 1.0,
            "oi_usd": 1e9,
            "top_ls_position_ratio": np.concatenate([np.exp(log_ratio), np.full(n, 9.0)]),
        }
    ).sort("ts_us")
    k, se, used = fit_kappa(funding, oi)
    assert used == n - 1
    assert 0 < se < kappa / 10
    assert abs(k - kappa) < 2 * se


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


def test_slippage_yield_uses_reset_turnover_not_full_round_trips():
    notional, capital, years = 1e7, 1.5e7, 2.0
    depth = book(10 * DAY_US, [(-1.0, BAND)])
    led = ledger(
        [
            (0, 200.0, 5e4, 0.0),  # seed, before the depth period: not priced
            (20 * DAY_US, 190.0, 5e4, 7.0),  # first reset in the depth period: full open
            (40 * DAY_US, 180.0, 6e4, 0.0),  # top-up buy-back, not a reset: not priced
            (50 * DAY_US, 200.0, 5e4, 3.0),  # reset: |dq| x mark = 20 x 5e4 = 1e6
            (60 * DAY_US, 0.0, 5e4, 9.0),  # last bar: full close
        ]
    )
    got = slippage_yield([notional, 2 * notional], depth, led, years, capital, notional)
    for size, y in zip([notional, 2 * notional], got, strict=True):
        k = size / notional  # the reset turnover scales with the position
        both_legs = 2 * (cost_usd(size) + cost_usd(k * 1e6) + cost_usd(size))
        assert y == pytest.approx(both_legs / years / (k * capital), rel=1e-9)
        full_round_trips = 2 * 3 * 2 * cost_usd(size) / years / (k * capital)
        assert y < full_round_trips / 2


def test_depth_snapshot_is_asof_backward():
    notional, capital, years = 1e7, 1.5e7, 1.0
    # a shallow book at t=10 s and a deep one at t=40 s; bars at t=35 s and t=70 s
    depth = pl.concat([book(10 * US, [(-1.0, 1e9)]), book(40 * US, [(-1.0, 1e12)])])
    led = ledger([(35 * US, 200.0, 5e4, 1.0), (70 * US, 0.0, 5e4, 1.0)])
    (got,) = slippage_yield([notional], depth, led, years, capital, notional)
    shallow = notional * notional / 1e9 * 0.005
    deep = notional * notional / 1e12 * 0.005
    assert got == pytest.approx(2 * (shallow + deep) / years / capital, rel=1e-9)


def test_units_are_fractions_of_capital():
    # s = 1e7 / 1e9 = 0.01; the haircut is a per-print rate on the notional
    oi = pl.DataFrame({"ts_us": [0, 1], "oi_btc": 1.0, "oi_usd": [1e9, 1e9]})
    kappa, notional, capital = 1e-4, 1e7, 1.5e7
    got = funding_haircut_yield(1e7, kappa, oi, capital, notional)
    assert got == pytest.approx(kappa * math.log(1.02) * 1095 * notional / capital, rel=1e-12)
    # the share of OI sets the haircut, not the base desk's notional
    assert funding_haircut_yield(1e7, kappa, oi, 2 * capital, 2 * notional) == pytest.approx(got)


def test_capacity_curve_subtracts_both_terms():
    oi = pl.DataFrame({"ts_us": [0], "oi_btc": 1.0, "oi_usd": [1e10]})
    depth = book(0, [(-1.0, 1e11)])
    led = ledger([(DAY_US, 200.0, 5e4, 1.0), (366 * DAY_US, 0.0, 5e4, 1.0)])
    curve = capacity_curve((1e7, 1e8), base(0.05), 1e-4, oi, depth, led)
    assert curve.columns == [
        "size_usd",
        "oi_share",
        "funding_haircut_yield",
        "slippage_yield",
        "modelled_spread",
    ]
    assert curve["oi_share"].to_list() == pytest.approx([1e-3, 1e-2])
    hair = curve["funding_haircut_yield"].to_list()
    slip = slippage_yield([1e7, 1e8], depth, led, 365 / 365.25, 1.5e7)
    assert curve["slippage_yield"].to_list() == pytest.approx(slip)
    assert curve["modelled_spread"].to_list() == pytest.approx(
        [0.05 - h - s for h, s in zip(hair, slip, strict=True)]
    )


def curve(modelled: list[float]) -> pl.DataFrame:
    return pl.DataFrame({"size_usd": [1e6, 1e7, 1e8], "modelled_spread": modelled})


def test_capacity_is_none_when_spread_nonpositive():
    assert capacity_usd(curve([-0.02, -0.03, -0.04]), base(-0.01)) is None
    assert capacity_usd(curve([-0.02, -0.03, -0.04]), base(0.0)) is None


def test_capacity_is_none_when_curve_flat():
    assert capacity_usd(curve([0.04, 0.04, 0.04]), base(0.04)) is None


def test_capacity_interpolates_half_spread():
    # half of 0.04 is 0.02, halfway from 0.03 at 1e7 to 0.01 at 1e8
    assert capacity_usd(curve([0.039, 0.03, 0.01]), base(0.04)) == pytest.approx(5.5e7)
    # at or below half already at the first grid point: that point
    assert capacity_usd(curve([0.02, 0.01, 0.0]), base(0.04)) == pytest.approx(1e6)
