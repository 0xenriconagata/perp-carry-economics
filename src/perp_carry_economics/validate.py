"""The validation report (Project 1's format) and the gate every published result sits behind."""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from perp_carry_economics import config
from perp_carry_economics.checks import CheckResult
from perp_carry_economics.io import file_hash, write_text_atomic

BINANCE_FOLDERS = ("funding", "mark", "mark_daily", "spot", "metrics", "depth")


def input_hashes(raw_dir: Path) -> dict[str, str]:
    """SHA-256 of every data file under raw_dir, keyed by its path relative to raw_dir.

    Data files are the `*.zip` files in the Binance folders and `fred/DTB3.csv`; sidecars
    (`.CHECKSUM`, `.sha256`), `.part` leftovers and anything else are not inputs."""
    files = [p for f in BINANCE_FOLDERS for p in (raw_dir / f).glob("*.zip")]
    files += [p for p in [raw_dir / "fred" / "DTB3.csv"] if p.exists()]
    return {p.relative_to(raw_dir).as_posix(): file_hash(p) for p in sorted(files) if p.is_file()}


def write_report(results: list[CheckResult], out_dir: Path, extra: dict[str, Any]) -> str:
    """Write validation_report.json (sorted keys, LF) and .md to out_dir; return the status."""
    status = "PASS" if results and all(r.status == "PASS" for r in results) else "FAIL"
    out_dir.mkdir(parents=True, exist_ok=True)
    body = {"status": status, **extra, "checks": [asdict(r) for r in results]}
    text = json.dumps(body, indent=2, sort_keys=True, default=str) + "\n"
    write_text_atomic(out_dir / "validation_report.json", text)
    lines = [f"# Validation report: **{status}**", ""]
    lines += [
        f"- {k}: {len(v)} entries (see the .json)" if isinstance(v, dict) else f"- {k}: `{v}`"
        for k, v in extra.items()
    ]
    lines += ["", "| Check | Result | Detail |", "|---|---|---|"]
    lines += [f"| {r.name} | {r.status} | {r.detail} |" for r in results]
    write_text_atomic(out_dir / "validation_report.md", "\n".join(lines) + "\n")
    return status


def require_pass(report_path: Path, raw_dir: Path | None = None) -> dict[str, Any]:
    """Refuse (SystemExit) unless every check passed on exactly the raw files now on disk;
    return the report."""
    raw_dir = config.RAW_DIR if raw_dir is None else raw_dir
    if not report_path.exists():
        raise SystemExit(f"{report_path} missing; run `pce validate` first")
    report: dict[str, Any] = json.loads(report_path.read_text(encoding="utf-8"))
    failed = [c["name"] for c in report["checks"] if c["status"] != "PASS"]
    if failed or report["status"] != "PASS" or not report["checks"]:
        raise SystemExit(f"validation did not pass ({', '.join(failed)}); see {report_path}")
    recorded: dict[str, str] = report.get("input_hashes", {})
    now = input_hashes(raw_dir)
    changed = sorted(k for k in recorded.keys() | now.keys() if recorded.get(k) != now.get(k))
    if not recorded or changed:
        listed = ", ".join(changed[:5]) + (" ..." if len(changed) > 5 else "")
        raise SystemExit(
            f"raw files changed since validation ({listed or 'no hashes recorded'}); "
            "run `pce validate` again"
        )
    return report
