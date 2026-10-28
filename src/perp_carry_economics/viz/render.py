"""The figure registry and the renderer: every figure, light and dark, into one directory."""

from collections.abc import Callable
from pathlib import Path

from matplotlib.figure import Figure

from perp_carry_economics.viz import hero, yearly
from perp_carry_economics.viz.data import Results, load_results
from perp_carry_economics.viz.style import MODES, save_svg, theme

FIGURES: list[tuple[str, Callable[[Results], Figure]]] = [
    ("00_hero", hero.hero),
    ("01_yearly_spread", yearly.spread_by_year),
]


def render_all(results_dir: Path, out_dir: Path) -> list[Path]:
    """Write each figure as `<stem>.svg` (light) and `<stem>_dark.svg`; drop every other .svg."""
    r = load_results(results_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for stem, fn in FIGURES:
        for mode in MODES:
            path = out_dir / (f"{stem}.svg" if mode == "light" else f"{stem}_dark.svg")
            with theme(mode):  # the figure reads the active palette while it is drawn
                save_svg(fn(r), path)
            written.append(path)
    for stale in set(out_dir.glob("*.svg")) - set(written):
        stale.unlink()
    return written
