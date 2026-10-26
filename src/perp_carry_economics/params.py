"""Pre-registered parameters of the cash-and-carry backtest (spec sections 4-11).

Every value here is fixed before the run. Later modules import from this file and nowhere
else; a change to a value must show in tests/test_params.py.
"""

from dataclasses import dataclass

from perp_carry_economics.config import HOUR_US, US

# Spec 7: position size and the primary margin setting. Capital = spot leg + initial margin
# + reserve (CAPITAL_MULT x notional).
NOTIONAL_USD = 10_000_000.0
M0 = 0.25  # initial margin fraction of notional
TRIGGER = 0.15  # top up when the margin ratio falls below this
TAU_US = 4 * HOUR_US  # top-up delay
RESERVE_FRAC = M0
CAPITAL_MULT = 1.0 + M0 + RESERVE_FRAC

# Spec 7: sensitivity grid (tau in hours x m0); trigger = TRIGGER_RATIO x m0, reserve = m0.
TAUS_H: tuple[int, ...] = (1, 4, 24)
M0S: tuple[float, ...] = (0.125, 0.25, 0.5)
TRIGGER_RATIO = 0.6

# Spec 6: entry filter (trailing funding prints) and the spread that makes a switch worth it.
WORTH_IT_SPREAD = 0.02
FILTER_PRINTS = 21

# Spec 7/9: hurdle amortisation window and the capacity share.
HURDLE_AMORTISE_DAYS = 30
CAPACITY_HALF = 0.5

# Spec 9: the capacity grid (must contain NOTIONAL_USD, the reference size) and the kappa fit's
# sampling: an OI sample older than OI_FRESH_US at a print is no sample; consecutive prints
# count as one print apart when 8 h +- PRINT_GAP_TOL_US apart.
SIZES: tuple[float, ...] = (1e6, 3e6, 1e7, 3e7, 1e8, 3e8, 1e9)
OI_FRESH_US = 10 * 60 * US
PRINT_GAP_TOL_US = 60 * US

# Spec 4: funding is paid every 8 hours.
PRINTS_PER_DAY = 3
PRINTS_PER_YEAR = 365 * 3

# Spec 11: data check tolerances (the (A) haircut lives only in assets/binance_rules.json).
MARK_GAP_FAIL_MIN = 24 * 60  # a mark-price gap this long fails the check
MARK_GAP_LIST_MIN = 5  # gaps this long are listed
OI_MISSING_DAYS_MAX_SHARE = 0.02


@dataclass(frozen=True)
class MarginSetting:
    m0: float
    trigger: float
    tau_us: int
    reserve_frac: float


PRIMARY = MarginSetting(M0, TRIGGER, TAU_US, RESERVE_FRAC)


def sensitivity_grid() -> list[MarginSetting]:
    """The 9 settings of spec 7, ordered by tau then m0."""
    return [
        MarginSetting(m0, TRIGGER_RATIO * m0, tau_h * HOUR_US, m0) for tau_h in TAUS_H for m0 in M0S
    ]
