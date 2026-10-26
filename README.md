# perp-carry-economics

Does the crypto cash-and-carry trade pay? A BTC funding backtest on Binance; the design is in [docs/specs/2026-10-26-perp-carry-economics-design.md](docs/specs/2026-10-26-perp-carry-economics-design.md).

## Reproduce

Run the steps in order from the repository root. `fetch`, `build` and `validate` take optional `--start` and `--end` (exclusive); the period defaults to 2020-09-01 up to the first day of the current month, so the current month is never requested. `--start` and `--end` only narrow the download and the bar table. The validation report always runs its engine checks on the fixed calibration window (2020-09-01 to 2020-12-01), and `pce run` takes its period from the freeze (or the calibration window), never from these flags. So `--start` must stay 2020-09-01 and `--end` at or after 2020-12-01; a later `--start` makes `validate` fail.

```bash
uv run pce fetch
```

Downloads the raw files into `data/raw/{funding,mark,spot,metrics,depth,fred}/`: monthly funding, mark-price and spot zips, daily metrics and bookDepth zips from data.binance.vision, plus one FRED CSV (DTB3). Several GB; bookDepth is most of it.

```bash
uv run pce build
```

Builds the minute bar table from the raw files.

```bash
uv run pce validate
```

Runs the data checks D1-D4 and the engine checks E1-E3, and writes the validation report.

```bash
uv run pce run
```

Runs every row, setting, episode and the capacity curve and writes `results/`. The run time is expected to be minutes but has not been measured.

`pce run` without `--calibration` requires `results/carry_freeze.json` to exist and refuses to run if the inputs, rules or parameters differ from what it records; the code checks the file's contents, the input hashes, the rules and the parameters. The git tag on the freeze commit is an audit trail for the reader. `pce freeze --period-end YYYY-MM-DD` writes the freeze; it needs a passing validation report and refuses until `assets/binance_rules.json` has `"verified": true`, which you set yourself after checking the fee and tier snapshot.

For a quick look without a freeze, `pce run --calibration` runs 2020-09-01 to 2020-12-01 and writes `results/calibration/`:

```bash
uv run pce run --calibration
```

### One command

`pce all [--calibration] [--start DATE] [--end DATE]` runs fetch, build, validate and run in that order. `--calibration` applies to `run` only, so fetch, build and validate still cover the full period. The dates do not bound `run`: without `--calibration` its period comes from the freeze, and a mismatch with the bars built fails its coverage check; with `--calibration` it is the fixed calibration window.

The first full pass is `pce all --calibration`, then `pce freeze` (after `verified: true`), then `pce run`. `pce all` without `--calibration` can only succeed once a freeze exists; it is for re-running a frozen pipeline.
