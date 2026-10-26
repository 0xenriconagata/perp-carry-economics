import math

import polars as pl
import pytest
from bars_factory import MIN_US, P0, TBILL, flat, frame, ts_of, with_funding

from perp_carry_economics.carry.book import Mode, run_book
from perp_carry_economics.carry.filter import filter_signal, negative_stretches
from perp_carry_economics.data.rules import Rules
from perp_carry_economics.params import MarginSetting

N = 1e6
F_S, F_P = 0.001, 0.0005
RULES = Rules(
    spot_taker=F_S,
    perp_taker=F_P,
    liquidation_fee=0.0125,
    btc_collateral_haircut=0.05,
    tiers=((math.inf, 0.005, 0.0),),
)
SETTING = MarginSetting(m0=0.25, trigger=0.15, tau_us=60 * MIN_US, reserve_frac=0.25)
TOL = 1e-6
# per-print rates; annualised (x 1095): 0.131, 0.066, 0.022 against T-bill 0.05 and the
# re-entry bar 0.05 + 0.003 x 365 / 30 = 0.0865
HI, MID, LO = 1.2e-4, 6e-5, 2e-5


def run(bars, signal=None):
    return run_book(bars, SETTING, RULES, Mode.SEPARATE, notional=N, signal=signal)


def _prints(rates):
    """A print bar followed by one bar without a print, per rate."""
    out = [x for r in rates for x in (r, None)]
    return pl.DataFrame(
        {"funding_rate": out, "tbill_rate": [TBILL] * len(out)},
        schema={"funding_rate": pl.Float64, "tbill_rate": pl.Float64},
    )


def _after_print(sig, k):
    """Signal on the k-th print's bar (1-based) and on the bar after it."""
    return sig[2 * k - 2], sig[2 * k - 1]


def _residual(lg):
    prev = lg.shift(1)
    flows = (
        lg["funding_usd"]
        + lg["tbill_usd"]
        - lg["fee_usd"]
        - lg["liq_loss_usd"]
        - lg["sweep_usd"]
        + lg["spot_mtm_usd"]
        + lg["perp_mtm_usd"]
    )
    return (lg["equity_usd"] - prev["equity_usd"] - flows)[1:].abs().max()


def test_signal_exits_below_tbill_and_reenters_above_hurdle():
    # 21 HI, then LO: the trailing mean (21 - k) HI + k LO first drops below the T-bill
    # rate at k = 16; 21 MID sit between the bars (hold out); then HI: (21 - k) MID + k HI
    # first clears the re-entry bar at k = 7; then 21 MID again (hold in)
    path = [HI] * 21 + [LO] * 21 + [MID] * 21 + [HI] * 21 + [MID] * 21
    sig = filter_signal(_prints(path), RULES)
    assert sig.name == "in_position" and sig.dtype == pl.Boolean and sig.len() == 2 * len(path)
    assert sig[: 2 * 36].all()  # through the 15th LO
    assert _after_print(sig, 37) == (False, False)  # the 16th LO, held to the next print
    assert not sig[2 * 36 : 2 * 69].any()  # all of the MID stretch and 6 HI
    assert _after_print(sig, 70) == (True, True)  # the 7th HI
    assert sig[2 * 69 :].all()  # MID after HI never falls below the T-bill rate
    # True until FILTER_PRINTS prints exist, whatever they are
    lows = filter_signal(_prints([LO] * 22), RULES)
    assert lows[:40].all() and not lows[40:].any()


def test_single_negative_print_does_not_flip():
    sig = filter_signal(_prints([HI] * 21 + [-3e-4] + [HI] * 5), RULES)
    assert sig.all()


def test_signal_none_or_always_true_leaves_ledger_unchanged():
    liq = frame([P0, 12_000.0] + [12_000.0] * 70, highs=[P0, 13_000.0] + [12_000.0] * 70)
    for bars in (with_funding(frame(flat(3)), [1e-4, -2e-4]), liq):
        on = pl.Series("in_position", [True] * bars.height)
        assert run(bars, on).equals(run(bars))
    # True while unhedged after a liquidation does nothing: re-established at tau as before
    assert run(liq)["hedged"][61] and not run(liq)["hedged"][60]


def test_flat_book_earns_tbill_on_all_capital():
    bars = with_funding(frame(flat(6), start_us=ts_of(2024, 1, 30)), [1e-4])
    out, back = 720, 5040  # Jan 30 12:00, Feb 2 12:00: the signal turns False / True there
    sig = pl.Series("in_position", [i < out or i >= back for i in range(bars.height)])
    lg = run(bars, sig)
    assert _residual(lg) < TOL
    assert lg["sweep_usd"].sum() == pytest.approx(
        lg["funding_usd"].sum() + lg["tbill_usd"].sum() - lg["fee_usd"].sum(), abs=TOL
    )
    q0 = N / P0
    assert lg["hedged"][out]
    ex = lg.row(out + 1, named=True)  # closed on the next bar
    assert ex["perp_qty"] == 0 and ex["spot_qty"] == 0 and not ex["hedged"]
    assert ex["margin_equity"] == 0 and ex["margin_frac"] is None
    assert ex["fee_usd"] == pytest.approx((F_S + F_P) * P0 * q0, abs=1e-9)
    assert ex["reserve"] == ex["equity_usd"]
    flat_rows = lg[out + 1 : back + 1]
    assert (flat_rows["perp_qty"] == 0).all() and not flat_rows["hedged"].any()
    assert (flat_rows["reserve"] == flat_rows["equity_usd"]).all()
    assert flat_rows["funding_usd"].abs().sum() == 0  # prints at 16:00, 00:00, 08:00 ...
    assert flat_rows["missed_funding_usd"].abs().sum() == 0  # flat by choice: nothing missed
    ts = lg["ts_us"].to_list()
    for day in (31, 2):  # plain day starts: the whole book accrues a day of T-bill
        r = ts.index(ts_of(2024, 1 if day == 31 else 2, day))
        assert lg["tbill_usd"][r] == pytest.approx(lg["reserve"][r - 1] * TBILL / 365, abs=TOL)
        assert lg["reserve"][r] == pytest.approx(lg["reserve"][r - 1] * (1 + TBILL / 365))
    r = ts.index(ts_of(2024, 2, 1))  # month start while flat: only the sweep, down to capital
    assert lg["sweep_usd"][r] == pytest.approx(lg["reserve"][r - 1] - 1.5 * N, abs=TOL)
    assert lg["reserve"][r] == pytest.approx(1.5 * N * (1 + TBILL / 365), abs=TOL)
    assert lg["perp_qty"][r] == 0
    # re-entry: the seed procedure at the current reserve
    reserve = lg["reserve"][back]
    q = reserve / (1.5 * P0)
    en = lg.row(back + 1, named=True)
    assert en["hedged"] and en["spot_qty"] == pytest.approx(q, rel=1e-12)
    assert en["perp_qty"] == en["spot_qty"]
    assert en["margin_equity"] == pytest.approx(0.25 * P0 * q - F_P * P0 * q, abs=TOL)
    assert en["reserve"] == pytest.approx(reserve - P0 * q * (1 + F_S) - 0.25 * P0 * q, abs=TOL)
    assert en["fee_usd"] == pytest.approx((F_S + F_P) * P0 * q, abs=1e-9)
    at16 = lg.row(ts.index(ts_of(2024, 2, 2, 16)), named=True)
    assert at16["funding_usd"] == pytest.approx(1e-4 * P0 * q, abs=TOL)


def test_exit_while_unhedged_after_liquidation_stays_flat_past_tau():
    bars = frame([P0, 12_000.0] + [12_000.0] * 70, highs=[P0, 13_000.0] + [12_000.0] * 70)
    lg = run(bars, pl.Series("in_position", [i < 10 for i in range(bars.height)]))
    assert lg["liquidated"][1] and lg["spot_qty"][10] > 0
    assert lg["spot_qty"][11] == 0  # the filter sells the unhedged spot too
    assert (lg["perp_qty"][11:] == 0).all()  # and tau never re-establishes the short
    assert not lg["hedged"][1:].any()
    assert _residual(lg) < TOL


def test_negative_stretches_counts_runs():
    funding = pl.DataFrame(
        {
            "ts_us": [8 * k for k in range(9)],
            "rate": [1e-4, -1e-4, -2e-4, 1e-4, -3e-4, 0.0, -1e-4, -1e-4, -1e-4],
        }
    )
    st = negative_stretches(funding)
    assert st.columns == ["start_us", "end_us", "prints", "mean_rate"]
    assert st["start_us"].to_list() == [8, 32, 48]
    assert st["end_us"].to_list() == [16, 32, 64]
    assert st["prints"].to_list() == [2, 1, 3]
    assert st["mean_rate"].to_list() == pytest.approx([-1.5e-4, -3e-4, -1e-4])
