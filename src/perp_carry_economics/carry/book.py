"""The design-(B) hold-through book (spec 5, 7): long spot BTC in custody, short perp on USD margin.

One plain loop over the minute bars. Every step moves cash between the pots (margin, reserve,
the PM via sweeps) or books a fee, a funding print, T-bill interest or a liquidation loss, so
on every bar

    Δequity_usd == funding + tbill − fee − liq_loss − sweep + spot_mtm + perp_mtm

where the two MTM terms are the legs' price changes at the quantities held at the bar's open.
Per-bar step order (ruling R2): funding → filter exit/re-entry → reset → T-bill → equity →
liquidation → re-establish → breach → landing; the seed replaces the reset on the first bar and
the close-out runs after everything on the last.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import polars as pl

from perp_carry_economics.data.rules import Rules
from perp_carry_economics.params import NOTIONAL_USD, MarginSetting


class Mode(StrEnum):
    SEPARATE = "B"
    ONE_ACCOUNT = "A"


@dataclass
class Book:
    perp_qty: float = 0.0  # BTC short; 0 while unhedged
    spot_qty: float = 0.0  # BTC in custody
    perp_entry: float = 0.0
    margin_cash: float = 0.0  # perp account: funding, realised perp P&L, perp fees
    reserve: float = 0.0  # custody USD in T-bills: spot proceeds and fees, top-ups
    pending_topup_us: int | None = None
    hedged: bool = False
    unhedged_until_us: int | None = None
    flat: bool = False  # out by the filter signal, not by a liquidation

    def margin_equity(self, mark: float) -> float:
        return self.margin_cash + (self.perp_entry - mark) * self.perp_qty

    def set_perp(self, qty: float, mark: float, fee_rate: float) -> float:
        """Realise the whole short at `mark`, resize it to `qty` there, fee from margin.

        Realising all of it (not only the part closed) leaves margin equity unchanged, so it is
        the same book as a partial close at the old entry."""
        fee = fee_rate * mark * abs(qty - self.perp_qty)
        self.margin_cash += (self.perp_entry - mark) * self.perp_qty - fee
        self.perp_qty, self.perp_entry = qty, mark
        return fee

    def set_spot(self, qty: float, price: float, fee_rate: float) -> float:
        """Buy or sell spot to `qty` at `price` against the reserve, fee from the reserve."""
        fee = fee_rate * price * abs(qty - self.spot_qty)
        self.reserve -= (qty - self.spot_qty) * price + fee
        self.spot_qty = qty
        return fee


LEDGER_SCHEMA: dict[str, Any] = {
    "ts_us": pl.Int64,
    "mark": pl.Float64,
    "spot": pl.Float64,
    "perp_qty": pl.Float64,
    "spot_qty": pl.Float64,
    "spot_value_usd": pl.Float64,
    "margin_equity": pl.Float64,
    "margin_frac": pl.Float64,
    "reserve": pl.Float64,
    "equity_usd": pl.Float64,
    "funding_usd": pl.Float64,
    "missed_funding_usd": pl.Float64,
    "unhedged_pnl_usd": pl.Float64,
    "spot_mtm_usd": pl.Float64,
    "perp_mtm_usd": pl.Float64,
    "tbill_usd": pl.Float64,
    "fee_usd": pl.Float64,
    "topup_usd": pl.Float64,
    "spot_sold_btc": pl.Float64,
    "liquidated": pl.Boolean,
    "liq_loss_usd": pl.Float64,
    "sweep_usd": pl.Float64,
    "hedged": pl.Boolean,
    "topup_pending": pl.Boolean,
}


def _tier(rules: Rules, notional_usd: float) -> tuple[float, float]:
    """(maintenance rate, amount) of the tier containing `notional_usd`."""
    return next((rate, amount) for bound, rate, amount in rules.tiers if notional_usd <= bound)


def run_book(
    bars: pl.DataFrame,
    setting: MarginSetting,
    rules: Rules,
    mode: Mode,
    notional: float = NOTIONAL_USD,
    signal: pl.Series | None = None,
) -> pl.DataFrame:
    """Replay `bars` through the book; one ledger row per bar, state at the bar's end.

    `signal` (the cut filter's `in_position`, one value per bar) is acted on at the next bar,
    after funding: turning False closes both legs and the whole book earns T-bill in the
    reserve (flat by choice: no missed funding, a month start only sweeps down to the
    capital); turning True re-enters with the seed procedure at the current reserve. Flat by
    signal is not unhedged after a liquidation: exiting then sells the spot and cancels the
    tau re-establish, and True while unhedged changes nothing until tau passes."""
    if signal is not None and signal.len() != bars.height:
        raise ValueError(f"signal has {signal.len()} values for {bars.height} bars")
    in_pos = [True] * bars.height if signal is None else signal.to_list()
    n_ = notional
    m0, f_s, f_p = setting.m0, rules.spot_taker, rules.perp_taker
    # spot + initial margin + reserve; 1.5 N at PRIMARY (= CAPITAL_MULT x N)
    capital = (1 + m0 + setting.reserve_frac) * n_
    separate = mode is Mode.SEPARATE
    haircut = 0.0 if separate else 1 - rules.btc_collateral_haircut
    b = Book()
    cols: dict[str, list[Any]] = {k: [] for k in LEDGER_SCHEMA}
    last = bars.height - 1
    m_prev = p_prev = 0.0

    for i, row in enumerate(bars.iter_rows(named=True)):
        ts: int = row["ts_us"]
        m, hi, p = float(row["mark"]), float(row["mark_high"]), float(row["spot"])
        funding = missed = unhedged = tbill = fee = topup = sold = liq_loss = sweep = 0.0
        liquidated = False

        if i == 0:  # seed: both legs at N / m (delta-neutral), spot from capital, post m0 N
            m_prev, p_prev = m, p
            spot_mtm = perp_mtm = 0.0
            b.reserve = capital
            fee += b.set_spot(n_ / m, p, f_s)
            b.reserve -= m0 * n_
            b.margin_cash = m0 * n_
            fee += b.set_perp(n_ / m, m, f_p)
            b.hedged = True
        else:
            spot_mtm = b.spot_qty * (p - p_prev)
            perp_mtm = (m_prev - m) * b.perp_qty
            # the print is at this bar's open: it pays on the previous bar's end state
            rate = row["funding_rate"]
            if b.hedged:
                if rate is not None:
                    funding = rate * m_prev * b.perp_qty
                    b.margin_cash += funding
            else:
                unhedged = spot_mtm
                if rate is not None:
                    missed = rate * m_prev * b.spot_qty  # 0 while flat: no spot held
            if b.flat and in_pos[i - 1]:  # re-enter: the seed at the current reserve
                q = b.reserve / ((1 + m0 + setting.reserve_frac) * p)
                fee += b.set_spot(q, p, f_s)
                b.reserve -= m0 * m * q
                b.margin_cash = m0 * m * q
                fee += b.set_perp(q, m, f_p)
                b.hedged, b.flat = True, False
            elif not b.flat and not in_pos[i - 1]:  # exit: close both legs into the reserve
                fee += b.set_perp(0.0, m, f_p)
                fee += b.set_spot(0.0, p, f_s)
                b.reserve += b.margin_cash
                b.margin_cash = 0.0
                b.hedged, b.unhedged_until_us, b.pending_topup_us = False, None, None
                b.flat = True
            if row["month_start"] and b.flat:
                sweep += b.reserve - capital
                b.reserve = capital
            elif row["month_start"]:  # reset both legs to N, margin to m0 N, sweep the rest
                q = n_ / m
                fee += b.set_perp(q, m, f_p)
                fee += b.set_spot(q, p, f_s)
                b.reserve += b.margin_cash - m0 * n_
                b.margin_cash = m0 * n_
                b.hedged, b.unhedged_until_us, b.pending_topup_us = True, None, None
                sweep += b.reserve - setting.reserve_frac * n_
                b.reserve = setting.reserve_frac * n_

        if row["day_start"]:
            tbill = b.reserve * row["tbill_rate"] / 365
            b.reserve += tbill

        if b.hedged:  # liquidation is intrabar, on the high (ruling R3)
            q = b.perp_qty
            collateral = haircut * b.spot_qty * p
            if b.margin_equity(hi) + collateral <= rules.maintenance(hi * q):
                if not separate:
                    # shortcut: (A) liquidation is not modelled (it needs roughly +2000% in a
                    # month); raise rather than book it wrong.
                    raise NotImplementedError(f"one-account book liquidated at ts_us={ts}")
                rate_m, amount = _tier(rules, hi * q)
                # price where equity == maintenance: cash + (entry - x) q == rate x q - amount
                m_liq = (b.margin_cash + b.perp_entry * q + amount) / (q * (1 + rate_m))
                m_liq = min(max(m_liq, m_prev), hi)
                # all equity left at m_liq is lost; == maintenance(m_liq q) unless clamped
                liq_loss = b.margin_equity(m_liq)
                perp_mtm += (m - m_liq) * q  # the MTM above ran to the close; stop it at m_liq
                unhedged = spot_mtm + perp_mtm  # the spot move net of what the perp still hedged
                b.margin_cash = b.perp_qty = 0.0
                b.hedged, b.unhedged_until_us, b.pending_topup_us = False, ts + setting.tau_us, None
                liquidated = True

        if separate:
            if b.unhedged_until_us is not None and ts >= b.unhedged_until_us:
                # re-establish: margin m0 m q from the reserve first, then by selling spot,
                # with q chosen so that spot_qty == perp_qty afterwards
                ds = max(0.0, (m0 * m * b.spot_qty - b.reserve) / (p * (1 - f_s) + m0 * m))
                fee += b.set_spot(b.spot_qty - ds, p, f_s)
                sold += ds
                post = m0 * m * b.spot_qty
                b.reserve -= post
                b.margin_cash += post
                fee += b.set_perp(b.spot_qty, m, f_p)
                b.hedged, b.unhedged_until_us = True, None

            if (
                b.hedged
                and b.pending_topup_us is None
                and b.margin_equity(m) / (m * b.perp_qty) < setting.trigger
            ):  # breach: decide now, land tau later at the state then prevailing
                b.pending_topup_us = ts + setting.tau_us
            elif b.pending_topup_us is not None and ts >= b.pending_topup_us:
                b.pending_topup_us = None
                equity = b.margin_equity(m)
                if equity / (m * b.perp_qty) < setting.trigger:  # not healed: land it
                    topup = m0 * m * b.perp_qty - equity
                    reserve0 = b.reserve
                    short = topup - max(reserve0, 0.0)
                    if short > 0:
                        # joint solve: sell ds spot, buy back ds perp, move the whole reserve so
                        # margin equity == m0 m (q - ds) after the buy-back fee
                        ds = short / (p * (1 - f_s) + m0 * m - f_p * m)
                        fee += b.set_spot(b.spot_qty - ds, p, f_s)
                        fee += b.set_perp(b.perp_qty - ds, m, f_p)
                        sold += ds
                        topup = b.reserve - min(reserve0, 0.0)  # a prior deficit stays put
                    b.reserve -= topup
                    b.margin_cash += topup

        if i == last:  # close both legs, everything above the capital is the P&L
            fee += b.set_perp(0.0, m, f_p)
            fee += b.set_spot(0.0, p, f_s)
            b.reserve += b.margin_cash
            b.margin_cash = 0.0
            sweep += b.reserve - capital
            b.reserve = capital
            b.hedged, b.unhedged_until_us, b.pending_topup_us = False, None, None

        spot_value = b.spot_qty * p
        margin_equity = b.margin_equity(m)
        frac = (margin_equity + haircut * spot_value) / (m * b.perp_qty) if b.perp_qty else None
        values = (
            ts, m, p, b.perp_qty, b.spot_qty, spot_value, margin_equity, frac, b.reserve,
            spot_value + margin_equity + b.reserve, funding, missed, unhedged, spot_mtm, perp_mtm,
            tbill, fee, topup, sold, liquidated, liq_loss, sweep, b.hedged,
            b.pending_topup_us is not None,
        )  # fmt: skip
        for col, v in zip(cols.values(), values, strict=True):
            col.append(v)
        m_prev, p_prev = m, p

    return pl.DataFrame(cols, schema=LEDGER_SCHEMA)


def events(ledger: pl.DataFrame) -> pl.DataFrame:
    """Rows with a liquidation, a top-up or a sweep."""
    return ledger.filter(
        pl.col("liquidated") | (pl.col("topup_usd") != 0) | (pl.col("sweep_usd") != 0)
    )
