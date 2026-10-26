import json
import shutil
from datetime import date

import pytest

from perp_carry_economics import cli, config, freeze, params
from perp_carry_economics.config import ROOT
from perp_carry_economics.data import fred
from perp_carry_economics.io import file_hash
from perp_carry_economics.validate import input_hashes

ASSET = ROOT / "assets" / "binance_rules.json"
END = date(2026, 9, 1)


def _lay_out(d):
    for sub in ("funding", "fred"):
        (d / sub).mkdir(parents=True)
    (d / "funding" / "a.zip").write_bytes(b"x")
    (d / "fred" / "DTB3.csv").write_bytes(b"observation_date,DTB3")
    return d


@pytest.fixture
def raw(tmp_path):
    return _lay_out(tmp_path / "raw")


def test_payload_has_every_param_name(raw):
    got = freeze.freeze_payload(raw, END)["params"]
    names = [n for n in dir(params) if n.isupper() and not n.startswith("_")]
    assert names and all(n in got for n in names)
    assert got["PRIMARY"] == {
        "m0": 0.25,
        "trigger": 0.15,
        "tau_us": params.TAU_US,
        "reserve_frac": 0.25,
    }
    assert len(got["sensitivity_grid"]) == 9 and got["TAUS_H"] == [1, 4, 24]


def test_payload_has_every_key_and_the_frozen_texts(raw):
    p = freeze.freeze_payload(raw, END)
    assert set(freeze.FREEZE_KEYS) <= set(p)
    assert p["period"] == {
        "start": "2020-09-01",
        "end": "2026-09-01",
        "calibration_end": "2020-12-01",
    }
    assert p["rules"]["json"] == json.loads(ASSET.read_text(encoding="utf-8"))
    assert p["rules"]["text_hash"]
    assert p["spec"]["text_hash"]
    assert list(p["input_hashes"]) == ["fred/DTB3.csv", "funding/a.zip"]
    assert p["dtb3"] == {"path": "assets/DTB3.csv", "hash": p["input_hashes"]["fred/DTB3.csv"]}
    assert p["check_tolerances"] == {
        "MARK_GAP_FAIL_MIN": 1440,
        "MARK_GAP_LIST_MIN": 5,
        "OI_MISSING_DAYS_MAX_SHARE": 0.02,
    }
    assert p["episode_rules"].startswith("1. **The liquidation week:**")
    assert "2. **The inversion week:**" in p["episode_rules"]
    assert "## 9" not in p["episode_rules"]
    assert p["prediction"] == "cut filter does not beat hold-through on net spread after fees"
    assert p["written_at"] == date.today().isoformat()


def test_payload_is_sorted_json_lf(raw, tmp_path):
    path = tmp_path / "out" / "carry_freeze.json"
    freeze.write_freeze(path, freeze.freeze_payload(raw, END))
    data = path.read_bytes()
    assert b"\r" not in data and data.endswith(b"\n")
    text = data.decode()
    assert text == json.dumps(json.loads(text), indent=2, sort_keys=True) + "\n"


def test_freeze_refuses_overwrite(raw, tmp_path):
    path = tmp_path / "carry_freeze.json"
    freeze.write_freeze(path, freeze.freeze_payload(raw, END))
    first = path.read_bytes()
    with pytest.raises(FileExistsError, match="exists"):
        freeze.write_freeze(path, {"other": 1})
    assert path.read_bytes() == first


def test_read_freeze_refuses_changed_inputs(raw, tmp_path):
    path = tmp_path / "carry_freeze.json"
    payload = freeze.freeze_payload(raw, END)
    freeze.write_freeze(path, payload)
    assert freeze.read_freeze(path, payload["input_hashes"])["prediction"]
    (raw / "funding" / "a.zip").write_bytes(b"changed")
    with pytest.raises(SystemExit, match=r"missing: -; changed: funding/a\.zip"):
        freeze.read_freeze(path, input_hashes(raw))


def test_read_freeze_ignores_extra_inputs(raw, tmp_path):
    path = tmp_path / "carry_freeze.json"
    freeze.write_freeze(path, freeze.freeze_payload(raw, END))
    (raw / "funding" / "later-month.zip").write_bytes(b"y")  # a month after the frozen period
    (raw / "funding" / "a.zip.CHECKSUM").write_bytes(b"sidecar")
    (raw / "funding" / "b.zip.part").write_bytes(b"leftover")
    assert freeze.read_freeze(path, input_hashes(raw))["prediction"]


def test_read_freeze_refuses_a_missing_input(raw, tmp_path):
    path = tmp_path / "carry_freeze.json"
    freeze.write_freeze(path, freeze.freeze_payload(raw, END))
    (raw / "funding" / "a.zip").unlink()
    with pytest.raises(SystemExit, match=r"missing: funding/a\.zip; changed: -"):
        freeze.read_freeze(path, input_hashes(raw))


def test_payload_refuses_without_dtb3(raw):
    (raw / "fred" / "DTB3.csv").unlink()
    with pytest.raises(SystemExit, match="DTB3.csv missing"):
        freeze.freeze_payload(raw, END)


def test_read_freeze_refuses_changed_rules(raw, tmp_path, monkeypatch):
    monkeypatch.setattr(freeze, "RULES_PATH", tmp_path / "rules.json")
    shutil.copy(ASSET, freeze.RULES_PATH)
    path = tmp_path / "carry_freeze.json"
    payload = freeze.freeze_payload(raw, END)
    freeze.write_freeze(path, payload)
    freeze.RULES_PATH.write_text(ASSET.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="rules.json changed"):
        freeze.read_freeze(path, payload["input_hashes"])


def test_read_freeze_refuses_changed_params(raw, tmp_path, monkeypatch):
    path = tmp_path / "carry_freeze.json"
    payload = freeze.freeze_payload(raw, END)
    freeze.write_freeze(path, payload)
    monkeypatch.setattr(params, "M0", 0.3)
    with pytest.raises(SystemExit, match=r"params changed since the freeze: M0$"):
        freeze.read_freeze(path, payload["input_hashes"])


def test_read_freeze_refuses_missing_keys_and_unreadable(tmp_path):
    path = tmp_path / "carry_freeze.json"
    with pytest.raises(SystemExit, match="cannot be read"):
        freeze.read_freeze(path, {})
    path.write_text('{"input_hashes": {}}')
    with pytest.raises(SystemExit, match="lacks"):
        freeze.read_freeze(path, {})


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(config, "VALIDATION_DIR", tmp_path / "data_validation")
    monkeypatch.setattr(freeze, "FREEZE_PATH", tmp_path / "results" / "carry_freeze.json")
    monkeypatch.setattr(freeze, "RULES_PATH", tmp_path / "rules.json")
    monkeypatch.setattr(fred, "ASSET_PATH", tmp_path / "DTB3_asset.csv")
    _lay_out(tmp_path / "raw")
    return tmp_path


def _rules(verified):
    d = json.loads(ASSET.read_text(encoding="utf-8"))
    d["verified"] = verified
    freeze.RULES_PATH.write_text(json.dumps(d), encoding="utf-8")


def test_freeze_cli_refuses_unverified_rules(cli_env):
    shutil.copy(ASSET, freeze.RULES_PATH)  # the asset ships with verified: false
    _rules(False)
    with pytest.raises(SystemExit, match="verified"):
        cli.main(["freeze", "--period-end", "2026-09-01"])
    assert not freeze.FREEZE_PATH.exists()


@pytest.mark.parametrize("end", ["2026-09-15", "2020-12-01", "2020-11-01", "2999-01-01"])
def test_freeze_cli_refuses_a_period_end_that_is_not_a_published_month_start(cli_env, end):
    _rules(True)
    with pytest.raises(SystemExit, match=f"--period-end {end} must be the first day of a month"):
        cli.main(["freeze", "--period-end", end])
    assert not freeze.FREEZE_PATH.exists()


def _passing_report(cli_env, period):
    report = cli_env / "data_validation" / "validation_report.json"
    report.parent.mkdir()
    ok = {"status": "PASS", "checks": [{"name": "D1", "status": "PASS"}]}
    hashes = input_hashes(cli_env / "raw")
    report.write_text(json.dumps({**ok, "period": period, "input_hashes": hashes}))


def test_freeze_cli_refuses_a_validation_report_for_another_period(cli_env):
    _rules(True)
    _passing_report(cli_env, "[2020-09-01, 2026-08-01)")
    with pytest.raises(SystemExit, match=r"covers \[2020-09-01, 2026-08-01\), not the period"):
        cli.main(["freeze", "--period-end", "2026-09-01"])
    assert not freeze.FREEZE_PATH.exists() and not fred.ASSET_PATH.exists()


def test_freeze_cli_refuses_failed_validation(cli_env):
    _rules(True)
    with pytest.raises(SystemExit, match="validate"):  # no report on disk
        cli.main(["freeze", "--period-end", "2026-09-01"])
    report = cli_env / "data_validation" / "validation_report.json"
    report.parent.mkdir()
    report.write_text(json.dumps({"status": "FAIL", "checks": [{"name": "D1", "status": "FAIL"}]}))
    with pytest.raises(SystemExit, match="did not pass"):
        cli.main(["freeze", "--period-end", "2026-09-01"])
    assert not freeze.FREEZE_PATH.exists()


def test_freeze_cli_writes_pins_dtb3_and_prints_next_step(cli_env, capsys):
    _rules(True)
    _passing_report(cli_env, "[2020-09-01, 2026-09-01)")
    cli.main(["freeze", "--period-end", "2026-09-01"])
    frozen = json.loads(freeze.FREEZE_PATH.read_text(encoding="utf-8"))
    assert fred.ASSET_PATH.read_bytes() == (cli_env / "raw" / "fred" / "DTB3.csv").read_bytes()
    assert frozen["dtb3"]["hash"] == file_hash(fred.ASSET_PATH)
    out = capsys.readouterr().out
    assert "git add results/carry_freeze.json assets/DTB3.csv && git commit" in out
    assert "git tag carry-preregistered" in out
