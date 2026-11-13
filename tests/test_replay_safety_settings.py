"""#66: the owner's Fed/CPI quiet window reaches the config replay too, not only the live engine.

`replay.config_replay.replay_config` (the replay `scripts/run_config_replays.py` logs as a trial for a saved config)
passed the pause and the VIX cutoff to `run_portfolio` but not `event_window_min`, so a +-90 min config replayed
as +-30. `run_portfolio(event_window=)` now widens the FOMC blackout exactly as `engine.events.in_blackout` does,
and leaves it untouched by default (the replay golden and parity stay byte-equal)."""

from __future__ import annotations

from datetime import date

import pytest
import spine_fixture as sf
from hypothesis import given
from hypothesis import strategies as st

from kaibot.engine import events
from kaibot.engine.userconfig import LEVEL_EVENT_WINDOW, ConfigError, defaults, tighten
from kaibot.replay import config_replay
from kaibot.replay.events import events_digest
from kaibot.replay.sim import BLACKOUT_FOMC, EXIT_M, fomc_blackout, run_portfolio
from kaibot.risk.profiles import load_profiles

FOMC_DAY = date(2026, 9, 16)


def _book(event_window=None, **kw):
    fx = sf.load()
    prep, reg = sf.prepared(fx)
    pol = load_profiles()["balanced"]
    return run_portfolio(prep.bars, prep.cands, reg, set(fx.fomc), pol, equity0=sf.EQUITY, variant="A",
                         event_window=event_window, **kw), fx


def test_default_blackout_is_todays_constant():
    assert fomc_blackout() == fomc_blackout(LEVEL_EVENT_WINDOW) == BLACKOUT_FOMC == (240, 300)


@given(st.integers(-10_000, 10_000))
def test_blackout_never_shrinks_below_today(w):
    lo, hi = fomc_blackout(w)
    assert lo <= BLACKOUT_FOMC[0] and hi >= BLACKOUT_FOMC[1]


@given(st.integers(LEVEL_EVENT_WINDOW, 90))
def test_replay_blackout_matches_the_engine(w):
    ev = events.from_fomc_dates({FOMC_DAY})
    lo, hi = fomc_blackout(w)
    engine_blocked = {m for m in range(EXIT_M + 1) if events.in_blackout(ev, FOMC_DAY, m, w)}
    assert engine_blocked == set(range(lo, hi + 1))


def test_defaults_replay_diff_is_zero():
    """No window, the level's 30, and the default Rules spelled out: the same events digest as today's replay."""
    today, fx = _book()
    pol = load_profiles()["balanced"]
    r = defaults(pol, ["dip_reversal"])
    spelled = tighten(pol, ["dip_reversal"], {"timing": {"pause_losses": 3, "pause_minutes": 60, "vix_max": 35,
                                                         "event_window_min": 30}},
                      version=1, universe=set(fx.stocks), themes={})
    for rules in (r, spelled):
        b, _ = _book(rules.event_window_min, pause_after_losses=rules.pause_losses,
                     pause_minutes=rules.pause_minutes)
        assert events_digest(b) == events_digest(today)
    b, _ = _book(30)
    assert events_digest(b) == events_digest(today)


def test_wider_window_blocks_more_on_the_fomc_day_only():
    today, _ = _book()
    wide, _ = _book(90)
    lo, hi = fomc_blackout(90)
    fomc = [t for t in wide.trades if str(t.date) == str(FOMC_DAY)]
    assert all(not (lo <= t.entry_m - 1 <= hi) for t in fomc)  # entry fills at m+1 after the decision at m
    blocked = [t for t in today.trades if str(t.date) == str(FOMC_DAY) and lo <= t.entry_m - 1 <= hi]
    assert blocked, "the fixture must have a FOMC-day entry inside +-90 for this test to mean anything"
    assert len(wide.trades) < len(today.trades)
    sep15 = lambda b: [(t.symbol, t.entry_m, t.pnl) for t in b.trades if str(t.date) == "2026-09-15"]  # noqa: E731
    assert sep15(wide) == sep15(today)  # days before the event are untouched


def test_config_replay_passes_the_owners_window(monkeypatch):
    seen = {}

    class Stop(Exception):
        pass

    def fake_run(*a, **kw):
        seen.update(kw)
        raise Stop

    fx = sf.load()
    pol = load_profiles()["balanced"]
    rules = tighten(pol, ["dip_reversal"], {"timing": {"event_window_min": 75, "pause_losses": 2,
                                                       "pause_minutes": 120, "vix_max": 25}},
                    version=1, universe=set(fx.stocks), themes={})
    prep, _ = sf.prepared(fx)
    monkeypatch.setattr(config_replay, "have_data", lambda *a: True)
    monkeypatch.setattr(config_replay, "load_symbol", lambda *a: None)
    monkeypatch.setattr(config_replay, "load_news_times", lambda *a: [])
    monkeypatch.setattr(config_replay, "prepare", lambda *a, **k: prep)
    monkeypatch.setattr(config_replay, "load_vix", lambda *a: fx.vix)
    monkeypatch.setattr(config_replay, "regime", lambda *a: {})
    monkeypatch.setattr(config_replay, "run_portfolio", fake_run)
    with pytest.raises(Stop):
        config_replay.replay_config(rules, {"market": "SPY", "sectors": {"XLK": ["AAA"]}}, sf.DIR, set())
    assert (seen["event_window"], seen["pause_after_losses"], seen["pause_minutes"]) == (75, 2, 120)


@pytest.mark.parametrize("hhmm", ["٠٩:٣٥", "0²:35", "09:3⁵", "０９:35"])
def test_entry_time_needs_ascii_digits_like_sql(hhmm):
    """str.isdigit() accepts Arabic-Indic, superscript and full-width digits; the SQL check does not."""
    pol = load_profiles()["balanced"]
    with pytest.raises(ConfigError):
        tighten(pol, ["dip_reversal"], {"timing": {"entry_start": hhmm}}, version=1, universe=set(), themes={})
