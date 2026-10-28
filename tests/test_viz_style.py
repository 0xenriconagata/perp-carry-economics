import matplotlib.pyplot as plt
import pytest
import vizfix

from perp_carry_economics.viz import style
from perp_carry_economics.viz.data import load_results
from perp_carry_economics.viz.style import MODES, fmt_pts, register_fonts, save_svg, theme


def test_both_modes_render_the_same_text_in_their_own_ink():
    for mode in MODES:
        with theme(mode) as p:
            assert plt.rcParams["text.color"] == p.ink
            assert plt.rcParams["figure.facecolor"] == p.background


def test_svg_is_byte_identical_across_two_renders(tmp_path):
    paths = [tmp_path / "a.svg", tmp_path / "sub" / "b.svg"]
    with theme("light"):
        for path in paths:
            fig, ax = plt.subplots()
            ax.bar(["x", "y"], [1.0, -2.0])
            ax.set_title("Net −2.0 pts")
            save_svg(fig, path)
    assert paths[0].read_bytes() == paths[1].read_bytes()


def test_fmt_pts_uses_unicode_minus_and_one_decimal():
    assert fmt_pts(-4.234) == "−4.2" and fmt_pts(0.42, signed=True) == "+0.4"
    assert fmt_pts(-0.04) == "−0.0"  # the caller decides when to merge such bars


def test_missing_font_file_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(style, "FONT_DIR", tmp_path)
    with pytest.raises(FileNotFoundError):
        register_fonts()


def test_load_results_reads_the_four_files(tmp_path):
    r = load_results(vizfix.results_dir(tmp_path))
    assert set(r.decomposition["label"]) >= {"hold_through_B", "cut_filter"}
    assert r.yearly.columns[:3] == ["year", "days", "partial"]
    assert r.yearly["first_day"].dtype.is_temporal()
