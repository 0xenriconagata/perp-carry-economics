from datetime import date

from perp_carry_economics import cli


def test_all_runs_fetch_build_validate_run_in_order(monkeypatch):
    calls = []
    for name in ("fetch", "build", "validate"):
        monkeypatch.setattr(
            cli,
            name,
            lambda a, n=name: calls.append((n, a.start, a.end, hasattr(a, "calibration"))),
        )
    monkeypatch.setattr(cli, "run_cmd", lambda a: calls.append(("run", a.calibration)))

    cli.main(["all", "--calibration", "--start", "2021-01-01", "--end", "2021-03-01"])

    assert [c[0] for c in calls] == ["fetch", "build", "validate", "run"]
    for _, start, end, has_calibration in calls[:3]:
        assert (start, end, has_calibration) == (date(2021, 1, 1), date(2021, 3, 1), False)
    assert calls[3] == ("run", True)
