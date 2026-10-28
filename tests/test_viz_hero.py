import math

import matplotlib.pyplot as plt
import polars as pl
import pytest
import vizfix

from perp_carry_economics.viz.data import load_results
from perp_carry_economics.viz.hero import hero, steps
from perp_carry_economics.viz.style import current, theme


def _row(tmp_path):
    r = load_results(vizfix.results_dir(tmp_path))
    return r.decomposition.filter(pl.col("label") == "hold_through_B").row(0, named=True)


def test_steps_sum_to_the_spread(tmp_path):
    s = steps(_row(tmp_path))
    assert [k for _, _, k in s] == ["total", "step", "step", "step", "step", "step", "answer"]
    assert math.isclose(sum(v for _, v, k in s if k != "answer"), s[-1][1], abs_tol=1e-9)
    assert [lbl for lbl, _, _ in s] == [
        "Positive funding on $10 M notional",
        "Same dollars on $15 M posted",
        "T-bill on idle cash",
        "Negative funding",
        "Fees and basis",
        "Minus T-bills on all $15 M",
        "Over cash",
    ]


def test_hero_label_names_liquidations_when_present():
    row = {
        "years": 1.0, "gross_funding_usd": 1e6, "negative_funding_usd": -1e5,
        "fees_usd": 5e4, "liq_loss_usd": 1000.0, "missed_funding_usd": 0.0,
        "unhedged_pnl_usd": 0.0, "tbill_on_idle_usd": 2e5, "basis_usd": -1e4,
        "net_usd": 1e6, "capital_usd": 1.5e7, "gross_yield": 0.1, "net_yield": 0.06,
        "tbill_mean": 0.03, "spread": 0.03,
    }  # fmt: skip
    assert steps(row)[4][0] == "Fees, basis, liquidations"


def test_bars_are_coloured_by_sign_and_answer_text_is_inside(tmp_path):
    with theme("light"):
        fig = hero(load_results(vizfix.results_dir(tmp_path)))
    ax = fig.axes[0]
    assert len(ax.patches) == 7
    # add bars share one facecolor, cut bars another, the answer bar a third
    assert len({p.get_facecolor() for p in ax.patches}) == 3
    texts = [t.get_text() for t in ax.texts]
    assert "over cash" in texts and any(t.endswith(" %") for t in texts)
    assert max(t.get_fontsize() for t in ax.texts) >= 28
    plt.close(fig)


def test_no_axes_decorations(tmp_path):
    with theme("dark"):
        fig = hero(load_results(vizfix.results_dir(tmp_path)))
    ax = fig.axes[0]
    assert not ax.get_yaxis().get_visible()
    assert not any(g.get_visible() for g in ax.get_ygridlines())
    assert not any(s.get_visible() for s in ax.spines.values())
    plt.close(fig)


def test_current_palette_only_inside_a_theme():
    with pytest.raises(RuntimeError):
        current()
    with theme("dark") as p:
        assert current() is p
    with pytest.raises(RuntimeError):
        current()
