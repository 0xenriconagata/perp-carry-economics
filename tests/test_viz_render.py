import vizfix

from perp_carry_economics.viz.render import render_all


def test_renders_two_figures_in_two_modes_and_removes_stale(tmp_path):
    out = tmp_path / "figures"
    out.mkdir()
    (out / "zz_stale.svg").write_text("x")
    (out / "keep.txt").write_text("x")
    written = render_all(vizfix.results_dir(tmp_path), out)
    assert [p.name for p in written] == [
        "00_hero.svg",
        "00_hero_dark.svg",
        "01_yearly_spread.svg",
        "01_yearly_spread_dark.svg",
    ]
    assert not (out / "zz_stale.svg").exists()
    assert (out / "keep.txt").exists()


def test_dark_svg_uses_the_dark_surface(tmp_path):
    out = tmp_path / "figures"
    render_all(vizfix.results_dir(tmp_path), out)
    assert "#0d1117" in (out / "00_hero_dark.svg").read_text(encoding="utf-8").lower()
    assert "#0d1117" not in (out / "00_hero.svg").read_text(encoding="utf-8").lower()


def test_rerender_is_byte_identical(tmp_path):
    rd, out = vizfix.results_dir(tmp_path), tmp_path / "figures"
    a = {p.name: p.read_bytes() for p in render_all(rd, out)}
    b = {p.name: p.read_bytes() for p in render_all(rd, out)}
    assert a == b
