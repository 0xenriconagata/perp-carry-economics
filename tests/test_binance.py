import hashlib
from datetime import date

import httpx
import polars as pl
import pytest
from fixtures import make_zips as mz

from perp_carry_economics.data import binance

T0_MS = 1_598_918_400_000  # 2020-09-01 00:00:00 UTC
T0_US = T0_MS * 1000
MIN_US = 60_000_000


class _Resp:
    def __init__(self, body: bytes):
        self.body = body
        self.text = hashlib.sha256(body).hexdigest() + "  file.zip\n"

    def raise_for_status(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_bytes(self):
        yield self.body


class FakeClient:
    """Stands in for httpx.Client: serves body_for(url); checksum is the body's real sha256."""

    def __init__(self, body_for, bad_checksum: bool = False, missing: tuple[str, ...] = ()):
        self.body_for = body_for
        self.bad_checksum = bad_checksum
        self.missing = missing  # url fragments that answer 404
        self.urls: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url):
        self.urls.append(url)
        assert url.endswith(".CHECKSUM")
        if any(m in url for m in self.missing):
            request, response = httpx.Request("GET", url), httpx.Response(404)
            raise httpx.HTTPStatusError("404", request=request, response=response)
        r = _Resp(self.body_for(url[: -len(".CHECKSUM")]))
        if self.bad_checksum:
            r.text = "0" * 64 + "  file.zip\n"
        return r

    def stream(self, method, url):
        self.urls.append(url)
        return _Resp(self.body_for(url))


def test_urls_match_binance_layout():
    fut = "https://data.binance.vision/data/futures/um"
    assert binance.funding_url(2020, 9) == (
        f"{fut}/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2020-09.zip"
    )
    assert binance.mark_klines_url(2020, 9) == (
        f"{fut}/monthly/markPriceKlines/BTCUSDT/1m/BTCUSDT-1m-2020-09.zip"
    )
    assert binance.spot_klines_url(2020, 9) == (
        "https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2020-09.zip"
    )
    assert binance.metrics_url(date(2020, 9, 1)) == (
        f"{fut}/daily/metrics/BTCUSDT/BTCUSDT-metrics-2020-09-01.zip"
    )
    assert binance.book_depth_url(date(2023, 1, 1)) == (
        f"{fut}/daily/bookDepth/BTCUSDT/BTCUSDT-bookDepth-2023-01-01.zip"
    )


def test_download_verified_skips_when_checksum_matches(tmp_path):
    client = FakeClient(lambda url: b"zip bytes")
    dest = tmp_path / "x" / "a.zip"
    assert binance.download_verified("https://h/a.zip", dest, client) == dest
    assert dest.read_bytes() == b"zip bytes"
    assert (tmp_path / "x" / "a.zip.CHECKSUM").exists()
    assert not list(tmp_path.rglob("*.part"))
    n = len(client.urls)
    binance.download_verified("https://h/a.zip", dest, client)
    assert len(client.urls) == n  # second call asked the network for nothing


def test_download_verified_raises_on_mismatch(tmp_path):
    client = FakeClient(lambda url: b"zip bytes", bad_checksum=True)
    dest = tmp_path / "a.zip"
    with pytest.raises(binance.ChecksumError):
        binance.download_verified("https://h/a.zip", dest, client)
    assert not dest.exists()
    assert not list(tmp_path.glob("*.part"))


def test_load_funding_sorts_and_casts(tmp_path):
    d = tmp_path / "funding"
    # later month first on purpose; the older file has the 2-column header
    mz.funding_zip(d / "BTCUSDT-fundingRate-2020-10.zip", [(T0_MS + 99, "0.00020000")])
    mz.funding_zip(
        d / "BTCUSDT-fundingRate-2020-09.zip",
        [(T0_MS + 28_800_000, "-0.00005000"), (T0_MS, "0.00010000")],
        header=mz.FUNDING_HEADER_2,
    )
    df = binance.load_funding(tmp_path)
    assert df.columns == ["ts_us", "rate"]
    assert df.schema == {"ts_us": pl.Int64, "rate": pl.Float64}
    assert df["ts_us"].to_list() == [T0_US, (T0_MS + 99) * 1000, T0_US + 28_800 * 1_000_000]
    assert df["rate"].to_list() == [0.0001, 0.0002, -0.00005]


def test_load_mark_converts_ms_to_us(tmp_path):
    rows = [(T0_MS + i * 60_000, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i) for i in range(3)]
    mz.klines_zip(tmp_path / "mark" / "BTCUSDT-1m-2020-09.zip", rows[::-1])
    df = binance.load_mark(tmp_path)
    assert df.columns == ["ts_us", "open", "high", "low", "close"]
    assert df.schema == {
        "ts_us": pl.Int64,
        "open": pl.Float64,
        "high": pl.Float64,
        "low": pl.Float64,
        "close": pl.Float64,
    }
    assert df["ts_us"].to_list() == [T0_US + i * MIN_US for i in range(3)]
    assert df["close"].to_list() == [100.5, 101.5, 102.5]


def test_load_spot_reads_its_own_folder_and_accepts_us_timestamps(tmp_path):
    mz.klines_zip(tmp_path / "mark" / "BTCUSDT-1m-2020-09.zip", [(T0_MS, 1.0, 1.0, 1.0, 1.0)])
    mz.klines_zip(tmp_path / "spot" / "BTCUSDT-1m-2025-01.zip", [(T0_US, 2.0, 3.0, 1.0, 2.5)])
    df = binance.load_spot(tmp_path)
    assert df.to_dicts() == [{"ts_us": T0_US, "open": 2.0, "high": 3.0, "low": 1.0, "close": 2.5}]


def test_load_oi_columns(tmp_path):
    rows = [
        ("2020-09-01 00:05:00", "39106.4", "455693833.6", "1.17", "1.22"),
        ("2020-09-01 00:00:00", "39080.2", "456144339.2", "1.18", "1.23"),
    ]
    mz.metrics_zip(tmp_path / "metrics" / "BTCUSDT-metrics-2020-09-01.zip", rows, dup=True)
    df = binance.load_oi(tmp_path)
    assert df.columns == ["ts_us", "oi_btc", "oi_usd", "top_ls_position_ratio"]
    assert df.schema == {
        "ts_us": pl.Int64,
        "oi_btc": pl.Float64,
        "oi_usd": pl.Float64,
        "top_ls_position_ratio": pl.Float64,
    }
    # the file repeats every row: one row per timestamp, sorted; positions ratio, not accounts
    assert df["ts_us"].to_list() == [T0_US, T0_US + 5 * MIN_US]
    assert df["oi_btc"].to_list() == [39080.2, 39106.4]
    assert df["top_ls_position_ratio"].to_list() == [1.23, 1.22]


def test_load_depth_columns(tmp_path):
    rows = [
        ("2023-01-01 00:06:05", -5, "16770.3", "271987123.35"),
        ("2023-01-01 00:06:05", -4, "15524.6", "252304241.75"),
        ("2023-01-01 00:06:05", 4, "14000.1", "230000000.5"),
        ("2023-01-01 00:06:32", 5, "15000.2", "250000000.5"),
    ]
    mz.depth_zip(tmp_path / "depth" / "BTCUSDT-bookDepth-2023-01-01.zip", rows)
    df = binance.load_depth(tmp_path)
    assert df.columns == ["ts_us", "pct", "notional_usd"]
    assert df.schema == {"ts_us": pl.Int64, "pct": pl.Float64, "notional_usd": pl.Float64}
    t = 1_672_531_565_000_000  # 2023-01-01 00:06:05 UTC
    assert df["ts_us"].to_list() == [t, t, t, t + 27_000_000]
    assert df["pct"].to_list() == [-5.0, -4.0, 4.0, 5.0]
    assert df["notional_usd"][0] == 271987123.35


def test_loader_without_files_names_the_folder(tmp_path):
    with pytest.raises(FileNotFoundError, match="funding"):
        binance.load_funding(tmp_path)


def test_fetch_all_requests_each_file_once_and_depth_only_from_2023(tmp_path, monkeypatch, capsys):
    client = FakeClient(lambda url: url.encode())
    monkeypatch.setattr(httpx, "Client", lambda **kw: client)
    binance.fetch_all(date(2022, 12, 30), date(2023, 1, 2), tmp_path)
    zips = [u.rsplit("/", 1)[1] for u in client.urls if not u.endswith(".CHECKSUM")]
    assert sorted(zips) == sorted(
        [
            "BTCUSDT-fundingRate-2022-12.zip",
            "BTCUSDT-fundingRate-2023-01.zip",
            "BTCUSDT-1m-2022-12.zip",  # mark
            "BTCUSDT-1m-2023-01.zip",  # mark
            "BTCUSDT-1m-2022-12.zip",  # spot
            "BTCUSDT-1m-2023-01.zip",  # spot
            "BTCUSDT-metrics-2022-12-30.zip",
            "BTCUSDT-metrics-2022-12-31.zip",
            "BTCUSDT-metrics-2023-01-01.zip",
            "BTCUSDT-bookDepth-2023-01-01.zip",
        ]
    )
    assert (tmp_path / "mark" / "BTCUSDT-1m-2022-12.zip").exists()
    assert (tmp_path / "spot" / "BTCUSDT-1m-2022-12.zip").exists()
    assert capsys.readouterr().out.count("ok ") == 10
    n = len(client.urls)
    binance.fetch_all(date(2022, 12, 30), date(2023, 1, 2), tmp_path)
    assert len(client.urls) == n  # everything already verified: no requests


def test_fetch_all_skips_unpublished_depth_day_but_not_other_404s(tmp_path, monkeypatch, capsys):
    client = FakeClient(lambda url: url.encode(), missing=("bookDepth-2023-01-01",))
    monkeypatch.setattr(httpx, "Client", lambda **kw: client)
    binance.fetch_all(date(2023, 1, 1), date(2023, 1, 2), tmp_path)
    assert "missing depth/BTCUSDT-bookDepth-2023-01-01.zip" in capsys.readouterr().out
    assert not list((tmp_path / "depth").glob("*"))

    client = FakeClient(lambda url: url.encode(), missing=("metrics-2023-01-01",))
    monkeypatch.setattr(httpx, "Client", lambda **kw: client)
    with pytest.raises(httpx.HTTPStatusError):
        binance.fetch_all(date(2023, 1, 1), date(2023, 1, 2), tmp_path / "fresh")
