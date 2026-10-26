from datetime import date

import polars as pl
from pytest import approx

from perp_carry_economics.data.fred import load_tbill


def _write(tmp_path, header="observation_date,DTB3"):
    d = tmp_path / "fred"
    d.mkdir()
    # Fri print, weekend gap, Mon "." holiday, Tue print
    (d / "DTB3.csv").write_text(
        f"{header}\n2024-01-05,5.25\n2024-01-08,.\n2024-01-09,5.40\n", encoding="utf-8"
    )


def test_load_tbill_forward_fills_dots_and_weekends(tmp_path):
    _write(tmp_path, header="DATE,DTB3")  # the older header name must load too
    df = load_tbill(tmp_path, date(2024, 1, 5), date(2024, 1, 11))
    assert df["day"].to_list() == [date(2024, 1, d) for d in range(5, 11)]
    assert df["rate"].null_count() == 0
    assert df["rate"].to_list() == approx([0.0525, 0.0525, 0.0525, 0.0525, 0.054, 0.054])


def test_load_tbill_divides_by_100(tmp_path):
    _write(tmp_path)
    df = load_tbill(tmp_path, date(2024, 1, 9), date(2024, 1, 10))
    assert df.schema == {"day": pl.Date, "rate": pl.Float64}
    assert df["rate"].to_list() == approx([0.054])


def test_load_tbill_back_fills_before_first_print(tmp_path):
    _write(tmp_path)
    df = load_tbill(tmp_path, date(2024, 1, 3), date(2024, 1, 6))
    assert df["rate"].to_list() == approx([0.0525] * 3)
