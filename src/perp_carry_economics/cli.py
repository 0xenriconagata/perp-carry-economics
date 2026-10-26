"""Command-line entry point (`pce`); later tasks register their subcommands in COMMANDS."""

import argparse
import json
from collections.abc import Callable
from datetime import date

import polars as pl

from perp_carry_economics import checks, config, freeze
from perp_carry_economics.bars import bars_path, build_bars, read_bars, write_bars
from perp_carry_economics.carry.book import Mode, run_book
from perp_carry_economics.config import CALIBRATION_END, PERIOD_START
from perp_carry_economics.data.binance import (
    fetch_all,
    load_funding,
    load_mark,
    load_oi,
    load_spot,
)
from perp_carry_economics.data.fred import fetch_dtb3, load_tbill
from perp_carry_economics.data.rules import load_rules
from perp_carry_economics.params import PRIMARY
from perp_carry_economics.validate import input_hashes, require_pass, write_report


def default_end(today: date) -> date:
    """First day of `today`'s month: the exclusive end keeps the last full (published) month
    and requests no day of the current one, whose monthly Binance files do not exist yet."""
    return today.replace(day=1)


def _period_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--start", type=date.fromisoformat, default=PERIOD_START)
    p.add_argument("--end", type=date.fromisoformat, default=default_end(date.today()))


def fetch(args: argparse.Namespace) -> None:
    """download the Binance and FRED raw files for [start, end)"""
    fetch_all(args.start, args.end, config.RAW_DIR)
    fetch_dtb3(config.RAW_DIR)
    print(f"fetched [{args.start}, {args.end}) into {config.RAW_DIR}")


def build(args: argparse.Namespace) -> None:
    """build the minute bar table for [start, end) from the raw files"""
    raw = config.RAW_DIR
    tbill = load_tbill(raw, args.start, args.end)
    bars = build_bars(
        load_mark(raw), load_spot(raw), load_funding(raw), tbill, args.start, args.end
    )
    config.INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    write_bars(bars, config.INTERIM_DIR)
    print(f"built {bars.height} bars [{args.start}, {args.end}) -> {bars_path(config.INTERIM_DIR)}")


def _dtb3_prints(raw: pl.DataFrame) -> pl.DataFrame:
    """Days with a DTB3 print (FRED's `.` is no print); columns read by position as in fred.py."""
    return raw.filter(pl.col(raw.columns[1]) != ".").select(
        day=pl.col(raw.columns[0]).str.to_date("%Y-%m-%d")
    )


def validate(args: argparse.Namespace) -> None:
    """run checks D1-D4 on the raw files and E1-E3 on the calibration window, write the report"""
    raw, start, end = config.RAW_DIR, args.start, args.end
    day = pl.from_epoch("ts_us", "us").dt.date()
    calib = read_bars(config.INTERIM_DIR).filter(
        day.is_between(PERIOD_START, CALIBRATION_END, closed="left")
    )
    if calib.is_empty():
        raise SystemExit("no bars in the calibration window; run `pce build` first")
    rules = load_rules()
    ledger = run_book(calib, PRIMARY, rules, Mode.SEPARATE)
    results = [
        checks.check_d1_funding_complete(load_funding(raw), start, end),
        checks.check_d2_mark_gaps(load_mark(raw), start, end),
        checks.check_d3_oi_daily(load_oi(raw), start, end),
        checks.check_d4_tbill_covers(
            _dtb3_prints(pl.read_csv(raw / "fred" / "DTB3.csv", infer_schema=False)), start, end
        ),
        checks.check_e1_funding_total(ledger, calib),
        checks.check_e2_rerun_identical(calib, PRIMARY, rules),
        checks.check_e3_cash_conservation(ledger),
    ]
    extra = {
        "period": f"[{start}, {end})",
        "calibration": f"[{PERIOD_START}, {CALIBRATION_END})",
        "calibration_bars": calib.height,
        "input_hashes": input_hashes(raw),
    }
    status = write_report(results, config.VALIDATION_DIR, extra)
    failed = [r.name for r in results if r.status != "PASS"]
    print(
        f"validation {status}: {len(results) - len(failed)}/{len(results)} checks pass"
        + (f" (failed: {', '.join(failed)})" if failed else "")
        + f" -> {config.VALIDATION_DIR / 'validation_report.json'}"
    )
    if failed:
        raise SystemExit(1)


def _freeze_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--period-end", type=date.fromisoformat, required=True)


def freeze_cmd(args: argparse.Namespace) -> None:
    """write the pre-registration freeze (once) after the rule snapshot and validation pass"""
    if json.loads(freeze.RULES_PATH.read_text(encoding="utf-8")).get("verified") is not True:
        raise SystemExit(
            f"{freeze.RULES_PATH} has verified: false; verify the fee/tier snapshot and set "
            "verified to true yourself first"
        )
    require_pass(config.VALIDATION_DIR / "validation_report.json")
    freeze.write_freeze(freeze.FREEZE_PATH, freeze.freeze_payload(config.RAW_DIR, args.period_end))
    print(
        f"froze {freeze.FREEZE_PATH}; next: git add results/carry_freeze.json && "
        'git commit -m "Pre-registration freeze" && git tag carry-preregistered'
    )


COMMANDS: dict[str, Callable[[argparse.Namespace], None]] = {
    "fetch": fetch,
    "build": build,
    "validate": validate,
    "freeze": freeze_cmd,
}
ARGS: dict[str, Callable[[argparse.ArgumentParser], None]] = {
    "fetch": _period_args,
    "build": _period_args,
    "validate": _period_args,
    "freeze": _freeze_args,
}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="pce", description="BTC cash-and-carry backtest.")
    sub = parser.add_subparsers(dest="command")
    for name, run in COMMANDS.items():
        p = sub.add_parser(name, help=run.__doc__)
        if name in ARGS:
            ARGS[name](p)
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return
    COMMANDS[args.command](args)
