"""M10 1.19 (#444): the intraday path is opt-in by config (`intraday.enabled` in config/profiles.yaml, off), and
what the engine can say about the intraday rules without importing them is what their classes say."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest
import yaml

from kaibot.engine import clock, userconfig
from kaibot.engine.book_market import BookMarket
from kaibot.engine.config import ROOT
from kaibot.evaluation import gate_report
from kaibot.replay import data as replay_data
from kaibot.risk.profiles import DEFAULT_PATH, intraday_enabled, load_profiles, load_strategies
from kaibot.strategies import dip, registry
from kaibot.strategies.base import Holding


def _profiles(tmp_path, enabled) -> object:
    cfg = yaml.safe_load(DEFAULT_PATH.read_text())
    if enabled is None:
        cfg.pop("intraday", None)
    else:
        cfg["intraday"] = {"enabled": enabled}
    p = tmp_path / "profiles.yaml"
    p.write_text(yaml.safe_dump(cfg))
    return p


def test_the_repository_runs_no_intraday_rule_on_the_live_path(monkeypatch):
    monkeypatch.delenv("KAIBOT_MULTIDAY", raising=False)
    assert intraday_enabled() is False
    assert load_strategies() == {lv: [] for lv in load_profiles()}
    monkeypatch.setenv("KAIBOT_MULTIDAY", "on")
    assert load_strategies() == {lv: ["trend_momentum"] for lv in load_profiles()}
    # the levels still say which rule they would run: the switch decides, the lists are kept
    cfg = yaml.safe_load(DEFAULT_PATH.read_text())
    assert all(lv["strategies"] == ["dip_reversal"] for lv in cfg["levels"].values())


@pytest.mark.parametrize(("enabled", "on"), [(True, True), (False, False), (None, False), ("yes", False), (1, False)])
def test_only_a_plain_true_switches_the_intraday_path_on(tmp_path, monkeypatch, enabled, on):
    monkeypatch.delenv("KAIBOT_MULTIDAY", raising=False)
    p = _profiles(tmp_path, enabled)
    assert intraday_enabled(p) is on
    want = ["dip_reversal"] if on else []
    assert load_strategies(p) == {lv: want for lv in load_profiles()}
    monkeypatch.setenv("KAIBOT_MULTIDAY", "on")
    assert load_strategies(p) == {lv: [*want, "trend_momentum"] for lv in load_profiles()}


def test_the_registrys_table_is_what_the_rules_classes_say():
    """`registry.INTRADAY` answers for the rules while their modules are not imported: names, order and mode."""
    assert tuple(registry.INTRADAY) == tuple(s.name for s in registry.STRATEGIES)
    for s in registry.STRATEGIES:
        mod, cls, mode = registry.INTRADAY[s.name]
        assert (type(s).__module__, type(s).__name__, s.mode) == (mod, cls, mode)
        assert s.holding is not Holding.MULTI_DAY
    assert {s.name: s for s in registry.STRATEGIES} == registry.BY_NAME
    assert registry.DEFAULT_LIVE == (dip.NAME,) == (gate_report.DIP,)
    assert registry.names() == tuple(registry.INTRADAY) and registry.registered_multi_day() == ()
    assert registry.RETIRED == {"spy_late_momentum"} <= set(registry.INTRADAY)


def test_session_minutes_moved_with_its_constants():
    assert (clock.ET, clock.N_MIN) == (replay_data.ET, replay_data.N_MIN)
    from kaibot.engine import market

    assert market.session_minutes is clock.session_minutes
    o, n = clock.session_minutes({"open": "09:30", "close": "13:00"}, date(2026, 11, 27))
    assert (o, n) == (datetime(2026, 11, 27, 14, 30, tzinfo=UTC), 210)


POL = load_profiles()["balanced"]
UNI = frozenset({"AAPL"})


def test_an_intraday_rule_left_on_in_the_owners_config_never_refuses_a_session_without_the_intraday_path():
    """A config saved while the dip rule ran says `dip_reversal: true`. With the intraday path off the level
    lists multi-day rules only: the toggle is ignored (it can switch nothing on), the session is not refused
    and the multi-day rule stays on. With the path on, a rule outside the level is refused as before."""
    body = {"strategies": {"dip_reversal": True}}
    r = userconfig.tighten(POL, ["trend_momentum"], body, version=3, universe=UNI, themes={})
    assert r.strategies == ("trend_momentum",)
    r = userconfig.tighten(POL, [], body, version=3, universe=UNI, themes={})
    assert r.strategies == ()  # no rule at this level at all: nothing to keep on, nothing refused
    with pytest.raises(userconfig.ConfigError, match="not part of this level"):
        userconfig.tighten(POL, ["spy_late_momentum"], body, version=3, universe=UNI, themes={})
    with pytest.raises(userconfig.ConfigError, match="at least one strategy must stay on"):
        userconfig.tighten(POL, ["dip_reversal"], {"strategies": {"dip_reversal": False}}, version=3,
                           universe=UNI, themes={})


class Feed:
    def __init__(self):
        self.calls, self.fail = [], False
        self.t0 = datetime(2026, 8, 3, 13, 30, tzinfo=UTC)

    def bars(self, symbols, start, end, feed, timeframe="1Min", **kw):
        self.calls.append((tuple(symbols), start, feed, timeframe))
        if self.fail:
            raise TimeoutError("no answer")
        if timeframe == "1Day":
            return {s: pd.DataFrame({"ts": [start], "c": [400.0]}) for s in symbols}
        n = int((end - self.t0).total_seconds() // 60) + 1
        ts = [self.t0 + timedelta(minutes=k) for k in range(n)]
        ts = [t for t in ts if t >= start]
        return {s: pd.DataFrame({"ts": pd.to_datetime(ts, utc=True), "o": 1.0, "h": 1.0, "l": 1.0,
                                 "c": [100.0 + (t - self.t0).total_seconds() / 60 for t in ts], "v": 10.0,
                                 "vw": 1.0}) for s in symbols}


def test_the_books_market_reads_spy_and_the_holdings_inside_the_old_universe():
    f = Feed()
    held: set[str] = set()
    m = BookMarket(f, ["AAPL", "MSFT", "XLK"], "SPY", holdings=lambda: held)
    assert m.set_custom({"ZZZ": "XLK"}) is False and m.custom == {} and m.all == ["AAPL", "MSFT", "SPY", "XLK"]
    m.prepare_day(date(2026, 8, 3), f.t0)
    assert f.calls == [(("SPY",), f.t0 - timedelta(days=10), "sip", "1Day")]  # no history load at all
    now = f.t0 + timedelta(minutes=5, seconds=7)
    assert m.last_complete_minute(now) == 4
    m.update(now)
    assert f.calls[-1][:2] == (("SPY",), f.t0) and m.last_close("SPY") == 104.0  # completed minutes only
    assert m.last_bar_end("SPY") == f.t0 + timedelta(minutes=5) and m.last_close("AAPL") is None
    assert m.spy_return() == pytest.approx(104.0 / 400.0 - 1)
    held.update({"AAPL", "NVDA"})  # NVDA is outside the universe LiveMarket read: the broker's price, as before
    now += timedelta(minutes=1)
    m.update(now)
    assert [c[:2] for c in f.calls[-2:]] == [(("SPY",), now.replace(second=0) - timedelta(minutes=3)),
                                             (("AAPL",), f.t0)]  # a new holding: read from the open, once
    assert m.last_close("AAPL") == 105.0 and m.last_close("NVDA") is None
    f.fail = True
    with pytest.raises(TimeoutError):  # the engine contains it (`_market_update`); the bars stay as they were
        m.update(now + timedelta(minutes=1))
    assert m.last_close("AAPL") == 105.0
    m.prepare_day(date(2026, 8, 4), f.t0 + timedelta(days=1))  # a new session forgets yesterday's bars
    assert m.last_close("AAPL") is None and m.spy_return() is None


def test_the_universe_file_is_what_the_live_build_hands_the_books_market():
    cfg = yaml.safe_load((ROOT / "config" / "universe_seed.yaml").read_text())
    etf_of = {s: etf for etf, syms in cfg["sectors"].items() for s in syms}
    m = BookMarket(None, {*etf_of, *etf_of.values()}, cfg["market"])
    assert set(m.all) == set(etf_of) | set(etf_of.values()) | {cfg["market"]}  # LiveMarket.all without customs
