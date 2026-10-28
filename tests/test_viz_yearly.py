import datetime as dt
from dataclasses import replace

import matplotlib.pyplot as plt
import polars as pl
import vizfix
from matplotlib.colors import to_hex

from perp_carry_economics.viz.data import load_results
from perp_carry_economics.viz.style import PALETTES, theme
from perp_carry_economics.viz.yearly import spread_by_year


def _draw(r):
    with theme("light"):
        return spread_by_year(r)


def test_one_bar_per_year_coloured_by_sign(tmp_path):
    r = load_results(vizfix.results_dir(tmp_path))
    later = r.yearly.with_columns(year=pl.col("year") + 1, spread=pl.lit(0.02))
    two = pl.concat([r.yearly, later])
    fig = _draw(replace(r, yearly=two))
    patches = fig.axes[0].patches
    assert len(patches) == two.height
    assert [to_hex(p.get_facecolor()).upper() for p in patches] == [
        PALETTES["light"].cut,
        PALETTES["light"].add,
    ]
    plt.close(fig)


def test_negative_year_is_a_cut_with_minus_sign(tmp_path):
    r = load_results(vizfix.results_dir(tmp_path))
    fig = _draw(replace(r, yearly=r.yearly.with_columns(spread=pl.lit(-0.01))))
    texts = [t.get_text() for t in fig.axes[0].texts]
    assert "−1.0" in texts and "-1.0" not in texts
    plt.close(fig)


def test_partial_years_name_their_months(tmp_path):
    r = load_results(vizfix.results_dir(tmp_path))
    spans = r.yearly.with_columns(
        year=pl.col("year") - 4,
        first_day=pl.lit(dt.date(2020, 9, 1)),
        last_day=pl.lit(dt.date(2020, 12, 31)),
    )
    fig = _draw(replace(r, yearly=spans))
    assert [t.get_text() for t in fig.axes[0].get_xticklabels()] == ["2020\n(Sep–Dec)"]
    plt.close(fig)
    fig = _draw(r)  # the fixture is 12 days of January 2024
    assert [t.get_text() for t in fig.axes[0].get_xticklabels()] == ["2024\n(Jan)"]
    plt.close(fig)


def test_episodes_are_marked_under_their_year(tmp_path):
    r = load_results(vizfix.results_dir(tmp_path))
    fig = _draw(r)
    texts = [t.get_text() for t in fig.axes[0].texts]
    assert "closest call" in texts and "inversion week" in texts
    plt.close(fig)
