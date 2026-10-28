import json

import pytest
import test_run
import vizfix

from perp_carry_economics import cli, config
from perp_carry_economics.bars import write_bars


def test_figures_refuses_without_results(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path / "results")
    with pytest.raises(SystemExit, match="run `pce run` first"):
        cli.main(["figures"])


def test_figures_refuses_on_calibration_results(monkeypatch, tmp_path):
    results = tmp_path / "results"
    results.mkdir()
    (results / "summary.json").write_text(json.dumps({"calibration": True}), encoding="utf-8")
    (results / "ledger_B_primary.parquet").write_bytes(b"")
    monkeypatch.setattr(config, "RESULTS_DIR", results)
    with pytest.raises(SystemExit, match="calibration"):
        cli.main(["figures"])


def test_figures_writes_yearly_and_four_svgs(monkeypatch, tmp_path, capsys):
    results = vizfix.results_dir(tmp_path)
    (results / "yearly.csv").unlink()
    interim = tmp_path / "interim"
    interim.mkdir()
    write_bars(test_run.world()[0], interim)
    monkeypatch.setattr(config, "RESULTS_DIR", results)
    monkeypatch.setattr(config, "INTERIM_DIR", interim)
    cli.main(["figures"])
    assert (results / "yearly.csv").exists()
    assert len(list((results / "figures").glob("*.svg"))) == 4
    assert "wrote 4 figure files to results/figures" in capsys.readouterr().out
