# Checks the README's headline numbers, fact lines and tables against the committed results/.
import json
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
text = (ROOT / "README.md").read_text(encoding="utf-8")
plain = text.replace("**", "")  # the primary sensitivity row is bold


def _pct(x):
    return f"{x * 100:.1f}"


def _musd(x):
    return f"${x / 1e6:,.1f}".removesuffix(".0") + " M"


def test_headline_numbers_match_the_run():
    d = pl.read_csv(ROOT / "results/decomposition.csv")
    b = d.filter(pl.col("label") == "hold_through_B").row(0, named=True)
    f = d.filter(pl.col("label") == "cut_filter").row(0, named=True)
    s = json.loads((ROOT / "results/summary.json").read_text(encoding="utf-8"))
    y = pl.read_csv(ROOT / "results/yearly.csv")
    assert f"paid {_pct(b['spread'])} % a year over T-bills" in text
    assert f"against a {_pct(b['gross_yield'])} % headline of positive funding on notional" in text
    assert f"Capacity about ${s['capacity_usd'] / 1e6:.0f} M before" in text
    assert f"cost {(b['spread'] - f['spread']) * 100:.1f} points a year" in text
    since_2022 = y.filter(pl.col("year") >= 2022)
    weighted = (since_2022["spread"] * since_2022["days"]).sum() / since_2022["days"].sum()
    assert f"about {weighted * 100:.1f} point over cash, and below cash since 2025." in text
    assert (y.filter(pl.col("year") >= 2025)["spread"] < 0).all()


def test_losing_months_match_the_monthly_sums():
    m = pl.read_csv(ROOT / "results/monthly.csv")
    neg, total = (m["sweep_usd"] < 0).sum(), m.height
    assert f"Only {neg} of {total} months lost money" in text


def test_capacity_table_matches_the_curve():
    for r in pl.read_csv(ROOT / "results/capacity.csv").iter_rows(named=True):
        spread = "n/a" if r["modelled_spread"] is None else f"{_pct(r['modelled_spread'])} %"
        row = f"| {_musd(r['size_usd'])} | {spread} | {'yes' if r['book_exhausted'] else 'no'} |"
        assert row in text, row


def test_sensitivity_table_matches_the_grid():
    for r in pl.read_csv(ROOT / "results/sensitivity.csv").iter_rows(named=True):
        m0 = f"{r['m0'] * 100:g} % ({1 / r['m0']:.0f}×)"
        row = (
            f"| {r['tau_h']:.0f} h | {m0} | {_musd(r['capital_usd'])} | {r['liquidations']} "
            f"| {r['topups']} | {_pct(r['spread'])} % |"
        )
        assert row in plain, row


def test_pictures_have_dark_sources_and_alt_text():
    assert text.count("<picture>") == 2 and text.count('media="(prefers-color-scheme: dark)"') == 2
    assert 'alt="' in text and "/tree/" not in text


def test_added_after_the_run_is_stated_next_to_the_yearly_figure():
    figure = text.index('src="results/figures/01_yearly_spread.svg"')
    note = text.index("The calendar-year split was added after the run")
    assert figure < note < text.index("\n- Zero liquidations")
