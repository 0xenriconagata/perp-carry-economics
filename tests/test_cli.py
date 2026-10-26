import pytest

from perp_carry_economics import cli


def test_help_lists_no_commands_yet(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    assert "usage: pce" in capsys.readouterr().out
