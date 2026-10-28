# Roadmap

Where the project stands and what comes next. Each piece of work has a design spec in
[`docs/specs/`](specs/); each spec describes what was built.

## Done

| Work | What it delivered | Spec |
|---|---|---|
| Engine and pre-registration tooling | Data download with checksum verification, minute bar table, carry book with the design-(B) margin model, cut filter, yield decomposition, episode rules, capacity model, checks D1–D4 and E1–E3, freeze, `pce run`, `pce all` | [spec](specs/2026-10-26-perp-carry-economics-design.md) |

## Next

- **Figures and the README briefing:** the figures and a README that tells the result as a story.
- **Hyperliquid robustness** (own design when it starts).

## Backlog

Deferred from the engine plan's reviews (2026-10-26); none changes a number, all are test hardening,
messages or docs. Do them in the figures/README plan or when the file is next touched.

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
- README (figures plan): the (A) row's `margin_equity` excludes spot collateral while
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
- Figures plan: write the VIP ledger too if the figures need it; slice the A and filter ledgers
  into the episode weeks (spec §8 "how each comparison row fared"); consider `kind="closest_call"`
  for the no-liquidation episode.
