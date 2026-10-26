"""Capacity model (spec 9): how much money the trade holds before the position is the funding rate.

A declared model, not a measurement. All capacity terms are annualised fractions of capital
deployed, the unit of `Decomposition.spread`. At position size S the capital deployed is
S x capital_usd / notional, the base ledger's capital per unit of notional (1.5 at PRIMARY), so
every term below is a cost at size S divided by the capital at size S.

- Funding impact: the position is a permanent short share s of open interest (USD). With
  top-trader shorts taken as half of OI and the position counted among them, it moves the
  top-trader positions long/short ratio by dlog ratio = -log(1 + 2s); funding moves by kappa
  times that on every print, kappa fitted by `fit_kappa`.
- Slippage: the engine's own perp trades from the start of the book-depth files, each walked
  through the bid side of the as-of-backward snapshot; the spot leg is proxied by the perp book.
"""

import math

import numpy as np
import polars as pl

from perp_carry_economics.carry.yields import Decomposition
from perp_carry_economics.config import DAY_US
from perp_carry_economics.params import (
    CAPACITY_HALF,
    NOTIONAL_USD,
    OI_FRESH_US,
    PRINT_GAP_TOL_US,
    PRINTS_PER_DAY,
    PRINTS_PER_YEAR,
)

PRINT_US = DAY_US // PRINTS_PER_DAY


def fit_kappa(funding: pl.DataFrame, oi: pl.DataFrame) -> tuple[float, float, int]:
    """OLS with an intercept of the per-print funding change on dlog(top_ls_position_ratio).

    The ratio is sampled as-of backward at each funding print and must be at most 10 minutes
    old, else the print is dropped; differences are taken only between kept prints one print
    apart (8 h +- 1 min), never across a gap; non-finite differences are dropped. The intercept
    absorbs funding's drift. Returns (kappa as fitted, classical OLS standard error of kappa,
    rows used). Positive kappa: more longs, higher funding; a non-positive kappa means no
    measurable impact, and `funding_haircut_yield` then charges 0."""
    df = (
        funding.sort("ts_us")
        .join_asof(
            oi.sort("ts_us").select("ts_us", "top_ls_position_ratio"),
            on="ts_us",
            strategy="backward",
            tolerance=OI_FRESH_US,
        )
        .drop_nulls("top_ls_position_ratio")
        .select(
            gap=pl.col("ts_us").diff(),
            dr=pl.col("rate").diff(),
            dx=pl.col("top_ls_position_ratio").log().diff(),
        )
        .filter(
            ((pl.col("gap") - PRINT_US).abs() <= PRINT_GAP_TOL_US)
            & pl.col("dr").is_finite()
            & pl.col("dx").is_finite()
        )
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
    is that x PRINTS_PER_YEAR x notional / capital_usd. A non-positive kappa means no
    measurable impact: the haircut is 0."""
    s = oi_share(size_usd, oi)
    return max(kappa, 0.0) * math.log1p(2 * s) * PRINTS_PER_YEAR * notional / capital_usd


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
    """The bars the model trades on: the first bar in the depth period (open), every later bar
    with a perp trade, and the ledger's last bar (close); with the turnover |dperp_qty| x mark
    at the base notional and the as-of-backward snapshot time `snap_us`."""
    snaps = depth.select("ts_us").unique().sort("ts_us").with_columns(snap_us=pl.col("ts_us"))
    i = pl.int_range(pl.len())
    return (
        ledger.with_columns(turnover=pl.col("perp_qty").diff().abs() * pl.col("mark"))
        .filter(pl.col("ts_us") >= snaps["ts_us"][0])
        .filter((i == 0) | (i == pl.len() - 1) | (pl.col("turnover") > 0))
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
    depth_years: float,
    base_years: float,
    capital_usd: float,
    notional: float = NOTIONAL_USD,
) -> list[float | None]:
    """Entry, turnover and exit slippage, as an annualised fraction of capital deployed, per size.

    At size S the book opens S on the first bar of the depth period, trades S / notional x
    |dperp_qty| x mark on every later bar with a perp trade and closes S on the last bar; each
    fill is priced with `depth_cost` at the as-of-backward snapshot, twice (spot proxied by the
    perp book). The open and close happen once per hold, so they are annualised over the base
    run (`base_years`), the turnover over the depth period (`depth_years`); both are divided by
    the capital at S, S x capital_usd / notional. None for a size whose fill exceeds the book."""
    trades = _trades(depth, ledger)
    books = depth.filter(pl.col("ts_us").is_in(trades["snap_us"].unique().implode())).partition_by(
        "ts_us", as_dict=True
    )
    snaps, turnover = trades["snap_us"].to_list(), trades["turnover"].to_list()

    def usd(fill: float, snap: int) -> float:
        return fill * depth_cost(fill, books[(snap,)]) if fill > 0 else 0.0

    out: list[float | None] = []
    for size in sizes_usd:
        k = size / notional
        try:
            once = usd(size, snaps[0]) + usd(size, snaps[-1])
            recurring = math.fsum(
                usd(k * t, s) for t, s in zip(turnover[1:-1], snaps[1:-1], strict=True)
            )
        except ValueError:  # the book is exhausted at this size
            out.append(None)
            continue
        out.append(2 * (once / base_years + recurring / depth_years) / (k * capital_usd))
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
    capital deployed. `ledger` is the base run behind `base`, at `notional`. A size whose fill
    exceeds the published book has `book_exhausted` and null slippage and modelled spread."""
    depth_years = _years(_trades(depth, ledger))
    slip = slippage_yield(
        list(sizes), depth, ledger, depth_years, base.years, base.capital_usd, notional
    )
    return pl.DataFrame(
        {
            "size_usd": list(sizes),
            "oi_share": [oi_share(s, oi) for s in sizes],
            "funding_haircut_yield": [
                funding_haircut_yield(s, kappa, oi, base.capital_usd, notional) for s in sizes
            ],
            "slippage_yield": pl.Series(slip, dtype=pl.Float64),
        }
    ).with_columns(
        modelled_spread=base.spread - pl.col("funding_haircut_yield") - pl.col("slippage_yield"),
        book_exhausted=pl.col("slippage_yield").is_null(),
    )


def capacity_usd(curve: pl.DataFrame, notional: float = NOTIONAL_USD) -> float | None:
    """The smaller of the smallest size where modelled_spread <= CAPACITY_HALF x the modelled
    spread at size == notional, the reference (linear between grid points; the first grid point
    if it is already there), and the smallest size that exhausts the published book (capacity
    is at most that). None if neither occurs or if the reference is <= 0 (half of nothing is
    not a capacity). A reference the book cannot fill leaves only the exhaustion cap. Raises
    ValueError if notional is not a grid size."""
    if notional not in curve["size_usd"].to_list():
        raise ValueError(f"notional {notional:,.0f} is not a capacity grid size")
    ref = curve.filter(pl.col("size_usd") == notional)["modelled_spread"][0]
    if ref is not None and ref <= 0:
        return None
    caps = curve.filter(pl.col("book_exhausted"))["size_usd"].to_list()
    rows = curve.filter(~pl.col("book_exhausted")).select("size_usd", "modelled_spread").rows()
    target = CAPACITY_HALF * ref if ref is not None else None
    for i, (s, v) in enumerate(rows if target is not None else []):
        if v <= target:
            if i:
                s0, v0 = rows[i - 1]
                s = s0 + (v0 - target) / (v0 - v) * (s - s0)
            caps.append(s)
            break
    return float(min(caps)) if caps else None
