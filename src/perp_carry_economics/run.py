"""`pce run`: every comparison row, the sensitivity grid, checks E1-E3, the episodes and the
capacity curve, written to one results directory. Nothing is written unless E1-E3 pass, and
every file is byte-identical on a rerun with the same inputs."""

import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any

import polars as pl

from perp_carry_economics import checks
from perp_carry_economics.capacity import SIZES, capacity_curve, capacity_usd, fit_kappa
from perp_carry_economics.carry.book import Mode, run_book
from perp_carry_economics.carry.filter import filter_signal, negative_stretches
from perp_carry_economics.carry.yields import decompose, worth_it
from perp_carry_economics.config import HOUR_US
from perp_carry_economics.data.rules import Rules
from perp_carry_economics.episodes import inversion_week, liquidation_week, week_slice
from perp_carry_economics.io import write_parquet_atomic, write_text_atomic
from perp_carry_economics.params import NOTIONAL_USD, PRIMARY, MarginSetting

ROWS = ("hold_through_B", "one_account_A", "cut_filter", "hold_through_B_vip")
LEDGER_FILES = {
    "hold_through_B": "ledger_B_primary.parquet",
    "one_account_A": "ledger_A.parquet",
    "cut_filter": "ledger_filter.parquet",
}
SENSITIVITY_COLUMNS = (
    "tau_h",
    "m0",
    "trigger",
    "reserve_frac",
    "capital_usd",
    "liquidations",
    "topups",
    "spot_sold_btc",
    "net_usd",
    "net_yield",
    "spread",
)
# capacity_curve's columns, for the header-only capacity.csv when there is no bookDepth
CAPACITY_SCHEMA = {
    "size_usd": pl.Float64,
    "oi_share": pl.Float64,
    "funding_haircut_yield": pl.Float64,
    "slippage_yield": pl.Float64,
    "modelled_spread": pl.Float64,
    "book_exhausted": pl.Boolean,
}


def turnover(ledger: pl.DataFrame) -> int:
    """Filter exits plus re-entries: flips of flat (`spot_qty == 0`) between bars, the last
    bar's close-out excluded. Only a filter exit sells all the spot, a liquidation keeps it, so
    liquidation-driven unhedging is not counted."""
    is_flat = (ledger["spot_qty"] == 0.0)[:-1]
    return int((is_flat != is_flat.shift(1))[1:].sum())


def _finite(v: Any) -> Any:
    """NaN and +-inf become null: JSON has neither, and `allow_nan=False` refuses them."""
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, dict):
        return {k: _finite(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_finite(x) for x in v]
    return v


def json_text(obj: Any) -> str:
    return json.dumps(_finite(obj), indent=2, sort_keys=True, allow_nan=False) + "\n"


def _sensitivity_row(
    bars: pl.DataFrame, s: MarginSetting, rules: Rules, notional: float
) -> dict[str, Any]:
    ledger = run_book(bars, s, rules, Mode.SEPARATE, notional)
    d = decompose(ledger, bars, s, notional)
    return {
        "tau_h": s.tau_us / HOUR_US,
        "m0": s.m0,
        "trigger": s.trigger,
        "reserve_frac": s.reserve_frac,
        "capital_usd": d.capital_usd,
        "liquidations": int(ledger["liquidated"].sum()),
        "topups": int((ledger["topup_usd"] != 0.0).sum()),
        "spot_sold_btc": math.fsum(ledger["spot_sold_btc"].to_list()),
        "net_usd": d.net_usd,
        "net_yield": d.net_yield,
        "spread": d.spread,
    }


def run_all(
    bars: pl.DataFrame,
    rules: Rules,
    oi: pl.DataFrame,
    depth: pl.DataFrame,
    out_dir: Path,
    settings: list[MarginSetting],
    notional: float = NOTIONAL_USD,
    run_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run everything on `bars` and write it to `out_dir`; return the summary as written.

    `run_info` (period, input hashes, freeze, calibration flag) goes into the summary as is."""
    ledger_b = run_book(bars, PRIMARY, rules, Mode.SEPARATE, notional)
    results = [
        checks.check_e1_funding_total(ledger_b, bars),
        checks.check_e2_rerun_identical(bars, PRIMARY, rules),
        checks.check_e3_cash_conservation(ledger_b),
    ]
    if failed := [r.name for r in results if r.status != "PASS"]:
        raise SystemExit(f"checks failed, nothing written: {', '.join(failed)}")

    ledgers = {
        "hold_through_B": ledger_b,
        "one_account_A": run_book(bars, PRIMARY, rules, Mode.ONE_ACCOUNT, notional),
        "cut_filter": run_book(
            bars, PRIMARY, rules, Mode.SEPARATE, notional, filter_signal(bars, rules)
        ),
        "hold_through_B_vip": run_book(bars, PRIMARY, rules.vip_variant(), Mode.SEPARATE, notional),
    }
    dec = {k: decompose(ledgers[k], bars, PRIMARY, notional) for k in ROWS}
    rows = [
        {**dec[k].row(k), "turnover": turnover(ledgers[k]) if k == "cut_filter" else None}
        for k in ROWS
    ]
    sensitivity = pl.DataFrame(
        [_sensitivity_row(bars, s, rules, notional) for s in settings]
    ).select(SENSITIVITY_COLUMNS)

    prints = bars.filter(pl.col("funding_rate").is_not_null()).select("ts_us", rate="funding_rate")
    stretches = negative_stretches(prints)
    lengths = stretches["prints"].to_list()
    base = dec["hold_through_B"]
    kappa, kappa_stderr, kappa_n = fit_kappa(prints, oi)
    depth_available = not depth.is_empty()  # bookDepth starts 2023-01-01: none in calibration
    curve = (
        capacity_curve(SIZES, base, kappa, oi, depth, ledger_b, notional)
        if depth_available
        else pl.DataFrame(schema=CAPACITY_SCHEMA)
    )
    episodes = [liquidation_week(ledger_b, PRIMARY, rules), inversion_week(bars)]

    summary = _finite(
        {
            **(run_info or {}),
            "bars": bars.height,
            "first_bar_us": bars["ts_us"][0],
            "last_bar_us": bars["ts_us"][-1],
            "checks": {r.name: asdict(r) for r in results},
            "rows": {r["label"]: r for r in rows},
            "worth_it": worth_it(base),
            "prediction_confirmed": dec["cut_filter"].spread <= base.spread,
            "kappa": kappa,
            "kappa_stderr": kappa_stderr,
            "kappa_n": kappa_n,
            "capacity_depth_available": depth_available,
            "capacity_usd": capacity_usd(curve, base) if depth_available else None,
            "book_exhausted_at_any_size": bool(curve["book_exhausted"].any()),
            "negative_stretches": {
                "count": len(lengths),
                "mean_prints": sum(lengths) / len(lengths) if lengths else None,
                "max_prints": max(lengths, default=None),
            },
        }
    )

    # every output is built before the first write: a failure leaves no old/new mix behind
    eps = [
        {**asdict(e), "week_start": e.week_start.isoformat(), "week_end": e.week_end.isoformat()}
        for e in episodes
    ]
    frames = {name: ledgers[k] for k, name in LEDGER_FILES.items()} | {
        f"episode_{e.kind}.parquet": week_slice(ledger_b, e) for e in episodes
    }
    texts = {
        "decomposition.csv": pl.DataFrame(rows).write_csv(),
        "sensitivity.csv": sensitivity.write_csv(),
        "capacity.csv": curve.write_csv(),
        "negative_stretches.csv": stretches.write_csv(),
        "episodes.json": json_text(eps),
        "summary.json": json_text(summary),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, df in frames.items():
        write_parquet_atomic(df, out_dir / name)
    for name, text in texts.items():
        write_text_atomic(out_dir / name, text)
    result: dict[str, Any] = summary
    return result
