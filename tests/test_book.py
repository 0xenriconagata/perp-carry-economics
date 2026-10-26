import math

import polars as pl
import pytest
from bars_factory import MIN_US, P0, TBILL, flat, frame, rally, ts_of, with_funding

from perp_carry_economics.carry.book import Mode, events, run_book
from perp_carry_economics.config import DAY_US
from perp_carry_economics.data.rules import Rules
from perp_carry_economics.io import write_parquet_atomic
from perp_carry_economics.params import MarginSetting

N = 1e6
F_S, F_P = 0.001, 0.0005
MAINT = 0.005
RULES = Rules(
    spot_taker=F_S,
    perp_taker=F_P,
    liquidation_fee=0.0125,
    btc_collateral_haircut=0.05,
    tiers=((math.inf, MAINT, 0.0),),
)
TAU = 60 * MIN_US
SETTING = MarginSetting(m0=0.25, trigger=0.15, tau_us=TAU, reserve_frac=0.25)
Q0 = N / P0  # BTC on both legs after a seed at P0 with spot == mark
CASH0 = 0.25 * N - F_P * N  # margin cash after the seed: m0 N less the perp entry fee
TOL = 1e-6


def run(bars, setting=SETTING, mode=Mode.SEPARATE):
    return run_book(bars, setting, RULES, mode, notional=N)


def _frac(m, cash=CASH0, entry=P0, q=Q0):
    """Hand margin ratio of the seeded short at close m (no funding, no top-up yet)."""
    return (cash + (entry - m) * q) / (m * q)


def _first(cond):
    return next(i for i, c in enumerate(cond) if c)


def _two_month_bars():
    """Jan + Feb 2024: flat, a one-bar +30% jump (liquidation), then a +15% 6-hour rally in
    February (top-up); spot carries a small daily-varying basis, highs sit 5 bp above."""
    marks = flat(10) + [13_000.0] + rally(0.01, 120, 13_000.0) + flat(25, 13_130.0)
    marks += rally(0.15, 360, 13_130.0)
    marks += flat((86_400 - len(marks)) / 1440, 13_130.0 * 1.15)
    bars = frame(marks, highs=[m * 1.0005 for m in marks])
    bars = bars.with_columns(
        spot=pl.col("mark") * (1 + 0.0003 * (pl.col("ts_us") // DAY_US % 3 - 1))
    )
    return with_funding(bars, [1e-4, 2e-4, -1e-4])


@pytest.fixture(scope="module")
def two_month():
    return run(_two_month_bars())


def test_ledger_schema(two_month):
    assert two_month.schema["ts_us"] == pl.Int64
    assert two_month.schema["margin_frac"] == pl.Float64
    assert two_month.schema["liquidated"] == pl.Boolean
    assert two_month.schema["hedged"] == pl.Boolean
    assert two_month.schema["topup_pending"] == pl.Boolean
    for col in ("equity_usd", "perp_mtm_usd", "spot_mtm_usd", "sweep_usd", "fee_usd"):
        assert two_month.schema[col] == pl.Float64
    assert two_month.height == 86_400


def test_equity_changes_only_by_funding_tbill_fees_losses_sweeps_and_mtm(two_month):
    lg = two_month
    assert lg["liquidated"].sum() == 1
    assert (lg["topup_usd"] != 0).sum() >= 1
    prev = lg.shift(1)
    spot_mtm = prev["spot_qty"] * (lg["spot"] - prev["spot"])
    assert (lg["spot_mtm_usd"] - spot_mtm)[1:].abs().max() < TOL
    perp_mtm = (prev["mark"] - lg["mark"]) * prev["perp_qty"]
    assert (lg["perp_mtm_usd"] - perp_mtm).filter(~lg["liquidated"])[1:].abs().max() < TOL
    flows = (
        lg["funding_usd"]
        + lg["tbill_usd"]
        - lg["fee_usd"]
        - lg["liq_loss_usd"]
        - lg["sweep_usd"]
        + lg["spot_mtm_usd"]
        + lg["perp_mtm_usd"]
    )
    residual = (lg["equity_usd"] - prev["equity_usd"] - flows)[1:]
    assert residual.abs().max() < TOL
    # unhedged P&L is the spot MTM on bars that started unhedged, and nothing else
    started_unhedged = ~prev["hedged"].fill_null(True)
    assert lg["unhedged_pnl_usd"].abs().sum() > 0
    assert (lg["unhedged_pnl_usd"] - lg["spot_mtm_usd"]).filter(started_unhedged).abs().max() == 0
    assert lg["unhedged_pnl_usd"].filter(~started_unhedged).abs().max() == 0
    # funding: the print pays rate x previous mark x previous short (0 while unhedged)
    rate = _two_month_bars()["funding_rate"]
    assert (
        lg["funding_usd"] - (rate * prev["mark"] * prev["perp_qty"]).fill_null(0.0)
    ).abs().max() == 0
    ev = events(lg)
    assert ev["liquidated"].sum() == 1
    assert ev.height == ((lg["topup_usd"] != 0) | (lg["sweep_usd"] != 0) | lg["liquidated"]).sum()


def test_flat_price_pnl_is_funding_minus_fees_and_tbill():
    lg = run(with_funding(frame(flat(60)), [1e-4]))
    assert lg["fee_usd"][0] == pytest.approx(F_S * N + F_P * N, abs=1e-9)
    # 60 days x 3 prints, none on the seed bar; the short is Q0 at P0 throughout
    assert lg["funding_usd"].sum() == pytest.approx(179 * 1e-4 * P0 * Q0, abs=TOL)
    assert lg["liquidated"].sum() == 0
    pnl = lg["funding_usd"].sum() + lg["tbill_usd"].sum() - lg["fee_usd"].sum()
    assert lg["sweep_usd"].sum() == pytest.approx(pnl, abs=TOL)


def test_rally_within_tau_liquidates_with_exact_loss():
    # the close (12,000) leaves equity above maintenance; the high (13,000) does not
    lg = run(frame([P0, 12_000.0] + [12_000.0] * 70, highs=[P0, 13_000.0] + [12_000.0] * 70))
    assert CASH0 + (P0 - 12_000.0) * Q0 > MAINT * 12_000.0 * Q0
    # liquidation price: CASH0 + (P0 - m) Q0 == MAINT m Q0
    m_liq = (CASH0 + P0 * Q0) / (Q0 * (1 + MAINT))
    assert P0 <= m_liq <= 13_000.0
    assert lg["liquidated"].to_list()[:3] == [False, True, False]
    assert lg["liq_loss_usd"][1] == pytest.approx(MAINT * m_liq * Q0, abs=TOL)
    assert lg["perp_mtm_usd"][1] == pytest.approx((P0 - m_liq) * Q0, abs=TOL)
    unhedged = lg[1:61]  # bars at +1 .. +60 minutes: before the liquidation bar + tau
    assert not unhedged["hedged"].any()
    assert (unhedged["perp_qty"] == 0).all()
    assert unhedged["margin_frac"].null_count() == 60
    back = lg.row(61, named=True)
    assert back["hedged"] and back["perp_qty"] > 0
    assert back["perp_qty"] == pytest.approx(back["spot_qty"], rel=1e-12)


def test_reestablish_uses_reserve_before_selling_spot():
    setting = MarginSetting(m0=0.1, trigger=0.06, tau_us=TAU, reserve_frac=0.25)
    lg = run(frame([P0, 11_000.0] + [11_000.0] * 70), setting)
    assert lg["liquidated"][1]
    back = lg.row(61, named=True)
    assert back["hedged"]
    assert lg["reserve"][60] >= 0.1 * 11_000.0 * Q0  # the reserve covers the new margin
    assert lg["spot_sold_btc"].sum() == 0
    assert back["spot_qty"] == Q0 and back["perp_qty"] == Q0
    assert back["reserve"] == pytest.approx(lg["reserve"][60] - 0.1 * 11_000.0 * Q0, abs=TOL)


def _landing(marks, ts, deadline):
    land = _first(t >= deadline for t in ts)
    m = marks[land]
    return land, 0.25 * m * Q0 - (CASH0 + (P0 - m) * Q0)


def test_slow_rally_tops_up_from_reserve_first_exact():
    marks = flat(1 / 24) + rally(0.15, 360) + flat(2 / 24, P0 * 1.15)
    lg = run(frame(marks))
    ts = lg["ts_us"].to_list()
    breach = _first(_frac(m) < 0.15 for m in marks)
    land, a = _landing(marks, ts, ts[breach] + TAU)
    reserve0 = (0.25 * N - F_S * N) * (1 + TBILL / 365)  # seed, then one day's T-bill
    assert a < reserve0
    topups = lg.filter(pl.col("topup_usd") != 0)
    assert topups.height == 1
    assert topups["ts_us"][0] == ts[land]
    assert topups["topup_usd"][0] == pytest.approx(a, abs=TOL)
    assert lg["reserve"][land] == pytest.approx(reserve0 - a, abs=TOL)
    assert lg["spot_sold_btc"].sum() == 0
    assert lg["topup_pending"][breach] and not lg["topup_pending"][breach - 1]
    assert lg["topup_pending"][land - 1] and not lg["topup_pending"][land]


def test_topup_sells_spot_when_reserve_exhausted_exact():
    setting = MarginSetting(m0=0.25, trigger=0.15, tau_us=TAU, reserve_frac=0.05)
    marks = flat(1 / 24) + rally(0.45, 2880) + flat(1 / 24, P0 * 1.45)
    lg = run(frame(marks), setting)
    ts = lg["ts_us"].to_list()
    breach = _first(_frac(m) < 0.15 for m in marks)
    land, a = _landing(marks, ts, ts[breach] + TAU)
    reserve0 = (0.05 * N - F_S * N) * (1 + TBILL / 365)
    shortfall = a - reserve0
    assert shortfall > 0
    ds = shortfall / (marks[land] * (1 - F_S))
    first = lg.filter(pl.col("topup_usd") != 0).row(0, named=True)
    assert first["ts_us"] == ts[land]
    assert first["topup_usd"] == pytest.approx(a, abs=TOL)
    assert first["spot_sold_btc"] == pytest.approx(ds, rel=1e-12)
    assert first["spot_qty"] == pytest.approx(Q0 - ds, rel=1e-12)
    assert first["reserve"] == pytest.approx(0.0, abs=TOL)
    hedged = lg.filter(pl.col("hedged"))
    assert ((hedged["perp_qty"] - hedged["spot_qty"]).abs() < 1e-12).all()


def test_breach_that_heals_before_tau_lands_nothing():
    marks = flat(1 / 24) + rally(0.12, 30) + rally(-0.12 / 1.12, 10, P0 * 1.12) + flat(2 / 24)
    lg = run(frame(marks))
    ts = lg["ts_us"].to_list()
    breach = _first(_frac(m) < 0.15 for m in marks)
    land = _first(t >= ts[breach] + TAU for t in ts)
    assert _frac(marks[land]) >= 0.15
    assert lg["topup_pending"][breach] and lg["topup_pending"][land - 1]
    assert not lg["topup_pending"][land:].any()
    assert lg["topup_usd"].abs().sum() == 0
    assert lg["spot_sold_btc"].sum() == 0
    assert lg["liquidated"].sum() == 0


def test_topup_lands_on_first_bar_after_gap():
    marks = flat(1 / 24) + rally(0.15, 360) + flat(2 / 24, P0 * 1.15)
    full = frame(marks)
    ts = full["ts_us"].to_list()
    deadline = ts[_first(_frac(m) < 0.15 for m in marks)] + TAU
    bars = full.filter(~pl.col("ts_us").is_between(deadline - 15 * MIN_US, deadline + 15 * MIN_US))
    assert bars.height == full.height - 31
    lg = run(bars)
    kept = bars["mark"].to_list()
    land, a = _landing(kept, lg["ts_us"].to_list(), deadline)
    assert lg["ts_us"][land] == deadline + 16 * MIN_US
    topups = lg.filter(pl.col("topup_usd") != 0)
    assert topups["ts_us"].to_list() == [lg["ts_us"][land]]
    assert topups["topup_usd"][0] == pytest.approx(a, abs=TOL)


def test_reset_cancels_pending_topup_and_restores_pots():
    marks = flat(1 / 24) + rally(0.12, 30) + flat(3.5 / 24, P0 * 1.12)
    lg = run(frame(marks, start_us=ts_of(2024, 1, 31, 22), tbill_rate=0.0))
    r = lg["ts_us"].to_list().index(ts_of(2024, 2, 1))
    breach = _first(_frac(m) < 0.15 for m in marks)
    assert lg["ts_us"][breach] + TAU > lg["ts_us"][r]  # the top-up would land after the reset
    assert lg["topup_pending"][r - 1]
    assert not lg["topup_pending"][r:].any()
    assert lg["topup_usd"].abs().sum() == 0
    reset = lg.row(r, named=True)
    m = P0 * 1.12
    assert reset["margin_equity"] == pytest.approx(0.25 * N, abs=TOL)
    assert reset["reserve"] == pytest.approx(0.25 * N, abs=TOL)
    assert reset["spot_qty"] == pytest.approx(N / m, rel=1e-12)
    assert reset["perp_qty"] == pytest.approx(N / m, rel=1e-12)
    assert reset["sweep_usd"] != 0


def test_reset_while_unhedged_rehedges():
    marks = flat(50 / 1440) + [13_000.0] + flat(10 / 24, 13_000.0)
    lg = run(with_funding(frame(marks, start_us=ts_of(2024, 1, 31, 23)), [1e-4]))
    ts = lg["ts_us"].to_list()
    liq = ts.index(ts_of(2024, 1, 31, 23, 50))
    r = ts.index(ts_of(2024, 2, 1))
    assert lg["liquidated"][liq] and lg["liquidated"].sum() == 1
    assert not lg["hedged"][r - 1]
    reset = lg.row(r, named=True)
    assert reset["hedged"]
    assert reset["perp_qty"] == pytest.approx(N / 13_000.0, rel=1e-12)
    assert reset["spot_qty"] == reset["perp_qty"]
    # the 00:00 print found the book unhedged; the 08:00 print is paid on the new short
    assert reset["funding_usd"] == 0
    assert reset["missed_funding_usd"] == pytest.approx(1e-4 * 13_000.0 * lg["spot_qty"][r - 1])
    at8 = lg.row(ts.index(ts_of(2024, 2, 1, 8)), named=True)
    assert at8["funding_usd"] == pytest.approx(1e-4 * 13_000.0 * N / 13_000.0, rel=1e-12)
    assert lg["spot_sold_btc"].sum() == 0  # rehedged by the reset, not by re-establishing


def test_sum_of_sweeps_equals_final_equity_minus_capital(two_month):
    lg = two_month
    last = lg.row(-1, named=True)
    # the end bar closes both legs and sweeps everything above the capital: equity is 1.5 N
    assert last["spot_qty"] == 0 and last["perp_qty"] == 0 and last["margin_equity"] == 0
    assert last["reserve"] == last["equity_usd"] == 1.5 * N
    pnl = (
        lg["funding_usd"].sum()
        + lg["tbill_usd"].sum()
        - lg["fee_usd"].sum()
        - lg["liq_loss_usd"].sum()
        + lg["spot_mtm_usd"].sum()
        + lg["perp_mtm_usd"].sum()
    )
    assert lg["sweep_usd"].sum() == pytest.approx(pnl, abs=TOL)


def test_one_account_mode_never_liquidates_on_rally():
    marks = [P0] + rally(3.0, 44_639)  # all of January 2024, +300%
    # the perp account alone would be far below maintenance at the top
    assert CASH0 + (P0 - marks[-1]) * Q0 < MAINT * marks[-1] * Q0
    lg = run(frame(marks), mode=Mode.ONE_ACCOUNT)
    assert lg["liquidated"].sum() == 0
    assert lg["hedged"][:-1].all()
    assert lg["topup_usd"].abs().sum() == 0
    assert lg["spot_sold_btc"].sum() == 0


def test_ledger_is_byte_identical_on_rerun(tmp_path, two_month):
    again = run(_two_month_bars())
    assert two_month.equals(again)
    write_parquet_atomic(two_month, tmp_path / "a.parquet")
    write_parquet_atomic(again, tmp_path / "b.parquet")
    assert (tmp_path / "a.parquet").read_bytes() == (tmp_path / "b.parquet").read_bytes()
