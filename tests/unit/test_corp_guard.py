"""Live-safety exclusion (#266, trend_momentum.md Amendment 6): announced corporate actions read from a fake
Alpaca feed (GET /v1/corporate-actions)."""

from __future__ import annotations

from datetime import date

import pytest

from kaibot.engine import corp_guard as cg


class Feed:
    """A fake /v1/corporate-actions: answers with `rows`, filtered by the request's symbols; `fail` raises."""

    def __init__(self, block: dict, fail: bool = False) -> None:
        self.block, self.fail, self.calls = block, fail, []

    def __call__(self, path: str, params: dict) -> dict:
        self.calls.append((path, dict(params)))
        if self.fail:
            raise OSError("network down")
        syms = set(params["symbols"].split(","))
        keys = {"symbol", "source_symbol", "acquiree_symbol", "old_symbol"}
        return {"corporate_actions": {k: [r for r in v if syms & {r.get(f) for f in keys}]
                                      for k, v in self.block.items()}, "next_page_token": None}


BLOCK = {
    "forward_splits": [{"symbol": "AAA", "ex_date": "2026-12-01", "new_rate": 2, "old_rate": 1}],
    "reverse_splits": [{"symbol": "BBB", "ex_date": "2026-11-19", "new_rate": 1, "old_rate": 10}],
    "spin_offs": [{"source_symbol": "CCC", "new_symbol": "CCCN", "ex_date": "2026-12-10"}],
    "cash_mergers": [{"acquiree_symbol": "DDD", "acquirer_symbol": "BIG", "effective_date": "2026-11-25"}],
    "name_changes": [{"old_symbol": "EEE", "new_symbol": "EEF", "process_date": "2026-11-20"}],
    "cash_dividends": [{"symbol": "FFF", "ex_date": "2026-11-20", "rate": 0.5}],  # not this guard's business
    "forward_splits_far": [],
}


def test_parse_reads_each_kind_and_ignores_dividends_and_unknown_lists():
    got = {a.symbol: (a.action, a.on) for a in cg.parse({"corporate_actions": BLOCK})}
    assert got == {"AAA": ("split", "2026-12-01"), "BBB": ("reverse split", "2026-11-19"),
                   "CCC": ("spin-off", "2026-12-10"), "DDD": ("cash merger", "2026-11-25"),
                   "EEE": ("symbol change", "2026-11-20")}
    assert cg.parse({"corporate_actions": {"spin_offs": [{"source_symbol": "X"}]}}) == []  # no date: ignored
    with pytest.raises(ValueError):
        cg.parse({"corporate_actions": ["not", "an", "object"]})


def test_window_is_at_least_35_sessions_from_the_fill_session():
    a, b = cg.window(date(2026, 11, 18))
    assert a == date(2026, 11, 18)
    assert b == date(2027, 1, 8)  # 37 weekdays on: 35 sessions even with Thanksgiving and Christmas closed


def test_lookup_finds_actions_in_the_window_only_and_reads_each_symbol_once_per_evening():
    far = {"forward_splits": [{"symbol": "AAA", "ex_date": "2027-03-01"}, {"symbol": "GGG", "ex_date": "2026-11-30"}]}
    feed = Feed(far)
    g = cg.CorporateActionGuard(get=feed)
    got, why = g.lookup(date(2026, 11, 17), date(2026, 11, 18), ["AAA", "GGG", "HHH"])
    assert why is None and set(got) == {"GGG"} and got["GGG"].on == "2026-11-30"
    assert feed.calls[0][0] == "/v1/corporate-actions"
    p = feed.calls[0][1]
    assert p["start"] == "2026-11-18" and p["end"] == "2027-01-08" and "spin_off" in p["types"].split(",")
    g.lookup(date(2026, 11, 17), date(2026, 11, 18), ["GGG", "AAA"])
    assert len(feed.calls) == 1  # cached for the evening
    g.lookup(date(2026, 11, 18), date(2026, 11, 19), ["GGG"])
    assert len(feed.calls) == 2  # a new evening reads again


def test_a_failed_read_is_reported_and_not_cached():
    feed = Feed(BLOCK, fail=True)
    g = cg.CorporateActionGuard(get=feed)
    got, why = g.lookup(date(2026, 11, 17), date(2026, 11, 18), ["AAA"])
    assert got == {} and "corporate-actions read failed" in why
    feed.fail = False
    got, why = g.lookup(date(2026, 11, 17), date(2026, 11, 18), ["AAA"])
    assert why is None and got["AAA"].action == "split"


def test_texts():
    a = cg.Announced("BBB", "reverse split", "2026-11-19")
    assert a.note() == "BBB: reverse split on 2026-11-19 — the engine does not handle this yet, check before then"
    assert a.skip_text() == "skipped: corporate action on 2026-11-19"
