"""#66: the owner's Fed/CPI quiet window reaches the config replay too, not only the live engine.

`replay.config_replay.replay_config` (the replay `scripts/run_config_replays.py` logs as a trial for a saved config)
passed the pause and the VIX cutoff to `run_portfolio` but not `event_window_min`, so a +-90 min config replayed
as +-30. `run_portfolio(event_window=)` now widens the FOMC blackout exactly as `engine.events.in_blackout` does,
and leaves it untouched by default (the replay golden and parity stay byte-equal). The config replay now also gates
on the live macro calendar (`events`), so CPI 08:30 blocks early entries once the window reaches 09:35 (QA-Ops)."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest
import spine_fixture as sf
from hypothesis import given
from hypothesis import strategies as st

from kaibot.engine import events
from kaibot.engine.userconfig import LEVEL_EVENT_WINDOW, ConfigError, defaults, tighten
from kaibot.replay import config_replay
from kaibot.replay.events import events_digest
from kaibot.replay.sim import BLACKOUT_FOMC, EXIT_M, entry_blocked, fomc_blackout, run_portfolio
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


# --- QA-Ops review on #193: the replay must apply the live engine's WHOLE macro blackout, CPI 08:30 included -----

CPI_DAY = "2026-09-17"  # a fixture day, used as a stand-in CPI morning (not a FOMC day)


def _fixture_events(cpi: bool = True) -> dict:
    fx = sf.load()
    ev = events.from_fomc_dates(fx.fomc)
    if cpi:
        hh, mm = events.EVENT_TIMES["cpi_0830"]
        ev.setdefault(date.fromisoformat(CPI_DAY), []).append(("cpi_0830", hh * 60 + mm - events.OPEN_MIN))
    return ev


def _early_book(event_window=None, ev=None):
    """The spine fixture plus candidates early on the CPI day (decisions at m = 4, 15, 30, 31: 09:34..10:01 ET)."""
    import pandas as pd

    fx = sf.load()
    prep, reg = sf.prepared(fx)
    day = prep.cands[prep.cands["date"].astype(str) == CPI_DAY]
    early = []
    for sym, m in (("AAA", 4), ("BBB", 15), ("CCC", 30), ("DDD", 31)):
        rows = day[day["symbol"] == sym]
        if len(rows):
            early.append(rows.iloc[[0]].assign(m=m))
    cands = pd.concat([prep.cands, *early], ignore_index=True)
    pol = load_profiles()["balanced"]
    return run_portfolio(prep.bars, cands, reg, set(fx.fomc), pol, equity0=sf.EQUITY, variant="A",
                         event_window=event_window, events=ev)


def _cpi_day_decisions(book) -> list[int]:
    return sorted(t.entry_m - 1 for t in book.trades if str(t.date) == CPI_DAY)


def test_cpi_morning_plus_minus_90_drops_entries_up_to_10_00():
    """Reviewer's case: CPI 08:30 = session minute -60, so +-90 blocks decisions m <= 30 (10:00 ET) like the engine."""
    base = _cpi_day_decisions(_early_book(90, _fixture_events(cpi=False)))
    assert any(m <= 30 for m in base) and 31 in base, "the fixture must have early CPI-day entries to mean anything"
    wide = _cpi_day_decisions(_early_book(90, _fixture_events()))
    assert wide and all(m > 30 for m in wide)
    assert 31 in wide  # 10:01 is the first decision after the window, as in_blackout says
    assert events.in_blackout(_fixture_events(), date.fromisoformat(CPI_DAY), 30, 90) == "cpi_0830"
    assert events.in_blackout(_fixture_events(), date.fromisoformat(CPI_DAY), 31, 90) is None


def test_cpi_morning_plus_minus_63_drops_none():
    ev = _fixture_events()
    assert events.in_blackout(ev, date.fromisoformat(CPI_DAY), 4, 63) is None  # 09:34 is first decision minute
    assert events_digest(_early_book(63, ev)) == events_digest(_early_book(63, _fixture_events(cpi=False)))


@given(st.sampled_from(sorted(events.EVENT_TIMES)), st.integers(LEVEL_EVENT_WINDOW, 90))
def test_replay_gate_is_the_engines_in_blackout_for_every_event_type(name, w):
    hh, mm = events.EVENT_TIMES[name]
    ev = {FOMC_DAY: [(name, hh * 60 + mm - events.OPEN_MIN)]}
    for m in range(EXIT_M + 1):
        assert entry_blocked(ev, set(), FOMC_DAY, m, w) == (events.in_blackout(ev, FOMC_DAY, m, w) is not None)
        assert entry_blocked(ev, set(), np.datetime64(FOMC_DAY), m, w) == entry_blocked(ev, set(), FOMC_DAY, m, w)


def test_config_replay_uses_the_live_calendar(monkeypatch):
    """With no events given, the config replay loads config/macro_calendar.yaml exactly as engine/__main__ does."""
    seen = {}

    class Stop(Exception):
        pass

    def fake_run(*a, **kw):
        seen.update(kw)
        raise Stop

    fx = sf.load()
    pol = load_profiles()["balanced"]
    prep, _ = sf.prepared(fx)
    for name, value in (("have_data", lambda *a: True), ("load_symbol", lambda *a: None),
                        ("load_news_times", lambda *a: []), ("prepare", lambda *a, **k: prep),
                        ("load_vix", lambda *a: fx.vix), ("regime", lambda *a: {}), ("run_portfolio", fake_run)):
        monkeypatch.setattr(config_replay, name, value)
    with pytest.raises(Stop):
        config_replay.replay_config(defaults(pol, ["dip_reversal"]), {"market": "SPY", "sectors": {"XLK": ["AAA"]}},
                                    sf.DIR, set())
    live, _ = events.load_calendar(config_replay.CALENDAR)
    assert {d: sorted(v) for d, v in seen["events"].items()} == {d: sorted(v) for d, v in live.items()}
    assert {e for evs in seen["events"].values() for e, _ in evs} == set(events.EVENT_TIMES)


@pytest.mark.parametrize("w", [None, LEVEL_EVENT_WINDOW])
def test_default_window_over_the_design_window_blocks_exactly_todays_fomc_minutes(w):
    """Diff = 0 for default replays: on every design-window day, the full calendar at +-30 blocks exactly the minutes
    the old FOMC-only (240, 300) rule blocked (CPI at 08:30 +-30 never reaches the session)."""
    live, _ = events.load_calendar(config_replay.CALENDAR)
    fomc = {np.datetime64(d, "D") for d, evs in live.items() if any(e == "fomc_statement_1400" for e, _ in evs)}
    lo, hi = np.datetime64(config_replay.DESIGN[0], "D"), np.datetime64(config_replay.DESIGN[1], "D")
    for d in np.arange(lo, hi + np.timedelta64(1, "D"), dtype="datetime64[D]"):
        for m in range(EXIT_M + 1):
            assert entry_blocked(live, fomc, d, m, w) == entry_blocked(None, fomc, d, m, w), (str(d), m)


def test_defaults_replay_diff_is_zero_with_the_full_calendar():
    """The fixture with the FOMC day plus a CPI morning at the default window: the same digest as today's replay."""
    today, _ = _book()
    for w in (None, LEVEL_EVENT_WINDOW):
        b, _ = _book(w, events=_fixture_events())
        assert events_digest(b) == events_digest(today)
