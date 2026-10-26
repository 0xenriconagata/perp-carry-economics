import json
import math
from datetime import date

import polars as pl
import pytest
from bars_factory import MIN_US, T0, flat, frame, with_funding

from perp_carry_economics import checks, cli, config, freeze, run
from perp_carry_economics.checks import CheckResult
from perp_carry_economics.data.rules import Rules
from perp_carry_economics.params import PRIMARY, MarginSetting

RULES = Rules(
    spot_taker=0.001,
    perp_taker=0.0005,
    liquidation_fee=0.0125,
    btc_collateral_haircut=0.05,
    tiers=((math.inf, 0.004, 0.0),),
    vip=(0.0008, 0.0003),
)
# 24 positive prints, 6 negative (the filter exits after the 3rd), 6 positive: one exit, no
# re-entry within the 12 days
RATES = [1e-4] * 24 + [-3e-4] * 6 + [1e-4] * 6
SETTINGS = [PRIMARY, MarginSetting(m0=0.5, trigger=0.3, tau_us=60 * MIN_US, reserve_frac=0.5)]
INFO = {"calibration": True, "period": {"start": "2024-01-01", "end": "2024-01-13"}}
FILES = [
    "ledger_B_primary.parquet",
    "ledger_A.parquet",
    "ledger_filter.parquet",
    "sensitivity.csv",
    "decomposition.csv",
    "episodes.json",
    "episode_liquidation.parquet",
    "episode_inversion.parquet",
    "capacity.csv",
    "negative_stretches.csv",
    "summary.json",
]


def world(days: float = 12) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    bars = with_funding(frame(flat(days)), RATES)
    oi_ts = [T0 + k * 5 * MIN_US for k in range(round(days * 288))]
    oi = pl.DataFrame(
        {
            "ts_us": oi_ts,
            "oi_btc": [30_000.0] * len(oi_ts),
            "oi_usd": [3e8] * len(oi_ts),
            "top_ls_position_ratio": [1.2 + 0.1 * math.sin(k / 7) for k in range(len(oi_ts))],
        }
    )
    levels = [(-p, 1e8 * p) for p in (1.0, 2.0, 3.0, 4.0, 5.0)]  # 5e8 USD of bids in all
    snaps = [T0 + h * 60 * MIN_US for h in range(round(days * 24))]
    depth = pl.DataFrame(
        [(t, p, n) for t in snaps for p, n in levels + [(-p, n) for p, n in levels]],
        schema={"ts_us": pl.Int64, "pct": pl.Float64, "notional_usd": pl.Float64},
        orient="row",
    )
    return bars, oi, depth


def run_into(out_dir):
    bars, oi, depth = world()
    return run.run_all(bars, RULES, oi, depth, out_dir, SETTINGS, 1e7, INFO)


@pytest.fixture(scope="module")
def first(tmp_path_factory):
    out = tmp_path_factory.mktemp("run")
    return out, run_into(out)


def test_run_on_fixture_bars_writes_every_file(first):
    out, summary = first
    assert sorted(p.name for p in out.iterdir()) == sorted(FILES)
    assert json.loads((out / "summary.json").read_text()) == summary
    for key in [
        "calibration",
        "period",
        "bars",
        "first_bar_us",
        "last_bar_us",
        "checks",
        "rows",
        "worth_it",
        "prediction_confirmed",
        "kappa",
        "kappa_stderr",
        "kappa_n",
        "capacity_usd",
        "book_exhausted_at_any_size",
        "negative_stretches",
    ]:
        assert key in summary, key
    assert summary["bars"] == 12 * 1440
    assert [c[:2] for c in summary["checks"]] == ["E1", "E2", "E3"]
    assert {c["status"] for c in summary["checks"].values()} == {"PASS"}

    dec = pl.read_csv(out / "decomposition.csv")
    assert dec["label"].to_list() == list(run.ROWS)
    assert dec["capital_usd"].to_list() == [1.5e7] * 4
    assert dec["turnover"].to_list() == [None, None, 1, None]
    rows = summary["rows"]
    assert summary["prediction_confirmed"] == (
        rows["cut_filter"]["spread"] <= rows["hold_through_B"]["spread"]
    )
    assert rows["hold_through_B_vip"]["fees_usd"] < rows["hold_through_B"]["fees_usd"]

    sens = pl.read_csv(out / "sensitivity.csv")
    assert sens.columns == list(run.SENSITIVITY_COLUMNS)
    assert sens["capital_usd"].to_list() == [1.5e7, 2e7]  # each setting's own capital
    assert sens["tau_h"].to_list() == [4.0, 1.0]

    assert summary["negative_stretches"] == {"count": 1, "mean_prints": 6.0, "max_prints": 6}
    assert pl.read_csv(out / "negative_stretches.csv")["prints"].to_list() == [6]
    curve = pl.read_csv(out / "capacity.csv")
    assert curve["book_exhausted"].to_list()[-1] is True  # 1e9 USD exceeds the 5e8 book
    assert summary["book_exhausted_at_any_size"] is True
    eps = json.loads((out / "episodes.json").read_text())
    assert [e["kind"] for e in eps] == ["liquidation", "inversion"]
    assert eps[0]["week_start"] == "2024-01-01"
    assert pl.read_parquet(out / "episode_inversion.parquet").height > 0


def test_summary_is_byte_identical_on_rerun(first, tmp_path):
    out, _ = first
    run_into(tmp_path)
    for name in FILES:
        assert (tmp_path / name).read_bytes() == (out / name).read_bytes(), name


def test_run_without_depth_skips_capacity(first, tmp_path):
    out, summary = first
    assert summary["capacity_depth_available"] is True
    bars, oi, depth = world()
    got = run.run_all(bars, RULES, oi, depth.clear(), tmp_path, SETTINGS, 1e7, INFO)
    assert got["capacity_depth_available"] is False
    assert got["capacity_usd"] is None and got["book_exhausted_at_any_size"] is False
    header = (out / "capacity.csv").read_text().splitlines()[0]
    assert (tmp_path / "capacity.csv").read_text() == header + "\n"


def test_run_refuses_when_e_check_fails(tmp_path, monkeypatch):
    fail = CheckResult("E3 cash conservation", "FAIL", "broken", {})
    monkeypatch.setattr(checks, "check_e3_cash_conservation", lambda ledger: fail)
    bars, oi, depth = world(days=1)
    with pytest.raises(SystemExit, match="E3"):
        run.run_all(bars, RULES, oi, depth, tmp_path / "out", SETTINGS, 1e7, INFO)
    assert not (tmp_path / "out").exists()


def test_json_writes_non_finite_as_null():
    text = run.json_text({"b": float("nan"), "a": [float("inf"), 1.5]})
    assert text == '{\n  "a": [\n    null,\n    1.5\n  ],\n  "b": null\n}\n'


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    calls = {}
    bars, _, _ = world(days=1)
    bars = bars.vstack(replace_ts(bars, 40))  # a day outside the frozen period below
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(freeze, "FREEZE_PATH", tmp_path / "results" / "carry_freeze.json")
    monkeypatch.setattr(cli, "input_hashes", lambda raw: {"f": "h"})
    monkeypatch.setattr(cli, "require_pass", lambda p: calls.setdefault("require_pass", p))
    monkeypatch.setattr(cli, "text_hash", lambda p: "fh")
    monkeypatch.setattr(cli, "read_bars", lambda d: bars)
    monkeypatch.setattr(cli, "load_rules", lambda: RULES)
    ts = pl.DataFrame({"ts_us": [T0, T0 + 40 * 1440 * MIN_US]})
    monkeypatch.setattr(cli, "load_oi", lambda raw: ts)
    monkeypatch.setattr(cli, "load_depth", lambda raw: ts)

    def read_freeze(path, hashes):
        calls["read_freeze"] = (path, hashes)
        return {"period": {"start": "2024-01-01", "end": "2024-01-02"}}

    def run_all(bars, rules, oi, depth, out_dir, settings, notional, info):
        calls["run_all"] = (bars, oi, depth, out_dir, settings, info)
        return {"worth_it": False, "prediction_confirmed": True}

    monkeypatch.setattr(freeze, "read_freeze", read_freeze)
    monkeypatch.setattr(run, "run_all", run_all)
    return tmp_path, calls


def replace_ts(bars, days):
    return bars.with_columns(pl.col("ts_us") + days * 1440 * MIN_US)


def test_cli_run_uses_the_frozen_period(cli_env):
    root, calls = cli_env
    cli.main(["run"])
    assert calls["require_pass"] == config.VALIDATION_DIR / "validation_report.json"
    assert calls["read_freeze"] == (root / "results" / "carry_freeze.json", {"f": "h"})
    bars, oi, depth, out_dir, settings, info = calls["run_all"]
    assert bars.height == 1440 and oi.height == 1 and depth.height == 1
    assert out_dir == root / "results"
    assert len(settings) == 9
    assert info == {
        "calibration": False,
        "period": {"start": "2024-01-01", "end": "2024-01-02"},
        "input_hashes": {"f": "h"},
        "freeze": {"path": "results/carry_freeze.json", "text_hash": "fh"},
    }


def test_cli_run_refuses_bars_that_stop_before_the_period_ends(cli_env, monkeypatch):
    _, calls = cli_env
    period = {"period": {"start": "2024-01-01", "end": "2024-01-03"}}
    monkeypatch.setattr(freeze, "read_freeze", lambda path, hashes: period)
    with pytest.raises(SystemExit, match="2024-01-01 to 2024-01-01"):
        cli.main(["run"])
    assert "run_all" not in calls


def test_cli_run_calibration_needs_no_freeze(cli_env, monkeypatch):
    root, calls = cli_env
    # the calibration window is PERIOD_START..CALIBRATION_END; point it at the fixture's day
    monkeypatch.setattr(cli, "PERIOD_START", date(2024, 1, 1))
    monkeypatch.setattr(cli, "CALIBRATION_END", date(2024, 1, 2))
    cli.main(["run", "--calibration"])
    assert "read_freeze" not in calls
    assert "require_pass" in calls
    bars, _, _, out_dir, _, info = calls["run_all"]
    assert bars.height == 1440
    assert out_dir == root / "results" / "calibration"
    assert info["calibration"] is True and info["freeze"] is None
    assert info["period"] == {"start": "2024-01-01", "end": "2024-01-02"}


def test_turnover_counts_filter_exits_and_entries_not_liquidations_or_the_close():
    # held, exit, flat, re-enter, held, close-out on the last bar
    assert run.turnover(pl.DataFrame({"spot_qty": [1.0, 1.0, 0.0, 0.0, 2.0, 2.0, 0.0]})) == 2
    # a liquidation unhedges the perp but keeps the spot: no flip
    assert run.turnover(pl.DataFrame({"spot_qty": [1.0, 1.0, 1.0, 0.9, 0.0]})) == 0
