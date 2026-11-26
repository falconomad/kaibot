"""#529 end to end on the deterministic simulation: B is bought at D1's close and its FILL activity never arrives.
Without the owner, the book does not hold B on D2 and D2's decision ("hold nothing") cannot sell it there (since
audit 2, 5.13, the decision in force sells it once it is booked: test_a_buy_booked_a_day_late_is_sold_by_the_decision
_taken_meanwhile in test_dst.py). Here an owner
reads the Engine page on D2 morning (the heartbeat's reconcile block: exactly what the screen shows) and sends
`accept_reconcile` with "book it" for the unbooked fill. The engine books it from the venue's view of the order,
D2's send sells it, and every invariant holds at quiescence (ledger and venue agree, no execution left unbooked,
the book is what the latest decision wanted). A screen that no longer matches is refused and books nothing."""

from __future__ import annotations

import pytest
import world as world_mod
from scenarios import quiet
from world import D1, D2, World, et, run_scenario

from kaibot.broker.faulty import Forced
from kaibot.engine.core import Engine

NEVER = 100_000  # minutes: the activity never arrives within the run


class Owner(World):
    """The owner of the scenario, plus one look at the Engine page at `look` and an answer to what it shows."""

    look = et(D2, "10:00")
    action = "book"
    tamper = None  # a function changing the row after the screen was drawn (a stale screen)
    extra: tuple = ()  # rows of other kinds sent in the same command

    def owner(self) -> None:
        super().owner()
        if getattr(self, "_looked", False) or self.eng is None or self.clock() < self.look:
            return
        st = self.eng._recon_status()
        rows = [d for d in st.get("diffs") or [] if d.get("kind") == "unbooked" and d.get("aged")]
        if not rows:
            return
        self._looked = True
        shown = [{"kind": "unbooked", "symbol": d["symbol"], "coid": d["coid"], "venue": d["venue"],
                  "booked": d["booked"], "action": self.action} for d in rows]
        if self.tamper is not None:
            shown = [self.tamper(x) for x in shown]
        shown += list(self.extra)
        self.replica.insert("command", {"kind": "accept_reconcile", "idempotency_key": "dst-owner-unbooked",
                                        "payload": {"reason": "Alpaca never listed the fill", "shown": shown},
                                        "applied_at": None})
        self.say(f"owner: {self.action} the unbooked fill(s) {[str(x.get('coid'))[-30:] for x in shown]}")


def _run(monkeypatch, **kw):
    for k, v in kw.items():
        monkeypatch.setattr(Owner, k, v)
    monkeypatch.setattr(world_mod, "World", Owner)
    return run_scenario(quiet({D1: ("B",), D2: ()}, forced=(Forced("fill_dropped", "|B|buy", NEVER),)))


def test_the_owner_books_a_fill_whose_activity_never_came_and_the_decision_sells_it(monkeypatch):
    res = _run(monkeypatch)
    assert res.ok, res.report()
    assert any("owner: book" in t for t in res.trace)
    story = [t for t in res.trace if " journal " in t and "-B-" in t]
    assert any("activity=owner:" in t and "side=buy" in t for t in story), "\n".join(story)
    assert any("side=sell" in t and " fill " in t for t in story), "\n".join(story)


def test_a_screen_that_no_longer_matches_books_nothing(monkeypatch):
    res = _run(monkeypatch, tamper=staticmethod(lambda x: {**x, "venue": x["venue"] + 1}))
    assert any("owner: book" in t for t in res.trace)
    assert not any("activity=owner:" in t for t in res.trace)  # nothing was guessed from the stale screen
    # audit 2, 5.13: the activity comes once the world heals (D3); the decision in force does not want B and sells
    # the late booking there, so the run ends clean without the owner (it used to end holding B: #529)
    assert res.ok, res.report()
    assert any(" om_intent " in t and "side=sell" in t and "-B-" in t for t in res.trace), res.report()


@pytest.mark.parametrize("action", ["accept"])
def test_accepting_it_leaves_the_books_as_they_are(monkeypatch, action):
    res = _run(monkeypatch, action=action)
    assert any(f"owner: {action}" in t for t in res.trace)
    assert not any("activity=owner:" in t for t in res.trace)


def test_a_command_that_also_accepts_other_differences_books_nothing(monkeypatch):
    """A booking changes the books' shares and cash: the other differences are accepted afterwards, for what is
    left, never in the same command."""
    res = _run(monkeypatch, extra=({"kind": "quantity", "symbol": "B", "offset": 23.0},))
    assert any("owner: book" in t for t in res.trace)
    assert not any("activity=owner:" in t for t in res.trace)


@pytest.mark.xfail(not hasattr(Engine, "_om_unsettled"), strict=True,
                   reason="needs #533 (PR #544): without it Flatten and stop ends the run before the owner can book")
def test_flatten_and_stop_waits_for_the_owner_to_book_then_sells_and_ends(monkeypatch):
    """QA-Ops, merge-together with #544: the buy fills at 15:51, its activity never comes, the owner presses
    "Flatten and stop" at 15:53. The command waits (#533); on D2 morning the owner books the fill from what the
    screen shows; the flatten sells it and the run ends, with the ledger and the venue in agreement."""
    monkeypatch.setattr(world_mod, "World", Owner)
    res = run_scenario(quiet({D1: ("B",)}, flatten=et(D1, "15:53"), forced=(Forced("fill_dropped", "|B|buy", NEVER),)))
    assert res.ok, res.report()
    story = [t for t in res.trace if " journal " in t and "-B-" in t]
    assert any("activity=owner:" in t for t in story) and any("flatten" in t and " fill " in t for t in story)
