"""Cost model v2 (issue #111, S71): a separate, selectable research model. RESEARCH ONLY for now.

The live engine and the dashboard's shadow fills keep `kaibot.broker.shadow_costs` (model "v1"); nothing live
imports this module. Research callers choose a model with `get_model("v1" | "v2")`; every default stays "v1" so
earlier reports reproduce byte for byte. Switching live reporting to v2 is a later, separate decision.

Why v2 (audit F4, report "Six basis points is about right in total and wrong in its parts"): v1's total of about
6 bp a round trip is plausible, but its spread is too low (1 bp half-spread floor), its impact is too high (a fixed
quarter-spread on top of the spread, which double counts, plus a square-root law fitted to institutional
metaorders) and its impact term is unbounded when the fill minute is thin.

PRE-DECLARED PARAMETERS (`V2Params()` defaults; any change to them is a parameter change and logs a trial):

Half-spread per side, by the stock's 20-session average dollar volume (ADV, sessions before the decision only).
The report's table gives a range per bucket; v2 charges the MIDPOINT of each range:

    ADV over $500M          1.0 - 2.5 bp  ->  1.75 bp
    ADV $100M - $500M       2.5 - 5.0 bp  ->  3.75 bp
    ADV $50M - $100M        5.0 - 8.0 bp  ->  6.50 bp
    ADV under $50M/unknown  (not in the report's table)  -> 10.0 bp, above the last bucket's upper end on purpose

- `spread_charge` 1.0: the share of the quoted half-spread a marketable order pays. The report's prudent range for
  Alpaca is 60% to 100% (price improvement 0% to 40%); 100% is the conservative end. Allowed: 0.6 to 1.0.
- Tick floor: the half-spread is at least half a cent per share ON THE RAW PRICE (one-cent tick).
- Venue: a continuous-session marketable order pays `spread_charge` x half-spread; a closing-auction fill pays one
  full half-spread ("half a spread per side", the conservative close setting); an opening-auction fill (and a stop
  gapped through at the open) pays twice the midday half-spread. The auction settings ignore `spread_charge`.
- Impact: near zero at our order sizes. Linear in size below the square-root crossover (Bucci et al.: linear
  below about 0.1% of daily volume, 0.4 x sigma x sqrt(Q/V) above it), so per side
  `impact = 0.4 / sqrt(0.001) x sigma_daily x (order $ / ADV)`: about 0.5 bp for $14k at $50M ADV, 0.05 bp at
  $500M. No fixed quarter-spread: the spread term already holds the "price impact" part of an effective spread.
- Participation cap 1%: shares above 1% of the fill period's share volume (the fill minute, the first or last
  minute for the open and the close) pay a square-root term `0.4 x sigma_daily x sqrt(excess $ / ADV)` each.
  Scaled by ADV, not by the minute, so a thin or stale minute cannot blow it up; total impact is capped at
  `impact_cap_bp` 25 bp per side in any case.
- Closing-auction impact: half of the continuous impact (impact at the close is about half of intraday).
- Fees, 2026 rates, on sales only: SEC Section 31 $20.60 per million dollars sold (from 4 April 2026); FINRA TAF
  $0.000195 a share, capped at $9.79 a trade; each fee rounded up to the next cent (Alpaca rounds up). Not
  modelled: the CAT fee (rate not in the research notes; fractions of a cent).
- Limit orders: a resting limit fills at its price, pays no spread, and fills only if the bar trades through it by
  a cent AND a deterministic draw keyed on the order passes `limit_fill_prob` 0.8 (queue position and the paper
  simulator's optimism; unfitted, to be calibrated against paper fills).
- Stops: a stop gapped through at the open fills at the open as an opening fill; otherwise at the stop level as a
  continuous market order.

RAW PRICES (S84 / #124): spreads are in bp, so only the tick floor and the per-share TAF depend on the price level.
Callers pass `raw_mid` (the unadjusted price of the same moment) and the floor and the share count for TAF are
taken on it. Until #124 merges the bars are split-adjusted and callers pass the ADJUSTED price (`raw_mid=None`):
the half-cent floor then still overstates costs for names that split heavily later (audit E4), exactly as in v1,
although much less (the bp spread now dominates above about $3 for the largest bucket).

Round trip at the defaults, $14k market in / market out, 2% daily volatility, ample minute volume, fees
included (pinned by tests/unit/test_costs_v2.py): over $500M ($1bn) about 3.8 bp, $100M-$500M ($250M) about 8.0 bp,
$50M-$100M ($75M) about 14.2 bp; the report's ranges are 3-6, 6-11 and 11-18 bp. v1 charges the same 4.1 to 5.5 bp
in every bucket (20,000 or 2,900 shares in the fill minute).
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Protocol

from kaibot.broker import shadow_costs

N_MIN = 390  # regular-session minutes: sigma_daily = sigma_1m x sqrt(390), as in features.py
VENUES = ("continuous", "open", "close")


@dataclass(frozen=True)
class FillCtx:
    """Everything a model may use to price one fill. Prices are in the units of the caller's series."""

    mid: float  # no-cost price the order fills around (the bar's open, close or the stop level)
    qty: float  # shares in the caller's units
    sigma_1m: float  # 1-minute return sigma (fraction), as v1 takes it
    vol: float  # share volume of the fill period (the fill minute; first / last minute at the open / close)
    adv: float = math.nan  # 20-session average dollar volume known at the decision; NaN = unknown
    venue: str = "continuous"  # continuous | open | close
    raw_mid: float | None = None  # unadjusted price of the same moment; None = `mid` (adjusted, audit E4)


@dataclass(frozen=True)
class Fill:
    price: float  # per share, after spread and impact
    half: float  # spread part, $ per share (>= 0)
    impact: float  # impact part, $ per share (>= 0)


class CostModel(Protocol):
    name: str

    def fill(self, side: str, ctx: FillCtx) -> Fill: ...

    def sell_fees(self, qty: float, price: float, raw_price: float | None = None) -> float: ...


def _sign(side: str) -> int:
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be buy or sell, not {side!r}")
    return 1 if side == "buy" else -1


# ---------------------------------------------------------------- v1, unchanged, behind the protocol


class CostV1:
    """Model v1 behind the protocol: the replay's half-spread (>= 1 bp, >= $0.005) around `mid` fed to the live
    `shadow_costs.shadow_fill_price`, and its fees. Venue, ADV and raw price are ignored, as v1 ignores them.
    Equal to `replay.sim.buy_px` / `sell_px` / `cost_parts` to the last bit (a test pins it)."""

    name = "v1"

    def fill(self, side: str, ctx: FillCtx) -> Fill:
        _sign(side)
        hs = max(ctx.mid * 0.0001, 0.005)
        px = shadow_costs.shadow_fill_price(side, ctx.qty, shadow_costs.QuoteCtx(
            ctx.mid - hs, ctx.mid + hs, ctx.sigma_1m, max(ctx.vol, 1.0)))
        return Fill(px, hs, abs(px - ctx.mid) - hs)

    def sell_fees(self, qty: float, price: float, raw_price: float | None = None) -> float:
        return shadow_costs.sell_fees(qty, price)


# ---------------------------------------------------------------- v2


@dataclass(frozen=True)
class V2Params:
    # (ADV lower bound in $, half-spread in bp per side), most liquid first; below the last bound: `half_bp_floor`
    buckets: tuple[tuple[float, float], ...] = ((500e6, 1.75), (100e6, 3.75), (50e6, 6.5))
    half_bp_floor: float = 10.0  # ADV under $50M or unknown
    spread_charge: float = 1.0  # share of the quoted half-spread paid by a continuous marketable order
    tick_half: float = 0.005  # $ per share on the raw price
    open_mult: float = 2.0  # opening auction / gapped stop: x the midday half-spread
    close_mult: float = 1.0  # closing auction: x the half-spread ("half a spread per side")
    close_impact_mult: float = 0.5
    participation_cap: float = 0.01  # share of the fill period's volume an order may take before the excess pays
    impact_lin_k: float = 0.4 / math.sqrt(0.001)  # linear regime, continuous with 0.4 sigma sqrt(Q/V) at Q/V 0.1%
    impact_sqrt_k: float = 0.4
    impact_adv_floor: float = 50e6  # ADV used for impact when it is unknown or lower
    impact_cap_bp: float = 25.0  # per side, any venue
    sec_fee_per_dollar: float = 20.60 / 1e6  # from 4 April 2026
    taf_per_share: float = 0.000195  # 2026
    taf_cap: float = 9.79  # per trade, 2026
    round_fees_up: bool = True  # each fee to the next cent, as Alpaca debits them
    limit_fill_prob: float = 0.8
    tick: float = 0.01  # a limit fills only when the bar trades through it by this much

    def __post_init__(self) -> None:
        if not 0.6 <= self.spread_charge <= 1.0:
            raise ValueError("spread_charge must be within 0.6 to 1.0 of the quoted half-spread (report range)")
        if not 0.0 <= self.limit_fill_prob <= 1.0:
            raise ValueError("limit_fill_prob must be a probability")
        bounds = [b for b, _ in self.buckets]
        if bounds != sorted(bounds, reverse=True):
            raise ValueError("buckets must be ordered from the most liquid down")


def _cent_up(x: float) -> float:
    return math.ceil(round(x * 100, 9)) / 100 if x > 0 else 0.0


@dataclass(frozen=True)
class CostV2:
    p: V2Params = field(default_factory=V2Params)
    name: str = "v2"

    # -- components

    def half_spread_bp(self, adv: float) -> float:
        """Quoted half-spread per side in bp for a 20-session ADV in $ (NaN / <= 0: unknown)."""
        if math.isfinite(adv) and adv > 0:
            for lo, bp in self.p.buckets:
                if adv >= lo:
                    return bp
        return self.p.half_bp_floor

    def venue_mult(self, venue: str) -> float:
        if venue == "continuous":
            return self.p.spread_charge
        if venue == "open":
            return self.p.open_mult
        if venue == "close":
            return self.p.close_mult
        raise ValueError(f"venue must be one of {VENUES}, not {venue!r}")

    def half_spread(self, ctx: FillCtx) -> float:
        """$ per share charged for the spread at this venue (tick floor on the raw price)."""
        raw = ctx.raw_mid if ctx.raw_mid is not None and ctx.raw_mid > 0 else ctx.mid
        quoted = max(self.half_spread_bp(ctx.adv) / 1e4 * raw, self.p.tick_half)  # on the raw price
        return quoted / raw * ctx.mid * self.venue_mult(ctx.venue)

    def impact_frac(self, ctx: FillCtx) -> float:
        """Impact per side as a fraction of the price: linear term plus the square-root term on the shares above
        the participation cap, capped at `impact_cap_bp`."""
        if ctx.qty <= 0 or ctx.mid <= 0:
            return 0.0
        sigma_d = max(ctx.sigma_1m, 0.0) * math.sqrt(N_MIN) if math.isfinite(ctx.sigma_1m) else 0.0
        adv = max(ctx.adv if math.isfinite(ctx.adv) else 0.0, self.p.impact_adv_floor)
        lin = self.p.impact_lin_k * sigma_d * (ctx.qty * ctx.mid / adv)
        vol = ctx.vol if math.isfinite(ctx.vol) and ctx.vol > 0 else 0.0
        excess = max(ctx.qty - self.p.participation_cap * vol, 0.0)
        sq = self.p.impact_sqrt_k * sigma_d * math.sqrt(excess * ctx.mid / adv) * (excess / ctx.qty)
        imp = lin + sq
        if ctx.venue == "close":
            imp *= self.p.close_impact_mult
        return min(imp, self.p.impact_cap_bp / 1e4)

    # -- the protocol

    def fill(self, side: str, ctx: FillCtx) -> Fill:
        s = _sign(side)
        half = self.half_spread(ctx)
        imp = self.impact_frac(ctx) * ctx.mid
        return Fill(ctx.mid + s * (half + imp), half, imp)

    def sell_fees(self, qty: float, price: float, raw_price: float | None = None) -> float:
        """SEC + FINRA TAF on a sale of `qty` shares at `price` (caller's units). With `raw_price` the TAF counts
        raw shares (qty x price / raw_price); the SEC fee is on the dollar value either way."""
        if qty <= 0 or price <= 0:
            return 0.0
        shares = qty * price / raw_price if raw_price and raw_price > 0 else qty
        sec = qty * price * self.p.sec_fee_per_dollar
        taf = min(shares * self.p.taf_per_share, self.p.taf_cap)
        if self.p.round_fees_up:
            return _cent_up(sec) + _cent_up(taf)
        return sec + taf

    # -- order types

    def limit_fill(self, side: str, limit: float, low: float, high: float, key: str) -> Fill | None:
        """A resting limit: fills at `limit` (no spread, no impact) only when the bar trades through it by a tick
        and the deterministic draw for `key` (e.g. "SYM|2024-03-01|exit") is below `limit_fill_prob`."""
        s = _sign(side)
        through = low <= limit - self.p.tick if s > 0 else high >= limit + self.p.tick
        if not through or _uniform(key) >= self.p.limit_fill_prob:
            return None
        return Fill(float(limit), 0.0, 0.0)

    def stop_fill(self, stop: float, open_: float, low: float, ctx: FillCtx) -> Fill | None:
        """A sell stop on a session's bar: gapped through at the open -> filled at the open as an opening fill;
        reached later -> at the stop level as a continuous market sell; not reached -> None. `ctx` supplies size,
        sigma, ADV and raw price; its `mid`, `venue` and `vol` are replaced here (`vol` is kept for the
        continuous case and also used at the open)."""
        if open_ <= stop:
            return self.fill("sell", FillCtx(open_, ctx.qty, ctx.sigma_1m, ctx.vol, ctx.adv, "open", ctx.raw_mid))
        if low <= stop:
            raw = None if ctx.raw_mid is None else ctx.raw_mid * stop / ctx.mid
            return self.fill("sell", FillCtx(stop, ctx.qty, ctx.sigma_1m, ctx.vol, ctx.adv, "continuous", raw))
        return None


def _uniform(key: str) -> float:
    h = hashlib.blake2b(key.encode(), digest_size=8).digest()
    return int.from_bytes(h, "big") / 2**64


MODELS = ("v1", "v2")


def get_model(name: str = "v1", **params) -> CostModel:
    """`v1` (the default everywhere, and the only model the live engine uses) or `v2` with optional overrides of
    `V2Params` (each override is a parameter change: log a trial)."""
    if name == "v1":
        if params:
            raise ValueError("v1 takes no parameters")
        return CostV1()
    if name == "v2":
        return CostV2(V2Params(**params))
    raise ValueError(f"unknown cost model {name!r}; choose one of {MODELS}")


def round_trip_bp(model: CostModel, *, order: float = 13_907.0, price: float = 100.0, adv: float = 1e9,
                  sigma_daily: float = 0.02, vol: float = 1e6, entry: str = "continuous",
                  exit: str = "continuous") -> dict:
    """Round-trip cost in bp of the order (market in at `entry`, market out at `exit`), split into spread, impact
    and fees, at no price move. For tables and tests; the default order is the audit's $13,907 at $100."""
    qty = math.floor(order / price)
    sig = sigma_daily / math.sqrt(N_MIN)
    b = model.fill("buy", FillCtx(price, qty, sig, vol, adv, entry))
    s = model.fill("sell", FillCtx(price, qty, sig, vol, adv, exit))
    fees = model.sell_fees(qty, s.price)
    notional = qty * price
    out = {"spread": (b.half + s.half) * qty / notional * 1e4, "impact": (b.impact + s.impact) * qty / notional * 1e4,
           "fees": fees / notional * 1e4}
    out["total"] = out["spread"] + out["impact"] + out["fees"]
    return {k: round(v, 3) for k, v in out.items()}
