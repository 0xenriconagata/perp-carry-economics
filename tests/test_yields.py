import dataclasses
import math

import polars as pl
import pytest
from bars_factory import P0, flat, frame, rally, with_funding

from perp_carry_economics.carry.book import LEDGER_SCHEMA, Mode, run_book
from perp_carry_economics.carry.yields import Decomposition, decompose, worth_it
from perp_carry_economics.config import DAY_US
from perp_carry_economics.data.rules import Rules
from perp_carry_economics.params import PRIMARY, WORTH_IT_SPREAD, MarginSetting

RULES = Rules(
    spot_taker=0.001,
    perp_taker=0.0005,
    liquidation_fee=0.0125,
    btc_collateral_haircut=0.05,
    tiers=((math.inf, 0.005, 0.0),),
)
NOTIONAL = 100.0
YEARS = 4 / 365.25
# (day, funding, tbill, fee, liq_loss, unhedged_pnl, missed, spot_mtm, perp_mtm, sweep): five
# daily rows. Hedged MTM that does not cancel: spot +10 / perp -11 on day 1 and spot -5 / perp
# +7 on day 4; day 3 is the liquidation bar (spot -5, perp +3, unhedged -2). Net = 2.
ROWS = [
    (0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    (1, 8.0, 1.0, 0.0, 0.0, 0.0, 0.0, 10.0, -11.0, 0.0),
    (2, -3.0, 0.0, 2.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0),
    (3, 4.0, 1.0, 0.0, 3.0, -2.0, 0.5, -5.0, 3.0, 0.0),
    (4, 0.0, 0.0, 2.0, 0.0, 0.0, 0.25, -5.0, 7.0, 1.0),
]
COLS = ["ts_us", "funding_usd", "tbill_usd", "fee_usd", "liq_loss_usd", "unhedged_pnl_usd"]
COLS += ["missed_funding_usd", "spot_mtm_usd", "perp_mtm_usd", "sweep_usd"]


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
    mtm = math.fsum(r[7] + r[8] for r in ROWS)
    unhedged = math.fsum(r[5] for r in ROWS)
    # engine conservation law: the basis is the legs' MTM less what the unhedged bars took
    assert x.basis_usd == pytest.approx(mtm - unhedged, abs=1e-12)
    assert x.basis_usd == pytest.approx(1.0, abs=1e-12)
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


def test_basis_reconciles_to_engine_mtm():
    marks = flat(10) + rally(0.01, 120) + flat(25, P0 * 1.01)
    bars = frame(marks).with_columns(
        spot=pl.col("mark") * (1 + 0.0003 * (pl.col("ts_us") // DAY_US % 3 - 1))
    )
    lg = run_book(with_funding(bars, [1e-4, -1e-4]), PRIMARY, RULES, Mode.SEPARATE, notional=1e6)
    x = decompose(lg, bars, PRIMARY, 1e6)
    mtm = lg["spot_mtm_usd"].sum() + lg["perp_mtm_usd"].sum()
    assert x.basis_usd != 0
    assert x.basis_usd == pytest.approx(mtm - lg["unhedged_pnl_usd"].sum(), abs=1e-6)
