# Figures and README Briefing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The README first screen from the brief, with its two figures rendered from `results/`
by a new `pce figures` command: the hero waterfall, the spread-by-calendar-year figure (computed
after the run and labelled so), and the briefing text whose numbers are tested against the
result files.

**Architecture:** A `viz/` package copied in shape from `../uniswap-lp-economics/src/uniswap_lp_economics/viz/`
(matplotlib, bundled Inter, deterministic SVG), reduced to what two figures need and extended
with a dark palette. `carry/yields.py` gains `yearly(...)`, the one new computation; `pce figures`
writes `results/yearly.csv` and `results/figures/*.svg` (light and dark). `pce run`, the freeze
and every existing result file are untouched. README numbers are read back by a test so the
page can never drift from the run.

**Tech Stack:** Python ≥ 3.13, uv, polars ≥ 2.0, matplotlib ≥ 3.11 (new dependency), the Inter
font files from Project 1 (`assets/fonts/`, OFL); pytest, ruff, mypy --strict.

**Spec:** `docs/specs/2026-10-28-readme-first-screen.md` (the brief) and
`docs/specs/2026-10-26-perp-carry-economics-design.md` §12 phase 4.

## Global Constraints

- No AI attribution in commits (user rule). Author `Enrico Nagata`.
- Nothing under `results/` that exists today changes: `results/carry_freeze.json`,
  `summary.json`, `decomposition.csv`, `episodes.json`, `sensitivity.csv`, `capacity.csv`,
  `negative_stretches.csv`, the two episode parquets. `pce run` is not re-run and not edited.
- Every new result file goes through `io.write_text_atomic`; SVGs are written with
  `metadata={"Date": None}` and `svg.hashsalt` fixed so two renders are byte-identical.
- Figures read only `results/` plus `data/interim/` bars (for the T-bill series) and the
  git-ignored `results/ledger_B_primary.parquet` (rebuilt by `pce run`).
- Hero order and labels, verbatim from the brief: "Positive funding on $10 M notional",
  "Same dollars on $15 M posted", "T-bill on idle cash", "Negative funding", "Fees and basis",
  "Minus T-bills on all $15 M", "Over cash". Values one decimal; the answer bar reads
  "5.1 %" over "over cash" (from the data, not typed).
- Colours, validated with the dataviz palette script on their own surface: light surface
  `#FFFFFF`, add `#2B6CD9`, cut `#7B8794`, answer fill `#143F7A`, ink `#1F2933`, muted
  `#5F6B76`; dark surface `#0D1117` (GitHub dark), add `#74ADFF`, cut `#66717F`, answer fill
  `#2F6FD6`, ink `#E6EDF3`, muted `#9DA7B3`. Text inside the answer fill is white in both.
- Marks: bars at 0.5 of the slot; floating bars square; 4 px radius on the data-end of the first
  and last bars only; connectors 1 px solid in `cut`; no y-axis, no gridlines; one sans (Inter).
- The calendar-year split is described on the page as added after the run, not pre-registered.
- Period and tag text: `2020-09-01` to `2026-10-01` exclusive, tag `carry-preregistered`; links
  to the tag use `/blob/<tag>/<file>`, never `/tree/<tag>`.

## Review Focus

- A ledger whose last bar is not a month start (the full run ends 2026-09-30 23:59) must put
  the final sweep in 2026, not drop it. → Task 1 `test_final_sweep_belongs_to_its_own_year`.
- A sweep landing on 1 January must count in the year that ended, not the new one. → Task 1
  `test_month_start_sweep_belongs_to_previous_year`.
- `pce figures` run before `pce run` (no summary, no ledger) must refuse with a message naming
  the step, not raise a traceback. → Task 6 `test_figures_refuses_without_results`.
- A hero row with a liquidation loss or unhedged P&L (a sensitivity cell, or a future run)
  must not hide it inside "Fees and basis". → Task 3 `test_hero_label_names_liquidations_when_present`.
- A spread exactly at zero or negative in a year must draw as a cut, label "−1.0", never
  "-1.0" or "0.0" with a sign. → Task 4 `test_negative_year_is_a_cut_with_minus_sign`.

---

### Task 1: Calendar-year split of the primary ledger

**Files:**
- Modify: `src/perp_carry_economics/carry/yields.py`
- Test: `tests/test_yearly.py`

**Interfaces:**
- Consumes: `Decomposition`, `decompose(ledger, bars, setting, notional)` as they are;
  `bars_factory.frame/flat/with_funding`, `test_run.RULES` for a tiny world.
- Produces: `yearly(ledger: pl.DataFrame, bars: pl.DataFrame, setting: MarginSetting, notional: float) -> pl.DataFrame`
  with columns `year Int32, days Int64, partial Boolean, first_day Date, last_day Date,
  gross_funding_usd, net_usd, gross_yield, net_yield, tbill_mean, spread` (Float64 from
  `gross_funding_usd` on), one row per calendar year the ledger touches, sorted by year.
  `first_day`/`last_day` are the first and last bar dates in that year (Task 4 prints the
  month range of partial years from them). Attribution rule: a bar's cash flows belong
  to the bar's own calendar year, except a sweep on a `month_start` bar, which belongs to the
  year of the day before. `days` is the count of distinct dates in that year; `partial` is
  `days < 365`. `gross_yield = gross_funding_usd / notional / (days/365.25)`; `net_yield =
  net_usd / capital / (days/365.25)` with `capital = (1 + m0 + reserve_frac) * notional`;
  `tbill_mean` is the mean of `bars.tbill_rate` on `day_start` rows of that year; `spread =
  net_yield - tbill_mean`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_yearly.py
def test_month_start_sweep_belongs_to_previous_year():
    # a flat world from 2023-12-20 to 2024-01-10 with positive funding; the 2024-01-01 sweep
    # carries December's funding, so 2023 net > 0 and 2024 net == the final sweep only
    ...
    assert y.filter(pl.col("year") == 2023)["net_usd"][0] > 0
    assert y["year"].to_list() == [2023, 2024]

def test_final_sweep_belongs_to_its_own_year():
    # a world ending mid-month: the last bar's sweep lands in that bar's year
    ...
    assert math.isclose(y["net_usd"].sum(), decompose(ledger, bars, PRIMARY, N).net_usd, rel_tol=1e-12)

def test_partial_flag_days_and_day_range():
    assert y.filter(pl.col("year") == 2024).select("partial", "days", "first_day", "last_day").row(0) == (
        True, 10, date(2024, 1, 1), date(2024, 1, 10))

def test_spread_is_net_yield_minus_tbill_mean():
    row = y.row(0, named=True)
    assert math.isclose(row["spread"], row["net_yield"] - row["tbill_mean"], abs_tol=1e-12)
```

Build the worlds with `bars_factory.frame(flat(days), start=...)` the way `test_run.world`
does; run the book with `run_book(bars, PRIMARY, RULES, Mode.SEPARATE)`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_yearly.py -v`
Expected: FAIL with `ImportError: cannot import name 'yearly'`

- [ ] **Step 3: Implement `yearly(...)` in `carry/yields.py`**

Derive `year` per bar with polars (`pl.from_epoch("ts_us", "us")`), then an attribution column
`sweep_year = when(month_start).then(year of ts_us - DAY_US).otherwise(year)`; group sums go
through `io.ordered_group_by`. Gross funding is the sum of positive `funding_usd` by bar year.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_yearly.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/perp_carry_economics/carry/yields.py tests/test_yearly.py
git commit -m "Yearly split of the primary ledger, sweeps attributed to the month they cover"
```

---

### Task 2: Figure style, palettes, fonts and results loader

**Files:**
- Create: `src/perp_carry_economics/viz/__init__.py`, `src/perp_carry_economics/viz/style.py`,
  `src/perp_carry_economics/viz/data.py`
- Create: `assets/fonts/Inter-Regular.ttf`, `Inter-SemiBold.ttf`, `Inter-Bold.ttf`,
  `LICENSE.txt` (copied from `../uniswap-lp-economics/assets/fonts/`)
- Modify: `pyproject.toml` (add `"matplotlib>=3.11.2"` to dependencies)
- Test: `tests/test_viz_style.py`, `tests/vizfix.py`

**Interfaces:**
- Consumes: `results/` layout as `run.run_all` writes it; `yearly()` from Task 1.
- Produces:
  - `style.Palette` (frozen dataclass: `background, ink, muted, add, cut, answer, answer_text`),
    `style.PALETTES: dict[str, Palette]` with keys `"light"`, `"dark"` and the hex values from
    Global Constraints; `style.MODES = ("light", "dark")`.
  - `style.theme(mode: str) -> ContextManager[Palette]` (registers fonts, applies rcParams:
    Inter, `svg.fonttype = "path"`, `svg.hashsalt = "pce-figures"`, no spines, no grid).
  - `style.save_svg(fig: Figure, path: Path) -> None` (parent mkdir, `metadata={"Date": None}`,
    closes the figure).
  - `style.fmt_pts(x: float) -> str`: one decimal, Unicode minus `−`, explicit `+` only when the
    caller asks (`fmt_pts(x, signed=True)`).
  - `data.Results` (frozen dataclass: `summary: dict[str, Any]`, `decomposition: pl.DataFrame`,
    `episodes: list[dict[str, Any]]`, `yearly: pl.DataFrame`) and
    `data.load_results(results_dir: Path) -> Results` reading `summary.json`,
    `decomposition.csv`, `episodes.json`, `yearly.csv`.
  - `tests/vizfix.py`: `results_dir(tmp_path: Path) -> Path` that runs `run.run_all` on
    `test_run.world()` with `test_run.RULES/SETTINGS/INFO` into `tmp_path / "results"`, then
    writes `yearly.csv` there from the ledger it produced (the same call Task 6 makes).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_viz_style.py
def test_both_modes_render_the_same_text_in_their_own_ink():
    for mode in MODES:
        with theme(mode) as p:
            assert plt.rcParams["text.color"] == p.ink

def test_svg_is_byte_identical_across_two_renders(tmp_path):
    # same figure drawn twice -> same bytes (no date, fixed hashsalt)

def test_fmt_pts_uses_unicode_minus_and_one_decimal():
    assert fmt_pts(-4.234) == "−4.2" and fmt_pts(0.42, signed=True) == "+0.4"
    assert fmt_pts(-0.04) == "−0.0"  # the caller decides when to merge such bars

def test_missing_font_file_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(style, "FONT_DIR", tmp_path)
    with pytest.raises(FileNotFoundError):
        register_fonts()

def test_load_results_reads_the_four_files(tmp_path):
    r = load_results(vizfix.results_dir(tmp_path))
    assert set(r.decomposition["label"]) >= {"hold_through_B", "cut_filter"}
    assert r.yearly.columns[:3] == ["year", "days", "partial"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_viz_style.py -v`
Expected: FAIL with `ModuleNotFoundError: perp_carry_economics.viz`

- [ ] **Step 3: Add matplotlib, copy the fonts, write `style.py` and `data.py`**

`uv add "matplotlib>=3.11.2"`. Copy the four font files. `style.py` follows Project 1's
`viz/style.py` with the palette fields and modes above, `matplotlib.use("Agg")` before pyplot,
and `CANVAS = (11.5, 6.2)`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_viz_style.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock assets/fonts src/perp_carry_economics/viz tests/test_viz_style.py tests/vizfix.py
git commit -m "Figure style: Inter, light and dark palettes, deterministic SVG, results loader"
```

---

### Task 3: The hero waterfall

**Files:**
- Create: `src/perp_carry_economics/viz/hero.py`
- Test: `tests/test_viz_hero.py`

**Interfaces:**
- Consumes: `Results`, `Palette`, `theme`, `fmt_pts`, `Decomposition` column names.
- Produces:
  - `hero.steps(row: dict[str, float]) -> list[tuple[str, float, str]]`: from one
    `decomposition.csv` row, the seven `(label, value_pts, kind)` tuples in brief order, `kind`
    in `{"total", "step", "answer"}`. Values in percentage points a year:
    `v1 = gross_yield*100`; `v2 = gross_funding_usd/capital_usd/years*100 - v1`;
    `v3 = tbill_on_idle_usd/capital_usd/years*100`; `v4 = negative_funding_usd/capital_usd/years*100`;
    `v5 = (-fees_usd - liq_loss_usd + unhedged_pnl_usd + basis_usd)/capital_usd/years*100`,
    labelled "Fees and basis" when `liq_loss_usd == 0 and unhedged_pnl_usd == 0`, else
    "Fees, basis, liquidations"; `v6 = -tbill_mean*100`; answer `= spread*100`.
  - `hero.hero(r: Results) -> Figure`: draws `steps(hold_through_B row)`; title "Where the
    headline funding goes"; subtitle with the period from `r.summary["period"]`; caption
    "Percentage points a year on the base each bar names. Source: Binance public data, FRED
    DTB3; run under the freeze `carry-preregistered`."; the answer value and "over cash"
    inside the last bar in `answer_text`, value at ≥ 28 pt.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_viz_hero.py
def test_steps_sum_to_the_spread(tmp_path):
    row = load_results(vizfix.results_dir(tmp_path)).decomposition.filter(pl.col("label") == "hold_through_B").row(0, named=True)
    s = steps(row)
    assert [k for _, _, k in s] == ["total", "step", "step", "step", "step", "step", "answer"]
    assert math.isclose(sum(v for _, v, k in s if k != "answer"), s[-1][1], abs_tol=1e-9)
    assert [lbl for lbl, _, _ in s] == ["Positive funding on $10 M notional", "Same dollars on $15 M posted",
        "T-bill on idle cash", "Negative funding", "Fees and basis", "Minus T-bills on all $15 M", "Over cash"]

def test_hero_label_names_liquidations_when_present():
    row = {..., "liq_loss_usd": 1000.0, ...}
    assert steps(row)[4][0] == "Fees, basis, liquidations"

def test_bars_are_coloured_by_sign_and_answer_text_is_inside(tmp_path):
    fig = hero(load_results(vizfix.results_dir(tmp_path)))
    patches = [p for p in fig.axes[0].patches]
    assert len(patches) == 7
    # add bars share one facecolor, cut bars another, the answer bar a third
    assert len({p.get_facecolor() for p in patches}) == 3
    texts = [t.get_text() for t in fig.axes[0].texts]
    assert "over cash" in texts and any(t.endswith(" %") for t in texts)

def test_no_axes_decorations(tmp_path):
    ax = hero(load_results(vizfix.results_dir(tmp_path))).axes[0]
    assert not ax.get_yaxis().get_visible() and not any(l.get_visible() for l in ax.get_ygridlines())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_viz_hero.py -v`
Expected: FAIL with `ModuleNotFoundError: perp_carry_economics.viz.hero`

- [ ] **Step 3: Implement `steps` and `hero` in `viz/hero.py`**

Draw with `ax.bar` at width 0.5, `FancyBboxPatch` only for the first and last bar's rounded end,
`ax.plot` hairlines in `cut` for connectors, labels via `fmt_pts` (signed for steps) above each
bar in `ink`; x tick labels wrapped at two lines; `ax.set_axis_off()` for y.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_viz_hero.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/perp_carry_economics/viz/hero.py tests/test_viz_hero.py
git commit -m "Hero waterfall: headline funding to spread over cash, seven steps from the decomposition"
```

---

### Task 4: Spread by calendar year

**Files:**
- Create: `src/perp_carry_economics/viz/yearly.py`
- Test: `tests/test_viz_yearly.py`

**Interfaces:**
- Consumes: `Results.yearly` (Task 1 columns), `Results.episodes` (`week_start`, `kind`).
- Produces: `yearly.spread_by_year(r: Results) -> Figure`: one bar per row of `r.yearly`,
  height `spread*100`, `add` when `>= 0` else `cut`, value label `fmt_pts` on each cap; x tick
  label is the year, with "(Sep–Dec)" / "(Jan–Sep)" style month ranges appended for `partial`
  rows (months from the ledger's first/last day in that year, abbreviated); each episode in
  `r.episodes` drawn as a small tick under its year's bar with the text "closest call" for kind
  `liquidation` and "inversion week" for kind `inversion`; title "What it paid each year, over
  cash"; caption "Calendar-year split of the frozen run, added after the run; not a
  pre-registered test." Month ranges come from `first_day`/`last_day` (Task 1).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_viz_yearly.py
def test_one_bar_per_year_coloured_by_sign(tmp_path):
    r = load_results(vizfix.results_dir(tmp_path))
    fig = spread_by_year(r)
    assert len(fig.axes[0].patches) == r.yearly.height

def test_negative_year_is_a_cut_with_minus_sign(tmp_path):
    # patch r.yearly so one year has spread -0.01
    ...
    assert "−1.0" in texts and "-1.0" not in texts

def test_partial_years_name_their_months(tmp_path):
    labels = [t.get_text() for t in fig.axes[0].get_xticklabels()]
    assert any("(" in l and "–" in l for l in labels)

def test_episodes_are_marked_under_their_year(tmp_path):
    texts = [t.get_text() for t in fig.axes[0].texts]
    assert "closest call" in texts and "inversion week" in texts
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_viz_yearly.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `spread_by_year` in `viz/yearly.py`**

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_viz_yearly.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/perp_carry_economics/viz/yearly.py tests/test_viz_yearly.py
git commit -m "Spread over cash by calendar year, episodes ticked, labelled as added after the run"
```

---

### Task 5: Registry and renderer, light and dark

**Files:**
- Create: `src/perp_carry_economics/viz/render.py`
- Test: `tests/test_viz_render.py`

**Interfaces:**
- Consumes: `hero.hero`, `yearly.spread_by_year`, `style.theme/MODES/save_svg`, `load_results`.
- Produces:
  - `render.FIGURES: list[tuple[str, Callable[[Results], Figure]]] = [("00_hero", hero.hero), ("01_yearly_spread", yearly.spread_by_year)]`.
  - `render.render_all(results_dir: Path, out_dir: Path) -> list[Path]`: for each figure and
    each mode writes `out_dir / f"{stem}.svg"` (light) and `out_dir / f"{stem}_dark.svg"`;
    removes any other `.svg` in `out_dir`; returns the written paths in order.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_viz_render.py
def test_renders_two_figures_in_two_modes_and_removes_stale(tmp_path):
    out = tmp_path / "figures"; out.mkdir(); (out / "zz_stale.svg").write_text("x")
    written = render_all(vizfix.results_dir(tmp_path), out)
    assert [p.name for p in written] == ["00_hero.svg", "00_hero_dark.svg", "01_yearly_spread.svg", "01_yearly_spread_dark.svg"]
    assert not (out / "zz_stale.svg").exists()

def test_dark_svg_uses_the_dark_surface(tmp_path):
    assert "#0D1117".lower() in (out / "00_hero_dark.svg").read_text().lower()

def test_rerender_is_byte_identical(tmp_path):
    a = {p.name: p.read_bytes() for p in render_all(rd, out)}
    b = {p.name: p.read_bytes() for p in render_all(rd, out)}
    assert a == b
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_viz_render.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `render.py`**

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_viz_render.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/perp_carry_economics/viz/render.py tests/test_viz_render.py
git commit -m "Figure registry and renderer: light and dark SVGs, stale files removed"
```

---

### Task 6: `pce figures`

**Files:**
- Modify: `src/perp_carry_economics/cli.py` (new command after `run`), `README.md` Reproduce
  section (one new step), `.gitignore` (nothing: `results/figures/` is tracked)
- Test: `tests/test_cli_figures.py`

**Interfaces:**
- Consumes: `config.RESULTS_DIR`, `config.INTERIM_DIR`, `read_bars`, `yearly()`, `PRIMARY`,
  `NOTIONAL_USD`, `render_all`, `io.write_text_atomic`.
- Produces: `figures_cmd(args) -> None` registered as `figures` with docstring
  `"write results/yearly.csv from the primary ledger and render results/figures/ (light and dark SVG)"`.
  Refuses with `SystemExit("results/summary.json missing; run `pce run` first")` when the
  summary is absent, and the same shape for a missing `ledger_B_primary.parquet`; refuses when
  `summary["calibration"]` is true (figures are for the frozen run only). Reads the ledger and
  the bars for the summary's period, writes `yearly.csv` (`write_csv` through
  `write_text_atomic`), then `render_all(RESULTS_DIR, RESULTS_DIR / "figures")`, and prints
  `wrote N figure files to results/figures`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli_figures.py
def test_figures_refuses_without_results(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path / "results")
    with pytest.raises(SystemExit, match="run `pce run` first"):
        cli.main(["figures"])

def test_figures_refuses_on_calibration_results(monkeypatch, tmp_path):
    # summary.json with "calibration": true
    with pytest.raises(SystemExit, match="calibration"):
        cli.main(["figures"])

def test_figures_writes_yearly_and_four_svgs(monkeypatch, tmp_path):
    # results via vizfix (which already writes yearly.csv); point INTERIM_DIR at bars written
    # with write_bars from test_run.world()[0]; delete yearly.csv first so the command makes it
    cli.main(["figures"])
    assert (tmp_path / "results" / "yearly.csv").exists()
    assert len(list((tmp_path / "results" / "figures").glob("*.svg"))) == 4
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli_figures.py -v`
Expected: FAIL (`figures` not a known command)

- [ ] **Step 3: Implement `figures_cmd` and register it; add the Reproduce step to README**

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli_figures.py tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 5: Run it for real and commit the outputs**

Run: `uv run pce figures`
Expected: `wrote 4 figure files to results/figures`; `results/yearly.csv` has 7 rows, 2020 and
2026 `partial = true`; the hero's last bar reads `5.1 %`.

```bash
git add src/perp_carry_economics/cli.py tests/test_cli_figures.py README.md results/yearly.csv results/figures
git commit -m "pce figures: yearly split and the two README figures, rendered from the frozen run"
```

---

### Task 7: Eye check of both renders

**Files:** none changed unless the check fails (then the figure module it concerns).

- [ ] **Step 1: Render PNG previews at README width**

Run (scratch, not committed): a short `uv run python -c` that calls `load_results`, then for
each `(stem, fn)` in `render.FIGURES` and each mode draws `fn(r)` inside `theme(mode)` and
`fig.savefig(<scratchpad>/f"{stem}_{mode}.png", dpi=110)`. Four PNGs.

- [ ] **Step 2: Look at all four**

Check, per the brief: labels do not collide at 900 px; the answer figure is inside its bar
with padding; connectors are solid; the floating bars are square; partial-year labels show the
month range; the two episode marks sit under 2022 and 2023; dark mode text is legible on
`#0D1117`. Fix in the owning module and re-run its tests and Task 6 Step 5 if anything fails.

---

### Task 8: README first screen and below the fold

**Files:**
- Modify: `README.md` (everything above the existing Reproduce section is replaced; Reproduce
  stays, with the `pce figures` step from Task 6)
- Test: `tests/test_readme.py`

**Interfaces:**
- Consumes: `results/decomposition.csv`, `results/summary.json`, `results/sensitivity.csv`,
  `results/yearly.csv`, `results/episodes.json` as committed.
- Produces: the page. Order, from the brief: title; the three-sentence headline (bold first
  sentence); `<picture>` for `00_hero` with a dark `<source>` and alt text that states the two
  end numbers; `<picture>` for `01_yearly_spread` with alt text naming the best and worst year;
  the three fact lines; the freeze line with links to `results/carry_freeze.json` and
  `docs/specs/2026-10-26-perp-carry-economics-design.md` at the tag; then "Below the fold"
  sections in the brief's order: the inversion week, the (A) row, the VIP 3 row, capacity, the
  sensitivity grid as a table, limitations (the six lines), "Added after the run", method
  (link to the spec and its §15), Reproduce. Every number on the page is read from a result file
  by the test below; the brief's provisional yearly table is replaced by `results/yearly.csv`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_readme.py — reads the committed results, so it runs on the real repo
def _pct(x): return f"{x*100:.1f}"

def test_headline_numbers_match_the_run():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    d = pl.read_csv(ROOT / "results/decomposition.csv")
    b = d.filter(pl.col("label") == "hold_through_B").row(0, named=True)
    f = d.filter(pl.col("label") == "cut_filter").row(0, named=True)
    s = json.loads((ROOT / "results/summary.json").read_text())
    y = pl.read_csv(ROOT / "results/yearly.csv")
    assert f"{_pct(b['spread'])} %" in text and f"{_pct(b['gross_yield'])} %" in text
    assert f"${s['capacity_usd']/1e6:.0f} M" in text
    assert f"{(b['spread']-f['spread'])*100:.1f} points" in text
    since_2022 = y.filter(pl.col("year") >= 2022)
    assert f"{(since_2022['spread'] * since_2022['days']).sum() / since_2022['days'].sum() * 100:.1f}" in text

def test_pictures_have_dark_sources_and_alt_text():
    assert text.count("<picture>") == 2 and text.count('media="(prefers-color-scheme: dark)"') == 2
    assert 'alt="' in text and "/tree/" not in text

def test_added_after_the_run_is_stated():
    assert "added after the run" in text.lower()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_readme.py -v`
Expected: FAIL (current README has none of these)

- [ ] **Step 3: Write the README**

Copy from the brief for the first screen. The headline's "since 2022" figure is the
day-weighted mean of `yearly.csv` spreads for 2022 onward, written in digits with one decimal
so the test finds it, e.g. "about 1.0 point over cash" (the brief's "about one point" becomes
that). Alt text, hero: "Waterfall from 12.7 % positive funding on notional to
5.1 % over T-bills on posted capital" with the two numbers read from the files when writing.

- [ ] **Step 4: Run the whole suite, ruff and mypy**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add README.md tests/test_readme.py
git commit -m "README: the briefing's first screen, two figures, facts and freeze line; numbers tested against results"
```

---

### Task 9: Roadmap and spec pointers

**Files:**
- Modify: `docs/roadmap.md` (phase 4 to Done with the two figure stems; Next = Hyperliquid
  robustness; drop any backlog line this plan closed), `docs/specs/2026-10-28-readme-first-screen.md`
  (replace the provisional yearly table with "see `results/yearly.csv`" and the attribution
  rule as implemented).

- [ ] **Step 1: Edit both files**
- [ ] **Step 2: Run the suite once more** — `uv run pytest -q`, expected green.
- [ ] **Step 3: Commit**

```bash
git add docs/roadmap.md docs/specs/2026-10-28-readme-first-screen.md
git commit -m "Roadmap: figures and README done; brief points at results/yearly.csv"
```
