# README first screen (phase 4 brief)

The brief for the README briefing and its two figures (spec §12, phase 4). Every number
below is from the run under the freeze (`results/`, commit 6f1307f). The yearly split is
the one thing on this page that was *not* a frozen deliverable; see "Added after the run".

## Reader and the one belief

A hiring manager at a crypto trading desk, thirty seconds, laptop, Slack open. Project 1
already proved care. This page has to prove trade judgment: quotes the trade on posted
capital and over cash, knows where it dies, committed to the method before seeing the
answer. The one belief to leave: *this person would have told me the right number before
the trade went on, and told me what it pays now, not what it averaged.*

## Above the fold, in order

1. **Headline (bold), three sentences.**

   > **BTC cash-and-carry on Binance paid 5.1 % a year over T-bills on the capital a desk
   > has to post, September 2020 to September 2026, against a 12.7 % headline of positive
   > funding.** Almost all of it was 2021: since 2022 the trade has paid about one point
   > over cash. How it would be measured was frozen before the run.

2. **Hero figure: the waterfall** (`figures/hero_waterfall`). Seven columns, in this order:
   positive funding on $10 M notional (12.7) → same dollars on $15 M posted (−4.2) →
   T-bill on idle cash (+0.4) → negative funding (−0.5) → fees and basis (−0.2) → minus
   T-bills on all $15 M (−3.1) → **5.1 over cash**. Units once, in a caption: percentage
   points a year on the base each bar names. Title and period/source inside the image.

   Rendering rules (from the dataviz review): two fills only, blue for bars that add and
   grey one step darker than the mockup for bars that cut, the answer bar in the dark blue
   with the hero figure inside in the same sans as the labels; bars about half the slot;
   square corners on floating bars, 4 px rounded data-ends on the first and last bar only;
   solid 1 px grey connectors, never dotted; one decimal everywhere; no y-axis, no
   gridlines; light and dark renders served through a `<picture>` element, each validated
   on its own surface.

3. **Second figure: spread over cash by calendar year** (`figures/yearly_spread`). One bar
   per year, same blue/grey by sign, the two episode weeks ticked on the axis. Provisional
   values from monthly sweeps (compute properly in the figures code, see below):

   | Year | Funding on notional | Net on capital | T-bill | Spread over cash |
   |---|---|---|---|---|
   | 2020 (Sep–Dec) | 18.1 % | 6.8 % | 0.4 % | 6.4 |
   | 2021 | 34.0 % | 23.3 % | 0.1 % | 23.3 |
   | 2022 | 4.2 % | 3.1 % | 2.0 % | 1.1 |
   | 2023 | 8.4 % | 5.2 % | 5.1 % | 0.1 |
   | 2024 | 12.9 % | 9.5 % | 5.0 % | 4.5 |
   | 2025 | 5.3 % | 4.5 % | 4.1 % | 0.4 |
   | 2026 (Jan–Sep) | 3.1 % | 2.7 % | 3.7 % | −1.0 |

4. **Three facts, one line each.**
   - Zero liquidations in six years at 4× margin with a four-hour top-up; closest call the
     week of 23 October 2023. The same book at 8× is liquidated 3 to 18 times, depending on
     how fast the desk tops up.
   - Capacity about $78 M before the modelled spread halves; past $300 M the perp book
     cannot fill the position.
   - Cutting the trade on negative funding cost 0.2 points a year, as the freeze predicted;
     57 round trips, five times the fees. Only 5 of 73 months lost money; the risk was never
     the bad month, it was five years of earning cash while posting margin for it.

5. **Freeze line.** Parameters, rules, data hashes and the prediction were frozen on
   2026-10-28, tag `carry-preregistered`, before the full period was run. Link the freeze
   file and the tag (`/blob/<tag>/...`, never `/tree/<tag>`).

## Below the fold, in order

The FTX inversion week (14–21 November 2022); the one-account (A) row; the VIP 3 row (fees
move the result by 0.08 points); the capacity curve; the sensitivity grid; limitations
(reverse carry needs a spot-borrow series Binance does not publish; the inverse coin-margined
perp is the near-unliquidatable textbook form, not built because liquidity sits in USD-M; dYdX
has no spot leg and a book two orders of magnitude smaller; Hyperliquid is a later phase;
the 5 % BTC collateral haircut is the design's assumption and touches only row A);
method and the spec's §15 amendments; reproduce.

## Added after the run

The calendar-year split was not in the freeze. It is a description of the frozen result,
not a test: no threshold hangs on it and nothing is redefined. The README says so in one
sentence next to the figure. Compute it in the figures code from the decomposition
attributed to the month each sweep covers (the provisional table above sums sweeps by the
calendar year they land in, so each year carries one month of the year before, and 2020 and
2026 are partial).

## Rules carried over from Project 1

No claim on the page stronger than the repo's own. The freeze is never edited. A check that
fails is reported, not redefined. Findings first, limitations honest, and the architecture
stays below the fold.
