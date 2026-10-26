"""Capacity model (spec 9): how much money the trade holds before the position is the funding rate.

A declared model, not a measurement. All capacity terms are annualised fractions of capital
deployed, the unit of `Decomposition.spread`. At position size S the capital deployed is
S x capital_usd / notional, the base ledger's capital per unit of notional (1.5 at PRIMARY), so
every term below is a cost at size S divided by the capital at size S.

- Funding impact: the position is a permanent short share s of open interest (USD). With
  top-trader shorts taken as half of OI and the position counted among them, it moves the
  top-trader positions long/short ratio by dlog ratio = -log(1 + 2s); funding moves by kappa
  times that on every print, kappa fitted by `fit_kappa`.
- Slippage: the engine's own trades from the start of the book-depth files, each walked
  through the bid side of the as-of-backward snapshot; the spot leg is proxied by the perp book.
"""

import math

import numpy as np
import polars as pl

from perp_carry_economics.carry.yields import Decomposition
from perp_carry_economics.config import DAY_US
from perp_carry_economics.params import CAPACITY_HALF, NOTIONAL_USD, PRINTS_PER_YEAR

SIZES: tuple[float, ...] = (1e6, 3e6, 1e7, 3e7, 1e8, 3e8, 1e9)


def fit_kappa(funding: pl.DataFrame, oi: pl.DataFrame) -> tuple[float, float, int]:
    """OLS with an intercept of the per-print funding change on dlog(top_ls_position_ratio).

    The ratio is sampled as-of backward at each funding print; rows where either difference is
    null are dropped; the intercept absorbs funding's drift. Returns (kappa, classical OLS
    standard error of kappa, rows used). Positive kappa: more longs, higher funding."""
    df = (
        funding.sort("ts_us")
        .join_asof(
            oi.sort("ts_us").select("ts_us", "top_ls_position_ratio"),
            on="ts_us",
            strategy="backward",
        )
        .select(dr=pl.col("rate").diff(), dx=pl.col("top_ls_position_ratio").log().diff())
        .drop_nulls()
    )
    y = df["dr"].to_numpy()
    x = np.column_stack([np.ones(df.height), df["dx"].to_numpy()])
    beta = np.linalg.lstsq(x, y, rcond=None)[0]
    resid = y - x @ beta
    cov = resid @ resid / (df.height - 2) * np.linalg.inv(x.T @ x)
    return float(beta[1]), math.sqrt(cov[1, 1]), df.height


def oi_share(size_usd: float, oi: pl.DataFrame) -> float:
    """Median of size / oi_usd over the period: the position's permanent share of OI."""
    return float(np.median(size_usd / oi["oi_usd"].to_numpy()))


def funding_haircut_yield(
    size_usd: float,
    kappa: float,
    oi: pl.DataFrame,
    capital_usd: float,
    notional: float = NOTIONAL_USD,
) -> float:
    """Funding lost to the position's own OI share, as an annualised fraction of capital deployed.

    kappa x log(1 + 2s) is a per-print rate on the position's notional, so the yield on capital
    is that x PRINTS_PER_YEAR x notional / capital_usd. Positive when kappa > 0."""
    s = oi_share(size_usd, oi)
    return kappa * math.log1p(2 * s) * PRINTS_PER_YEAR * notional / capital_usd


def depth_cost(size_usd: float, depth_at: pl.DataFrame) -> float:
    """Slippage fraction of one perp sell of `size_usd` against one bookDepth snapshot.

    Only the bids (negative pct) count. `notional_usd` is cumulative to each level and `pct`
    is in percent. Bands are consumed outward from mid with uniform density, so a slice taking
    fraction x of band k (from |pct_k-1| to |pct_k|, pct_0 = 0) costs |pct_k-1| + x (|pct_k| -
    |pct_k-1|) / 2; the fill costs the notional-weighted mean. Raises ValueError if the size
    needs more than the published book."""
    bids = depth_at.filter(pl.col("pct") < 0).sort("pct", descending=True)
    lo = cum = cost = 0.0
    left = size_usd
    for pct, notional in bids.select("pct", "notional_usd").iter_rows():
        hi, band = -pct / 100, notional - cum
        take = min(left, max(band, 0.0))
        if take > 0:
            cost += take * (lo + take / band * (hi - lo) / 2)
            left -= take
            if left <= 0:
                return float(cost / size_usd)
        lo, cum = hi, notional
    raise ValueError(f"fill of {size_usd:,.0f} USD exceeds the published depth ({cum:,.0f} USD)")


def _trades(depth: pl.DataFrame, ledger: pl.DataFrame) -> pl.DataFrame:
    """The bars the model trades on: the first reset (sweep_usd != 0) in the depth period
    (open), the later resets, and the ledger's last bar (close); with the reset turnover
    |dperp_qty| x mark at the base notional and the as-of-backward snapshot time `snap_us`."""
    snaps = depth.select("ts_us").unique().sort("ts_us").with_columns(snap_us=pl.col("ts_us"))
    last_ts = ledger["ts_us"][-1]
    return (
        ledger.with_columns(turnover=pl.col("perp_qty").diff().abs() * pl.col("mark"))
        .filter(
            ((pl.col("sweep_usd") != 0) | (pl.col("ts_us") == last_ts))
            & (pl.col("ts_us") >= snaps["ts_us"][0])
        )
        .select("ts_us", "turnover")
        .sort("ts_us")
        .join_asof(snaps, on="ts_us", strategy="backward")
    )


def _years(trades: pl.DataFrame) -> float:
    return float(trades["ts_us"][-1] - trades["ts_us"][0]) / (365.25 * DAY_US)


def slippage_yield(
    sizes_usd: list[float],
    depth: pl.DataFrame,
    ledger: pl.DataFrame,
    years: float,
    capital_usd: float,
    notional: float = NOTIONAL_USD,
) -> list[float]:
    """Entry, reset and exit slippage, as an annualised fraction of capital deployed, per size.

    At size S the book opens S at the first reset in the depth period, trades S / notional x
    |dperp_qty| x mark at every later reset and closes S on the last bar; each fill is priced
    with `depth_cost` at the as-of-backward snapshot, twice (spot proxied by the perp book),
    summed, / years (of the depth period) / (S x capital_usd / notional)."""
    trades = _trades(depth, ledger)
    books = depth.filter(pl.col("ts_us").is_in(trades["snap_us"].unique().implode())).partition_by(
        "ts_us", as_dict=True
    )
    out = []
    for size in sizes_usd:
        k = size / notional
        fills = [size, *(k * trades["turnover"][1:-1]), size]
        usd = math.fsum(
            f * depth_cost(f, books[(snap,)])
            for f, snap in zip(fills, trades["snap_us"], strict=True)
            if f > 0
        )
        out.append(2 * usd / years / (k * capital_usd))
    return out


def capacity_curve(
    sizes: tuple[float, ...],
    base: Decomposition,
    kappa: float,
    oi: pl.DataFrame,
    depth: pl.DataFrame,
    ledger: pl.DataFrame,
    notional: float = NOTIONAL_USD,
) -> pl.DataFrame:
    """Modelled spread over T-bill at each size; all capacity terms are annualised fractions of
    capital deployed. `ledger` is the base run behind `base`, at `notional`."""
    years = _years(_trades(depth, ledger))
    return pl.DataFrame(
        {
            "size_usd": list(sizes),
            "oi_share": [oi_share(s, oi) for s in sizes],
            "funding_haircut_yield": [
                funding_haircut_yield(s, kappa, oi, base.capital_usd, notional) for s in sizes
            ],
            "slippage_yield": slippage_yield(
                list(sizes), depth, ledger, years, base.capital_usd, notional
            ),
        }
    ).with_columns(
        modelled_spread=base.spread - pl.col("funding_haircut_yield") - pl.col("slippage_yield")
    )


def capacity_usd(curve: pl.DataFrame, base: Decomposition) -> float | None:
    """Smallest size where modelled_spread <= CAPACITY_HALF x base.spread, linear between grid
    points (the first grid point if it is already there); None if never reached or if
    base.spread <= 0 (half of nothing is not a capacity)."""
    if base.spread <= 0:
        return None
    target = CAPACITY_HALF * base.spread
    rows = list(zip(curve["size_usd"], curve["modelled_spread"], strict=True))
    for i, (s, v) in enumerate(rows):
        if v <= target:
            if i == 0:
                return float(s)
            s0, v0 = rows[i - 1]
            return float(s0 + (v0 - target) / (v0 - v) * (s - s0))
    return None
