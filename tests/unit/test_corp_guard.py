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
    # QA-Ops (#271): a guarded action with no readable date is kept (the stock is not bought), one with no symbol
    # cannot be placed
    undated = {"corporate_actions": {"spin_offs": [{"source_symbol": "X"}]}}
    assert cg.parse(undated) == [cg.Announced("X", "spin-off", "")]
    assert cg.parse({"corporate_actions": {"spin_offs": [{"ex_date": "2026-12-01"}]}}) == []
    with pytest.raises(ValueError):
        cg.parse({"corporate_actions": ["not", "an", "object"]})


def test_window_is_at_least_35_sessions_from_the_fill_session():
    a, b = cg.window(date(2026, 11, 18))
    assert a == date(2026, 11, 18)
    # QA-Ops (#271): 35 NYSE sessions after the fill session. 37 weekdays (8 Jan 2027) are only 34 sessions here:
    # Thanksgiving, Christmas and New Year's Day are all closed
    assert b == date(2027, 1, 11)


def test_lookup_finds_actions_in_the_window_only_and_reads_each_symbol_once_per_evening():
    far = {"forward_splits": [{"symbol": "AAA", "ex_date": "2027-03-01"}, {"symbol": "GGG", "ex_date": "2026-11-30"}]}
    feed = Feed(far)
    g = cg.CorporateActionGuard(get=feed)
    got, why = g.lookup(date(2026, 11, 17), date(2026, 11, 18), ["AAA", "GGG", "HHH"])
    assert why is None and set(got) == {"GGG"} and got["GGG"].on == "2026-11-30"
    assert feed.calls[0][0] == "/v1/corporate-actions"
    p = feed.calls[0][1]
    assert p["start"] == "2026-11-18" and p["end"] == "2027-01-11" and "spin_off" in p["types"].split(",")
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


# ---------------------------------------------------------------- QA-Ops second key (#271)


@pytest.mark.parametrize("fill", [date(2026, 11, 18), date(2026, 12, 23), date(2027, 3, 15), date(2027, 6, 30)])
def test_the_window_always_reaches_35_sessions_after_the_fill_session(fill):
    from kaibot.ingest import nyse_sessions

    a, b = cg.window(fill)
    assert a == fill
    assert len([d for d in nyse_sessions.sessions(fill, b) if d > fill]) >= cg.GUARD_SESSIONS


@pytest.mark.parametrize("answer", [None, {}, [], "<html>bad gateway</html>", {"message": "forbidden"},
                                    {"corporate_actions": ["x"]}])
def test_a_garbled_answer_is_a_failed_read_never_nothing_announced(answer):
    g = cg.CorporateActionGuard(get=lambda path, params: answer)
    got, why = g.lookup(date(2026, 11, 17), date(2026, 11, 18), ["AAA"])
    assert got == {} and why is not None and "read failed" in why
    assert g.known(date(2026, 11, 17), ["AAA"]) == {}  # nothing cached: the next read asks again


def test_an_empty_answer_of_alpacas_shape_means_nothing_announced():
    g = cg.CorporateActionGuard(get=lambda path, params: {"corporate_actions": {}, "next_page_token": None})
    assert g.lookup(date(2026, 11, 17), date(2026, 11, 18), ["AAA"]) == ({}, None)


def test_an_announced_action_without_a_date_blocks_the_buy():
    g = cg.CorporateActionGuard(get=Feed({"cash_mergers": [{"acquiree_symbol": "DDD", "acquirer_symbol": "BIG"}]}))
    got, why = g.lookup(date(2026, 11, 17), date(2026, 11, 18), ["DDD", "AAA"])
    assert why is None and set(got) == {"DDD"}
    assert got["DDD"].skip_text() == "skipped: corporate action on a date not given yet"


def test_a_slow_read_stops_at_its_deadline_and_counts_as_failed():
    t = [0.0]

    def slow(path, params):
        t[0] += 20.0  # each page takes 20 s
        return {"corporate_actions": {}, "next_page_token": "more"}

    g = cg.CorporateActionGuard(get=slow, clock=lambda: t[0])
    got, why = g.lookup(date(2026, 11, 17), date(2026, 11, 18), ["AAA"])
    assert got == {} and "longer than 25 s" in why and t[0] <= 40.0


def test_the_engines_corporate_action_reads_are_bounded_like_an_order_call():
    """QA-Ops (#275): the guard's and the session start's reads (RecentActions) run in the engine's loop. Both get
    the market-data client with the order calls' bounds (6 s an attempt, 20 s in all), never the plain one (30 s an
    attempt, no deadline); the plain client itself is left as it is."""
    from kaibot.broker.alpaca import ORDER_DEADLINE_S, ORDER_TIMEOUT_S
    from kaibot.engine import __main__ as entry
    from kaibot.engine.alpaca import AlpacaData

    data = AlpacaData("k", "s")
    seen = []
    get = entry.bounded_data_get(data)
    http = next(c.cell_contents for c in get.__closure__ if hasattr(c.cell_contents, "deadline_s"))
    assert (http.timeout_s, http.deadline_s) == (ORDER_TIMEOUT_S, ORDER_DEADLINE_S)
    assert data.http.deadline_s is None and data.http.timeout_s == 30.0  # the plain client is unchanged
    http.call = lambda method, url, params=None, body=None: seen.append((method, url)) or {"corporate_actions": {}}
    assert get("/v1/corporate-actions", {}) == {"corporate_actions": {}} and seen[0][0] == "GET"
    assert seen[0][1].startswith("https://data.alpaca.markets/")
