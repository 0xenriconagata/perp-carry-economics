# Perp Carry Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Everything up to and including the pre-registered run of the BTC cash-and-carry
backtest on Binance: data download and validation, the carry engine with the design-(B) margin
model, the (A) and cut-filter comparison rows, episode selection, the capacity model, the freeze,
and a `pce run` that writes `results/`. Figures and the README briefing are a second plan.

**Architecture:** Public Binance files and FRED DTB3 are downloaded with checksum verification
into `data/raw/`, joined into one 1-minute bar table in `data/interim/`, and replayed by a small
state machine (`carry/book.py`) that owns the book: monthly USD reset, margin account, top-ups
with latency, liquidation and re-establishment. Rows ((B) hold-through, (A) one-account, cut
filter) are the same engine with a mode. Checks gate every write; parameters live in one frozen
module; `pce freeze` hashes inputs and parameters into `results/carry_freeze.json` before
`pce run` will execute on the full period.

**Tech Stack:** Python ≥ 3.13, uv (`uv_build`), polars ≥ 2.0, pyarrow, numpy, httpx,
python-dotenv; pytest, ruff, mypy --strict. Conventions copied from
`../uniswap-lp-economics` (same author).

**Spec:** `docs/specs/2026-10-26-perp-carry-economics-design.md`

## Global Constraints

- Package `perp_carry_economics`, CLI entry point `pce`, `requires-python = ">=3.13"`, MIT,
  author `Enrico Nagata`; no AI attribution in commits (user rule).
- Timestamps are integer UTC microseconds in an `Int64` column named `ts_us` (`US = 1_000_000`).
- Prices and USD amounts are `Float64`; funding rates are `Float64` fractions per print (0.0001
  = 1 bp). No Int128 columns are needed in this project.
- Float group sums go through `io.ordered_group_by` (polars parallel sums vary run to run).
- Every file written under `results/` or `data/interim/` goes through a `.part` rename
  (`io.write_text_atomic`, `io.write_parquet_atomic`), with `sort_keys=True` JSON and LF line
  endings, so re-runs are byte-identical.
- Period: `2020-09-01` to the first day of the last full month published at freeze (spec 4).
  Calibration window for development: `2020-09-01` to `2020-12-01` (author's call, declared in
  the freeze).
- Frozen parameters (spec 7), verbatim: notional `10_000_000.0`; `M0 = 0.25`;
  `TRIGGER = 0.15`; `TAU_US = 4 * HOUR_US`; reserve `= M0 × notional`; capital deployed
  `= 1.5 × notional`; sensitivity `TAUS_H = (1, 4, 24)`, `M0S = (0.125, 0.25, 0.5)`, trigger
  `= 0.6 × m0`, reserve `= m0`; threshold `WORTH_IT_SPREAD = 0.02`; filter lookback 7 days
  (21 prints), re-entry hurdle = round-trip taker fees amortised over 30 days.
- `ruff check`, `ruff format --check`, `mypy src` and `pytest -q` pass at every commit; CI is
  the same workflow as Project 1 with `timeout-minutes: 20` and `permissions: contents: read`.
- Nothing is pushed and no GitHub remote is created during this plan (user decides).

## Review Focus

Spec-implied inputs no task's tests would otherwise exercise; each line's test is added to the
owning task.

1. **A mark-kline gap across a breach** (Binance maintenance): a top-up deadline that falls in
   a gap must land on the first bar after the gap, not be skipped. Task 7.
2. **Funding print with no mark bar at that minute**: the payment uses the last mark at or
   before the print, never a later one. Task 6.
3. **A monthly reset on the same bar as a pending top-up**: the reset restores margin to `M0`
   and the pending top-up is cancelled, not applied twice. Task 7.
4. **Reserve exhausted and spot sale needed on the same top-up**: the spot sale covers only
   the remainder, and the short is reduced by exactly the BTC quantity sold. Task 7.
5. **DTB3 missing values** (FRED writes `.` on holidays): the daily rate is forward-filled,
   never parsed as zero. Task 4.

---

### Task 1: Repository scaffold

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.gitattributes`, `.env.example`, `LICENSE`,
  `README.md`, `.github/workflows/ci.yml`, `src/perp_carry_economics/__init__.py`,
  `src/perp_carry_economics/config.py`, `src/perp_carry_economics/io.py`,
  `src/perp_carry_economics/cli.py`, `tests/test_cli.py`, `tests/test_io.py`

**Interfaces:**
- Produces: `config.US`, `config.HOUR_US`, `config.DAY_US`, `config.SYMBOL = "BTCUSDT"`,
  `config.PERIOD_START = date(2020, 9, 1)`, `config.CALIBRATION_END = date(2020, 12, 1)`,
  `config.ROOT`, `config.DATA_DIR` (env `DATA_DIR`, default `ROOT / "data"`),
  `config.RAW_DIR = DATA_DIR / "raw"`, `config.INTERIM_DIR = DATA_DIR / "interim"`,
  `config.RESULTS_DIR = ROOT / "results"`;
  `io.write_text_atomic(path: Path, text: str) -> None`,
  `io.write_parquet_atomic(df: pl.DataFrame, path: Path) -> None`,
  `io.ordered_group_by(df: pl.DataFrame, key: str) -> GroupBy`,
  `io.file_hash(path: Path) -> str` (sha256 hex of bytes),
  `io.text_hash(path: Path) -> str` (sha256 of the text with CRLF normalised to LF);
  `cli.main() -> None` with argparse subcommands added in later tasks.

- [ ] **Step 1: Copy conventions from Project 1**

Copy `../uniswap-lp-economics/.github/workflows/ci.yml` and `.gitignore` verbatim. Write
`pyproject.toml` from Project 1's with: name `perp-carry-economics`, version `0.1.0`,
description `Does the crypto cash-and-carry trade pay? BTC funding on Binance, net of fees,
margin and the weeks it loses.`, dependencies `httpx`, `numpy`, `polars>=2.0.0`, `pyarrow`,
`python-dotenv`, `tzdata`; dev group `mypy`, `pytest`, `ruff`; script `pce =
"perp_carry_economics.cli:main"`; same ruff/mypy/pytest tables (no `live` marker). MIT
`LICENSE` with `Enrico Nagata`. `.gitattributes`: `* text=auto eol=lf`. `.env.example`: the
`DATA_DIR` lines only. `README.md`: title line and one sentence pointing at the spec.

- [ ] **Step 2: Write failing tests**

```python
# tests/test_io.py
def test_write_text_atomic_leaves_no_part_file(tmp_path): ...  # writes "a\n", no "*.part" left
def test_text_hash_ignores_crlf(tmp_path): ...  # "a\r\nb" and "a\nb" hash equal
def test_ordered_group_by_is_deterministic(): ...  # 200k rows, 7 keys, sum bits equal on 5 runs
# tests/test_cli.py
def test_help_lists_no_commands_yet(capsys): ...  # main(["--help"]) exits 0
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv sync && uv run pytest -q`  Expected: FAIL (modules missing).

- [ ] **Step 4: Implement `config.py`, `io.py`, `cli.py`**

`io.ordered_group_by` and `write_text_atomic` are Project 1's functions verbatim;
`write_parquet_atomic` is `write_exact` without the Int128 cast. `cli.main(argv: list[str] |
None = None)` builds the parser and dispatches via a `dict[str, Callable[[argparse.Namespace],
None]]` that later tasks extend.

- [ ] **Step 5: Run tests, ruff, mypy**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "Scaffold package, CI and atomic I/O"
```

---

### Task 2: Frozen parameters module

**Files:**
- Create: `src/perp_carry_economics/params.py`, `tests/test_params.py`

**Interfaces:**
- Produces (all module constants, spec 7): `NOTIONAL_USD = 10_000_000.0`, `M0 = 0.25`,
  `TRIGGER = 0.15`, `TAU_US = 4 * HOUR_US`, `RESERVE_FRAC = M0`,
  `CAPITAL_MULT = 1.0 + M0 + RESERVE_FRAC` (= 1.5), `TAUS_H = (1, 4, 24)`,
  `M0S = (0.125, 0.25, 0.5)`, `TRIGGER_RATIO = 0.6`, `WORTH_IT_SPREAD = 0.02`,
  `FILTER_PRINTS = 21`, `HURDLE_AMORTISE_DAYS = 30`, `BTC_COLLATERAL_HAIRCUT = 0.05`
  (row (A), verify at freeze), `CAPACITY_HALF = 0.5`, `PRINTS_PER_DAY = 3`,
  `PRINTS_PER_YEAR = 365 * 3`;
  `@dataclass(frozen=True) class MarginSetting: m0: float; trigger: float; tau_us: int;
  reserve_frac: float`; `PRIMARY = MarginSetting(0.25, 0.15, 4 * HOUR_US, 0.25)`;
  `def sensitivity_grid() -> list[MarginSetting]` (9 settings, trigger `= 0.6 × m0`,
  reserve `= m0`, ordered by tau then m0).

- [ ] **Step 1: Write failing test** asserting every value above, `len(sensitivity_grid()) ==
  9`, `PRIMARY in sensitivity_grid()` (the grid's trigger at m0 = 0.25 is 0.6 × 0.25 = 0.15,
  the primary's) and `CAPITAL_MULT == 1.5`.

- [ ] **Step 2: Run, FAIL.** `uv run pytest tests/test_params.py -q`

- [ ] **Step 3: Implement `params.py`** with the spec section numbers as comments, Project 1's
  `strategy/params.py` as the style.

- [ ] **Step 4: Run, PASS. Commit** `git commit -m "Frozen parameters (spec 7)"`

---

### Task 3: Binance public files: download, parse, load

**Files:**
- Create: `src/perp_carry_economics/data/__init__.py`,
  `src/perp_carry_economics/data/binance.py`, `tests/test_binance.py`,
  `tests/fixtures/make_zips.py` (helper that builds tiny Binance-shaped zips in `tmp_path`)

**Interfaces:**
- Produces: `FUTURES_MONTHLY`, `FUTURES_DAILY`, `SPOT_MONTHLY` base URLs;
  `funding_url(year, month) -> str`, `mark_klines_url(year, month) -> str` (1m
  `markPriceKlines`), `spot_klines_url(year, month) -> str` (1m), `metrics_url(day: date) ->
  str`, `book_depth_url(day: date) -> str`;
  `download_verified(url: str, dest: Path, client: httpx.Client) -> Path` (Project 1's,
  verbatim, including the `.CHECKSUM` sidecar);
  `fetch_all(start: date, end: date, raw_dir: Path) -> None` (monthly files for every month in
  `[start, end)`, daily metrics for every day, daily bookDepth from `max(start, 2023-01-01)`;
  prints `ok <name>` per file; skips files already verified);
  loaders returning sorted frames: `load_funding(raw_dir) -> DataFrame[ts_us: Int64, rate:
  Float64]`; `load_mark(raw_dir) -> DataFrame[ts_us, open, high, low, close]` (1m, `ts_us` =
  bar open); `load_spot(raw_dir)` same schema; `load_oi(raw_dir) -> DataFrame[ts_us, oi_btc,
  oi_usd, top_ls_position_ratio]` (5-min metrics); `load_depth(raw_dir) -> DataFrame[ts_us,
  pct: Float64, notional_usd: Float64]` (per minute, signed `pct` levels as published).

- [ ] **Step 1: Verify the five URL shapes once against data.binance.vision** (one `HEAD` each
  with `httpx`, for 2024-01 / 2024-01-15) and record the exact column headers of the metrics
  and bookDepth CSVs in the module docstring. The spec's coverage table was taken on trust;
  this step is where it is checked. If `markPriceKlines` 1m monthly files do not exist for a
  month in the period, say so in the docstring and fall back to daily files for that month.

- [ ] **Step 2: Write failing tests** using `make_zips.py` to write one funding zip (3 prints),
  one mark zip (3 bars), one metrics zip (2 rows), one bookDepth zip (4 rows):
  `test_funding_url_matches_binance_layout`, `test_download_verified_skips_when_checksum_matches`
  (mock `httpx.Client` with `respx` **not** added — use a tiny fake client class),
  `test_download_verified_raises_on_mismatch`, `test_load_funding_sorts_and_casts`,
  `test_load_mark_converts_ms_to_us`, `test_load_oi_columns`, `test_load_depth_columns`.

- [ ] **Step 3: Run, FAIL.**

- [ ] **Step 4: Implement `binance.py`**: copy `_sha256`, `download_verified`, `_read_zip_csv`,
  `_to_us` from Project 1's `data/binance.py`; loaders concatenate per-file frames with
  `pl.concat` then `.sort("ts_us")`; metrics `create_time` is a datetime string, parse with
  `pl.col(...).str.to_datetime("%Y-%m-%d %H:%M:%S")` to µs.

- [ ] **Step 5: Run, PASS. Commit** `git commit -m "Binance public data: verified downloads and loaders"`

---

### Task 4: FRED DTB3 risk-free series

**Files:**
- Create: `src/perp_carry_economics/data/fred.py`, `tests/test_fred.py`

**Interfaces:**
- Produces: `DTB3_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTB3"`;
  `fetch_dtb3(raw_dir: Path) -> Path` (saves `raw_dir / "fred" / "DTB3.csv"` plus a
  `.sha256` sidecar; no checksum is published, so the hash is recorded, not verified);
  `load_tbill(raw_dir: Path, start: date, end: date) -> DataFrame[day: Date, rate: Float64]`
  — one row per calendar day in `[start, end)`, `rate` as a fraction per year (DTB3 is a
  percent, discount basis; divide by 100 and use as-is, stated in the docstring), forward-filled
  across `.` values and weekends/holidays, back-filled only for days before the first print.

- [ ] **Step 1: Write failing tests** `test_load_tbill_forward_fills_dots_and_weekends`
  (fixture CSV with a `.` and a weekend gap: every day present, no nulls, filled value equals
  the previous print) and `test_load_tbill_divides_by_100` (Review Focus 5).

- [ ] **Step 2: Run, FAIL. Step 3: Implement. Step 4: Run, PASS.**

- [ ] **Step 5: Commit** `git commit -m "FRED DTB3 daily risk-free series"`

---

### Task 5: Exchange rules snapshot (fees, maintenance tiers, liquidation fee)

**Files:**
- Create: `assets/binance_rules.json`, `src/perp_carry_economics/data/rules.py`,
  `tests/test_rules.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) class Rules: spot_taker: float; perp_taker: float;
  liquidation_fee: float; btc_collateral_haircut: float; tiers: tuple[tuple[float, float],
  ...]` (tiers as `(notional_upper_bound_usd, maintenance_rate)` ascending, last bound
  `inf`); `load_rules(path: Path = ROOT / "assets" / "binance_rules.json") -> Rules`;
  `Rules.maintenance_rate(self, notional_usd: float) -> float`;
  `Rules.round_trip_fee(self) -> float` (= `2 * (spot_taker + perp_taker)`);
  `Rules.vip_variant(self, spot_taker, perp_taker) -> Rules`.
- JSON fields: the five above plus `"vip": {"spot_taker", "perp_taker"}` (the tier a $10M
  desk plausibly holds, used by Task 14's VIP row), `"source_urls"`, `"snapshot_date"`. Values
  at this task: spot taker `0.001`, perp taker `0.0005`, liquidation fee `0.0125`, haircut
  `0.05`, vip `0.0004` / `0.0002`, tiers
  copied from Binance's BTCUSDT leverage-and-margin page on the day — with
  `"snapshot_date"` set and `"verified": false` until the freeze task flips it.

- [ ] **Step 1: Write failing tests** `test_maintenance_rate_picks_tier_by_notional`
  (`1e4 → first tier`, `1e7 → the tier containing 1e7`, `1e12 → last`),
  `test_round_trip_fee`, `test_load_rules_reads_asset_file`.

- [ ] **Step 2–4: FAIL, implement, PASS.**

- [ ] **Step 5: Commit** `git commit -m "Binance fee and margin-tier snapshot"`

---

### Task 6: Minute bar table

**Files:**
- Create: `src/perp_carry_economics/bars.py`, `tests/test_bars.py`

**Interfaces:**
- Consumes: Task 3 loaders, Task 4 `load_tbill`.
- Produces: `build_bars(mark: DataFrame, spot: DataFrame, funding: DataFrame, tbill:
  DataFrame, start: date, end: date) -> pl.DataFrame` with columns `ts_us: Int64` (every
  minute of `[start, end)` that has a mark bar; minutes without a mark bar are **absent**, not
  filled), `mark: Float64` (close), `spot: Float64` (spot close, as-of joined backward),
  `funding_rate: Float64 | null` (set on the bar whose `ts_us` equals the print time; if that
  minute has no bar, on the **last bar at or before** the print — Review Focus 2),
  `tbill_rate: Float64` (that calendar day's rate), `month_start: Boolean` (first bar of each
  calendar month), `day_start: Boolean`;
  `bars_path(interim_dir: Path) -> Path` (`bars.parquet`); `write_bars`, `read_bars`.

- [ ] **Step 1: Write failing tests** on 3 synthetic days: `test_funding_lands_on_print_bar`,
  `test_funding_moves_to_last_bar_before_print_when_minute_missing`,
  `test_month_start_flag_once_per_month`, `test_minutes_without_mark_are_absent`,
  `test_spot_is_asof_backward`.

- [ ] **Step 2–4: FAIL, implement (`join_asof(strategy="backward")` for spot and funding;
  `tbill` joined on `ts_us // DAY_US`), PASS.**

- [ ] **Step 5: Commit** `git commit -m "Minute bar table joining mark, spot, funding and T-bill"`

---

### Task 7: The book: design-(B) hold-through engine

**Files:**
- Create: `src/perp_carry_economics/carry/__init__.py`,
  `src/perp_carry_economics/carry/book.py`, `tests/test_book.py`, `tests/bars_factory.py`
  (builds synthetic bar frames: `flat(days)`, `rally(pct, minutes)`, `with_funding(rates)`)

**Interfaces:**
- Consumes: `params.MarginSetting`, `rules.Rules`, bar schema from Task 6.
- Produces:
  `class Mode(StrEnum): SEPARATE = "B"; ONE_ACCOUNT = "A"`;
  `@dataclass class Book` (mutable state): `qty: float` (BTC, both legs), `spot_qty: float`,
  `perp_entry: float`, `margin_cash: float`, `reserve: float`, `pending_topup_us: int | None`,
  `hedged: bool`, `unhedged_until_us: int | None`;
  `run_book(bars: pl.DataFrame, setting: MarginSetting, rules: Rules, mode: Mode,
  notional: float = NOTIONAL_USD) -> pl.DataFrame` returning the **ledger**, one row per bar
  with columns: `ts_us`, `qty`, `spot_qty`, `mark`, `margin_equity`, `margin_frac`,
  `reserve`, `funding_usd` (signed, + = received), `fee_usd`, `topup_usd`, `spot_sold_btc`,
  `liquidated: Boolean`, `liq_loss_usd`, `sweep_usd` (non-zero on month-start bars only),
  `hedged: Boolean`;
  `events(ledger) -> pl.DataFrame` (rows where `liquidated` or `topup_usd != 0` or
  `sweep_usd != 0`).
- Algorithm (the spec pins it; the implementer writes the body):
  1. **Reset** on `month_start` bars (and the first bar): target `q* = notional / mark`;
     trade spot `|spot_qty − q*|` at `spot` and perp `|qty − q*|` at `mark`, each paying its
     taker fee; set `perp_entry = mark`, `margin_cash = m0 × notional`, `reserve = reserve_frac
     × notional`; `sweep = total_equity_before − CAPITAL_MULT × notional − fees`; cancel any
     pending top-up (Review Focus 3).
  2. **Funding** on bars with `funding_rate`: `funding_usd = rate × mark × qty` credited to
     `margin_cash` (short receives when positive) while `hedged`.
  3. **T-bill** on `day_start` bars: `reserve *= (1 + tbill_rate / 365)`.
  4. **Equity**: `margin_equity = margin_cash + (perp_entry − mark) × qty`;
     `margin_frac = margin_equity / (mark × qty)`. In `ONE_ACCOUNT` mode add `(1 − haircut) ×
     spot_qty × spot` to `margin_equity`, keep `reserve` idle in T-bills and never top up.
  5. **Liquidation** if `margin_frac ≤ rules.maintenance_rate(mark × qty)` while hedged:
     `liq_loss_usd = margin_equity + rules.liquidation_fee × mark × qty` (equity is lost;
     the fee is charged in addition — stated conservative), `margin_cash = 0`, `hedged =
     False`, `unhedged_until_us = ts_us + tau_us`, clear pending top-up.
  6. **Re-establish** on the first bar with `ts_us ≥ unhedged_until_us`: `q = spot_qty / (1 +
     m0)`; sell `spot_qty − q` spot, open short `q` at mark, `margin_cash = m0 × mark × q`,
     fees on both; `hedged = True`.
  7. **Breach** while hedged and no pending: if `margin_frac < trigger`, `pending_topup_us =
     ts_us + tau_us`.
  8. **Landing** on the first bar with `ts_us ≥ pending_topup_us` (first bar after a gap
     counts — Review Focus 1): re-evaluate; if `margin_frac` still `< trigger`, shortfall
     `A = m0 × mark × qty − margin_equity`; take `min(A, reserve)` from reserve; remainder `B`:
     sell `ΔS = B / (spot × (1 − spot_taker))` spot, buy back `ΔS` perp at mark (fee), `qty =
     spot_qty`; credit `A` to `margin_cash`. Clear pending.
  9. **End**: on the last bar, close both legs with fees; final `sweep` of all equity.

- [ ] **Step 1: Write failing tests** (synthetic bars, `rules` with 10 bp / 5 bp fees, tiers
  `((inf, 0.005),)`, `tau = 60 min`):
  - `test_flat_price_pnl_is_funding_minus_fees_and_tbill` — 2 months flat, 3 prints/day at
    1 bp: Σ`sweep_usd` equals Σ funding + reserve T-bill − fees to 1e-6.
  - `test_rally_within_tau_liquidates` — +40% in 30 minutes with `m0=0.25`: exactly one
    `liquidated` row, `liq_loss_usd > 0`, `hedged` False for 60 minutes, then re-established
    with `qty == spot_qty` and `qty < qty_before`.
  - `test_slow_rally_tops_up_from_reserve_first` — +15% over 6 hours: one `topup_usd` row,
    `reserve` reduced by that amount, `spot_sold_btc == 0`, no liquidation.
  - `test_topup_sells_spot_when_reserve_exhausted` — +45% over 2 days with `reserve_frac` 0.05:
    `spot_sold_btc > 0` and after the landing `qty == spot_qty` (Review Focus 4).
  - `test_breach_that_heals_before_tau_lands_nothing`.
  - `test_topup_lands_on_first_bar_after_gap` — remove the 30 bars around the deadline.
  - `test_reset_cancels_pending_topup_and_restores_m0` (Review Focus 3).
  - `test_one_account_mode_never_liquidates_on_rally` — +300% over a month in `ONE_ACCOUNT`.
  - `test_ledger_is_byte_identical_on_rerun` — `run_book` twice, `frame_equal` and identical
    parquet bytes via `write_parquet_atomic`.

- [ ] **Step 2: Run, FAIL. Step 3: Implement `book.py`** as a plain Python loop over
  `bars.iter_rows(named=True)` collecting lists, then `pl.DataFrame`; 3M bars in pure Python
  is tens of seconds, acceptable (one run per setting; nine settings).

- [ ] **Step 4: Run, PASS.** Also `uv run mypy src`.

- [ ] **Step 5: Commit** `git commit -m "Carry book: USD-reset margin engine with top-ups and liquidation"`

---

### Task 8: Cut filter row

**Files:**
- Create: `src/perp_carry_economics/carry/filter.py`, `tests/test_filter.py`
- Modify: `src/perp_carry_economics/carry/book.py` (add `signal: pl.Series | None = None`
  parameter to `run_book`)

**Interfaces:**
- Produces: `filter_signal(bars: pl.DataFrame, rules: Rules) -> pl.Series[Boolean]` named
  `in_position`, one value per bar: trailing `FILTER_PRINTS` prints' mean rate ×
  `PRINTS_PER_YEAR` is the annualised funding; exit (False) when it falls below `tbill_rate`;
  re-enter (True) when it exceeds `tbill_rate + rules.round_trip_fee() × 365 /
  HURDLE_AMORTISE_DAYS`; starts True; evaluated on print bars, held between prints.
- `run_book(..., signal=...)`: on a bar where `signal` turns False, close both legs at the
  next bar with fees and move all equity to `reserve` (earning T-bill); where it turns True,
  re-enter with the reset procedure. `hedged` is False while flat; `funding_usd` is 0.
- `negative_stretches(funding: pl.DataFrame) -> pl.DataFrame[start_us, end_us, prints,
  mean_rate]` — maximal runs of consecutive negative prints (the "short and mean-reverting"
  prior, reported in results).

- [ ] **Step 1: Write failing tests** `test_signal_exits_below_tbill_and_reenters_above_hurdle`
  (hysteresis: a rate between the two thresholds keeps the current state),
  `test_single_negative_print_does_not_flip`, `test_flat_book_earns_tbill_on_all_capital`
  (ledger `reserve` grows at `tbill_rate / 365` daily while flat),
  `test_negative_stretches_counts_runs`.

- [ ] **Step 2–4: FAIL, implement, PASS. Step 5: Commit** `git commit -m "Cut filter with hysteresis and negative-funding stretches"`

---

### Task 9: Yield decomposition

**Files:**
- Create: `src/perp_carry_economics/carry/yields.py`, `tests/test_yields.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) class Decomposition`: `years: float`,
  `gross_funding_usd` (Σ positive `funding_usd`), `negative_funding_usd` (Σ negative),
  `fees_usd`, `liq_loss_usd`, `tbill_on_idle_usd`, `net_usd` (Σ `sweep_usd`),
  `capital_usd` (= `CAPITAL_MULT × notional`), `gross_yield` (= gross / notional / years,
  the pitch-deck number), `net_yield` (= net / capital / years), `tbill_mean` (capital-weighted
  mean daily rate over the period), `spread` (= net_yield − tbill_mean);
  `decompose(ledger: pl.DataFrame, bars: pl.DataFrame, notional: float) -> Decomposition`;
  `Decomposition.row(self, label: str) -> dict[str, float | str]` for tables;
  `worth_it(d: Decomposition) -> bool` (= `d.spread >= WORTH_IT_SPREAD`).
- Identity test: `net_usd == gross + negative − fees − liq_loss + tbill_on_idle ±
  basis_usd` where `basis_usd` is the residual (spot-vs-mark convergence realised at resets),
  reported as its own field `basis_usd`.

- [ ] **Step 1: Write failing tests** on a tiny hand-built ledger: `test_decomposition_identity_closes`
  (residual equals the hand-computed basis), `test_gross_uses_spot_notional_net_uses_capital`,
  `test_worth_it_threshold_is_2pp`.

- [ ] **Step 2–4: FAIL, implement, PASS. Step 5: Commit** `git commit -m "Yield decomposition and spread over T-bill"`

---

### Task 10: Episode selection

**Files:**
- Create: `src/perp_carry_economics/episodes.py`, `tests/test_episodes.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) class Episode: kind: str; week_start: date; week_end:
  date; anchor_us: int; note: str`;
  `liquidation_week(ledger: pl.DataFrame, setting: MarginSetting, rules: Rules) -> Episode`
  — the ISO week (Monday 00:00 UTC) containing the first `liquidated` bar; if none, the week
  containing the bar with the highest `closest_call = (trigger − min margin_frac within the
  following tau) / (trigger − maintenance)` over bars where a top-up was pending, `note =
  "no liquidation; closest call"`;
  `inversion_week(bars: pl.DataFrame) -> Episode` — the week containing the print with the
  lowest trailing-21-print mean funding;
  `week_slice(ledger, episode) -> pl.DataFrame` (that week's rows, for figures later).

- [ ] **Step 1: Write failing tests** `test_liquidation_week_is_first_liquidation`,
  `test_closest_call_when_no_liquidation`, `test_inversion_week_is_lowest_trailing_funding`,
  `test_week_starts_monday_utc`.

- [ ] **Step 2–4: FAIL, implement, PASS. Step 5: Commit** `git commit -m "Episode selection by frozen rule"`

---

### Task 11: Capacity model

**Files:**
- Create: `src/perp_carry_economics/capacity.py`, `tests/test_capacity.py`

**Interfaces:**
- Consumes: Task 3 `load_oi`, `load_depth`; Task 9 `Decomposition`.
- Produces:
  `fit_kappa(funding: pl.DataFrame, oi: pl.DataFrame) -> tuple[float, float, int]` —
  OLS of per-print funding change `Δr_t` on `Δlog(top_ls_position_ratio)` sampled at the same
  prints; returns `(kappa, stderr, n)`; `kappa` is expected positive (more longs → higher
  funding) and is used with `Δlog ratio = −log(1 + s)` for a short that is share `s` of OI.
  **The regression form is frozen in `results/carry_freeze.json` by Task 13; this is the
  author's default** and the README labels it a model.
  `oi_share(size_usd: float, oi: pl.DataFrame) -> float` (median `size / oi_usd` over the
  period); `funding_haircut(size_usd, kappa, oi) -> float` (per-print rate reduction `=
  kappa × log(1 + oi_share)`);
  `depth_cost(size_usd: float, depth: pl.DataFrame, reset_ts: pl.Series) -> float` — mean
  round-trip slippage fraction walking the published `pct`/`notional_usd` levels at each
  reset minute from 2023-01-01, perp side, applied to both legs (spot proxied — spec 9);
  `capacity_curve(sizes: tuple[float, ...], base: Decomposition, kappa: float, oi, depth,
  reset_ts, years: float) -> pl.DataFrame[size_usd, oi_share, funding_haircut_yield,
  slippage_yield, modelled_spread]` where `modelled_spread = base.spread − haircut ×
  PRINTS_PER_YEAR − slippage × 12` (monthly round trips);
  `capacity_usd(curve, base) -> float | None` — smallest size where `modelled_spread ≤
  CAPACITY_HALF × base.spread`, linear interpolation, `None` if never reached;
  `SIZES = (1e6, 3e6, 1e7, 3e7, 1e8, 3e8, 1e9)`.

- [ ] **Step 1: Write failing tests** `test_fit_kappa_recovers_slope_on_synthetic`
  (ratio-driven funding with noise, slope within 2 stderr), `test_depth_cost_walks_levels`
  (hand-built two-level book: exact expected slippage), `test_capacity_is_none_when_curve_flat`,
  `test_capacity_interpolates_half_spread`.

- [ ] **Step 2–4: FAIL, implement (numpy `lstsq` for OLS), PASS. Step 5: Commit**
  `git commit -m "Capacity model: funding sensitivity and depth slippage"`

---

### Task 12: Validation checks and `pce fetch` / `build` / `validate`

**Files:**
- Create: `src/perp_carry_economics/checks.py`, `src/perp_carry_economics/validate.py`,
  `tests/test_checks.py`, `tests/test_cli_pipeline.py`
- Modify: `src/perp_carry_economics/cli.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) class CheckResult: name: str; status: Literal["PASS",
  "FAIL"]; detail: str; numbers: dict[str, float | int]`;
  `check_d1_funding_complete(funding, start, end) -> CheckResult` (exactly 3 prints per day;
  lists missing);
  `check_d2_mark_gaps(mark, start, end, max_gap_min: int = 60) -> CheckResult` (no gap longer
  than `max_gap_min`; all gaps > 5 min listed in `numbers["gaps"]` count);
  `check_d3_oi_daily(oi, start, end)` (at least one row per day);
  `check_d4_tbill_covers(tbill, start, end)`;
  `check_e1_funding_total(ledger, bars)` (Σ `funding_usd` recomputed from `funding_rate ×
  mark × qty` on the ledger's own `qty` equals the ledger's total exactly, `== 0.0` difference
  after `math.fsum` on both sides);
  `check_e2_rerun_identical(bars, setting, rules)` (two runs, identical parquet bytes);
  `write_report(results: list[CheckResult], out_dir: Path, extra: dict[str, Any]) -> str`
  writing `data_validation/validation_report.json` and `.md` (Project 1's format);
  `require_pass(report_path: Path) -> None` raising `SystemExit` unless every status is PASS
  and the report's input hashes match the files on disk.
- CLI: `pce fetch [--start YYYY-MM-DD] [--end YYYY-MM-DD]` (defaults: `PERIOD_START`, first
  day of the current month) → `fetch_all` + `fetch_dtb3`; `pce build` → `write_bars`;
  `pce validate` → D1–D4 on raw, E1–E2 on the calibration window, report written.

- [ ] **Step 1: Write failing tests** for each check with a passing and a failing fixture,
  `test_require_pass_refuses_fail`, `test_require_pass_refuses_changed_input_hash`, and
  `test_cli_build_then_validate_on_fixture_data(tmp_path, monkeypatch)` (fixture zips from
  Task 3's helper, `DATA_DIR` pointed at `tmp_path`).

- [ ] **Step 2–4: FAIL, implement, PASS. Step 5: Commit** `git commit -m "Checks D1-D4, E1-E2 and the fetch/build/validate commands"`

---

### Task 13: Pre-registration freeze

**Files:**
- Create: `src/perp_carry_economics/freeze.py`, `tests/test_freeze.py`
- Modify: `src/perp_carry_economics/cli.py`, `assets/binance_rules.json` (`"verified": true`
  at freeze time, by the user, not by code)

**Interfaces:**
- Produces: `FREEZE_PATH = RESULTS_DIR / "carry_freeze.json"`;
  `freeze_payload(raw_dir: Path, period_end: date) -> dict[str, Any]` with keys:
  `"spec"` (path + `text_hash`), `"params"` (every constant in `params.py` by name, the
  grid as a list), `"rules"` (the asset JSON verbatim + its hash), `"period"` (`start`,
  `end`, `calibration_end`), `"capacity_model"` (`"delta_funding ~ kappa * delta_log(top_ls_
  position_ratio); position share s applied as -log(1+s) per print; depth walk at resets
  from 2023-01-01, perp book as spot proxy"`), `"episode_rules"` (spec 8 text), `"prediction"`
  (`"cut filter does not beat hold-through on net spread after fees"`), `"input_hashes"`
  (`file_hash` of every raw file), `"written_at"` (date only);
  `write_freeze(path, payload)` (refuses if exists — Project 1's), `read_freeze(path,
  input_hashes) -> dict` (refuses on missing keys or hash mismatch — Project 1's).
- CLI: `pce freeze --period-end YYYY-MM-DD` → `require_pass` then `write_freeze`; prints the
  next step: `git add results/carry_freeze.json && git commit && git tag carry-preregistered`.

- [ ] **Step 1: Write failing tests** `test_freeze_refuses_overwrite`,
  `test_read_freeze_refuses_changed_inputs`, `test_payload_has_every_param_name`
  (introspect `params` module constants, all present), `test_payload_is_sorted_json_lf`.

- [ ] **Step 2–4: FAIL, implement, PASS. Step 5: Commit** `git commit -m "Pre-registration freeze"`

---

### Task 14: `pce run` and results

**Files:**
- Create: `src/perp_carry_economics/run.py`, `tests/test_run.py`
- Modify: `src/perp_carry_economics/cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `run_all(bars, rules, oi, depth, out_dir: Path, settings: list[MarginSetting],
  notional: float) -> dict[str, Any]` (the summary) writing to `out_dir`:
  `ledger_B_primary.parquet`, `ledger_A.parquet`, `ledger_filter.parquet`,
  `sensitivity.csv` (one row per grid setting: liquidations, top-ups, spot sold, net, spread),
  `decomposition.csv` (rows: `hold_through_B`, `one_account_A`, `cut_filter`, plus the VIP-fee
  variant of `hold_through_B`), `episodes.json`, `capacity.csv`, `negative_stretches.csv`,
  `summary.json` (every headline number, `worth_it`, `prediction_confirmed: bool` = filter
  spread ≤ hold-through spread, `kappa`, `capacity_usd`, period, run inputs' hashes).
- CLI: `pce run [--calibration]`: without the flag, `read_freeze` must succeed and the period
  is the frozen one, output `results/`; with it, the calibration window, output
  `results/calibration/` (git-ignored), no freeze needed. Both call `require_pass` first.
- `tests/test_run.py::test_run_on_fixture_bars_writes_every_file` and
  `test_summary_is_byte_identical_on_rerun`.

- [ ] **Step 1–4: tests FAIL, implement, PASS.**

- [ ] **Step 5: Add `/results/calibration/` to `.gitignore`. Commit**
  `git commit -m "pce run: rows, sensitivity, episodes, capacity and summary"`

---

### Task 15: `pce all`, reproduce notes, roadmap

**Files:**
- Modify: `src/perp_carry_economics/cli.py`, `README.md`
- Create: `docs/roadmap.md`, `tests/test_cli_all.py`

- [ ] **Step 1: Test** `test_all_runs_fetch_build_validate_run_in_order` (monkeypatched
  functions record call order).
- [ ] **Step 2–3: Implement `pce all [--calibration]`** = fetch → build → validate → run.
  README gets a "Reproduce" section: the four commands, data size, run time, and the sentence
  that the freeze tag must exist before `pce run` without `--calibration`. `docs/roadmap.md`
  in Project 1's format: Done (this plan), Next (figures and README briefing; Hyperliquid
  robustness).
- [ ] **Step 4: Full suite, ruff, mypy, PASS. Commit** `git commit -m "pce all and reproduce notes"`

---

## Self-review

- **Spec coverage:** §3 venue/data → Tasks 3–5; §4 period → Task 1 constants, Task 13 freeze;
  §5 trade and (A) row → Task 7; §6 strategy rule and prediction → Tasks 8, 13, 14; §7
  parameters → Task 2 (denominator and idle-cash rule in Tasks 7–9); §8 episodes → Task 10;
  §9 capacity → Task 11; §10 freeze → Task 13; §11 checks → Task 12; §12 phases 1–3 and the
  run → Tasks 12–15; §13 toolchain → Task 1. Not in this plan, by design: figures, the README
  briefing, Hyperliquid, the site.
- **Step scan:** each step names one file, signature or assertion; the only bodies given are
  Task 7's algorithm (not determined by its signature) and none elsewhere.
- **Type consistency:** `MarginSetting` fields (`m0, trigger, tau_us, reserve_frac`) are used
  by Tasks 7, 10, 12, 14 with those names; `Rules` methods `maintenance_rate`,
  `round_trip_fee`, `vip_variant` by Tasks 7, 8, 14; ledger column names in Task 7 are the ones
  Tasks 9, 10, 12 read.
- **Review Focus:** all five pinned to tests in Tasks 4, 6, 7.
- **Proportion:** the plan is about the spec's length; only Task 7 carries an algorithm, and
  the spec does not determine it from the signature alone.
