# perp-carry-economics

Does the crypto cash-and-carry trade pay? A BTC funding backtest on Binance; the design is in [docs/specs/2026-10-26-perp-carry-economics-design.md](docs/specs/2026-10-26-perp-carry-economics-design.md).

## Reproduce

Run the four steps in order from the repository root. `--start` and `--end` (exclusive) are optional on `fetch`, `build` and `validate`; the period defaults to 2020-09-01 up to the first day of the current month, so the current month is never requested.

```bash
uv run pce fetch
```

Downloads the raw files into `data/raw/{funding,mark,spot,metrics,depth,fred}/`: about 75 monthly funding, mark-price and spot zips, about 2,250 daily metrics zips and about 1,400 daily bookDepth zips from data.binance.vision, plus one FRED CSV (DTB3). Several GB; bookDepth is most of it.

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

Runs every row, setting, episode and the capacity curve and writes `results/`. About 15 engine passes over roughly 3.2M minute bars; the run time is expected to be minutes but has not been measured.

`pce run` without `--calibration` requires `results/carry_freeze.json` (the pre-registration freeze, tagged in git) to exist, and it refuses to run if the inputs, rules or parameters differ from what was frozen. `pce freeze --period-end YYYY-MM-DD` writes the freeze; it refuses until `assets/binance_rules.json` has `"verified": true`, which you set yourself after checking the fee and tier snapshot.

For a quick look without a freeze, `pce run --calibration` runs 2020-09-01 to 2020-12-01 and writes `results/calibration/`:

```bash
uv run pce run --calibration
```

`pce all [--calibration] [--start DATE] [--end DATE]` runs fetch, build, validate and run in that order. `--calibration` applies to `run` only, so fetch, build and validate still cover the full period.
