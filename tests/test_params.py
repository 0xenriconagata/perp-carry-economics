from perp_carry_economics import params as p
from perp_carry_economics.config import HOUR_US


def test_scalar_constants():
    assert p.NOTIONAL_USD == 10_000_000.0
    assert p.M0 == 0.25
    assert p.TRIGGER == 0.15
    assert p.TAU_US == 4 * HOUR_US
    assert p.RESERVE_FRAC == p.M0
    assert p.CAPITAL_MULT == 1.5
    assert p.TAUS_H == (1, 4, 24)
    assert p.M0S == (0.125, 0.25, 0.5)
    assert p.TRIGGER_RATIO == 0.6
    assert p.WORTH_IT_SPREAD == 0.02
    assert p.FILTER_PRINTS == 21
    assert p.HURDLE_AMORTISE_DAYS == 30
    assert p.CAPACITY_HALF == 0.5
    assert p.PRINTS_PER_DAY == 3
    assert p.PRINTS_PER_YEAR == 365 * 3
    assert p.MARK_GAP_FAIL_MIN == 24 * 60
    assert p.MARK_GAP_LIST_MIN == 5
    assert p.OI_MISSING_DAYS_MAX_SHARE == 0.02


def test_primary_and_grid():
    assert p.MarginSetting(0.25, 0.15, 4 * HOUR_US, 0.25) == p.PRIMARY
    grid = p.sensitivity_grid()
    assert len(grid) == 9
    assert p.PRIMARY in grid
    assert [(s.tau_us, s.m0) for s in grid] == [(t * HOUR_US, m) for t in p.TAUS_H for m in p.M0S]
    assert all(s.trigger == 0.6 * s.m0 and s.reserve_frac == s.m0 for s in grid)
