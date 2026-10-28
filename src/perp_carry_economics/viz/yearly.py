"""The second figure: the spread over T-bills in each calendar year, with the episodes ticked."""

from calendar import month_abbr
from typing import Any

import matplotlib.pyplot as plt
from matplotlib.figure import Figure

from perp_carry_economics.viz.data import Results
from perp_carry_economics.viz.style import AX_RECT, BAR_WIDTH, CANVAS, FONT, current, fmt_pts

CAPTION = "Calendar-year split of the frozen run, added after the run; not a pre-registered test."
EPISODE_TEXT = {"liquidation": "closest call", "inversion": "inversion week"}
# room under the axis for two-line year labels and the episode marks: lift the axes bottom
RECT = (AX_RECT[0], 0.29, AX_RECT[2], AX_RECT[1] + AX_RECT[3] - 0.29)


def _year_label(row: dict[str, Any]) -> str:
    if not row["partial"]:
        return str(row["year"])
    first, last = month_abbr[row["first_day"].month], month_abbr[row["last_day"].month]
    return f"{row['year']}\n({first if first == last else f'{first}–{last}'})"


def spread_by_year(r: Results) -> Figure:
    """Draw one bar per calendar year in the active theme."""
    p = current()
    rows = r.yearly.to_dicts()
    pts = [row["spread"] * 100 for row in rows]
    low, high = min(0.0, *pts), max(0.0, *pts)
    room = ((high - low) or 1.0) * 0.14
    year_x = {row["year"]: i for i, row in enumerate(rows)}

    fig = plt.figure(figsize=CANVAS)
    ax = fig.add_axes(RECT)
    ax.set_xlim(-0.6, len(rows) - 0.4)
    ax.set_ylim(low - (room if low < 0 else 0.0), high + room)
    ax.get_yaxis().set_visible(False)
    ax.grid(False)
    for spine in ax.spines.values():
        spine.set_visible(False)

    ax.axhline(0, color=p.cut, linewidth=1)
    for i, v in enumerate(pts):
        ax.bar(i, v, width=BAR_WIDTH, color=p.add if v >= 0 else p.cut, linewidth=0)
        ax.annotate(
            fmt_pts(v),
            (i, v),
            xytext=(0, 5 if v >= 0 else -5),
            textcoords="offset points",
            ha="center",
            va="bottom" if v >= 0 else "top",
            color=p.ink,
            fontsize=13,
            fontweight="semibold",
        )
    ax.set_xticks(range(len(rows)), [_year_label(row) for row in rows], color=p.muted)
    ax.tick_params(axis="x", length=0, pad=8, labelsize=10.5)

    # episodes: a tick and a note under the year's bar, clear of the two-line year label
    under = ax.get_xaxis_transform()  # x in data, y in axes fractions
    per_year: dict[int, int] = {}
    for e in r.episodes:
        year = int(e["week_start"][:4])
        if year not in year_x:
            continue
        n = per_year[year] = per_year.get(year, 0) + 1
        x = year_x[year]
        ax.plot([x, x], [-0.185, -0.225], color=p.cut, linewidth=1, transform=under, clip_on=False)
        ax.text(
            x,
            -0.24 - 0.06 * (n - 1),
            EPISODE_TEXT[e["kind"]],
            transform=under,
            ha="center",
            va="top",
            color=p.muted,
            fontsize=10,
        )

    fig.text(
        0.04,
        0.93,
        "What it paid each year, over cash",
        fontsize=22,
        fontweight="bold",
        family=FONT,
        color=p.ink,
        va="top",
    )
    fig.text(
        0.04,
        0.855,
        "Spread of net yield on posted capital over the 3-month T-bill, percentage points a year",
        fontsize=12,
        color=p.muted,
        va="top",
    )
    fig.text(0.04, 0.04, CAPTION, fontsize=9.5, color=p.muted, va="bottom")
    return fig
