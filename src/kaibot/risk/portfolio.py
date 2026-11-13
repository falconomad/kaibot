"""The portfolio builder (issue #119, M7·S79; decision 15 sections 3 and 6; audit C1). Pure: no clock, no broker,
no database, no logging side effects (what was skipped is returned in the plan).

Target lists of multi-day strategies + the current book + limits -> whole-share target positions per
(strategy, symbol). In this order, each step can only make a position smaller:

1. volatility size: shares = equity x k x weight / ATR(20); weight clamped to 0..1, k = min(k, the level's
   `atr_risk_pct`), so no rule sizes above the level's ceiling;
2. index trend filter (only for a list with `index_filter`): while SPY is not above its 200-day average (False or
   unknown) no position may grow; sells go through;
3. maximum names (held names first, then the higher weight, then the symbol), per-name cap (one symbol across all
   sleeves), budget per strategy, sector cap (an unknown sector is one bucket), gross exposure with the cash
   buffer (intraday exposure counts), overnight exposure, gap-scenario loss (`gate.gap_loss_ps`): each scales the
   positions it covers down pro rata to the room left;
4. whole shares (rounded down); a name whose single share costs more than its room is skipped and logged;
5. no trade within 10% of the target (a kept position is re-checked against every cap; if it breaks one the
   position goes to its target instead).

Positions of strategies that sent no list this time are left alone and count against the caps. A listed name
that is not held and has no usable price or ATR is skipped and logged; a held name with a price but no usable ATR
keeps its current position and is logged. A held name with no usable price is an error for the whole build
(`ValueError`, fail closed): the book's exposure cannot be measured without it. The intraday path does not
use the builder (it keeps the gate's per-trade sizing until #118); the same builder serves the ten-year test and
live paper trading, because it only sees numbers.

`fill="ranked"` (issue #168): the pre-registered sizing of the momentum rules (docs/prereg/trend_momentum.md and
mom_12_1_band.md, choices 8-11 and Amendment 3), so that what trades live is what was tested. Instead of steps 3-5:

- a held name missing from its list is sold; the room is counted at the held prices as if every order fills, and
  sales make room for purchases at the same print;
- a list with `resize=False` keeps its held names as they are; with `resize=True` a held name whose volatility
  size differs from its current size by MORE than `band` x the size is traded to it, reductions first, an increase
  cut to the room left;
- new names in the list's order (best first): stop at `max_names`; a size below one share is skipped; a name that
  does not fit in full is cut to the room left and buying stops there;
- the room is the gross / overnight cap and, with `cash_reserve`, the cash (`build(cash=...)`) plus the plan's
  sales less `cash_reserve` x equity. The per-name cap is part of the size, as the pre-registrations write it.
- the sector cap is part of the pass (Amendment 4 of both pre-registrations): a buy or a resize increase is cut
  to its sector's room, best-ranked first, and buying goes on with the next name; a stock without a sector is
  its own sector (the pro-rata fill keeps one "unknown" bucket).
- The other book caps (gross with the cash floor, overnight, gap scenario, per-name across sleeves, budgets) are
  OUTER GUARDS: checked on the finished plan, they only cut what the plan buys, worst-ranked first, and every cut
  is in `Plan.guarded`. A run that claims the pre-registered sizing asserts `guarded` stays empty.
- `Instrument.can_open=False`: the research harness refuses an entry without the previous close or a 14-session
  ATR at the fill, after the room was counted; such a name is sized and counted, and gets no order.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from kaibot.risk.gate import BookLimits, gap_loss_ps, validate_book
from kaibot.strategies.base import TargetList

Key = tuple[str, str]  # (strategy, symbol)
UNKNOWN_SECTOR = "unknown"


@dataclass(frozen=True)
class Instrument:
    """Point-in-time numbers for one symbol at the decision: last price, ATR(20) in $, sector (None = unknown)."""

    px: float
    atr20: float
    sector: str | None = None
    can_open: bool = True  # False: no new position this time (see the module docstring)


@dataclass(frozen=True)
class Skip:
    strategy: str
    symbol: str
    reason: str  # no_data | unaffordable | max_names | trend | name_cap | budget | sector | gross | overnight | gap
    #              ranked fill also: cash | refused
    detail: str = ""


@dataclass
class Exposure:
    gross: float  # $ of everything held, intraday included
    overnight: float  # $ of the multi-day book
    sectors: dict[str, float]  # $ of the multi-day book per sector
    gap_loss: float  # $ the multi-day book loses in the gap scenario

    def snapshot_fields(self, symbol: str, market: Mapping[str, Instrument]) -> dict:
        """The book fields of `gate.Snapshot` for an order in `symbol` of a multi-day sleeve."""
        return {"gross_exposure": self.gross, "overnight_exposure": self.overnight,
                "sector_exposure": self.sectors.get(_sector(market[symbol]), 0.0), "gap_loss": self.gap_loss,
                "overnight": True}


@dataclass
class Plan:
    targets: dict[Key, int]  # every (strategy, symbol) of the strategies that sent a list; 0 = sell
    orders: dict[Key, int]  # target - current, only where they differ (+ buy, - sell)
    binding: dict[Key, str]  # what decided each listed or held position's size
    skipped: list[Skip] = field(default_factory=list)
    exposure: Exposure | None = None  # of the whole book after the plan
    sequence: list[Key] = field(default_factory=list)  # the orders in the order to send them (sells first)
    guarded: dict[Key, str] = field(default_factory=dict)  # ranked fill: purchases an outer guard cut, and which


def _sector(inst: Instrument) -> str:
    return inst.sector or UNKNOWN_SECTOR


def _own_sector(market: Mapping[str, Instrument], sym: str) -> str:
    """The ranked fill's sector bucket: a stock without a sector is its own sector (momentum Amendment 4)."""
    inst = market.get(sym)
    return (inst.sector if inst is not None else None) or f"own:{sym}"


def _ok(x) -> bool:
    return isinstance(x, int | float) and not isinstance(x, bool) and math.isfinite(x) and x > 0


def _qty(q) -> int:
    q = getattr(q, "qty", q)  # a number, or a ledger Sleeve
    return max(int(math.floor(float(q) + 1e-9)), 0)


def _px(market: Mapping[str, Instrument], sym: str) -> float:
    inst = market.get(sym)
    if inst is None or not _ok(inst.px):
        raise ValueError(f"no price for held symbol {sym}")
    return float(inst.px)


def exposures(book: Mapping[Key, float], market: Mapping[str, Instrument], other_exposure: float = 0.0) -> Exposure:
    """Exposure of a multi-day book (shares per (strategy, symbol)) at the market's prices; `other_exposure` is the
    $ held outside it (intraday sleeves), counted in gross only."""
    over, gap, sectors = 0.0, 0.0, {}
    for (_, sym), q in sorted(book.items()):
        q = _qty(q)
        if q <= 0:
            continue
        px = _px(market, sym)
        inst = market[sym]
        over += q * px
        gap += q * gap_loss_ps(px, inst.atr20 if _ok(inst.atr20) else None)
        sectors[_sector(inst)] = sectors.get(_sector(inst), 0.0) + q * px
    return Exposure(gross=over + max(other_exposure, 0.0), overnight=over, sectors=sectors, gap_loss=gap)


@dataclass(frozen=True)
class PortfolioBuilder:
    limits: BookLimits
    k: float = 0.001  # decision 15: a 1-ATR move costs 0.1% of equity per position
    band: float = 0.10  # no trade within this fraction of the target
    fill: str = "pro_rata"  # pro_rata | ranked (the pre-registered sizing, see the module docstring)
    cash_reserve: float | None = None  # ranked: purchases fit in cash + sales - this x equity (None: no cash limit)

    def __post_init__(self) -> None:
        validate_book(self.limits)
        if not (self.k > 0 and math.isfinite(self.k)) or not (0 <= self.band < 1):
            raise ValueError("k must be > 0 and band in [0, 1)")
        if self.fill not in ("pro_rata", "ranked"):
            raise ValueError("fill is pro_rata or ranked")
        if self.cash_reserve is not None and (self.fill != "ranked" or not 0 <= self.cash_reserve < 1):
            raise ValueError("cash_reserve is for the ranked fill, in [0, 1)")

    def build(self, targets: Iterable[TargetList], current: Mapping[Key, float], market: Mapping[str, Instrument],
              *, equity: float, budgets: Mapping[str, float] | None = None, index_up: bool | None = None,
              other_exposure: float = 0.0, cash: float | None = None) -> Plan:
        """`current`: shares per (strategy, symbol), every multi-day sleeve (numbers or ledger sleeves).
        `budgets`: % of equity per strategy (absent = no budget beyond the book caps). `index_up`: SPY above its
        200-day average (None = unknown, treated as below). `other_exposure`: $ held by intraday sleeves.
        `cash`: the account's cash before the plan's fills (ranked fill with a `cash_reserve`: required)."""
        if not _ok(equity):
            raise ValueError(f"equity must be > 0, got {equity!r}")
        if self.fill == "ranked":
            return self._ranked(targets, current, market, float(equity), budgets, index_up, other_exposure, cash)
        lim, eq = self.limits, float(equity)
        lists = sorted(targets, key=lambda t: t.strategy)
        if len({t.strategy for t in lists}) != len(lists):
            raise ValueError("a strategy sent its target list twice")
        active = {t.strategy for t in lists}
        cur = {k: _qty(q) for k, q in current.items() if _qty(q) > 0}
        px = {sym: _px(market, sym) for (_, sym) in cur}

        fixed: dict[Key, int] = {k: q for k, q in cur.items() if k[0] not in active}
        want: dict[Key, float] = {}  # $ wanted per active key
        weight: dict[Key, float] = {}
        binding: dict[Key, str] = {}
        skipped: list[Skip] = []
        logged: set[Key] = set()

        def skip(key: Key, reason: str, detail: str = "") -> None:
            if key not in logged:
                logged.add(key)
                skipped.append(Skip(key[0], key[1], reason, detail))

        k_eff = min(self.k, lim.atr_risk_pct / 100)
        filtered = {t.strategy for t in lists if t.index_filter and index_up is not True}
        for t in lists:
            for sym in sorted(t.weights):
                key = (t.strategy, sym)
                w = t.weights[sym]
                w = min(max(float(w), 0.0), 1.0) if isinstance(w, int | float) and math.isfinite(w) else 0.0
                inst = market.get(sym)
                if w > 0 and (inst is None or not _ok(inst.px) or not _ok(inst.atr20)):
                    skip(key, "no_data", "price or ATR(20) missing")
                    if cur.get(key, 0) > 0:
                        fixed[key] = cur[key]  # keep it, and count it against the caps
                    continue
                if w > 0 and cur.get(key, 0) > 0 and not t.resize:
                    fixed[key], binding[key] = cur[key], "hold"  # the list keeps its held names as they are
                    continue
                if w > 0 and cur.get(key, 0) == 0 and not inst.can_open:
                    skip(key, "refused", "no new position in this name now")
                    continue
                weight[key] = w
                if w > 0:
                    want[key] = eq * k_eff * w / float(inst.atr20) * float(inst.px)
                    binding[key] = "vol"
                    px[sym] = float(inst.px)
        for key in cur:  # held by a strategy that sent a list, and not (usably) in it: sell
            if key[0] in active and key not in want and key not in fixed:
                want[key], binding[key] = 0.0, "exit"
                weight.setdefault(key, 0.0)

        def cut(keys: Iterable[Key], room: float, reason: str, per_dollar=lambda key: 1.0) -> None:
            keys = sorted(k for k in keys if want.get(k, 0) > 0)
            total = sum(want[k] * per_dollar(k) for k in keys)
            room = max(room, 0.0)
            if total <= room + 1e-12:
                return
            f = room / total if total > 0 else 0.0
            for k in keys:
                want[k] *= f
                binding[k] = reason

        # 2. trend filter: no position of a filtered rule may grow
        for key in sorted(want):
            if key[0] in filtered and want[key] > cur.get(key, 0) * px[key[1]] + 1e-9:
                want[key] = cur.get(key, 0) * px[key[1]]
                binding[key] = "trend"

        # 3a. maximum names
        fixed_names = {s for (_, s), q in fixed.items() if q > 0}
        cand = {s for (_, s), v in want.items() if v > 0} - fixed_names
        slots = max(lim.max_names - len(fixed_names), 0)

        def rank(sym: str):
            held = any(cur.get(k, 0) > 0 for k in want if k[1] == sym)
            return (0 if held else 1, -max(weight.get(k, 0) for k in want if k[1] == sym),
                    -sum(want[k] for k in want if k[1] == sym), sym)

        for sym in sorted(cand, key=rank)[slots:]:
            for key in sorted(k for k in want if k[1] == sym and want[k] > 0):
                want[key], binding[key] = 0.0, "max_names"

        def fixed_dollars(pred) -> float:
            return sum(q * px[s] for (n, s), q in sorted(fixed.items()) if pred(n, s))

        # 3b. per-name cap (one symbol across all sleeves)
        for sym in sorted({s for (_, s) in want}):
            room = eq * lim.max_name_pct / 100 - fixed_dollars(lambda n, s, sym=sym: s == sym)
            cut([k for k in want if k[1] == sym], room, "name_cap")
        # 3c. budget per strategy
        for strat, pct in sorted((budgets or {}).items()):
            if strat in active:
                room = eq * float(pct) / 100 - fixed_dollars(lambda n, s, strat=strat: n == strat)
                cut([k for k in want if k[0] == strat], room, "budget")
        # 3d. sector cap
        sec = {sym: _sector(market[sym]) for (_, sym) in {**fixed, **want}}
        for bucket in sorted(set(sec.values())):
            cut([k for k in want if sec[k[1]] == bucket],
                eq * lim.max_sector_pct / 100 - fixed_dollars(lambda n, s, b=bucket: sec[s] == b), "sector")
        # 3e. gross (with the cash buffer; intraday counts) and overnight
        fixed_all = fixed_dollars(lambda n, s: True)
        gross_room = eq * min(lim.max_gross_pct, 100 - lim.min_cash_pct) / 100 - max(other_exposure, 0.0) - fixed_all
        over_room = eq * lim.max_overnight_pct / 100 - fixed_all
        if over_room < gross_room:
            cut(want, over_room, "overnight")
        else:
            cut(want, gross_room, "gross")
        # 3f. gap-scenario loss
        def gap_ps(sym: str) -> float:
            inst = market[sym]
            return gap_loss_ps(px[sym], inst.atr20 if _ok(inst.atr20) else None)

        fixed_gap = sum(q * gap_ps(s) for (_, s), q in sorted(fixed.items()))
        cut(want, eq * lim.max_gap_loss_pct / 100 - fixed_gap, "gap", per_dollar=lambda k: gap_ps(k[1]) / px[k[1]])

        # 4. whole shares
        tgt: dict[Key, int] = {}
        for key in sorted(want):
            q = max(int(math.floor(want[key] / px[key[1]] + 1e-9)), 0)
            if want[key] > 0 and q == 0:
                binding[key] = "unaffordable"
                skip(key, "unaffordable", f"one share ${px[key[1]]:.2f} > room ${want[key]:.2f}")
            tgt[key] = q

        # 5. no-trade band, re-checked against every cap
        banded: dict[Key, int] = {}
        for key, q in tgt.items():
            c = cur.get(key, 0)
            if c > 0 and q > 0 and c != q and abs(q - c) < self.band * q:
                banded[key] = q
                tgt[key] = c
        if banded and not self._fits({**fixed, **tgt}, market, px, eq, budgets, active, other_exposure):
            for key, q in banded.items():
                if tgt[key] > q:  # kept above its target: back to the target (a kept smaller one only helps)
                    tgt[key] = q
        for key in banded:
            if tgt[key] == cur.get(key, 0):
                binding[key] = "band"

        # everything a rule wanted and does not get is logged with what stopped it
        for key, w in sorted(weight.items()):
            if w > 0 and tgt.get(key, 0) == 0 and cur.get(key, 0) == 0:
                skip(key, binding.get(key, "vol"))
        for key in fixed:
            if key[0] in active:
                tgt[key] = fixed[key]  # a listed name without data keeps its position
        orders = {k: q - cur.get(k, 0) for k, q in sorted(tgt.items()) if q != cur.get(k, 0)}
        book = {**fixed, **tgt}
        return Plan(targets=dict(sorted(tgt.items())), orders=orders, binding=dict(sorted(binding.items())),
                    skipped=skipped, exposure=exposures(book, market, other_exposure),
                    sequence=[k for k, q in orders.items() if q < 0] + [k for k, q in orders.items() if q > 0])

    # ---------------------------------------------------------------- the ranked fill (#168)

    def _ranked(self, targets: Iterable[TargetList], current: Mapping[Key, float], market: Mapping[str, Instrument],
                eq: float, budgets, index_up, other_exposure: float, cash: float | None) -> Plan:
        """The pre-registered sizing (module docstring). The arithmetic is the research harness's, operation for
        operation (the momentum core of the research harness, #127), so the same inputs give the same whole shares."""
        lim = self.limits
        lists = sorted(targets, key=lambda t: t.strategy)
        if len({t.strategy for t in lists}) != len(lists):
            raise ValueError("a strategy sent its target list twice")
        if self.cash_reserve is not None and (cash is None or not math.isfinite(cash)):
            raise ValueError("the ranked fill with a cash reserve needs the account's cash")
        active = {t.strategy for t in lists}
        cur = {k: _qty(q) for k, q in current.items() if _qty(q) > 0}  # the book's order is kept
        px: dict[str, float] = {}
        for (_, sym) in cur:
            px[sym] = _px(market, sym)
        fixed = {k: q for k, q in cur.items() if k[0] not in active}
        binding: dict[Key, str] = {}
        skipped: list[Skip] = []
        k_eff = min(self.k, lim.atr_risk_pct / 100)
        filtered = {t.strategy for t in lists if t.index_filter and index_up is not True}

        def weight(t: TargetList, sym: str) -> float:
            w = t.weights.get(sym, 0.0)
            return min(max(float(w), 0.0), 1.0) if isinstance(w, int | float) and math.isfinite(w) else 0.0

        listed = {(t.strategy, s): weight(t, s) for t in lists for s in t.weights if weight(t, s) > 0}
        resize_of = {t.strategy: bool(t.resize) for t in lists}
        sell = [k for k in cur if k[0] in active and k not in listed]  # in the book's order

        def size(key: Key) -> float | None:
            """shares = equity x k x weight / ATR(20), whole, under the per-name cap; None without a price or ATR."""
            inst = market.get(key[1])
            if inst is None or not _ok(inst.px) or not _ok(inst.atr20):
                return None
            room = eq * lim.max_name_pct / 100
            other = sum(q * px[k[1]] for k, q in cur.items() if k[1] == key[1] and k != key)  # other sleeves
            if other:
                room -= other
            return float(min(math.floor(eq * k_eff * listed[key] / inst.atr20), math.floor(room / inst.px)))

        cap = min(lim.max_gross_pct, lim.max_overnight_pct) / 100 * eq
        exposure = sum(q * px[k[1]] for k, q in cur.items() if k not in sell)
        if other_exposure > 0:
            exposure += other_exposure
        names_held = {k[1] for k in cur if k not in sell}
        names = len(names_held)
        money = None if self.cash_reserve is None else (
            cash + sum(q * px[k[1]] for k, q in cur.items() if k in sell) - self.cash_reserve * eq)

        def fits(p: float) -> float:  # whole shares that fit under the cap and in the cash
            n = math.floor(max(cap - exposure, 0.0) / p)
            return n if money is None else min(n, math.floor(max(money, 0.0) / p))

        def stop_reason(p: float) -> str:
            return "cash" if money is not None and math.floor(max(money, 0.0) / p) < math.floor(
                max(cap - exposure, 0.0) / p) else "gross"

        # the sector cap (Amendment 4 of the momentum pre-registrations): held names at their prices; a stock
        # without a sector is its own sector
        sec_cap = lim.max_sector_pct / 100 * eq
        used: dict[str, float] = {}
        for k, q in cur.items():
            if k not in sell:
                used[_own_sector(market, k[1])] = used.get(_own_sector(market, k[1]), 0.0) + q * px[k[1]]

        def sector_room(sym: str, p: float) -> float:
            return math.floor(max(sec_cap - used.get(_own_sector(market, sym), 0.0), 0.0) / p)

        tgt: dict[Key, int] = {k: 0 for k in sell}
        for k in sell:
            binding[k] = "exit"
        sequence: list[Key] = list(sell)
        # resizes of held names, reductions first (they make room); a filtered rule's names may not grow
        want = []
        for key, q in cur.items():
            if key not in listed:
                continue
            tgt[key], binding[key] = q, "hold"
            if not resize_of[key[0]]:
                continue
            t = size(key)
            if t is None:
                binding[key] = "no_data"
                skipped.append(Skip(key[0], key[1], "no_data", "price or ATR(20) missing"))
            elif t >= 1 and abs(q - t) > self.band * t:
                want.append((key, q, t))
            else:
                binding[key] = "band"
        resized = []
        for key, q, t in sorted(want, key=lambda w: w[2] - w[1]):
            if t > q:
                if key[0] in filtered:
                    binding[key] = "trend"
                    continue
                room = fits(px[key[1]])
                t = q + min(t - q, room, sector_room(key[1], px[key[1]]))
                if t <= q:
                    binding[key] = stop_reason(px[key[1]]) if room < 1 else "sector"
                    continue
            exposure += (t - q) * px[key[1]]
            if money is not None:
                money -= (t - q) * px[key[1]]
            b = _own_sector(market, key[1])
            used[b] = used.get(b, 0.0) + (t - q) * px[key[1]]
            tgt[key], binding[key] = int(t), "resize"
            resized.append(key)
        sequence += resized
        # new names, best first
        for t in lists:
            stopped: str | None = "trend" if t.strategy in filtered else None
            for sym in t.weights:
                key = (t.strategy, sym)
                if key not in listed or key in cur:
                    continue
                if stopped is None and names >= lim.max_names:
                    stopped = "max_names"
                if stopped is not None:
                    binding[key] = stopped
                    skipped.append(Skip(*key, stopped))
                    continue
                n = size(key)
                if n is None:
                    binding[key] = "no_data"
                    skipped.append(Skip(*key, "no_data", "price or ATR(20) missing"))
                    continue
                inst = market[sym]
                if n < 1:
                    binding[key] = "unaffordable"
                    skipped.append(Skip(*key, "unaffordable", "the size is below one share"))
                    continue
                p = float(inst.px)
                room = fits(p)
                if room < 1:
                    stopped = binding[key] = stop_reason(p)
                    skipped.append(Skip(*key, stopped))
                    continue
                q = min(n, room, sector_room(sym, p))
                if q < 1:  # its sector is full: the next name may fit
                    binding[key] = "sector"
                    skipped.append(Skip(*key, "sector"))
                    continue
                why = "vol" if q == n else stop_reason(p) if q == room else "sector"
                exposure += q * p
                if money is not None:
                    money -= q * p
                b = _own_sector(market, sym)
                used[b] = used.get(b, 0.0) + q * p
                if sym not in names_held:
                    names_held.add(sym)
                    names += 1
                px[sym] = p
                binding[key] = why
                if inst.can_open:
                    tgt[key] = int(q)
                    sequence.append(key)
                else:  # sized and counted as the harness does, then refused at the fill
                    binding[key] = "refused"
                    skipped.append(Skip(*key, "refused", "no new position in this name now"))
                if q == room and room < n:
                    stopped = why  # cut to the room left, and buying stops there
        guarded = self._guards(tgt, cur, fixed, sequence, market, px, eq, budgets, active, other_exposure)
        for key, why in guarded.items():
            binding[key] = f"guard:{why}"
        orders = {k: tgt[k] - cur.get(k, 0) for k in sequence if tgt[k] != cur.get(k, 0)}
        book = {**fixed, **tgt}
        return Plan(targets=dict(sorted(tgt.items())), orders=orders, binding=dict(sorted(binding.items())),
                    skipped=skipped, exposure=exposures(book, market, other_exposure),
                    sequence=[k for k in sequence if k in orders], guarded=guarded)

    def _guards(self, tgt: dict[Key, int], cur, fixed, sequence, market, px, eq, budgets, active,
                other_exposure) -> dict[Key, str]:
        """Outer guards of the ranked fill: while the finished book breaks a book cap, the last-ranked purchase
        that adds to the breach is cut by the shares that clear it (never below the current position). A breach
        no purchase adds to (prices moved) is left to the sells; the caps never force a sale here."""
        guarded: dict[Key, str] = {}
        lim, tol = self.limits, 1e-9 * eq

        def gap_ps(sym: str) -> float:
            inst = market[sym]
            return gap_loss_ps(px[sym], inst.atr20 if _ok(inst.atr20) else None)

        caps = [("gross", lambda k: True, px.__getitem__,
                 eq * min(lim.max_gross_pct, 100 - lim.min_cash_pct) / 100 - max(other_exposure, 0.0)),
                ("overnight", lambda k: True, px.__getitem__, eq * lim.max_overnight_pct / 100),
                ("gap", lambda k: True, gap_ps, eq * lim.max_gap_loss_pct / 100)]
        caps += [("name_cap", lambda k, s=s: k[1] == s, px.__getitem__, eq * lim.max_name_pct / 100)
                 for s in sorted({s for (_, s) in {**fixed, **tgt}})]
        caps += [("budget", lambda k, n=n: k[0] == n, px.__getitem__, eq * float(p) / 100)
                 for n, p in sorted((budgets or {}).items()) if n in active]
        for name, covers, per_share, room in caps:
            for key in reversed(sequence):
                excess = sum(q * per_share(k[1]) for k, q in {**fixed, **tgt}.items() if q > 0 and covers(k)) - room
                if excess <= tol:
                    break
                grow = tgt[key] - cur.get(key, 0)
                if grow <= 0 or not covers(key):
                    continue
                cut = min(grow, math.ceil(excess / per_share(key[1]) - 1e-9))
                tgt[key] -= cut
                guarded.setdefault(key, name)
        return guarded

    def _fits(self, book: dict[Key, int], market, px, eq, budgets, active, other_exposure) -> bool:
        lim, tol = self.limits, 1e-9 * eq
        ex = exposures(book, market, other_exposure)
        by_sym: dict[str, float] = {}
        by_strat: dict[str, float] = {}
        for (n, s), q in book.items():
            by_sym[s] = by_sym.get(s, 0.0) + q * px[s]
            by_strat[n] = by_strat.get(n, 0.0) + q * px[s]
        return (ex.gross <= eq * min(lim.max_gross_pct, 100 - lim.min_cash_pct) / 100 + tol
                and ex.overnight <= eq * lim.max_overnight_pct / 100 + tol
                and all(v <= eq * lim.max_sector_pct / 100 + tol for v in ex.sectors.values())
                and ex.gap_loss <= eq * lim.max_gap_loss_pct / 100 + tol
                and all(v <= eq * lim.max_name_pct / 100 + tol for v in by_sym.values())
                and all(by_strat.get(n, 0.0) <= eq * float(p) / 100 + tol
                        for n, p in (budgets or {}).items() if n in active))
