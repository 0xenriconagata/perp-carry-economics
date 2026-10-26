"""Binance fee and BTCUSDT maintenance-margin snapshot, loaded from assets/binance_rules.json."""

import json
import math
from dataclasses import dataclass, replace
from pathlib import Path

from perp_carry_economics.config import ROOT


@dataclass(frozen=True)
class Rules:
    spot_taker: float
    perp_taker: float
    liquidation_fee: float
    btc_collateral_haircut: float
    # (upper_bound_usd, maintenance_rate, maintenance_amount_usd), bounds ascending, last inf
    tiers: tuple[tuple[float, float, float], ...]
    vip: tuple[float, float] | None = None  # (spot_taker, perp_taker) at the VIP tier

    def maintenance(self, notional_usd: float) -> float:
        """Maintenance margin in USD: rate x notional - amount of the tier containing notional."""
        for bound, rate, amount in self.tiers:
            if notional_usd <= bound:
                return rate * notional_usd - amount
        raise ValueError("tiers must end with an infinite bound")

    def round_trip_fee(self) -> float:
        return 2 * (self.spot_taker + self.perp_taker)

    def vip_variant(self) -> "Rules":
        if self.vip is None:
            raise ValueError("these rules have no vip fees")
        return replace(self, spot_taker=self.vip[0], perp_taker=self.vip[1])


def load_rules(path: Path = ROOT / "assets" / "binance_rules.json") -> Rules:
    d = json.loads(path.read_text(encoding="utf-8"))
    # JSON has no infinity: the last tier's bound is null there and math.inf here.
    tiers = tuple(
        (math.inf if bound is None else float(bound), float(rate), float(amount))
        for bound, rate, amount in d["tiers"]
    )
    return Rules(
        spot_taker=d["spot_taker"],
        perp_taker=d["perp_taker"],
        liquidation_fee=d["liquidation_fee"],
        btc_collateral_haircut=d["btc_collateral_haircut"],
        tiers=tiers,
        vip=(d["vip"]["spot_taker"], d["vip"]["perp_taker"]),
    )
