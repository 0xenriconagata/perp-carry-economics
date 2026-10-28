# Reads the committed results, so it runs on the real repo: the page cannot drift from the files.
import json
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
text = (ROOT / "README.md").read_text(encoding="utf-8")


def _pct(x):
    return f"{x * 100:.1f}"


def test_headline_numbers_match_the_run():
    d = pl.read_csv(ROOT / "results/decomposition.csv")
    b = d.filter(pl.col("label") == "hold_through_B").row(0, named=True)
    f = d.filter(pl.col("label") == "cut_filter").row(0, named=True)
    s = json.loads((ROOT / "results/summary.json").read_text(encoding="utf-8"))
    y = pl.read_csv(ROOT / "results/yearly.csv")
    assert f"{_pct(b['spread'])} %" in text and f"{_pct(b['gross_yield'])} %" in text
    assert f"${s['capacity_usd'] / 1e6:.0f} M" in text
    assert f"{(b['spread'] - f['spread']) * 100:.1f} points" in text
    since_2022 = y.filter(pl.col("year") >= 2022)
    weighted = (since_2022["spread"] * since_2022["days"]).sum() / since_2022["days"].sum()
    assert f"{weighted * 100:.1f}" in text


def test_pictures_have_dark_sources_and_alt_text():
    assert text.count("<picture>") == 2 and text.count('media="(prefers-color-scheme: dark)"') == 2
    assert 'alt="' in text and "/tree/" not in text


def test_added_after_the_run_is_stated():
    assert "added after the run" in text.lower()
