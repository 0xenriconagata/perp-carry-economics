"""House style for the figures: bundled Inter, light and dark palettes, deterministic SVG."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

from perp_carry_economics.config import ROOT  # noqa: E402

FONT_DIR = ROOT / "assets" / "fonts"
FONT_FILES = ("Inter-Regular.ttf", "Inter-SemiBold.ttf", "Inter-Bold.ttf")
FONT = "Inter"
CANVAS = (11.5, 6.2)
MODES = ("light", "dark")
SVG_SALT = "pce-figures"


@dataclass(frozen=True)
class Palette:
    background: str
    ink: str
    muted: str
    add: str
    cut: str
    answer: str
    answer_text: str


PALETTES = {
    "light": Palette(
        background="#FFFFFF",
        ink="#1F2933",
        muted="#5F6B76",
        add="#2B6CD9",
        cut="#7B8794",
        answer="#143F7A",
        answer_text="#FFFFFF",
    ),
    "dark": Palette(
        background="#0D1117",
        ink="#E6EDF3",
        muted="#9DA7B3",
        add="#74ADFF",
        cut="#66717F",
        answer="#2F6FD6",
        answer_text="#FFFFFF",
    ),
}


def register_fonts() -> None:
    """Register the bundled Inter files; a missing file raises instead of falling back."""
    for name in FONT_FILES:
        path = FONT_DIR / name
        if not path.exists():
            raise FileNotFoundError(f"{path} missing: the figures need the bundled Inter font")
        font_manager.fontManager.addfont(str(path))


_active: Palette | None = None


def current() -> Palette:
    """The palette of the enclosing `theme` block; raises outside one."""
    if _active is None:
        raise RuntimeError("no active theme: draw inside `with theme(mode):`")
    return _active


@contextmanager
def theme(mode: str) -> Iterator[Palette]:
    """House style for everything drawn inside the block."""
    global _active
    if mode not in PALETTES:
        raise ValueError(f"unknown mode {mode!r}; expected one of {MODES}")
    register_fonts()
    p = PALETTES[mode]
    rc = {
        "font.family": [FONT],
        "font.size": 10.5,
        "text.parse_math": False,  # "$" is money here
        "text.color": p.ink,
        "axes.facecolor": p.background,
        "figure.facecolor": p.background,
        "savefig.facecolor": p.background,
        "axes.labelcolor": p.muted,
        "axes.edgecolor": p.muted,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.spines.left": False,
        "axes.spines.bottom": False,
        "axes.grid": False,
        "xtick.color": p.muted,
        "ytick.color": p.muted,
        "xtick.major.size": 0,
        "ytick.major.size": 0,
        "svg.fonttype": "path",
        "svg.hashsalt": SVG_SALT,
    }
    previous, _active = _active, p
    try:
        with plt.rc_context(rc):  # type: ignore[arg-type]  # rcParams keys typed as literals
            yield p
    finally:
        _active = previous


def save_svg(fig: Figure, path: Path) -> None:
    """Write the figure without a date stamp, then close it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, format="svg", metadata={"Date": None})
    plt.close(fig)


def fmt_pts(x: float, signed: bool = False) -> str:
    """One decimal with a true minus sign; a `+` only when asked and the value is not negative."""
    s = f"{x:.1f}".replace("-", "−")
    return f"+{s}" if signed and not s.startswith("−") else s
