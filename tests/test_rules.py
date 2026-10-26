import json
import math

import pytest

from perp_carry_economics.data.rules import load_rules


def test_load_rules_reads_asset_file():
    r = load_rules()
    assert (r.spot_taker, r.perp_taker) == (0.001, 0.0005)
    assert (r.liquidation_fee, r.btc_collateral_haircut) == (0.0125, 0.05)
    assert r.tiers[0][0] == 300_000.0
    assert math.isinf(r.tiers[-1][0])
    assert all(a[0] < b[0] for a, b in zip(r.tiers, r.tiers[1:], strict=False))


def test_load_rules_from_hand_built_file(tmp_path):
    path = tmp_path / "rules.json"
    path.write_text(
        json.dumps(
            {
                "spot_taker": 0.002,
                "perp_taker": 0.001,
                "liquidation_fee": 0.01,
                "btc_collateral_haircut": 0.1,
                "tiers": [[100.0, 0.01, 0.0], [None, 0.02, 1.0]],
                "vip": {"tier": "T", "spot_taker": 0.0004, "perp_taker": 0.0002},
                "source_urls": [],
                "snapshot_date": "2026-01-01",
                "verified": False,
            }
        )
    )
    r = load_rules(path)
    assert r.tiers == ((100.0, 0.01, 0.0), (math.inf, 0.02, 1.0))
    assert r.maintenance(50.0) == 0.5
    assert r.maintenance(1000.0) == 0.02 * 1000 - 1.0


@pytest.mark.parametrize(
    "tiers",
    [
        [[100.0, 0.01, 0.0], [100.0, 0.02, 1.0], [None, 0.03, 2.0]],  # not strictly ascending
        [[200.0, 0.01, 0.0], [100.0, 0.02, 1.0], [None, 0.03, 2.0]],  # descending
        [[100.0, 0.01, 0.0], [200.0, 0.02, 1.0]],  # last bound finite
        [[None, 0.01, 0.0], [None, 0.02, 1.0]],  # infinite before the last
        [],
    ],
)
def test_load_rules_refuses_bad_tiers(tmp_path, tiers):
    d = json.loads(load_rules.__defaults__[0].read_text(encoding="utf-8"))
    path = tmp_path / "rules.json"
    path.write_text(json.dumps({**d, "tiers": tiers}))
    with pytest.raises(ValueError, match="tier bounds must ascend"):
        load_rules(path)


def test_maintenance_uses_tier_rate_minus_amount():
    r = load_rules()
    assert r.maintenance(1e4) == 0.004 * 1e4
    # 1e7 sits in the 3e6-12e6 tier: rate 0.01, amount 12000
    assert r.maintenance(1e7) == pytest.approx(0.01 * 1e7 - 12_000)
    # 1e12 is past every finite bound: last tier
    rate, amount = r.tiers[-1][1:]
    assert r.maintenance(1e12) == pytest.approx(rate * 1e12 - amount)


def test_maintenance_is_continuous_at_tier_bounds():
    r = load_rules()
    for i, (bound, _, _) in enumerate(r.tiers[:-1]):
        _, rate, amount = r.tiers[i + 1]
        # the next tier's formula evaluated at the bound equals this tier's value there
        assert rate * bound - amount == pytest.approx(r.maintenance(bound), abs=1e-6)


def test_round_trip_fee():
    r = load_rules()
    assert r.round_trip_fee() == pytest.approx(2 * (0.001 + 0.0005))


def test_vip_variant_changes_only_fees():
    r = load_rules()
    v = r.vip_variant()
    assert (v.spot_taker, v.perp_taker) == (0.0004, 0.0002)
    assert v.tiers == r.tiers
    assert (v.liquidation_fee, v.btc_collateral_haircut) == (0.0125, 0.05)
    assert v.vip_variant() == v
