import dataclasses
import math

import polars as pl
import pytest

from perp_carry_economics.carry.book import LEDGER_SCHEMA
from perp_carry_economics.carry.yields import Decomposition, decompose, worth_it
from perp_carry_economics.config import DAY_US
from perp_carry_economics.params import PRIMARY, WORTH_IT_SPREAD, MarginSetting

NOTIONAL = 100.0
YEARS = 4 / 365.25
# (day, funding, tbill, fee, liq_loss, unhedged_pnl, missed, sweep): five daily rows, resets on
# days 2 and 4. Basis realised at reset 1: spot +10, perp -11 -> -1; at the close: spot -5,
# perp +7 -> +2. Hand basis = +1; the sweeps carry it (net = 2, see the test).
ROWS = [
    (0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0),
    (1, 8.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    (2, -3.0, 0.0, 2.0, 0.0, 0.0, 0.0, 1.0),
    (3, 4.0, 1.0, 0.0, 3.0, -2.0, 0.5, 0.0),
    (4, 0.0, 0.0, 2.0, 0.0, 0.0, 0.25, 1.0),
]
COLS = ["ts_us", "funding_usd", "tbill_usd", "fee_usd", "liq_loss_usd"]
COLS += ["unhedged_pnl_usd", "missed_funding_usd", "sweep_usd"]


def ledger():
    data = {c: [r[k] for r in ROWS] for k, c in enumerate(COLS)}
    data["ts_us"] = [int(day * DAY_US) for day in data["ts_us"]]
    return pl.DataFrame(data, schema={c: LEDGER_SCHEMA[c] for c in COLS})


BARS = pl.DataFrame(
    {"day_start": [True, False, True, False], "tbill_rate": [0.04, 0.04, 0.05, 0.05]}
)


def d():
    return decompose(ledger(), BARS, PRIMARY, NOTIONAL)


def test_decomposition_identity_closes():
    x = d()
    assert x.basis_usd == pytest.approx(1.0, abs=1e-12)  # the hand-computed convergence
    assert x.net_usd == 2.0
    parts = x.gross_funding_usd + x.negative_funding_usd - x.fees_usd - x.liq_loss_usd
    parts += x.unhedged_pnl_usd + x.tbill_on_idle_usd + x.basis_usd
    assert parts == pytest.approx(x.net_usd, abs=1e-12)
    assert (x.gross_funding_usd, x.negative_funding_usd) == (12.0, -3.0)
    assert (x.fees_usd, x.liq_loss_usd, x.unhedged_pnl_usd) == (5.0, 3.0, -2.0)
    assert (x.tbill_on_idle_usd, x.missed_funding_usd) == (2.0, 0.75)


def test_gross_uses_spot_notional_net_uses_capital():
    x = d()
    assert x.capital_usd == 1.5 * NOTIONAL
    assert x.gross_yield == pytest.approx(12.0 / NOTIONAL / YEARS)
    assert x.net_yield == pytest.approx(2.0 / (1.5 * NOTIONAL) / YEARS)
    assert x.tbill_mean == pytest.approx(0.045)
    assert x.spread == pytest.approx(x.net_yield - 0.045)
    other = decompose(ledger(), BARS, MarginSetting(0.5, 0.3, 1, 0.5), NOTIONAL)
    assert other.capital_usd == 2.0 * NOTIONAL


def test_years_from_bar_span():
    assert d().years == pytest.approx(YEARS)


def test_worth_it_threshold_is_2pp():
    x = d()
    assert worth_it(dataclasses.replace(x, spread=WORTH_IT_SPREAD))
    assert not worth_it(dataclasses.replace(x, spread=WORTH_IT_SPREAD - 1e-9))


def test_row_is_label_plus_every_field():
    r = d().row("B")
    assert r["label"] == "B"
    assert r["net_yield"] == d().net_yield
    assert set(r) == {"label"} | {f.name for f in dataclasses.fields(Decomposition)}
    assert all(math.isfinite(v) for k, v in r.items() if isinstance(v, float))
