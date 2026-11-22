"""Reconciliation of the ledger with the venue (S78, issue #118; audit D8): quantity per symbol and cash, from the
account's own records, at start-up and periodically. The engine refuses to ADD risk on a difference it cannot
explain; it never refuses to take risk off.

`compare` is pure. What it checks:
- quantity: the ledger's shares per symbol (all sleeves) against the broker's positions;
- fills: every FILL activity is booked once to an order the manager knows (`OrderManager.foreign`), and no order
  reports more filled shares than its booked activities (`unbooked`: the activity has not arrived yet);
- cash: the broker's cash change since the run began against the ledger's, plus dividends accrued at the ex-date
  and not yet paid (receivables).

Tolerance (cash): max(CASH_TOLERANCE_USD, CASH_TOLERANCE_PCT % of equity), $1 or 0.05%, $50 at $100k. It absorbs
what the ledger does not model to the cent: a regulatory fee taken and not yet posted (Alpaca's FEE activities are
booked at the next session start, #238), cash in lieu and dividend amounts rounded by the broker, and a dividend's
withholding. Quantity has no tolerance.

What is refused while a difference is open (`Reconciler.allows`):
- a buy in a symbol with a share, fill or unbooked difference, and every buy while cash differs;
- a sale of more shares than min(ledger, broker) in a symbol whose shares differ (it could sell shares that are
  not there). A smaller sale only reduces exposure and goes out;
- never: exits, flattens, protective stops, cancels (purposes in `NEVER_BLOCKED`).

Resolving. A difference heals by itself when the next check finds none (a late activity). A real, persistent one
(an unmodelled fee, a broker correction) is accepted by the owner, with a reason: `Reconciler.resolve(reason)`
journals the differences of the last check as ACCEPTED OFFSETS (`reconcile_resolved`). Every later check, and every
restart (folded from the journal), subtracts them; the same difference stays accepted, a CHANGED one (beyond the
tolerance around the accepted value) is a new difference and blocks again.

Edge cases (issue #179):
- a difference that has HEALED is never accepted: a clean check that follows a journalled difference is journalled
  too, so the owner's command sees that the last check is clean and refuses; the engine's command reads the venue
  again first and accepts only the differences still there at the value the owner was shown (a healed or a changed
  one is refused: the owner looks again). Otherwise a later recurrence of the same amount would pass unseen;
- accepted offsets EXPIRE at the next session start, where they are re-evaluated: the session-start check (and
  the start-up check) keeps an offset only while its difference is still there at the accepted value; one that
  healed or changed expires (`reconcile_expired`, journalled) and no longer covers anything. A persistent,
  unchanged difference stays accepted from session to session without a daily re-acceptance; a failed read
  expires nothing (the read fails closed by itself).

The owner's command: `python -m kaibot.engine.reconcile --accept "reason"` (accepts the last check's differences
in the engine's journal; `--status` shows them). From the dashboard (M9 1.14, #439; migration 0037):
`kb_command('accept_reconcile', {"reason": ..., "shown": [{"kind", "symbol", "offset"}, ...]})`, applied by the
engine like `pause` (`ACCEPT_COMMAND`). `shown` is what the owner's screen displayed when they confirmed (the
heartbeat's `reconcile.diffs`, `Reconciler.status`): `resolve(..., shown=...)` refuses unless it is exactly the set
of acceptable differences the engine has now, at the same values, so a tap never accepts a difference that
appeared or changed after the screen was drawn.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

CASH_TOLERANCE_USD, CASH_TOLERANCE_PCT = 1.0, 0.05
QTY_EPS = 1e-6
NEVER_BLOCKED = ("stop", "cancel", "exit", "flatten")
ACCEPT_COMMAND = "accept_reconcile"
SESSION_CHECKS = ("startup", "session_start")  # checks at which accepted offsets are re-evaluated (and expire)
# QA-Ops (#522): the fill differences name one stock and say nothing about the rest of the book, so they stop buys
# in that stock only (`Difference.blocks`); every other difference stops every buy (`Reconciler.blocks_every_buy`)
SYMBOL_ONLY = ("foreign_fill", "unbooked")
Key = tuple[str, str | None]


@dataclass(frozen=True)
class Difference:
    kind: str  # quantity | foreign_fill | unbooked | cash
    symbol: str | None
    detail: dict

    @property
    def key(self) -> Key:
        return (self.kind, self.symbol)

    @property
    def offset(self) -> float | None:
        """The amount an owner accepts: broker minus ledger (quantity: shares; cash: dollars)."""
        if self.kind == "quantity":
            return float(self.detail["broker"]) - float(self.detail["ledger"])
        if self.kind == "cash":
            return float(self.detail["broker_change"]) - float(self.detail["ledger_change"])
        return None

    def blocks(self, symbol: str, side: str, purpose: str, qty: float | None = None) -> bool:
        if purpose in NEVER_BLOCKED:
            return False
        if self.kind == "cash":
            return side == "buy"
        if self.symbol != symbol:
            return False
        if side == "buy":
            return True
        if self.kind == "quantity" and qty is not None:  # a sale that only reduces exposure goes out
            return float(qty) > min(float(self.detail["ledger"]), float(self.detail["broker"])) + QTY_EPS
        return False


@dataclass
class Reconciliation:
    diffs: list[Difference] = field(default_factory=list)
    accepted: list[Difference] = field(default_factory=list)  # differences an owner resolution covers

    @property
    def ok(self) -> bool:
        return not self.diffs

    def summary(self) -> dict:
        return {"ok": self.ok, "diffs": [{"kind": d.kind, "symbol": d.symbol, **d.detail} for d in self.diffs],
                "accepted": [{"kind": d.kind, "symbol": d.symbol, **d.detail} for d in self.accepted]}


def cash_tolerance(equity: float | None) -> float:
    return max(CASH_TOLERANCE_USD, CASH_TOLERANCE_PCT / 100 * abs(equity or 0.0))


def compare(ledger_qty: Mapping[str, float], broker_positions: Mapping[str, dict], *, om=None,
            ledger_cash: float | None = None, ledger_cash0: float | None = None, broker_cash: float | None = None,
            broker_cash0: float | None = None, receivable: float = 0.0, equity: float | None = None,
            accepted: Mapping[Key, float] | None = None, accepted_fills=()) -> Reconciliation:
    """`accepted`: owner-accepted offsets per (kind, symbol), broker minus ledger. `accepted_fills`: the activity
    ids of the foreign fills the owner accepted (`accepted_foreign`)."""
    out = Reconciliation()
    acc = accepted or {}
    tol = cash_tolerance(equity)

    def add(d: Difference, band: float) -> None:
        off = acc.get(d.key)
        if off is not None and d.offset is not None and abs(d.offset - off) <= band:
            out.accepted.append(d)
        else:
            out.diffs.append(d)

    bq = {s: float(p.get("qty") or 0) for s, p in broker_positions.items()}
    for s in sorted(set(bq) | {s for s, q in ledger_qty.items() if abs(q) > QTY_EPS}):
        mine, theirs = float(ledger_qty.get(s, 0.0)), bq.get(s, 0.0)
        if abs(mine - theirs) > QTY_EPS:
            add(Difference("quantity", s, {"ledger": mine, "broker": theirs}), QTY_EPS)
    if om is not None:
        for aid, a in sorted(om.foreign.items()):
            d = Difference("foreign_fill", a.get("symbol"), {"activity": aid, "order_id": a.get("order_id"),
                                                             "qty": a.get("qty"), "side": a.get("side")})
            (out.accepted if str(aid) in accepted_fills else out.diffs).append(d)
        for r in sorted(om.orders.values(), key=lambda r: r.coid):
            if r.cum > r.booked + QTY_EPS:
                out.diffs.append(Difference("unbooked", r.symbol, {"coid": r.coid, "venue": r.cum,
                                                                   "booked": r.booked}))
    if None not in (ledger_cash, ledger_cash0, broker_cash, broker_cash0):
        theirs = broker_cash - broker_cash0
        mine = ledger_cash - ledger_cash0 - receivable
        if abs(theirs - mine) > tol:
            add(Difference("cash", None, {"ledger_change": round(mine, 2), "broker_change": round(theirs, 2),
                                          "receivable": round(receivable, 2), "tolerance": tol}), tol)
    return out


def accepted_offsets(journal) -> dict[Key, float]:
    """The owner's accepted offsets, folded from the journal (the latest resolution of a key wins)."""
    out: dict[Key, float] = {}
    if journal is None:
        return out
    for _, _, kind, b in journal.entries(kinds=("reconcile_resolved", "reconcile_expired")):
        for d in b.get("offsets") or []:
            if kind == "reconcile_expired":
                out.pop((d["kind"], d.get("symbol")), None)
            else:
                out[(d["kind"], d.get("symbol"))] = float(d["offset"])
    return out


def accepted_foreign(journal) -> set[str]:
    """QA-Ops (#522): the activity ids of the foreign fills an owner's acceptance covered (`reconcile_resolved`,
    `foreign`). A fill is one event that never changes, so its acceptance does not expire; a later fill on an
    unknown order has another id and is a new difference."""
    if journal is None:
        return set()
    return {str(i) for *_, b in journal.entries(kinds=("reconcile_resolved",)) for i in b.get("foreign") or []}


def _foreign_ids(diffs) -> set[str]:
    return {str(d.detail.get("activity")) for d in diffs if d.kind == "foreign_fill"}


def _same(a: Difference, b: Difference) -> bool:
    """The same difference at the same value (cash: within the tolerance; quantity: exactly)."""
    if a.key != b.key or a.offset is None or b.offset is None:
        return False
    band = float(b.detail.get("tolerance") or a.detail.get("tolerance") or 0.0) if a.kind == "cash" else QTY_EPS
    return abs(a.offset - b.offset) <= band


def _shown_mismatch(have: list[Difference], shown) -> str | None:
    """None when `shown` (the owner's screen: [{"kind", "symbol", "offset"}]) is exactly `have` at the same values;
    else why not, in the owner's words."""
    seen: dict[Key, float] = {}
    try:
        for x in shown:
            seen[(str(x["kind"]), x.get("symbol"))] = float(x["offset"])
    except (TypeError, KeyError, ValueError, AttributeError):
        return "the command does not say which differences were shown"
    if not seen:
        return "the command does not say which differences were shown"
    if not have:
        return "the difference has healed"
    for d in have:
        band = float(d.detail.get("tolerance") or 0.0) if d.kind == "cash" else QTY_EPS
        if d.key not in seen or abs(seen[d.key] - float(d.offset)) > band:
            return "the difference has changed since it was shown: check again and accept the new value"
    if set(seen) - {d.key for d in have}:
        return "a difference that was shown has healed: check again and accept what is left"
    return None


def _diff(d: dict) -> Difference:
    return Difference(d["kind"], d.get("symbol"), {k: v for k, v in d.items() if k not in ("kind", "symbol")})


def resolve_last(journal, reason: str, by: str = "owner") -> list[dict]:
    """Accept the differences of the last journalled check (what the owner was shown), with a reason."""
    if not str(reason or "").strip():
        raise ValueError("a resolution needs a reason")
    last = journal.entries(kinds=("reconcile",))
    if not last:
        raise ValueError("nothing to accept: no reconciliation check in the journal")
    diffs = last[-1][3].get("diffs") or []
    if not diffs:
        raise ValueError("nothing to accept: the last check found no difference (it has healed)")
    offsets = [{"kind": x.kind, "symbol": x.symbol, "offset": x.offset} for x in map(_diff, diffs)
               if x.offset is not None]
    foreign = sorted(_foreign_ids(map(_diff, diffs)))  # QA-Ops (#522): the fills on unknown orders that were shown
    if not offsets and not foreign:
        raise ValueError("nothing to accept: these differences cannot be accepted (" +
                         ", ".join(sorted({d["kind"] for d in diffs})) + "); they heal only when the books agree")
    journal.append("reconcile_resolved", {"reason": str(reason).strip(), "by": by, "offsets": offsets,
                                          "foreign": foreign, "diffs": diffs})
    return offsets + [{"kind": "foreign_fill", "activity": i} for i in foreign]


class Reconciler:
    """`check` reads the venue through `read` (a callable returning the keyword arguments of `compare`), so the
    same object serves the daily runner and the live engine. Checks with differences, and every resolution, are
    journalled; accepted offsets are folded from the journal at every check (a resolution written by the owner's
    command while the engine runs counts at the next check)."""

    def __init__(self, read: Callable[[], dict], journal=None) -> None:
        self.read, self.journal = read, journal
        self.last = Reconciliation()
        self.checked = False
        self._mem: dict[Key, float] = {}  # accepted offsets when there is no journal
        self._mem_fills: set[str] = set()  # accepted foreign fills when there is no journal
        self._was_dirty: bool | None = None

    @property
    def accepted(self) -> dict[Key, float]:
        return accepted_offsets(self.journal) if self.journal is not None else dict(self._mem)

    @property
    def accepted_fills(self) -> set[str]:
        return accepted_foreign(self.journal) if self.journal is not None else set(self._mem_fills)

    def _compare(self) -> Reconciliation:
        return compare(**self.read(), accepted=self.accepted, accepted_fills=self.accepted_fills)

    def blocks_every_buy(self) -> bool:
        """QA-Ops (#522): a difference of the last check stops every buy (shares, cash, a failed read). False when
        only fill differences are open: those stop buys in their own stock (`allows`)."""
        return any(d.kind not in SYMBOL_ONLY or not d.symbol for d in self.last.diffs)

    def check(self, when: str = "periodic") -> Reconciliation:
        """`when` in SESSION_CHECKS: accepted offsets not matched by a difference of this check expire."""
        acc = self.accepted
        self.last = compare(**self.read(), accepted=acc, accepted_fills=self.accepted_fills)
        self.checked = True
        if when in SESSION_CHECKS:
            keep = {d.key for d in self.last.accepted}
            gone = [{"kind": k[0], "symbol": k[1], "offset": v} for k, v in sorted(acc.items(), key=str)
                    if k not in keep]
            if gone:
                if self.journal is not None:
                    self.journal.append("reconcile_expired", {"when": when, "offsets": gone,
                                                              "why": "healed or changed at the session start"})
                for g in gone:
                    self._mem.pop((g["kind"], g["symbol"]), None)
        if self.journal is not None and (self.last.diffs or when == "startup" or self._dirty()):
            self.journal.append("reconcile", {"when": when, **self.last.summary()})
            self._was_dirty = bool(self.last.diffs)
        return self.last

    def _dirty(self) -> bool:
        """The last journalled check had differences (a clean check after it is journalled: it healed)."""
        if self._was_dirty is None:
            last = self.journal.entries(kinds=("reconcile",))
            self._was_dirty = bool(last and last[-1][3].get("diffs"))
        return self._was_dirty

    def allows(self, symbol: str, side: str, purpose: str = "target", qty: float | None = None) -> str | None:
        """None when an order may go out; else why not."""
        if purpose in NEVER_BLOCKED:
            return None
        if not self.checked:
            return None if side == "sell" else "not reconciled yet"
        for d in self.last.diffs:
            if d.blocks(symbol, side, purpose, qty):
                return f"unresolved {d.kind} difference" + (f" in {d.symbol}" if d.symbol else "")
        return None

    def status(self, limit: int = 20) -> dict:
        """The last check for the heartbeat (the dashboard shows it before the owner accepts, #439). Each difference
        carries `offset` (broker minus ledger; None: it cannot be accepted and heals only when the books agree)."""
        out: dict = {"ok": not self.last.diffs, "checked": self.checked}
        if self.last.diffs:
            out["diffs"] = [{"kind": d.kind, "symbol": d.symbol, **d.detail, "offset": d.offset}
                            for d in self.last.diffs[:limit]]
            if len(self.last.diffs) > limit:
                out["more"] = len(self.last.diffs) - limit
        return out

    def resolve(self, reason: str, by: str = "owner", shown=None) -> None:
        """The owner looked at the differences and accepts them as they are (journalled with the reason).
        `shown` (the dashboard's command): what the owner's screen displayed; anything else is refused.
        QA-Ops (#522): the fills on unknown orders (`foreign_fill`) of the last check are accepted with it, each
        by its activity id, when they are still there; one that arrived since is a new difference. With nothing
        else open they are accepted alone (the command line; the dashboard's command needs a `shown` row)."""
        if not str(reason or "").strip():
            raise ValueError("a resolution needs a reason")
        owner = shown
        shown = [d for d in self.last.diffs if d.offset is not None]
        seen_fills = _foreign_ids(self.last.diffs)
        if owner is not None:
            why = _shown_mismatch(shown, owner)
            if why:
                raise ValueError("nothing accepted: " + why)
        if not shown and not seen_fills:
            return
        # issue #179: read the venue again; accept only what is still there at the value shown
        fresh = self._compare()
        take = [d for d in fresh.diffs if any(_same(s, d) for s in shown)]
        fills = [d for d in fresh.diffs if d.kind == "foreign_fill" and str(d.detail.get("activity")) in seen_fills]
        why = None
        if not shown:  # only fills on unknown orders were open
            if not fills:
                why = "nothing to accept: the difference has healed"
        elif not take:
            healed = not any(d.key in {s.key for s in shown} for d in fresh.diffs)
            why = "nothing to accept: the difference " + ("has healed" if healed else
                  "has changed since it was shown: check again and accept the new value")
        elif owner is not None:  # the dashboard's command is all or nothing: the owner agreed to one screen
            why = _shown_mismatch([d for d in fresh.diffs if d.offset is not None], owner)
            why = why and "nothing accepted: " + why
        if why:
            self.last, self.checked = fresh, True
            if self.journal is not None:
                self.journal.append("reconcile", {"when": "resolve", **fresh.summary()})
                self._was_dirty = bool(fresh.diffs)
            raise ValueError(why)
        if self.journal is not None:
            self.journal.append("reconcile", {"when": "resolve", **fresh.summary()})
            self._was_dirty = bool(fresh.diffs)
            self.journal.append("reconcile_resolved", {
                "reason": str(reason).strip(), "by": by, "diffs": [x for x in fresh.summary()["diffs"]
                                                                   if _diff(x).key in {d.key for d in take + fills}],
                "offsets": [{"kind": d.kind, "symbol": d.symbol, "offset": d.offset} for d in take],
                "foreign": sorted(_foreign_ids(fills))})
        else:
            self._mem.update({d.key: d.offset for d in take})
            self._mem_fills.update(_foreign_ids(fills))
        self.last = self._compare()


# ---------------------------------------------------------------- the owner's command
def main(argv: list[str] | None = None, journal=None) -> int:
    """`python -m kaibot.engine.reconcile --status | --accept "reason"` on the engine's journal."""
    ap = argparse.ArgumentParser(description="reconciliation differences: show them, or accept them with a reason")
    ap.add_argument("--journal", type=Path, default=None)
    ap.add_argument("--accept", default=None, metavar="REASON")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args(argv)
    if journal is None:
        from kaibot.engine.config import Settings
        from kaibot.engine.journal import Journal, journal_path

        journal = Journal(a.journal or journal_path(Settings.from_env().data_dir))
    if a.accept is not None:
        try:
            got = resolve_last(journal, a.accept, by="owner (command line)")
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
        print(json.dumps({"accepted": got}))
        return 0
    last = journal.entries(kinds=("reconcile",))
    print(json.dumps({"last": last[-1][3] if last else None, "accepted": [
        {"kind": k[0], "symbol": k[1], "offset": v} for k, v in accepted_offsets(journal).items()]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
