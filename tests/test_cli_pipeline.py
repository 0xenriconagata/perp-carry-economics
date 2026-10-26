import json
import math
from datetime import date

import pytest
from fixtures.make_zips import funding_zip, klines_zip, metrics_zip

from perp_carry_economics import cli, config
from perp_carry_economics.bars import read_bars
from perp_carry_economics.config import PERIOD_START
from perp_carry_economics.validate import require_pass

T0_MS = 1_598_918_400_000  # 2020-09-01 00:00 UTC
DAYS = 3
PERIOD = ["--start", "2020-09-01", "--end", "2020-09-04"]


def _lay_out_raw(raw):
    """Three days of Binance-shaped files from 2020-09-01 and a FRED CSV spanning them."""
    minutes = range(DAYS * 1440)
    mark = [10_000 + 20 * math.sin(m / 300) for m in minutes]
    klines_zip(
        raw / "mark" / "BTCUSDT-1m-2020-09.zip",
        [(T0_MS + m * 60_000, p, p + 1, p - 1, p) for m, p in zip(minutes, mark, strict=True)],
    )
    klines_zip(
        raw / "spot" / "BTCUSDT-1m-2020-09.zip",
        [(T0_MS + m * 60_000, p, p, p, p - 3) for m, p in zip(minutes, mark, strict=True)],
    )
    funding_zip(
        raw / "funding" / "BTCUSDT-fundingRate-2020-09.zip",
        [(T0_MS + k * 8 * 3_600_000, "0.0001") for k in range(DAYS * 3)],
    )
    for d in range(1, DAYS + 1):
        row = (f"2020-09-0{d} 00:05:00", "30000.0", "300000000.0", "1.2", "1.1")
        metrics_zip(raw / "metrics" / f"BTCUSDT-metrics-2020-09-0{d}.zip", [row])
    (raw / "fred").mkdir(parents=True)
    (raw / "fred" / "DTB3.csv").write_text(
        "observation_date,DTB3\n2020-08-31,0.11\n2020-09-01,0.11\n2020-09-02,.\n"
        "2020-09-03,0.12\n2020-09-04,0.12\n2020-09-08,0.11\n"
    )


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(config, "INTERIM_DIR", tmp_path / "interim")
    monkeypatch.setattr(config, "VALIDATION_DIR", tmp_path / "data_validation")
    _lay_out_raw(tmp_path / "raw")
    return tmp_path


def test_cli_build_then_validate_on_fixture_data(data_dir, capsys):
    cli.main(["build", *PERIOD])
    assert read_bars(data_dir / "interim").height == DAYS * 1440
    cli.main(["validate", *PERIOD])
    path = data_dir / "data_validation" / "validation_report.json"
    report = json.loads(path.read_text())
    assert [c["name"][:2] for c in report["checks"]] == ["D1", "D2", "D3", "D4", "E1", "E2", "E3"]
    assert {c["status"] for c in report["checks"]} == {"PASS"}
    assert report["status"] == "PASS"
    assert report["period"] == "[2020-09-01, 2020-09-04)"
    assert "fred/DTB3.csv" in report["input_hashes"]
    assert path.with_suffix(".md").exists()
    require_pass(path, data_dir / "raw")
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("built 4320 bars")
    assert out[1].startswith("validation PASS")


def test_cli_validate_exits_nonzero_and_still_writes_the_report_on_fail(data_dir):
    cli.main(["build", *PERIOD])
    (data_dir / "raw" / "metrics" / "BTCUSDT-metrics-2020-09-02.zip").unlink()  # 1 of 3 OI days
    with pytest.raises(SystemExit):
        cli.main(["validate", *PERIOD])
    report = json.loads((data_dir / "data_validation" / "validation_report.json").read_text())
    assert report["status"] == "FAIL"
    assert report["checks"][2]["status"] == "FAIL"


def test_fetch_defaults_to_the_period_start_and_current_month_start(data_dir, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "fetch_all", lambda *a: calls.append(("binance", *a)))
    monkeypatch.setattr(cli, "fetch_dtb3", lambda *a: calls.append(("fred", *a)))
    cli.main(["fetch"])
    end = cli.default_end(date.today())
    assert calls == [("binance", PERIOD_START, end, data_dir / "raw"), ("fred", data_dir / "raw")]


@pytest.mark.parametrize(
    ("today", "end"),
    [
        (date(2026, 10, 26), date(2026, 10, 1)),
        (date(2026, 10, 1), date(2026, 10, 1)),
        (date(2026, 1, 31), date(2026, 1, 1)),
    ],
)
def test_default_end_is_first_day_of_current_month(today, end):
    # exclusive end: the last full month is in, no day of the current month is requested
    assert cli.default_end(today) == end == today.replace(day=1)
