"""The run's results as the figures read them."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl


@dataclass(frozen=True)
class Results:
    summary: dict[str, Any]
    decomposition: pl.DataFrame
    episodes: list[dict[str, Any]]
    yearly: pl.DataFrame


def load_results(results_dir: Path) -> Results:
    def text(name: str) -> str:
        return (results_dir / name).read_text(encoding="utf-8")

    return Results(
        summary=json.loads(text("summary.json")),
        decomposition=pl.read_csv(results_dir / "decomposition.csv"),
        episodes=json.loads(text("episodes.json")),
        yearly=pl.read_csv(results_dir / "yearly.csv", try_parse_dates=True),
    )
