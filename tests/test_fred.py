from datetime import date

import httpx
import polars as pl
import pytest
from pytest import approx

from perp_carry_economics.data import fred
from perp_carry_economics.data.fred import DTB3_URL, fetch_dtb3, load_tbill
from perp_carry_economics.io import file_hash


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


def _mock_fred(monkeypatch, handler):
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
    )


def test_fetch_dtb3_keeps_existing_file_and_makes_no_request(tmp_path, monkeypatch):
    monkeypatch.setattr(fred, "ASSET_PATH", tmp_path / "no_asset.csv")
    bodies = [b"observation_date,DTB3\n2024-01-05,5.25\n", b"revised\n"]
    urls = []

    def handler(request):
        urls.append(str(request.url))
        return httpx.Response(200, content=bodies[len(urls) - 1])

    _mock_fred(monkeypatch, handler)
    path = fetch_dtb3(tmp_path)
    assert urls == [DTB3_URL]
    assert path.read_bytes() == bodies[0]
    sidecar = path.with_name("DTB3.csv.sha256")
    assert sidecar.read_text().strip() == file_hash(path)

    sidecar.unlink()  # a missing sidecar is rewritten from the file, still without a request
    assert fetch_dtb3(tmp_path) == path
    assert urls == [DTB3_URL]
    assert path.read_bytes() == bodies[0]
    assert sidecar.read_text().strip() == file_hash(path)


def test_fetch_dtb3_seeds_from_the_pinned_asset_without_a_request(tmp_path, monkeypatch):
    asset = tmp_path / "assets" / "DTB3.csv"
    asset.parent.mkdir()
    asset.write_bytes(b"observation_date,DTB3\r\n2024-01-05,5.25\r\n")  # bytes kept as-is
    monkeypatch.setattr(fred, "ASSET_PATH", asset)
    _mock_fred(monkeypatch, lambda request: pytest.fail(f"requested {request.url}"))
    path = fetch_dtb3(tmp_path / "raw")
    assert path.read_bytes() == asset.read_bytes()
    assert path.with_name("DTB3.csv.sha256").read_text().strip() == file_hash(asset)
    assert not list((tmp_path / "raw").rglob("*.part"))
