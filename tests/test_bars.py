from datetime import date

import polars as pl
import pytest

from perp_carry_economics.bars import bars_path, build_bars, read_bars, write_bars
from perp_carry_economics.config import DAY_US, US

MIN_US = 60 * US
D0 = date(2024, 1, 30)  # 3 days: Jan 30, Jan 31, Feb 1
T0 = (D0 - date(1970, 1, 1)).days * DAY_US
END = date(2024, 2, 2)
NO_FUNDING = pl.DataFrame(schema={"ts_us": pl.Int64, "rate": pl.Float64})


def _klines(ts, scale=1.0):
    return pl.DataFrame(
        {
            "ts_us": ts,
            "open": [100.0 * scale + i for i in range(len(ts))],
            "high": [102.0 * scale + i for i in range(len(ts))],
            "low": [99.0 * scale + i for i in range(len(ts))],
            "close": [101.0 * scale + i for i in range(len(ts))],
        }
    )


def _minutes(skip=()):
    return [T0 + m * MIN_US for m in range(3 * 1440) if T0 + m * MIN_US not in skip]


def _tbill():
    days = [date(2024, 1, 30), date(2024, 1, 31), date(2024, 2, 1)]
    return pl.DataFrame({"day": days, "rate": [0.05, 0.051, 0.052]})


def _build(mark_ts=None, spot_ts=None, funding=NO_FUNDING):
    mark_ts = _minutes() if mark_ts is None else mark_ts
    spot = _klines(_minutes() if spot_ts is None else spot_ts, scale=2.0)
    return build_bars(_klines(mark_ts), spot, funding, _tbill(), D0, END)


def _prints(*items):
    return pl.DataFrame({"ts_us": [t for t, _ in items], "rate": [r for _, r in items]})


def test_columns_and_mark_high():
    bars = _build()
    assert bars.columns == [
        "ts_us",
        "mark",
        "mark_high",
        "spot",
        "funding_rate",
        "tbill_rate",
        "month_start",
        "day_start",
    ]
    assert bars.schema["ts_us"] == pl.Int64
    assert bars["mark"][0] == 101.0
    assert bars["mark_high"][0] == 102.0
    assert bars["mark_high"][5] == 107.0
    assert bars["tbill_rate"].null_count() == 0
    assert bars["tbill_rate"][0] == 0.05
    assert bars["tbill_rate"][-1] == 0.052


def test_funding_lands_on_print_bar():
    p = T0 + 8 * 60 * MIN_US
    # calc_time is a few ms after the hour; the print still lands on the 08:00 bar
    bars = _build(funding=_prints((p + 3000, 0.0001), (p + 8 * 60 * MIN_US, -0.0002)))
    rows = bars.filter(pl.col("funding_rate").is_not_null())
    assert rows["ts_us"].to_list() == [p, p + 8 * 60 * MIN_US]
    assert rows["funding_rate"].to_list() == [0.0001, -0.0002]


def test_funding_moves_to_last_bar_before_print_when_minute_missing():
    p = T0 + 8 * 60 * MIN_US
    bars = _build(mark_ts=_minutes(skip={p, p - MIN_US}), funding=_prints((p, 0.0003)))
    rows = bars.filter(pl.col("funding_rate").is_not_null())
    assert rows["ts_us"].to_list() == [p - 2 * MIN_US]
    assert rows["funding_rate"].to_list() == [0.0003]


def test_month_start_flag_once_per_month():
    bars = _build()
    assert bars.filter("month_start")["ts_us"].to_list() == [T0, T0 + 2 * DAY_US]
    assert bars.filter("day_start")["ts_us"].to_list() == [T0, T0 + DAY_US, T0 + 2 * DAY_US]


def test_month_start_when_midnight_bar_missing():
    bars = _build(mark_ts=_minutes(skip={T0 + 2 * DAY_US}))
    assert bars.filter("month_start")["ts_us"].to_list() == [T0, T0 + 2 * DAY_US + MIN_US]


def test_minutes_without_mark_are_absent_and_outside_window_dropped():
    gone = {T0 + 10 * MIN_US, T0 + 11 * MIN_US}
    outside = [T0 - MIN_US, T0 + 3 * DAY_US]
    bars = _build(mark_ts=sorted(outside + _minutes(skip=gone)))
    assert bars.height == 3 * 1440 - 2
    assert not gone & set(bars["ts_us"].to_list())
    assert bars["ts_us"].is_sorted()
    assert bars["ts_us"][0] == T0


def test_spot_is_asof_backward():
    spot_ts = _minutes(skip={T0 + 5 * MIN_US, T0 + 6 * MIN_US})
    bars = _build(spot_ts=spot_ts)
    spot = dict(zip(bars["ts_us"].to_list(), bars["spot"].to_list(), strict=True))
    assert spot[T0 + 6 * MIN_US] == spot[T0 + 4 * MIN_US]
    assert spot[T0 + 7 * MIN_US] != spot[T0 + 4 * MIN_US]


def test_write_read_roundtrip(tmp_path):
    bars = _build()
    write_bars(bars, tmp_path)
    assert bars_path(tmp_path).name == "bars.parquet"
    assert read_bars(tmp_path).equals(bars)


def test_missing_tbill_day_raises():
    with pytest.raises(ValueError, match="T-bill"):
        build_bars(_klines(_minutes()), _klines(_minutes()), NO_FUNDING, _tbill().head(2), D0, END)


def test_colliding_prints_raise_naming_both():
    p1, p2 = T0 + 8 * 60 * MIN_US, T0 + 16 * 60 * MIN_US
    gap = {t for t in _minutes() if T0 + 7 * 60 * MIN_US <= t < T0 + 17 * 60 * MIN_US}
    with pytest.raises(ValueError, match=rf"{p1}.*{p2}"):
        _build(mark_ts=_minutes(skip=gap), funding=_prints((p1, 0.0001), (p2, 0.0002)))


@pytest.mark.parametrize("leg", ["mark", "spot"])
def test_duplicated_minute_raises_naming_it(leg):
    twice = _minutes() + [T0 + 5 * MIN_US]  # Binance's 2020-09-01 metrics had every row twice
    with pytest.raises(ValueError, match=rf"{leg} has duplicated ts_us: \[{T0 + 5 * MIN_US}\]"):
        _build(**{f"{leg}_ts": twice})


def test_duplicate_outside_the_window_is_ignored():
    before = T0 - MIN_US
    assert _build(mark_ts=[before, before, *_minutes()]).height == 3 * 1440


def test_print_before_first_bar_raises():
    p = T0 + 60 * MIN_US
    with pytest.raises(ValueError, match=str(p)):
        _build(mark_ts=_minutes()[120:], funding=_prints((p, 0.0001)))


def test_print_landing_25h_late_raises():
    p = T0 + 26 * 60 * MIN_US
    gap = {t for t in _minutes() if T0 + 60 * MIN_US < t <= p}
    with pytest.raises(ValueError, match=str(p)):
        _build(mark_ts=_minutes(skip=gap), funding=_prints((p, 0.0001)))
