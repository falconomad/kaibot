"""Issue #310: the pre-open review in SHADOW mode on the live daily path, driven on SimBroker exactly as the parity
test drives the engine (tests/unit/test_live_daily.py). An observer only: with the shadow review on (even one that
would veto every buy) the plan, the orders and the fills are byte for byte the same as with it off; its verdicts are
journalled (`md_review_shadow`), sent as events and shown in the heartbeat. No key, no network, no spend."""

from __future__ import annotations

import json
from datetime import time

import pytest
from test_live_daily import I0, I1, POL, Harness, _at, venue_fills, venue_orders

from kaibot.agents import preopen_shadow as ps
from kaibot.agents.evalset.preopen import offline_gateway
from kaibot.agents.preopen_daily import DeterministicReviewer
from kaibot.engine import live_daily as ld
from kaibot.engine.daily import momentum_limits
from kaibot.strategies import momentum as mom

S = mom.VERSIONS["trend_momentum"]


class ShadowHarness(Harness):
    def __init__(self, *a, shadow=None, **kw):
        self._shadow = shadow
        super().__init__(*a, **kw)

    def build(self) -> None:
        super().build()
        self.live.shadow = self._shadow


class Exploding:
    """A reviewer that raises: the shadow review must only log."""

    def __call__(self, session, cands):
        raise RuntimeError("model down")


@pytest.fixture(scope="module")
def data():
    from kaibot.engine import daily_report as dr
    from kaibot.engine.daily import DailyData

    m, _ = dr.load_fixture(dr.FIXTURE)
    return DailyData.from_market(m)


def shadow(reviewer):
    return ps.ShadowDesk(reviewer, offline_gateway(), inline=True, needs_key=False)


@pytest.fixture(scope="module")
def runs(data, tmp_path_factory):
    lim = momentum_limits(S, POL, 100.0)
    out = {}
    off = Harness(data, S, ld.live_builder(S, lim), tmp_path_factory.mktemp("off"))
    off.run()
    out["off"] = off
    all_syms = set(data.symbols) if hasattr(data, "symbols") else set()
    for name, rev in (("veto_all", DeterministicReviewer(veto=all_syms or {"__none__"})),
                      ("policy", DeterministicReviewer()), ("broken", Exploding())):
        h = ShadowHarness(data, S, ld.live_builder(S, lim), tmp_path_factory.mktemp(name), shadow=shadow(rev))
        h.run()
        out[name] = h
    return out


def journal(h, skip=(ps.KIND,)):
    """Every journal entry but the shadow record, as canonical JSON (kind, body), in order."""
    return [json.dumps([kind, body], sort_keys=True, default=str)
            for _, _, kind, body in h.journal.entries() if kind not in skip]


def bodies(h, kind):
    return [b for _, _, _, b in h.journal.entries(kinds=(kind,))]


@pytest.mark.parametrize("name", ["veto_all", "policy", "broken"])
def test_plan_orders_and_fills_are_byte_for_byte_identical_with_shadow_on_and_off(runs, name):
    off, on = runs["off"], runs[name]
    assert len(venue_orders(off.broker)) > 20
    assert venue_orders(on.broker) == venue_orders(off.broker)
    assert venue_fills(on.broker) == venue_fills(off.broker)
    for kind in ("md_decision", "md_send", "md_sent", "fill"):
        a = [json.dumps(b, sort_keys=True, default=str) for b in bodies(off, kind)]
        b = [json.dumps(x, sort_keys=True, default=str) for x in bodies(on, kind)]
        assert a == b and a, kind
    assert journal(on) == journal(off)  # the whole journal, but for the shadow records
    other = [e for e in on.events if not e[0].startswith(ps.EVENT)]
    assert json.dumps(other, sort_keys=True, default=str) == json.dumps(off.events, sort_keys=True, default=str)


def test_the_verdicts_are_journalled_with_reasons_and_sent_as_events(runs):
    h = runs["veto_all"]
    recs = bodies(h, ps.KIND)
    sends = [b for b in bodies(h, "md_send")]
    assert recs and len(recs) == len(sends)  # one record per session's plan
    with_buys = [r for r in recs if r["buys"]]
    assert with_buys and all(r["status"] == "done" and r["acted"] is False for r in with_buys)
    for r in with_buys:
        assert set(r["verdicts"].values()) == {"veto"}
        assert sorted(r["would_have"]["vetoed"]) == sorted(k.split(":", 1)[1] for k in r["buys"])
    evs = [b for k, b in h.events if k == ps.EVENT]
    assert len(evs) == len(recs) and all(e["mode"] == "shadow" and e["acted"] is False for e in evs)
    assert any(e["reasons"] for e in evs)


def test_the_shadow_record_follows_the_send_and_never_comes_before_it(runs):
    h = runs["policy"]
    seqs = {}
    for seq, _, kind, body in h.journal.entries(kinds=("md_send", ps.KIND)):
        seqs.setdefault((body["strategy"], body["session"]), []).append((kind, seq))
    assert seqs and all([k for k, _ in v] == ["md_send", ps.KIND] for v in seqs.values())


def test_errors_only_log_and_are_recorded(runs):
    recs = bodies(runs["broken"], ps.KIND)
    with_buys = [r for r in recs if r["buys"]]
    assert with_buys and all(r["status"] == "failed" and "model down" in (r["error"] or "") for r in with_buys)


def test_the_heartbeat_shows_the_shadow_line(runs):
    h = runs["veto_all"]
    day = h.d.day(I1)
    st = h.live.status(day)
    assert st["preopen_review_shadow"] == "on"
    row = st["rules"][S.name]["review_shadow"]
    assert row["mode"] == "shadow" and row["status"] in ("done", "not_yet") and row["note"].startswith("shadow")
    assert "review_shadow" not in runs["off"].live.status(day)["rules"][S.name]
    assert "preopen_review_shadow" not in runs["off"].live.status(day)


def test_a_restart_does_not_review_twice(data, tmp_path):
    lim = momentum_limits(S, POL, 100.0)
    rev = DeterministicReviewer()
    h = ShadowHarness(data, S, ld.live_builder(S, lim), tmp_path, shadow=shadow(rev))
    day = data.day(I0)
    h.live.after_close(data.day(I0 - 1))
    h.clock.set(_at(day, time(9, 31)))
    h.live.send(day)
    h.live.sync()
    n = rev.calls
    h.restart()  # the same desk object survives here; a new engine would also find the journal entry
    h.live.shadow = ps.ShadowDesk(rev, offline_gateway(), inline=True, needs_key=False)
    for minute in (32, 40):
        h.clock.set(_at(day, time(9, minute)))
        h.live.send(day)
        h.live.sync()
    assert rev.calls == n and len(bodies(h, ps.KIND)) == 1


def test_the_comparison_script_reads_the_engine_journal(runs):
    from kaibot.agents import shadow_compare as sc

    h = runs["veto_all"]
    rows = sc.from_journal(h.path, horizon=5)
    assert rows and {r["verdict"] for r in rows} == {"veto"}
    assert all(r["fill_px"] for r in rows)  # nothing acted: every vetoed buy was really bought
    assert sum(r["ret"] is not None for r in rows) > 10
    assert sc.main(["--journal", str(h.path), "--json"]) == 0
