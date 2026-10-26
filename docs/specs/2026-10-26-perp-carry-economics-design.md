# perp-carry-economics — design

Date: 2026-10-26. Status: approved design; amended as built (section 15).

## 1. Intent

A verdict on the crypto cash-and-carry trade (long spot BTC, short BTC perpetual, collect
funding), delivered the way a junior trader briefs a PM. The code is the evidence; the README
is the briefing.

The reader is a hiring manager at a crypto trading firm. Project 1 (uniswap-lp-economics)
already shows that the author is careful, honest and reproducible; a desk treats that as
table stakes. The question this project answers is whether the author has trade judgment:
would they have found the catch in a "free money" trade before the desk put it on. Every desk
has seen the pitch deck promising 15–30% delta-neutral yield; the people they hire ask
*net of what, during which weeks, at what size*.

The verdict must be honest. If the trade is genuinely good, the README says so and names the
conditions. A forced "it's a trap" conclusion reads as posturing and is an anti-goal.

## 2. Goals, non-goals, anti-goals

Goals

- Above the fold, in this order:
  1. **The gap.** Gross funding yield next to net yield (after taker fees, margin drag,
     negative-funding periods, liquidation losses), same period, as a spread over the
     risk-free rate on capital actually deployed, at a stated size and leverage. One row.
  2. **The two weeks.** Two dated episodes with P&L path and cause, both picked by a frozen
     rule, not by hand: the week the short perp is liquidated, and the week funding and basis
     invert.
  3. **Capacity.** How much money the trade holds before the position is the funding rate.
     Stated up front as a *model*, with its assumption.
- Every number reproducible from free, keyless public files (`curl` is enough).
- Pre-registration before the run, as in Project 1: thresholds, parameters, episode rules and
  the expected result of the comparison row are frozen and tagged before any result is seen.

Non-goals

- Predicting funding. The headline strategy has zero parameters on purpose.
- Multi-venue parity. One venue carries the headline; Hyperliquid is a later robustness phase;
  dYdX is one line in limitations.
- A website. The README is the deliverable of v1; a MkDocs site like Project 1's can follow.

Anti-goals

- Reading as "I built a backtester." Architecture, adapters and reproducibility go below the
  fold.
- Any choice of period, episode or threshold made after seeing results.
- A grid of filters. One comparison rule, frozen, with its prediction written down.

## 3. Venue and data (decided)

**Headline venue: Binance USD-M, BTCUSDT perpetual, spot BTC/USDT.** Reasons, in order of
weight: (1) history covers more than one regime (2021 bull, 2022 FTX unwind, Aug 2024,
Oct 2025) — "run everything the venue publishes" is only a strong rule when the venue
publishes enough; (2) the spot leg is where a desk would hold it; (3) every input is a public
file with no key or cloud account, matching Project 1's reproducibility story and reusing its
Binance tooling.

Binance public data (data.binance.vision), verified 2026-10-25:

| Series | Coverage | Use |
|---|---|---|
| Funding rate, 8h | 2020-01 → latest month | funding P&L |
| Open interest (coin and USD), 5-min | 2020-09-01 → latest | period start, capacity model, episode colour |
| Mark-price klines, 1-min | monthly files | liquidation path, basis |
| Spot klines BTC/USDT | 2017-08 → latest | spot leg, basis |
| Book depth (daily files) | 2023-01-01 → latest | entry/exit slippage in the capacity model |

Risk-free rate: 3-month T-bill, FRED series DTB3, daily. Fee schedule and maintenance-margin
tiers: snapshot of Binance's published tables at freeze time, stored in the repo.

Not needed: a market-liquidation feed. The strategy's own liquidation is computed from the
mark path and the margin model; Binance's USD-M liquidation history was never published.

**Hyperliquid** is a robustness phase after the Binance verdict ships ("does the verdict
survive on-chain", including the Hyperliquid–Binance funding spread). Its archive has
documented data traps (funding-scale flip on 2023-06-08, zeros written where nulls belong),
hourly funding from 2023-06, S3 open interest is requester-pays (~$1). Its design is written
when that phase starts, not here.

**dYdX** is dropped to one line in limitations: no spot leg and a BTC book two orders of
magnitude smaller than the others, so the capacity verdict there is trivial.

## 4. Period (decided)

Frozen rule: **from the first day Binance publishes BTCUSDT open interest (2020-09-01) to the
last full calendar month published at freeze time.** The whole range is run; no sub-period is
chosen by hand. The capacity model's depth-based component is fitted on 2023-01-01 onward
(where depth files exist) and declared as such.

## 5. The trade as backtested (decided)

**Design (B): separate custody.** Spot BTC sits in custody (not on the exchange). The short
perp runs on USDC margin, sized to a buffer, with an automated top-up from a cash reserve.
This is the setup a desk in size uses (November 2022 taught the cost of keeping spot and
margin behind one exchange's solvency), and the only one worth backtesting: under one-account
BTC-collateral the collateral gains what the short loses, so the backtest says "never
liquidated" and is silent about what actually killed that design (exchange default,
collateral revaluation), neither of which is in the mark path.

Liquidation risk under (B) is a function of two frozen numbers the reader can argue with:
the margin buffer and the top-up latency.

**The book is sized in USD and reset monthly.** A static-quantity position cannot survive a
6× move: with any plausible margin plus reserve, a short entered in September 2020 is liquidated
before 2021 and the backtest would be a ten-week verdict (or, left unhedged, a BTC long with
extra steps). Instead, at each month start both legs are reset to the target notional (sell
spot and cover perp as BTC rises, the reverse as it falls); reset fees count against the trade.
Intra-month the spot leg is the **collateral of last resort**: top-ups draw funding received
first, then the reserve, then sell spot (reducing the short by the same quantity, so the book
stays delta-neutral and shrinks). τ is literally the time to sell and move spot.

**Liquidation means exactly one thing:** the mark moved from the top-up trigger to the
maintenance level within τ. It does not end the backtest: the short is liquidated (clearance
fee, slippage, posted margin lost), the book is unhedged until τ passes, then the short is
re-established from spot. "Liquidation losses" are those fees plus the basis and funding missed
while unhedged. The run continues to period end.

**Funding settles into the margin account.** The margin balance is unrealised perp P&L plus
funding received minus funding paid minus fees. In a positive regime the book self-funds its
top-ups; in a negative regime the margin bleeds and pulls from the reserve. That is the
mechanism by which negative funding hurts, and it falls out for free; funding is not routed
anywhere else.

Monthly reset is a judgment call: the worst months in the period run +40–50%, so the book
reaches roughly $15M before a reset and the $10M capacity reference is a month-start number.
A drift band (reset when notional strays 25% from target) is what a desk more often does;
monthly is simpler and its fee accounting cleaner, so it is kept and said in the README.

**Comparison rows** (same files, below the headline):

- **(A) one account, BTC as collateral** (Binance Multi-Assets mode with its published
  haircut): survives every week on price, concentrates the whole position on one exchange.
- **Cut filter** (section 6).

**Limitations, one sentence each:** the coin-margined inverse perp with BTC as margin is the
near-unliquidatable textbook form, not built because liquidity sits in USD-M; reverse carry
(short spot, long perp) needs a spot-borrow series Binance does not publish.

## 6. Strategy rule (decided)

**Headline: hold-through.** Zero parameters. Negative-funding periods are carried, so the whole
gross-to-net gap is attributable to the trade itself (fees, margin drag, negative weeks,
liquidation), not to a filter. This is "what does it pay if I just sit in it".

**Comparison row: one filter, frozen, not a grid.** Exit both legs when trailing 7-day
realised funding, annualised, falls below the T-bill rate. Re-enter when it climbs back above
the T-bill rate plus a fee-recovery hurdle (hysteresis, so single negative 8-hour prints do not
churn it). Reported with its turnover and fee drag next to hold-through.

**Pre-registered prediction:** the filter does *not* beat hold-through after fees, because
negative-funding regimes are short and mean-reverting and a 7-day average fires when the
regime is half over. Beating it is a finding; not beating it is the prediction confirmed.
"Short and mean-reverting" is a prior the run measures (count and length of negative stretches
since 2020) and reports.

## 7. Parameters (author's calls, frozen at pre-registration; the user checks these)

- Asset: BTC only.
- Reference position: **$10M spot notional** for the headline row; capacity is reported as a
  curve around it.
- Three frozen margin parameters, the ones the reader argues with:
  initial margin **m0 = 25% of notional** (4× on the perp leg); top-up **trigger at 15%**,
  restoring to m0; top-up **latency τ = 4 hours** (primary). Liquidation at Binance's published
  maintenance-margin rate for the position's notional tier, plus its published clearance fee
  (both frozen snapshots).
- Cash reserve: equal to initial margin (another 25% of notional), held in T-bills.
- Capital deployed: spot at target + initial margin + reserve at target = **1.5 × notional**,
  fixed at each monthly reset. Anything above that at the reset (accumulated funding, basis
  convergence) is swept out to the PM and counted as return; anything below is a shortfall and
  counted as loss. Without the sweep a good year leaves cash in the reserve and the reported
  yield drifts down as the base grows. Posted margin earns nothing (this is the margin drag);
  the reserve and any idle cash earn the T-bill rate.
- Sensitivity table for margin parameters (assumptions, not alpha, so a table is honest):
  τ ∈ {1h, 4h, 24h} × m0 ∈ {12.5%, 25%, 50%}, trigger at 0.6 × m0, reserve = m0.
- Fees: Binance regular-user taker rates for spot and USD-M at freeze time, no BNB discount.
  One sensitivity row at a VIP tier a $10M desk would plausibly hold.
- Margin fractions (m0, the top-up threshold, maintenance) are measured against **current mark
  notional**, not entry notional. Breaches are checked on every 1-minute mark bar. A top-up is
  decided at the breach and lands τ later at the state then prevailing; if the breach has
  healed by then, nothing lands.
- Net yield = (funding received − funding paid − fees − liquidation losses + T-bill interest
  on idle cash) ÷ capital deployed, annualised. **Spread over T-bill** = net yield minus the
  T-bill rate on all deployed capital. Gross yield = funding received ÷ spot notional.
- **Every row uses the same denominator**, 1.5 × notional, and every row earns the T-bill rate
  on whatever cash is idle in it (row (A)'s unposted reserve; the cut filter's whole book while
  flat). Otherwise (A) looks better by leverage alone and the filter's prediction confirms
  itself by construction.
- Basis P&L is realised at exit and shown mark-to-market inside the episodes.
- "Worth putting on" threshold: **full-period net spread over T-bill ≥ 2 percentage points
  annualised** under the primary setting.
- Cut-filter hurdle: re-entry when trailing 7-day funding exceeds T-bill + round-trip taker
  fees amortised over 30 days.

## 8. Episode rules (frozen)

1. **The liquidation week:** the calendar week containing the first liquidation under the
   primary setting. If none occurs, the week in which the mark came closest to covering the
   trigger-to-maintenance distance within τ (the closest call), labelled as such.
2. **The inversion week:** the calendar week with the lowest trailing 7-day realised funding.
   Expected (but not pre-chosen) to be October 2025; both the OI collapse (96.7k → 72.6k BTC
   on 2025-10-10) and the mark path are in the free files as colour.

Each episode shows: the dated P&L path of both legs and funding, what caused it, and how
each comparison row fared in the same week.

## 9. Capacity (modelled, declared)

Capacity cannot be measured from public data; it is modelled, and the README says so in the
same breath as the number.

- **Funding impact:** the position is modelled as a *permanent* share of open interest, not a
  one-off entry print (a transient-impact regression amortised over years gives infinite
  capacity, which is the wrong answer dressed as a model). Fit the sensitivity of 8-hour
  funding to the level of short-side open interest from history (exact specification frozen
  at pre-registration), then apply the position's share at each size to every print.
- **Entry/exit slippage:** from the USD-M book-depth files (2023 onward), walked for each
  size, for the perp leg. Binance publishes no spot depth; spot slippage is proxied with the
  perp book and the proxy is stated. For a one-round-trip hold this component is small.
- **Capacity** is defined as the position size at which modelled net spread over T-bill falls
  to half its $10M value. Reported as a curve with the assumption stated.

## 10. Pre-registration

Mirror Project 1: a frozen JSON of every parameter in sections 4–9, the prediction in
section 6, the fee and margin-tier snapshots, and input-file hashes, committed and tagged
(`carry-preregistered`) before the engine is run on the full period. Never edited afterwards.
Development uses a short calibration window at the start of the period, declared in the
freeze.

## 11. Validation checks

As in Project 1, a numbered set of checks that must PASS before results are written, including
at least: funding files complete (every 8-hour print present); mark klines complete; OI series
present on every day in the period; funding P&L recomputed from raw prints equals the engine's
total exactly; the hold-through run is byte-identical on re-run.

## 12. Deliverables and phases

1. **Data:** download, verify and cache the Binance files and DTB3; raw-file hashes.
2. **Engine:** carry P&L, margin model with top-up and liquidation, cut filter, (A) row;
   validation checks; tests.
3. **Pre-registration freeze** and tag.
4. **Run, episodes, capacity model, README** (the briefing, template from Project 1).
5. **Hyperliquid robustness** (own design when it starts).
6. **Site**, if wanted, on Project 1's MkDocs setup.

## 13. Toolchain and conventions (author's call)

Same as Project 1: Python ≥ 3.13, uv, polars, pyarrow, matplotlib, httpx; pytest, ruff, mypy
strict; the same README template, CI workflow and repo map convention; MIT; `Enrico Nagata`
as author; no AI attribution in commits. Package `perp_carry_economics`, CLI entry point
`pce`. Float group sums go through an ordered group-by (polars parallel sums vary run to run).

## 14. Left to the builder

Module layout; the exact regression specification for the funding-impact model (frozen at
step 3, not here); how the maintenance-margin tier table is encoded; the calibration window
length; chart design; the repo map and badges.

## 15. Amendments (as built, 2026-10-26)

Where the engine differs from or makes precise the sections above, it does this. The sections
above are left as approved.

- [§5] Liquidation is checked intrabar on the mark high, and all remaining margin equity at the
  liquidation mark m_liq is lost; the clearance fee is inside that loss, so `liquidation_fee`
  in the rules snapshot is unused.
- [§5] Re-establishment after τ posts m0 of the new short's notional from the reserve first,
  then by selling spot, solved jointly so that spot and short quantities match afterwards.
- [§5] A month-start reset re-hedges a liquidated book even inside τ.
- [§5/§7] The seed opens both legs at N / mark on the first bar.
- [§7] Sensitivity rows are measured on their own setting's capital, `(1 + m0 + reserve_frac)
  · N`; the headline rows on 1.5N.
- [§7] Net yield is the sum of the monthly sweeps over capital, so it includes realised basis
  and unhedged P&L; both are reported.
- [§9] Funding impact is regressed on Δlog of the top-trader long/short *position* ratio, and a
  short of share s of OI shifts it by −log(1 + 2s).
- [§9] Slippage prices every perp trade plus the open and close, both legs on the perp book.
- [§9] The capacity target is half the modelled spread at $10M (the grid point equal to the
  notional), capped by the smallest size that exhausts the published book.
- [§8] The closest call is the maximum per-bar score over bars with a top-up pending or
  landing.
- [§11] D2 tolerates mark gaps up to 24 h (and lists gaps of 5 min or more), D3 tolerates up to
  2 % missing OI days (listed), and E1 recomputes funding from the bar table.
- [§10] DTB3 is pinned in `assets/DTB3.csv` at freeze; `pce fetch` takes it from there.
