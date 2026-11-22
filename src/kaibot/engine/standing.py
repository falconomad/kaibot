"""Standing targets (M10 1.19, #444; engine audit, "Six innovations", one): what the latest decision still owes.

The latest decision's targets stay due until they are met or replaced by a newer decision. One record per decision
of a rule says where it stands, read from the journal alone (`md_decision`, `md_send`), so a late start, a missed
send window and an order the venue let expire all read the same record and heal by the same step of the book's loop
(engine/book_loop.py: `send`):

    undecided   the session has no decision record            -> the decision is taken (LiveDaily.catch_up)
    due         decided, its plan never sent                   -> the plan is built and sent (LiveDaily._plan)
    sent        sent, buys still short of the plan's shares    -> the short buys are carried (LiveDaily._carry)
    met         sent, nothing short
    replaced    a newer decision with a target list exists     -> never sent, never carried again

This module only reads. It is the one place that says which decision is in force and whether its plan went out:
`LiveDaily._latest`, `_sent_plan` and `_unsent` (M9 1.3 and 1.4, PRs #517 and #518) are now views of this record,
entry for entry what they computed before, and the heartbeat shows it (`multi_day.rules.<rule>.standing`). The
rules by which a short buy is carried or left out (trend_momentum Amendments 11 to 13 and 20) are not here and are
not changed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

UNDECIDED, DUE, SENT, MET, REPLACED = "undecided", "due", "sent", "met", "replaced"
BUYS = ("buy", "resize")


@dataclass(frozen=True)
class Standing:
    """One decision of one rule, of one run, and what became of it."""

    rule: str
    run_id: int
    decision: Mapping  # the `md_decision` entry: session, fill_session, target, held, late
    send: Mapping | None = None  # the `md_send` that holds its plan: of its fill session, or the late one
    replaced_by: str | None = None  # the session of a newer decision with a target list
    _plan: tuple = field(default=(), repr=False)

    @property
    def decided(self) -> str:
        return str(self.decision["session"])

    @property
    def fill_session(self) -> str:
        return str(self.decision["fill_session"])

    @property
    def sent_on(self) -> str | None:
        return str(self.send["session"]) if self.send is not None else None

    @property
    def late_of(self) -> str | None:
        return (self.send or {}).get("late_of")

    def plan(self) -> list[tuple[str, int, str]]:
        """(symbol, target shares, purpose) of the plan as sent, with the buys a late send left out and still owes
        (`late.due`, M9 1.4) after them. Empty while nothing was sent."""
        if self.send is None:
            return []
        out = [(s, int(q), p) for s, q, p, _ in self.send.get("targets") or []]
        return out + [(s, int(q), p) for s, q, p in (self.send.get("late") or {}).get("due") or []]

    def short(self, held: Mapping[str, float]) -> dict[str, float]:
        """The buys still short of their target: symbol -> shares missing, against the shares held now."""
        out: dict[str, float] = {}
        for s, q, p in self.plan():
            if p in BUYS and q > float(held.get(s, 0.0)):
                out[s] = max(out.get(s, 0.0), q - float(held.get(s, 0.0)))
        return out

    def state(self, held: Mapping[str, float] | None = None) -> str:
        if self.replaced_by is not None:
            return REPLACED
        if self.send is None:
            return DUE
        return SENT if held is None or self.short(held) else MET

    def summary(self, held: Mapping[str, float] | None = None) -> dict:
        """The heartbeat's block: plain values only."""
        out = {"decided": self.decided, "fill_session": self.fill_session, "state": self.state(held),
               "sent_on": self.sent_on}
        if self.late_of:
            out["late_of"] = self.late_of
        if held is not None and self.send is not None:
            out["short"] = sorted(self.short(held))
        if self.replaced_by:
            out["replaced_by"] = self.replaced_by
        return out


def holds_plan(send: Mapping, decision: Mapping) -> bool:
    """Is `send` the journalled send of `decision`'s plan? The `md_send` of its fill session that holds the
    builder's plan (`equity`; a send of that day without it sent only pending sales, the view was gone), or the
    late one (`late_of`, M9 1.4)."""
    return send.get("late_of") == decision["fill_session"] or (
        send.get("session") == decision["fill_session"] and "equity" in send)


def fold(decisions: Iterable[Mapping], sends: Iterable[Mapping], rule: str, run_id: int,
         before: str | None = None) -> list[Standing]:
    """Every decision of `rule` in run `run_id` that has a target list (and, with `before`, a fill session before
    that session), oldest first, each with its plan's send and the newer decision that replaced it. Journal order
    decides: the last matching entry wins, as the lookups this replaces read it."""
    mine = [d for d in decisions if d.get("run_id") == run_id and d.get("strategy") == rule
            and d.get("target") is not None and (before is None or d.get("fill_session") < before)]
    plans = [b for b in sends if b.get("strategy") == rule and b.get("run_id") == run_id]
    out = []
    for i, d in enumerate(mine):
        send = None
        for b in plans:
            if holds_plan(b, d):
                send = b
        nxt = str(mine[i + 1]["session"]) if i + 1 < len(mine) else None
        out.append(Standing(rule, run_id, d, send, nxt))
    return out


def latest(decisions: Iterable[Mapping], sends: Iterable[Mapping], rule: str, run_id: int,
           before: str | None = None) -> Standing | None:
    """The decision in force: the latest one with a target list. Never an older one: the newer one replaced it."""
    mine = None
    for d in decisions:
        if d.get("run_id") == run_id and d.get("strategy") == rule and d.get("target") is not None \
                and (before is None or d.get("fill_session") < before):
            mine = d
    if mine is None:
        return None
    send = None
    for b in sends:
        if b.get("strategy") == rule and b.get("run_id") == run_id and holds_plan(b, mine):
            send = b
    return Standing(rule, run_id, mine, send)
