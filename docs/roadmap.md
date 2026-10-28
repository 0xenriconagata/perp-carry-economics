# Roadmap

Where the project stands and what comes next. Each piece of work has a design spec in
[`docs/specs/`](specs/); each spec describes what was built.

## Done

| Work | What it delivered | Spec |
|---|---|---|
| Figures and the README briefing (2026-10-28) | `yearly()` and `pce figures`, which writes `results/yearly.csv` and the two figures, `00_hero` (waterfall) and `01_yearly_spread`, each in light and dark SVG under `results/figures/`; the README first screen, with its numbers tested against the result files | [spec](specs/2026-10-28-readme-first-screen.md) |
| Pre-registration freeze and the full-period run (2026-10-28) | Rules file verified at Binance VIP 3 with fee and bracket snapshots, validation 7/7 PASS, freeze for [2020-09-01, 2026-10-01) tagged `carry-preregistered`, `pce run` over 3,198,214 bars with E1–E3 PASS; summary, decomposition, sensitivity, capacity, episodes and negative stretches in `results/` (minute ledgers git-ignored, rebuilt by `pce run`) | [spec](specs/2026-10-26-perp-carry-economics-design.md) |
| Engine and pre-registration tooling | Data download with checksum verification, minute bar table, carry book with the design-(B) margin model, cut filter, yield decomposition, episode rules, capacity model, checks D1–D4 and E1–E3, freeze, `pce run`, `pce all` | [spec](specs/2026-10-26-perp-carry-economics-design.md) |

## Next

- **Hyperliquid robustness** (own design when it starts).

## Backlog

Deferred from the engine plan's reviews (2026-10-26); none changes a number, all are test hardening,
messages or docs. Do them when the file is next touched.

- Tests: literal spot-check of one grid setting; a pre-window T-bill print filling into `start`;
  spot ≠ mark at a filter re-entry; a funding print on a filter exit bar; conservation on a
  spot-funded top-up bar; a yield reconciliation that goes unhedged; `test_cli_all` without
  `--calibration`; assert `capacity_usd` on the run fixture and add a liquidation to it; rename
  `test_help_lists_no_commands_yet`; assert the missing bookDepth days equal the four known ones.
- Code: `build_bars` should raise clearly on a null `spot`; move `_dtb3_prints` from `cli.py` to
  `data/fred.py`; `pce freeze` should check the freeze file exists before hashing all raw files;
  clear messages instead of tracebacks for a missing DTB3/bars file and a report without `checks`;
  `pce run --calibration` should not load the full bookDepth history; reuse `ledger_b` for the
  PRIMARY grid row; compute `_trades` once per curve; `freeze_cmd` should refuse or filter data
  files dated after `--period-end` (a freezer with extra months on disk makes a freeze that
  `--end <frozen end>` cannot satisfy); when a stale raw `DTB3.csv` differs from the asset the
  message should say to delete the raw file.
- README data notes (not yet on the page): the (A) row's `margin_equity` excludes spot collateral while
  `margin_frac` includes it; exits/re-entries happen at the close before the intrabar
  liquidation check; `book_exhausted_at_any_size` is read with `capacity_depth_available`;
  capacity's first grid point is returned when the curve is already below half there; the filter
  hurdle is 3.65 %/yr at the placeholder fees; `pce fetch` on the 1st–3rd of a month requests an
  unpublished month; a `--start` after 2020-09-01 only certainly fails `validate` from 2020-12-01.
- From the first real run (2026-10-28): `test_fetch_all` lost its `ok`-line count assertion
  (now 11); `download_verified` creates the folder before the checksum GET, so a depth 404 leaves an
  empty `depth/`; `load_mark`'s dedup now runs before `build_bars`' duplicate guard can see a
  monthly-internal duplicate; junk strings in the top-trader ratio become null silently (bound the
  null share or warn); an unquoted blank `sum_open_interest` would pass the strict cast as null.
- From the full run (2026-10-28): `oi_share` divides by `oi_usd` on 485 metrics rows (24 days between 2021-05-22 and 2025-07-21) where Binance reports zero open interest, raising a numpy divide-by-zero warning; the median is unaffected, so the number stands. Drop or warn on those rows when the file is next touched, and name them in the README's data notes.
- Figures (deferred): write the VIP ledger too if the figures need it; slice the A and filter ledgers
  into the episode weeks (spec §8 "how each comparison row fared"); consider `kind="closest_call"`
  for the no-liquidation episode.
- Review minors from the figures plan (2026-10-28), none changes a number: the yearly test does not
  independently assert the days/365.25 divisor and the capital denominator; the yearly left join
  would drop a nonzero sweep on a first-bar 1 January; `register_fonts` re-adds fonts on every
  `theme()` call; the hero colour test asserts only three distinct facecolors, and the answer text
  is not asserted equal to `fmt_pts(spread*100)`; the "5.1 %" answer text has about 3 px of margin
  inside its bar (a two-digit spread would overflow); `spread_by_year` raises on an empty yearly
  frame and silently skips an episode whose year has no bar; `pce figures` checks ledger/bars
  alignment by row count only.
- Whole-branch review minors from the figures branch (2026-10-28), none changes a number:
  - The figures are drawn on an 11.5 in canvas, so at GitHub's mobile width (~343 px) step values
    and tick labels scale to about 4–5 px and only the title and the answer figure stay legible; a
    narrower canvas with larger type needs a new eye check.
  - `tests/test_readme.py` does not pin the yearly alt text, row A, VIP 3, the inversion week, the
    method line, the negative-stretch counts, the round-trip count or "3 to 18".
  - `style.register_fonts` lets a system-installed Inter win `findfont` ties; assert the resolved
    path is inside `FONT_DIR`.
  - `hero.py` hard-codes "$10 M" and "$15 M" in its labels although `steps()` accepts any row.
  - A value in (−0.05, 0) prints "−0.0".
  - `pce figures` always recomputes from the ledger, so re-rendering a colour change needs the full
    rebuild; render from the committed CSVs when the ledger is absent.
  - `render_all` leaves old and new SVGs mixed if a figure raises.
  - `pce figures` with the ledger present but `data/interim/bars.parquet` missing raises a polars
    traceback.
  - T1: the ledger/bars positional alignment is unasserted.
  - T2: the hash salt is pinned only within one process.
  - T5: the stale-SVG glob is case-sensitive on Linux.
  - T6: bar loading is duplicated from `run_cmd`, and the refusal paths are untested.
