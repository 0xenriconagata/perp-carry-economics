"""The pre-registration freeze (spec 10): every parameter, rule, snapshot and input hash,
written once before the full-period run; the run refuses to proceed if any of it changed."""

import json
from dataclasses import asdict, is_dataclass
from datetime import date
from pathlib import Path
from typing import Any

from perp_carry_economics import params
from perp_carry_economics.config import CALIBRATION_END, PERIOD_START, RESULTS_DIR, ROOT
from perp_carry_economics.data import fred
from perp_carry_economics.io import text_hash, write_text_atomic
from perp_carry_economics.validate import input_hashes

FREEZE_PATH = RESULTS_DIR / "carry_freeze.json"
RULES_PATH = ROOT / "assets" / "binance_rules.json"
SPEC_PATH = ROOT / "docs" / "specs" / "2026-10-26-perp-carry-economics-design.md"

FREEZE_KEYS = (
    "spec",
    "params",
    "rules",
    "dtb3",
    "period",
    "period_end_exclusive",
    "capital",
    "seed",
    "seed_funding",
    "tbill",
    "liquidation_accounting",
    "capacity_model",
    "capacity_rule",
    "closest_call_rule",
    "kappa_fit",
    "slippage",
    "filter",
    "filter_breakeven_note",
    "check_tolerances",
    "episode_rules",
    "prediction",
    "input_hashes",
    "written_at",
)

_TEXTS = {
    "period_end_exclusive": "period.end is exclusive: the run covers [start, end)",
    "tbill": "FRED DTB3 percent / 100 used as-is (discount basis), accrued on idle cash at "
    "rate/365 on each day_start bar",
    "seed_funding": "the seed bar pays no funding; the first print after it does",
    "filter_breakeven_note": "under the primary setting a hedged book earns funding on N plus "
    "T-bill on 0.25N while a flat book earns T-bill on 1.5N, so the spec's threshold (trailing "
    "funding vs T-bill) is below economic break-even (~1.25x T-bill); the spec's rule is kept "
    "as pre-registered",
    "capital": "(1 + m0 + reserve_frac) x notional per setting; 1.5 x notional at the primary "
    "setting",
    "seed": "both legs opened at N / mark on the first bar; funding starts at the first print "
    "after it",
    "liquidation_accounting": "all remaining margin equity at the liquidation price is lost "
    "(covers the clearance fee); liquidation is checked intrabar on the mark high; conservative",
    "capacity_model": "delta_funding ~ kappa * delta_log(sum_toptrader_long_short_ratio); a "
    "short of share s of OI shifts the ratio by -log(1+2s) (top-trader shorts assumed half of "
    "OI); haircut = kappa*log(1+2s)*1095/1.5; slippage from every bar with a perp trade plus "
    "the open and close priced on bookDepth snapshots from 2023-01-01, uniform density within a "
    "band, perp book as spot proxy; all terms annualised fractions of capital deployed "
    "(1.5 x notional)",
    "capacity_rule": "capacity = smallest size at which the modelled spread falls to "
    "CAPACITY_HALF x the modelled spread at size == notional (the 1e7 grid point), linear "
    "interpolation between grid sizes; the smallest size whose fill exceeds the published book "
    "caps it; None if the reference spread <= 0",
    "closest_call_rule": "max over candidate bars (top-up pending or landing) of (trigger - "
    "margin_frac) / (trigger - maintenance_frac at that bar); same score over all hedged bars "
    "when no top-up was ever pending",
    "kappa_fit": "OLS with intercept of per-print funding change on delta "
    "log(sum_toptrader_long_short_ratio) sampled as-of <= 10 min stale, differences only "
    "between consecutive prints 8h +- 1 min apart, non-finite rows dropped; classical OLS "
    "stderr; haircut uses max(kappa, 0); prints whose OI sample has a blank top-trader "
    "ratio are dropped (Binance published it on only ~20 days between 2021-12-30 and "
    "2022-12-14)",
    "slippage": "bid side of bookDepth, uniform density within a band, last snapshot at or "
    "before each trade bar; one-off open/close fills annualised over the hold, recurring "
    "|delta perp_qty| x mark turnover over the depth period; both legs priced on the perp book "
    "(spot proxy); a fill exceeding the published book marks that size book_exhausted; the "
    "N-sized ledger is scaled linearly to each size (notional tiers ignored)",
    "filter": "exit when trailing 21-print mean funding x 1095 < tbill_rate; re-enter when it "
    "exceeds tbill_rate + round_trip_fee x 365/30; exit/re-entry on the bar after the signal "
    "at the close; flat book earns T-bill; month_start while flat sweeps to capital",
    "prediction": "cut filter does not beat hold-through on net spread after fees",
}


def _plain(v: Any) -> Any:
    if is_dataclass(v) and not isinstance(v, type):
        return asdict(v)
    return list(v) if isinstance(v, tuple) else v


def _params() -> dict[str, Any]:
    out = {n: _plain(getattr(params, n)) for n in dir(params) if n.isupper()}
    out["sensitivity_grid"] = [asdict(s) for s in params.sensitivity_grid()]
    return out


def _episode_rules() -> str:
    """Spec section 8's text, between its heading and the next one."""
    text = SPEC_PATH.read_bytes().decode("utf-8").replace("\r\n", "\n")
    return text.split("## 8. Episode rules (frozen)\n", 1)[1].split("\n## 9.", 1)[0].strip()


def freeze_payload(raw_dir: Path, period_end: date) -> dict[str, Any]:
    hashes = input_hashes(raw_dir)
    if "fred/DTB3.csv" not in hashes:
        raise SystemExit(f"{raw_dir / 'fred' / 'DTB3.csv'} missing; run `pce fetch`")
    return {
        "spec": {"path": SPEC_PATH.relative_to(ROOT).as_posix(), "text_hash": text_hash(SPEC_PATH)},
        "params": _params(),
        "rules": {
            "json": json.loads(RULES_PATH.read_text(encoding="utf-8")),
            "text_hash": text_hash(RULES_PATH),
        },
        # `pce freeze` copies the hashed raw file to this path; fetch_dtb3 seeds from it
        "dtb3": {"path": fred.ASSET_REL, "hash": hashes["fred/DTB3.csv"]},
        "period": {
            "start": PERIOD_START.isoformat(),
            "end": period_end.isoformat(),
            "calibration_end": CALIBRATION_END.isoformat(),
        },
        **_TEXTS,
        "check_tolerances": {
            n: getattr(params, n)
            for n in ("MARK_GAP_FAIL_MIN", "MARK_GAP_LIST_MIN", "OI_MISSING_DAYS_MAX_SHARE")
        },
        "episode_rules": _episode_rules(),
        "input_hashes": hashes,
        "written_at": date.today().isoformat(),
    }


def write_freeze(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(f"{path} exists: the freeze is written once, never edited")
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False)  # a NaN fails here
    write_text_atomic(path, text + "\n")


def read_freeze(path: Path, hashes: dict[str, str]) -> dict[str, Any]:
    """The frozen record; stops (never writes) if the file is unreadable, a frozen input is
    missing from or differs in `hashes`, or the rule snapshot or params changed. Files in
    `hashes` that the freeze does not list (a later month, say) are ignored."""
    keep = "it is never rewritten: restore it from git"
    try:
        frozen = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SystemExit(f"{path} cannot be read ({e}); {keep}") from None
    if not isinstance(frozen, dict):
        raise SystemExit(f"{path} is not a JSON object; {keep}")
    if missing := [k for k in FREEZE_KEYS if k not in frozen]:
        raise SystemExit(f"{path} lacks {missing}; {keep}")
    got = frozen["input_hashes"]
    known: dict[str, str] = got if isinstance(got, dict) else {}
    missing = sorted(k for k in known if k not in hashes)
    changed = sorted(k for k in known if k in hashes and hashes[k] != known[k])
    if not known or missing or changed:

        def few(ks: list[str]) -> str:
            return ", ".join(ks[:5]) + (" ..." if len(ks) > 5 else "")

        raise SystemExit(
            f"{path} was frozen on other inputs (missing: {few(missing) or '-'}; changed: "
            f"{few(changed) or '-'}{'' if known else '; no input_hashes recorded'}); {keep}"
        )
    rules = frozen["rules"]
    if not isinstance(rules, dict) or rules.get("text_hash") != text_hash(RULES_PATH):
        raise SystemExit(f"{RULES_PATH} changed since the freeze; restore the frozen snapshot")
    now = json.loads(json.dumps(_params()))  # tuples as lists, as frozen
    then = frozen["params"] if isinstance(frozen["params"], dict) else {}
    if changed := sorted(k for k in now.keys() | then.keys() if now.get(k) != then.get(k)):
        raise SystemExit(f"params changed since the freeze: {', '.join(changed)}")
    return frozen
