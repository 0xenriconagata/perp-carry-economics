"""Binance public data (data.binance.vision) for BTCUSDT: checksum-verified downloads and loaders.

Layout, checked live on 2026-10-26 with one HEAD per type at the start of the period (all 200):

    futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2020-09.zip
    futures/um/monthly/markPriceKlines/BTCUSDT/1m/BTCUSDT-1m-2020-09.zip
    spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2020-09.zip
    futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-2020-09-01.zip
    futures/um/daily/bookDepth/BTCUSDT/BTCUSDT-bookDepth-2023-01-01.zip

Every URL shape matches the plan. markPriceKlines 1m monthly files exist for 2020-09, so no daily
fallback is needed. bookDepth starts 2023-01-01 (2022-12-31 is a 404), hence DEPTH_START.

A HEAD scan of every file from 2020-09 to 2026-10-24 found all of them present except four
bookDepth days that Binance never published: 2023-02-08, 2023-02-09, 2024-04-18, 2026-10-08.
`fetch_all` reports those as `missing` and goes on; any other 404 raises. All 73 monthly files
of each type (2020-09..2026-09) and all 2245 metrics days are present.

CSV headers of the oldest file of each type:

    funding  calc_time,funding_interval_hours,last_funding_rate   (3 columns already in 2020-09;
             the plan expected 2. The parser selects by header name, so a 2-column
             calc_time,last_funding_rate file loads too.)
    mark     no header, 12 positions as spot: open_time,open,high,low,close,volume,close_time,
             quote_volume,count,taker_base,taker_quote,ignore (volume columns are 0 for mark price)
    spot     no header, the same 12 positions
    metrics  create_time,symbol,sum_open_interest,sum_open_interest_value,
             count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,
             count_long_short_ratio,sum_taker_long_short_vol_ratio
    depth    timestamp,percentage,depth,notional

Facts about the content that the loaders rely on:

- Timestamps in funding and kline files are milliseconds (spot switched to microseconds in 2025;
  `_to_us` handles both). metrics `create_time` and bookDepth `timestamp` are UTC datetime strings.
- The 2020-09-01 metrics file lists every 5-minute row twice (576 rows, 288 distinct), so
  `load_oi` drops exact duplicate rows.
- bookDepth is not per minute: a snapshot of 10 levels (percentage -5..-1 and 1..5) about every
  30 seconds at irregular seconds (2856 snapshots on 2023-01-01). `load_depth` returns them as
  published; downstream code aligns to minutes.

Column mapping (the capacity model relies on it):

    load_oi.oi_btc                 <- sum_open_interest         (BTC)
    load_oi.oi_usd                 <- sum_open_interest_value   (USD)
    load_oi.top_ls_position_ratio  <- sum_toptrader_long_short_ratio, the top traders' *positions*
                                      long/short ratio, not the count_toptrader_ accounts ratio.
    load_depth.pct                 <- percentage (signed level, negative = bids)
    load_depth.notional_usd        <- notional (USD to that level)

Files are stored under raw_dir/{funding,mark,spot,metrics,depth}/ because mark and spot files
share a name.
"""

import hashlib
import zipfile
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

import httpx
import polars as pl

from perp_carry_economics.config import SYMBOL

HOST = "https://data.binance.vision/data"
FUTURES_MONTHLY = f"{HOST}/futures/um/monthly"
FUTURES_DAILY = f"{HOST}/futures/um/daily"
SPOT_MONTHLY = f"{HOST}/spot/monthly"
DEPTH_START = date(2023, 1, 1)

KLINE_COLS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "count",
    "taker_base",
    "taker_quote",
    "ignore",
]


class ChecksumError(RuntimeError):
    pass


def funding_url(year: int, month: int) -> str:
    return f"{FUTURES_MONTHLY}/fundingRate/{SYMBOL}/{SYMBOL}-fundingRate-{year}-{month:02d}.zip"


def mark_klines_url(year: int, month: int) -> str:
    return f"{FUTURES_MONTHLY}/markPriceKlines/{SYMBOL}/1m/{SYMBOL}-1m-{year}-{month:02d}.zip"


def spot_klines_url(year: int, month: int) -> str:
    return f"{SPOT_MONTHLY}/klines/{SYMBOL}/1m/{SYMBOL}-1m-{year}-{month:02d}.zip"


def metrics_url(day: date) -> str:
    return f"{FUTURES_DAILY}/metrics/{SYMBOL}/{SYMBOL}-metrics-{day}.zip"


def book_depth_url(day: date) -> str:
    return f"{FUTURES_DAILY}/bookDepth/{SYMBOL}/{SYMBOL}-bookDepth-{day}.zip"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download_verified(url: str, dest: Path, client: httpx.Client) -> Path:
    """Download url to dest unless a file matching Binance's published SHA256 is already there.

    The .CHECKSUM file is saved next to the zip as the exact bytes received, so validation can
    re-verify offline (it is not an input: `validate.input_hashes` skips it). If both files
    exist and the zip matches the stored checksum, nothing is requested at all. A zip that is
    missing, corrupt, or has no stored checksum goes through the network path.
    """
    checksum_path = dest.with_name(dest.name + ".CHECKSUM")
    if dest.exists() and checksum_path.exists():
        stored = checksum_path.read_bytes().split()
        if stored and _sha256(dest) == stored[0].decode():
            return dest
    checksum_body = client.get(url + ".CHECKSUM").raise_for_status().content
    expected = checksum_body.split()[0].decode()
    if dest.exists() and _sha256(dest) == expected:
        checksum_path.write_bytes(checksum_body)
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    with client.stream("GET", url) as r, tmp.open("wb") as f:
        r.raise_for_status()
        for chunk in r.iter_bytes():
            f.write(chunk)
    if _sha256(tmp) != expected:
        tmp.unlink()
        raise ChecksumError(f"checksum mismatch for {dest.name}")
    tmp.replace(dest)
    checksum_path.write_bytes(checksum_body)
    return dest


def _read_zip_csv(zip_path: Path, columns: list[str]) -> pl.DataFrame:
    with zipfile.ZipFile(zip_path) as z:
        raw = z.read(z.namelist()[0])
    has_header = not raw[:1].isdigit()
    return pl.read_csv(raw, has_header=has_header, new_columns=columns, infer_schema=False)


def _read_zip_header(zip_path: Path) -> pl.DataFrame:
    """CSV whose first line names the columns; callers select by name, so extra or missing
    optional columns (e.g. funding_interval_hours) do not matter."""
    with zipfile.ZipFile(zip_path) as z:
        return pl.read_csv(z.read(z.namelist()[0]), infer_schema=False)


def _to_us(col: str) -> pl.Expr:
    # Binance moved spot data from ms to us; any value above 1e14 is already in us.
    ts = pl.col(col).cast(pl.Int64)
    return pl.when(ts > 10**14).then(ts).otherwise(ts * 1000)


def _datetime_to_us(col: str) -> pl.Expr:
    return pl.col(col).str.to_datetime("%Y-%m-%d %H:%M:%S", time_unit="us").dt.epoch("us")


def _months(start: date, end: date) -> list[tuple[int, int]]:
    """Every (year, month) whose first day is before end, from start's month on."""
    out, y, m = [], start.year, start.month
    while date(y, m, 1) < end:
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def fetch_all(start: date, end: date, raw_dir: Path) -> None:
    """Download every file for [start, end); files already verified on disk are not requested."""
    jobs: list[tuple[str, str]] = []
    for y, m in _months(start, end):
        jobs += [
            ("funding", funding_url(y, m)),
            ("mark", mark_klines_url(y, m)),
            ("spot", spot_klines_url(y, m)),
        ]
    for i in range((end - start).days):
        day = start + timedelta(days=i)
        jobs.append(("metrics", metrics_url(day)))
        if day >= DEPTH_START:
            jobs.append(("depth", book_depth_url(day)))
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        for folder, url in jobs:
            dest = raw_dir / folder / url.rsplit("/", 1)[1]
            try:
                download_verified(url, dest, client)
            except httpx.HTTPStatusError as e:
                if folder != "depth" or e.response.status_code != 404:
                    raise
                print(f"missing {folder}/{dest.name} (not published)")  # see module docstring
                continue
            print(f"ok {folder}/{dest.name}")


def _load(
    raw_dir: Path, folder: str, pattern: str, parse: Callable[[Path], pl.DataFrame]
) -> pl.DataFrame:
    files = sorted((raw_dir / folder).glob(pattern))
    if not files:
        raise FileNotFoundError(f"no {pattern} files in {raw_dir / folder}; run fetch_all")
    return pl.concat(parse(p) for p in files).sort("ts_us", maintain_order=True)


def _klines(p: Path) -> pl.DataFrame:
    return _read_zip_csv(p, KLINE_COLS).select(
        ts_us=_to_us("open_time"),
        open=pl.col("open").cast(pl.Float64),
        high=pl.col("high").cast(pl.Float64),
        low=pl.col("low").cast(pl.Float64),
        close=pl.col("close").cast(pl.Float64),
    )


def load_funding(raw_dir: Path) -> pl.DataFrame:
    """Funding prints: ts_us (calc_time) and rate, a fraction per print."""
    return _load(
        raw_dir,
        "funding",
        f"{SYMBOL}-fundingRate-*.zip",
        lambda p: _read_zip_header(p).select(
            ts_us=_to_us("calc_time"), rate=pl.col("last_funding_rate").cast(pl.Float64)
        ),
    )


def load_mark(raw_dir: Path) -> pl.DataFrame:
    """Mark-price 1-minute OHLC; ts_us is the bar open."""
    return _load(raw_dir, "mark", f"{SYMBOL}-1m-*.zip", _klines)


def load_spot(raw_dir: Path) -> pl.DataFrame:
    """Spot 1-minute OHLC; ts_us is the bar open."""
    return _load(raw_dir, "spot", f"{SYMBOL}-1m-*.zip", _klines)


def load_oi(raw_dir: Path) -> pl.DataFrame:
    """5-minute open interest and top-trader positions long/short ratio (see module docstring)."""
    return _load(
        raw_dir,
        "metrics",
        f"{SYMBOL}-metrics-*.zip",
        lambda p: (
            _read_zip_header(p)
            .select(
                ts_us=_datetime_to_us("create_time"),
                oi_btc=pl.col("sum_open_interest").cast(pl.Float64),
                oi_usd=pl.col("sum_open_interest_value").cast(pl.Float64),
                top_ls_position_ratio=pl.col("sum_toptrader_long_short_ratio").cast(pl.Float64),
            )
            .unique(maintain_order=True)
        ),  # the 2020-09-01 file lists every row twice
    )


def load_depth(raw_dir: Path) -> pl.DataFrame:
    """bookDepth snapshots as published: one row per (snapshot, signed pct level)."""
    return _load(
        raw_dir,
        "depth",
        f"{SYMBOL}-bookDepth-*.zip",
        lambda p: _read_zip_header(p).select(
            ts_us=_datetime_to_us("timestamp"),
            pct=pl.col("percentage").cast(pl.Float64),
            notional_usd=pl.col("notional").cast(pl.Float64),
        ),
    )
