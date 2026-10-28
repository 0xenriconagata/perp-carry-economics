"""The hero figure: the headline funding yield walked down to the spread over T-bills."""

import textwrap

import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch

from perp_carry_economics.viz.data import Results
from perp_carry_economics.viz.style import CANVAS, FONT, current, fmt_pts

CAPTION = (
    "Percentage points a year on the base each bar names. "
    "Source: Binance public data, FRED DTB3; run under the freeze `carry-preregistered`."
)
BAR_WIDTH = 0.72
RADIUS_PX = 4
AX_RECT = (0.04, 0.17, 0.92, 0.60)  # left, bottom, width, height as figure fractions


def steps(row: dict[str, float]) -> list[tuple[str, float, str]]:
    """Seven (label, percentage points a year, kind) tuples; the first six sum to the answer."""
    per_year = row["capital_usd"] * row["years"]
    first = row["gross_yield"] * 100
    with_liq = row["liq_loss_usd"] != 0 or row["unhedged_pnl_usd"] != 0
    fees_basis = (
        (-row["fees_usd"] - row["liq_loss_usd"] + row["unhedged_pnl_usd"] + row["basis_usd"])
        / per_year
        * 100
    )
    return [
        ("Positive funding on $10 M notional", first, "total"),
        ("Same dollars on $15 M posted", row["gross_funding_usd"] / per_year * 100 - first, "step"),
        ("T-bill on idle cash", row["tbill_on_idle_usd"] / per_year * 100, "step"),
        ("Negative funding", row["negative_funding_usd"] / per_year * 100, "step"),
        ("Fees, basis, liquidations" if with_liq else "Fees and basis", fees_basis, "step"),
        ("Minus T-bills on all $15 M", -row["tbill_mean"] * 100, "step"),
        ("Over cash", row["spread"] * 100, "answer"),
    ]


def hero(r: Results) -> Figure:
    """Draw the waterfall in the active theme."""
    p = current()
    row = r.decomposition.filter(r.decomposition["label"] == "hold_through_B").row(0, named=True)
    bars = steps(row)

    # (bottom, top) of each bar and the running level after it; first and last stand on zero
    spans: list[tuple[float, float]] = []
    levels: list[float] = []
    level = 0.0
    for _, v, kind in bars:
        start, level = (level, level + v) if kind == "step" else (0.0, v)
        spans.append((min(start, level), max(start, level)))
        levels.append(level)
    low = min(0.0, *(s[0] for s in spans))
    high = max(s[1] for s in spans)
    room = (high - low) * 0.12
    low = low - room if low < 0 else 0.0

    fig = plt.figure(figsize=CANVAS)
    ax = fig.add_axes(AX_RECT)
    ax.set_xlim(-0.6, len(bars) - 0.4)
    ax.set_ylim(low, high + room)
    ax.get_yaxis().set_visible(False)
    ax.grid(False)
    for spine in ax.spines.values():
        spine.set_visible(False)

    # rounded ends: RADIUS_PX in both axes whatever the data scale
    w_px = fig.get_figwidth() * fig.dpi * AX_RECT[2]
    h_px = fig.get_figheight() * fig.dpi * AX_RECT[3]
    x_per_px = len(bars) / w_px
    y_per_px = (high + room - low) / h_px

    for i, ((_, v, kind), (lo, hi)) in enumerate(zip(bars, spans, strict=True)):
        color = p.answer if kind == "answer" else p.add if kind == "total" or v > 0 else p.cut
        if kind == "step":
            ax.bar(i, hi - lo, bottom=lo, width=BAR_WIDTH, color=color, linewidth=0)
        else:
            ax.add_patch(
                FancyBboxPatch(
                    (i - BAR_WIDTH / 2, lo),
                    BAR_WIDTH,
                    hi - lo,
                    boxstyle=f"round,pad=0,rounding_size={RADIUS_PX * x_per_px}",
                    mutation_aspect=y_per_px / x_per_px,
                    facecolor=color,
                    linewidth=0,
                )
            )
        if i:  # connector at the level the previous bar ended on
            ax.plot(
                [i - 1 + BAR_WIDTH / 2, i - BAR_WIDTH / 2],
                [levels[i - 1]] * 2,
                color=p.cut,
                linewidth=1,
                linestyle="-",
            )
        if kind == "answer":
            ax.text(
                i,
                hi - 0.03 * (high - low),
                fmt_pts(v) + " %",
                ha="center",
                va="top",
                color=p.answer_text,
                fontsize=28,
                fontweight="bold",
            )
            ax.text(
                i,
                hi - 0.03 * (high - low) - 0.17 * (high - low),
                "over cash",
                ha="center",
                va="top",
                color=p.answer_text,
                fontsize=13,
            )
        else:
            ax.annotate(
                fmt_pts(v, signed=kind == "step"),
                (i, hi),
                xytext=(0, 5),
                textcoords="offset points",
                ha="center",
                va="bottom",
                color=p.ink,
                fontsize=13,
                fontweight="semibold",
            )

    ax.set_xticks(range(len(bars)), [textwrap.fill(b[0], 18) for b in bars], color=p.muted)
    ax.tick_params(axis="x", length=0, pad=8, labelsize=10.5)

    period = r.summary["period"]
    fig.text(
        0.04,
        0.93,
        "Where the headline funding goes",
        fontsize=22,
        fontweight="bold",
        family=FONT,
        color=p.ink,
        va="top",
    )
    fig.text(
        0.04,
        0.855,
        f"Binance USD-M BTCUSDT, {period['start']} to {period['end']} (exclusive)",
        fontsize=12,
        color=p.muted,
        va="top",
    )
    fig.text(0.04, 0.04, CAPTION, fontsize=9.5, color=p.muted, va="bottom")
    return fig
