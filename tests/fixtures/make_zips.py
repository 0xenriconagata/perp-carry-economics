"""Tiny Binance-shaped zips for tests. Each helper writes one single-CSV zip, returns its path."""

import zipfile
from pathlib import Path

FUNDING_HEADER_3 = "calc_time,funding_interval_hours,last_funding_rate"
FUNDING_HEADER_2 = "calc_time,last_funding_rate"
METRICS_HEADER = (
    "create_time,symbol,sum_open_interest,sum_open_interest_value,"
    "count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,"
    "count_long_short_ratio,sum_taker_long_short_vol_ratio"
)
DEPTH_HEADER = "timestamp,percentage,depth,notional"


def write_zip(path: Path, lines: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(path.stem + ".csv", "\n".join(lines) + "\n")
    return path


def funding_zip(path: Path, rows: list[tuple[int, str]], header: str = FUNDING_HEADER_3) -> Path:
    """rows are (calc_time_ms, rate); the 3-column header adds a constant 8-hour interval."""
    fmt = "{},8,{}" if header == FUNDING_HEADER_3 else "{},{}"
    return write_zip(path, [header, *(fmt.format(t, r) for t, r in rows)])


def klines_zip(path: Path, rows: list[tuple[int, float, float, float, float]]) -> Path:
    """Headerless 12-column kline CSV (2020-era layout); rows are (open_time, o, h, l, c)."""
    return write_zip(
        path,
        [f"{t},{o},{h},{lo},{c},1.5,{t + 59999},100.0,7,0.5,50.0,0" for t, o, h, lo, c in rows],
    )


def metrics_zip(path: Path, rows: list[tuple[str, str, str, str, str]], dup: bool = False) -> Path:
    """rows are (create_time, oi_btc, oi_usd, accounts_ratio, positions_ratio); dup writes each
    row twice, as the real 2020-09-01 file does."""
    lines = [f"{t},BTCUSDT,{btc},{usd},{acc},{pos},1.3,0.9" for t, btc, usd, acc, pos in rows]
    return write_zip(path, [METRICS_HEADER, *(x for line in lines for x in (line,) * (1 + dup))])


def depth_zip(path: Path, rows: list[tuple[str, int, str, str]]) -> Path:
    """rows are (timestamp, percentage, depth_btc, notional_usd)."""
    return write_zip(path, [DEPTH_HEADER, *(",".join(map(str, r)) for r in rows)])
