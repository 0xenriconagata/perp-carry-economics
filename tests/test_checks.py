import json
from datetime import date

import polars as pl
import pytest
from bars_factory import MIN_US, P0, flat, frame, ts_of, with_funding

from perp_carry_economics import checks
from perp_carry_economics.carry.book import Mode, run_book
from perp_carry_economics.checks import (
    CheckResult,
    check_d1_funding_complete,
    check_d2_mark_gaps,
    check_d3_oi_daily,
    check_d4_tbill_covers,
    check_e1_funding_total,
    check_e2_rerun_identical,
    check_e3_cash_conservation,
)
from perp_carry_economics.config import DAY_US
from perp_carry_economics.data.rules import load_rules
from perp_carry_economics.params import MARK_GAP_FAIL_MIN, NOTIONAL_USD, PRIMARY
from perp_carry_economics.validate import input_hashes, require_pass, write_report

START, END = date(2024, 1, 1), date(2024, 1, 3)
RULES = load_rules()


def _ts_frame(ts):
    return pl.DataFrame({"ts_us": ts}, schema={"ts_us": pl.Int64})


def _minutes(skip=()):
    t0 = ts_of(2024, 1, 1)
    return [t0 + m * MIN_US for m in range(2 * 1440) if m not in skip]


# D1 funding prints


def test_d1_passes_on_three_prints_a_day():
    prints = _ts_frame([ts_of(2024, 1, d, h) for d in (1, 2) for h in (0, 8, 16)])
    r = check_d1_funding_complete(prints, START, END)
    assert r.status == "PASS"
    assert r.numbers == {"days": 2, "prints": 6, "bad_days": 0}


def test_d1_fails_and_lists_a_day_missing_a_print():
    prints = _ts_frame([ts_of(2024, 1, 1, h) for h in (0, 8, 16)] + [ts_of(2024, 1, 2, 0)])
    r = check_d1_funding_complete(prints, START, END)
    assert r.status == "FAIL"
    assert r.numbers["bad_days"] == 1
    assert "2024-01-02 (1)" in r.detail


# D2 mark gaps


def test_d2_passes_and_lists_gaps_over_the_list_threshold():
    # a 10-minute hole at 01:00 is listed, a 3-minute one at 02:00 is not
    r = check_d2_mark_gaps(_ts_frame(_minutes(skip={*range(60, 70), *range(120, 123)})), START, END)
    assert r.status == "PASS"
    assert r.numbers["gap_2024-01-01T01:00_min"] == 10
    assert r.numbers["listed_gaps"] == 1
    assert r.numbers["longest_gap_min"] == 10
    assert "2024-01-01T01:00 (10 min)" in r.detail
    assert "02:00" not in r.detail


def test_d2_fails_on_a_gap_longer_than_the_fail_threshold():
    hole = set(range(100, 100 + MARK_GAP_FAIL_MIN + 1))
    r = check_d2_mark_gaps(_ts_frame(_minutes(skip=hole)), START, END)
    assert r.status == "FAIL"
    assert r.numbers["longest_gap_min"] == MARK_GAP_FAIL_MIN + 1


def test_d2_counts_missing_minutes_at_the_window_edges():
    r = check_d2_mark_gaps(_ts_frame(_minutes()[:-7]), START, END)
    assert r.numbers["gap_2024-01-02T23:53_min"] == 7


# D3 open interest days


def _oi(days, missing):
    t0 = ts_of(2024, 1, 1, 0, 5)
    return _ts_frame([t0 + d * DAY_US for d in range(days) if d not in missing])


def test_d3_passes_at_the_missing_share_limit_and_lists_the_days():
    r = check_d3_oi_daily(_oi(100, {3, 40}), date(2024, 1, 1), date(2024, 4, 10))
    assert r.status == "PASS"
    assert r.numbers["missing_days"] == 2
    assert "2024-01-04" in r.detail and "2024-02-10" in r.detail


def test_d3_fails_above_the_missing_share_limit():
    r = check_d3_oi_daily(_oi(100, {3, 40, 41}), date(2024, 1, 1), date(2024, 4, 10))
    assert r.status == "FAIL"
    assert r.numbers["missing_share"] == 0.03


# D4 T-bill coverage


def _prints(*days):
    return pl.DataFrame({"day": list(days)}, schema={"day": pl.Date})


def test_d4_passes_when_prints_span_the_window():
    r = check_d4_tbill_covers(_prints(date(2023, 12, 29), date(2024, 1, 2)), START, END)
    assert r.status == "PASS"


@pytest.mark.parametrize(
    "days",
    [(date(2024, 1, 2), date(2024, 1, 5)), (date(2023, 12, 29), date(2024, 1, 1))],
    ids=["no-print-on-or-before-start", "no-print-on-the-last-day-or-later"],
)
def test_d4_fails_when_prints_stop_short_of_the_window(days):
    assert check_d4_tbill_covers(_prints(*days), START, END).status == "FAIL"


# E1-E3 on a liquidated, re-established run


@pytest.fixture(scope="module")
def bars():
    """Two flat days around a +30% one-bar jump: the PRIMARY short liquidates, stays unhedged
    for tau and re-establishes; funding prints every 8 hours throughout."""
    return with_funding(frame(flat(1) + [P0 * 1.3] + flat(1, P0 * 1.3)), [1e-4, -2e-4, 3e-4])


@pytest.fixture(scope="module")
def ledger(bars):
    lg = run_book(bars, PRIMARY, RULES, Mode.SEPARATE)
    assert lg["liquidated"].any() and lg["hedged"][-2]  # the run the checks must survive
    return lg


def _bump(ledger, col, at, by):
    return ledger.with_columns(
        pl.when(pl.int_range(pl.len()) == at).then(pl.col(col) + by).otherwise(pl.col(col))
    )


def test_e1_passes_on_the_engine_ledger(ledger, bars):
    r = check_e1_funding_total(ledger, bars)
    assert r.status == "PASS"
    assert r.numbers["difference_usd"] == 0.0
    assert 0 < r.numbers["prints"] < bars["funding_rate"].count()  # unhedged prints pay nothing


def test_e1_fails_on_a_funding_row_off_by_a_cent(ledger, bars):
    at = int((ledger["funding_usd"] != 0).arg_true()[0])
    r = check_e1_funding_total(_bump(ledger, "funding_usd", at, 0.01), bars)
    assert r.status == "FAIL"


def test_e2_passes_on_the_engine(bars):
    assert check_e2_rerun_identical(bars, PRIMARY, RULES).status == "PASS"


def test_e2_fails_when_a_rerun_differs(bars, ledger, monkeypatch):
    runs = iter([ledger, _bump(ledger, "fee_usd", 5, 1e-9)])
    monkeypatch.setattr(checks, "run_book", lambda *a, **k: next(runs))
    assert check_e2_rerun_identical(bars, PRIMARY, RULES).status == "FAIL"


def test_e3_passes_on_the_engine_ledger(ledger):
    r = check_e3_cash_conservation(ledger)
    assert r.status == "PASS"
    assert r.numbers["max_abs_residual_usd"] < 1e-6 * NOTIONAL_USD


def test_e3_fails_when_equity_moves_without_a_flow(ledger):
    r = check_e3_cash_conservation(_bump(ledger, "equity_usd", 100, 100.0))
    assert r.status == "FAIL"


@pytest.mark.parametrize("by", [float("nan"), None], ids=["nan", "null"])
def test_e3_fails_and_names_the_first_non_finite_bar(ledger, by):
    bumped = _bump(ledger, "equity_usd", 100, by)
    r = check_e3_cash_conservation(bumped)
    assert r.status == "FAIL"
    assert f"bar 100 (ts_us={ledger['ts_us'][100]})" in r.detail


# Report and gate


def _raw(tmp_path):
    raw = tmp_path / "raw"
    (raw / "fred").mkdir(parents=True)
    (raw / "fred" / "DTB3.csv").write_text("observation_date,DTB3\n2024-01-02,5.2\n")
    (raw / "funding").mkdir()
    (raw / "funding" / "x.zip").write_bytes(b"zip")
    return raw


def _report(tmp_path, raw, status="PASS"):
    results = [
        CheckResult("D1 funding prints complete", "PASS", "ok", {"days": 2}),
        CheckResult("E3 cash conservation", status, "residual", {"bars": 3}),
    ]
    out = tmp_path / "data_validation"
    extra = {"period": "[2024-01-01, 2024-01-03)", "input_hashes": input_hashes(raw)}
    write_report(results, out, extra)
    return out / "validation_report.json"


def test_input_hashes_key_every_raw_file_by_relative_path(tmp_path):
    assert sorted(input_hashes(_raw(tmp_path))) == ["fred/DTB3.csv", "funding/x.zip"]


def test_input_hashes_skip_sidecars_part_files_and_strays(tmp_path):
    raw = _raw(tmp_path)
    for name in ("funding/x.zip.CHECKSUM", "funding/y.zip.part", "fred/DTB3.csv.sha256"):
        (raw / name).write_bytes(b"not an input")
    (raw / "notes.zip").write_bytes(b"outside the Binance folders")
    assert sorted(input_hashes(raw)) == ["fred/DTB3.csv", "funding/x.zip"]


def test_write_report_json_is_sorted_lf_and_has_a_markdown_twin(tmp_path):
    path = _report(tmp_path, _raw(tmp_path))
    text = path.read_bytes().decode()
    assert "\r\n" not in text
    assert text == json.dumps(json.loads(text), indent=2, sort_keys=True) + "\n"
    report = json.loads(text)
    assert report["status"] == "PASS"
    assert report["period"] == "[2024-01-01, 2024-01-03)"
    assert report["checks"][1] == {
        "name": "E3 cash conservation",
        "status": "PASS",
        "detail": "residual",
        "numbers": {"bars": 3},
    }
    assert "PASS" in path.with_suffix(".md").read_text()


def test_require_pass_accepts_a_passing_report_on_unchanged_files(tmp_path):
    raw = _raw(tmp_path)
    require_pass(_report(tmp_path, raw), raw)


def test_require_pass_refuses_fail(tmp_path):
    raw = _raw(tmp_path)
    with pytest.raises(SystemExit, match="E3 cash conservation"):
        require_pass(_report(tmp_path, raw, status="FAIL"), raw)


def test_require_pass_refuses_changed_input_hash(tmp_path):
    raw = _raw(tmp_path)
    path = _report(tmp_path, raw)
    (raw / "funding" / "x.zip").write_bytes(b"zap")
    with pytest.raises(SystemExit, match="funding/x.zip"):
        require_pass(path, raw)


def test_require_pass_refuses_an_added_raw_file(tmp_path):
    raw = _raw(tmp_path)
    path = _report(tmp_path, raw)
    (raw / "funding" / "y.zip").write_bytes(b"new")
    with pytest.raises(SystemExit, match="funding/y.zip"):
        require_pass(path, raw)
