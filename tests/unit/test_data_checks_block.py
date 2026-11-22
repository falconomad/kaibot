"""M12 ticket 3.11 (#473), the blocking half: the minute-bar data checks block ENTRIES when KAIBOT_DATA_CHECKS_BLOCK
is on (off by default). A blocking finding on a stock blocks new positions in that stock; one on the index (the
minute feed itself) blocks every intraday entry; a check that fails blocks them too (fail closed). Sales, stops and
flattens never ask. With the switch off nothing is blocked and nothing new is read or written."""

from __future__ import annotations

import logging

import pandas as pd
import pytest
from test_data_checks import FRAMES, at, bars, live

from kaibot.engine import market as mk
from kaibot.ingest import data_checks as dc


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(mk.DATA_CHECKS_ENV, raising=False)
    monkeypatch.delenv(mk.DATA_BLOCK_ENV, raising=False)


@pytest.mark.parametrize(("value", "want"), [
    (None, frozenset()), ("", frozenset()), ("0", frozenset()), ("off", frozenset()), ("False", frozenset()),
    ("1", mk.BLOCKING_DEFAULT), ("on", mk.BLOCKING_DEFAULT), (" YES ", mk.BLOCKING_DEFAULT),
    ("bad_price", frozenset({dc.BAD_PRICE})), ("stale, bar_gap", frozenset({dc.STALE, dc.BAR_GAP})),
])
def test_the_switch_is_off_unless_set_and_names_the_checks(value, want):
    assert mk.data_block_checks({} if value is None else {mk.DATA_BLOCK_ENV: value}) == want


@pytest.mark.parametrize("value", ["bad_prices", "stale,typo", ","])
def test_an_unknown_check_name_is_an_error_at_start(value):
    with pytest.raises(ValueError, match="KAIBOT_DATA_CHECKS_BLOCK"):
        mk.data_block_checks({mk.DATA_BLOCK_ENV: value})


def test_the_default_set_leaves_out_the_bar_gap_check():
    """On IEX a quiet stock has minutes without a bar and nothing is wrong: report only unless named."""
    assert dc.BAR_GAP not in mk.BLOCKING_DEFAULT
    assert mk.BLOCKING_DEFAULT | {dc.BAR_GAP} == mk.ALL_CHECKS


def test_off_nothing_is_blocked_and_nothing_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(mk, "check_all", lambda *a, **k: pytest.fail("the checks ran with both switches off"))
    m = live(tmp_path, FRAMES)
    m.update(at(60))
    assert m.block_checks == frozenset() and m.data_block("BAD") is None and m.data_block_status() is None


def test_report_only_finds_but_does_not_block(tmp_path, monkeypatch):
    monkeypatch.setenv(mk.DATA_CHECKS_ENV, "1")
    m = live(tmp_path, FRAMES)
    m.update(at(60))
    assert [(f.symbol, f.check) for f in m.last_data_findings] == [("BAD", dc.BAD_PRICE)]
    assert m.data_block("BAD") is None


def test_on_a_bad_print_blocks_that_stock_only(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv(mk.DATA_BLOCK_ENV, "on")
    m = live(tmp_path, FRAMES)
    with caplog.at_level(logging.WARNING, logger="kaibot.engine.market"):
        m.update(at(60))
    assert m.data_block("BAD").startswith("market data: BAD bad_price")
    assert m.data_block("GOOD") is None and m.data_block("NOT_SCANNED") is None
    assert "data check (blocks entries): BAD bad_price" in caplog.text
    assert m.data_block_status() == {"blocking": sorted(mk.BLOCKING_DEFAULT), "blocked": 1, "first": ["BAD bad_price"]}


def test_a_move_the_index_does_not_share_blocks_and_clears_when_it_is_gone(tmp_path, monkeypatch):
    monkeypatch.setenv(mk.DATA_BLOCK_ENV, "on")
    frames = {"SPY": bars(70, 500.0), "XLK": bars(70, 200.0), "JUMP": bars(70, m59=108.0)}
    m = live(tmp_path, frames)
    m.update(at(60))
    assert m.data_block("JUMP").startswith("market data: JUMP unmatched_move")
    m2 = live(tmp_path, frames)  # the same bars ten minutes later: the move is out of the window
    m2.update(at(70))
    assert m2.data_block("JUMP") is None


def test_a_stale_or_empty_index_blocks_every_entry_but_not_the_stock_only_view(tmp_path, monkeypatch):
    monkeypatch.setenv(mk.DATA_BLOCK_ENV, "on")
    m = live(tmp_path, {"SPY": bars(40, 500.0), "XLK": bars(60, 200.0), "GOOD": bars(60)})
    m.update(at(60))
    why = m.data_block("GOOD")
    assert why.startswith("market data: index SPY stale")
    assert m.data_block("GOOD", index=False) is None  # the multi-day path asks about the stock alone


def test_only_the_named_checks_block(tmp_path, monkeypatch):
    monkeypatch.setenv(mk.DATA_BLOCK_ENV, "stale")
    m = live(tmp_path, FRAMES)
    m.update(at(60))
    assert m.last_data_findings and m.data_block("BAD") is None  # bad_price is found, but it is not named


def test_a_check_that_fails_blocks_every_entry_when_blocking(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv(mk.DATA_BLOCK_ENV, "on")

    def boom(*a, **k):
        raise ValueError("a bug in a check")

    monkeypatch.setattr(mk, "check_all", boom)
    m = live(tmp_path, FRAMES)
    with caplog.at_level(logging.ERROR, logger="kaibot.engine.market"):
        m.update(at(60))  # does not raise: the tick goes on
    assert m.data_block("GOOD").startswith("data checks failed (ValueError")
    assert m.data_block("GOOD", index=False) is None
    assert "every intraday entry is blocked" in caplog.text
    monkeypatch.setattr(mk, "check_all", dc.check_all)
    m.update(at(60, 30))
    assert m.data_block("GOOD") is None


def test_the_bars_the_engine_reads_are_the_same_whatever_the_switch(tmp_path, monkeypatch):
    off = live(tmp_path, FRAMES)
    off.update(at(60))
    monkeypatch.setenv(mk.DATA_BLOCK_ENV, "on")
    on = live(tmp_path, FRAMES)
    on.update(at(60))
    for s in off.today_bars:
        pd.testing.assert_frame_equal(on.today_bars[s], off.today_bars[s])


# ---------------------------------------------------------------- the engine asks; sales never do
class Market:
    def __init__(self, why):
        self.why, self.asked = why, []

    def data_block(self, symbol, index=True):
        self.asked.append((symbol, index))
        return self.why


def test_engine_data_block_asks_the_market_and_fails_closed():
    from kaibot.engine.core import Engine

    e = Engine.__new__(Engine)
    e.market = Market("market data: X bad_price (1 bar)")
    assert e.data_block("X") == "market data: X bad_price (1 bar)" and e.market.asked == [("X", True)]
    e.market = object()  # a replay's market has no checks: never blocks
    assert e.data_block("X") is None

    class Broken:
        def data_block(self, symbol, index=True):
            raise RuntimeError("boom")

    e.market = Broken()
    assert e.data_block("X") == "data checks failed (RuntimeError)"


@pytest.mark.parametrize(("side", "purpose", "blocked"), [
    ("buy", "target", True), ("sell", "target", False), ("sell", "stop", False), ("sell", "flatten", False),
    ("buy", "stop", False),
])
def test_the_multi_day_path_asks_for_buys_only_and_about_the_stock_alone(side, purpose, blocked):
    from kaibot.engine import live_daily as ld

    class Recon:
        def allows(self, *a):
            return None

    class E:
        market = Market("market data: B bad_price (1 bar)")
        recon = Recon()

        def data_block(self, symbol, index=True):
            return self.market.data_block(symbol, index)

    acc = ld.EngineAccount.__new__(ld.EngineAccount)
    acc.e = E()
    why = acc.allows("B", side, purpose)
    assert (why == "entries blocked: market data: B bad_price (1 bar)") is blocked
    assert acc.e.market.asked == ([("B", False)] if blocked else [])


def test_the_switch_is_declared_once_with_the_same_check_names():
    """M10 1.23: KAIBOT_DATA_CHECKS_BLOCK is parsed by kaibot.common.switches, with these checks."""
    from kaibot.common import switches

    s = switches.SWITCHES[mk.DATA_BLOCK_ENV]
    assert frozenset(s.choices) == mk.ALL_CHECKS and frozenset(s.choices[:-1]) == mk.BLOCKING_DEFAULT
    assert switches.load({}).data_checks_block == ()
    with pytest.raises(switches.SwitchError, match=mk.DATA_BLOCK_ENV):
        switches.load({mk.DATA_BLOCK_ENV: "bad_prices"})


# ---------------------------------------------------------------- Product's rulings on #583
def test_the_switch_on_without_a_market_that_checks_refuses_the_start(monkeypatch, tmp_path):
    """Fail closed: the book's loop alone (BookMarket, the live default) reads the bars of its holdings only and has
    no data checks, so KAIBOT_DATA_CHECKS_BLOCK on could block nothing: the engine does not start."""
    from kaibot.engine import __main__ as engine_main
    from kaibot.engine.book_market import BookMarket

    book = BookMarket(object(), ["A"], "SPY")
    engine_main.refuse_unchecked_block(book)  # off: starts as before
    monkeypatch.setenv(mk.DATA_BLOCK_ENV, "on")
    with pytest.raises(SystemExit) as e:
        engine_main.refuse_unchecked_block(book)
    assert mk.DATA_BLOCK_ENV in str(e.value) and "Not starting" in str(e.value)
    engine_main.refuse_unchecked_block(live(tmp_path, FRAMES))  # the intraday path's market checks: it starts
    src = (engine_main.ROOT / "src" / "kaibot" / "engine" / "__main__.py").read_text()
    assert "    refuse_unchecked_block(market)" in src.split("def build(", 1)[1].split("\ndef ", 1)[0]


def test_an_intraday_entry_in_a_blocked_stock_is_vetoed_with_the_reason(monkeypatch, tmp_path):
    """The signal row of an intraday entry in a stock whose bars fail a blocking check: rules_veto "market data:
    ...", no order. A's price jumps 8 % at 10:20 while the index does not move; the candidate comes at 10:24."""
    from test_engine import fake_candidate, make, run_day, synth_frames

    def jump(c):
        c = c.copy()
        c[50:] *= 1.08
        return c

    monkeypatch.setenv(mk.DATA_BLOCK_ENV, "on")
    eng, broker, clock, store, open_utc = make(tmp_path, synth_frames(shock=jump))
    fake_candidate(eng, at=(54,))
    run_day(eng, broker, clock, open_utc, until=60)
    sig = [s for s in store.tables.get("signal", []) if s["symbol"] == "A"]
    assert sig and sig[0]["rules_veto"].startswith("market data: A unmatched_move"), sig[:1]
    assert "trade" not in store.tables and not [o for o in broker.orders.values() if o["side"] == "buy"]
    monkeypatch.delenv(mk.DATA_BLOCK_ENV)
    eng2, broker2, clock2, store2, open2 = make(tmp_path / "off", synth_frames(shock=jump))
    fake_candidate(eng2, at=(54,))
    run_day(eng2, broker2, clock2, open2, until=60)
    sig2 = [s for s in store2.tables.get("signal", []) if s["symbol"] == "A"]
    assert sig2 and not str(sig2[0]["rules_veto"] or "").startswith("market data")  # off: not vetoed by it
