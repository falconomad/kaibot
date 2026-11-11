"""M5 S50 (#66): the three fixed safety values are tighten-only settings the engine reads at the session open.

- VIX no-new-trades cutoff: 35 -> 20..35
- Fed/CPI quiet window: +-30 min -> +-30..90 min
- loss pause: 3 losses / 60 min -> 2..3 losses, 60..240 min

Three things are pinned here, all in fake sessions (no network, no broker):
1. each setting changes what the engine does when the owner tightens it;
2. with no config, and with a config that spells out today's constants, the engine does exactly what it did before
   (same signals, same gate decisions, same trades);
3. properties: whatever is typed into the config, the value the engine ends up with is never looser than today's.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from test_engine import DAY, fake_candidate, make, run_day, synth_frames

from kaibot.engine import core, events, userconfig
from kaibot.engine.userconfig import ConfigError, Rules, tighten
from kaibot.risk.profiles import load_profiles

POL = load_profiles()["balanced"]

UNI = frozenset({"A", "B1", "B2", "B3", "B4", "B5", "C1"})
TODAY = {"pause_losses": 3, "pause_minutes": 60, "vix_max": 35, "event_window_min": 30}  # the old constants
EVERY_20 = tuple(range(24, 380, 20))  # a candidate every 20 minutes, on the engine's 5-minute decision grid


def slide(c):
    """Symbol A falls all day, so every entry is stopped out a few minutes later: a clean losing streak."""
    c = c.copy()
    c[10:] = c[10] * np.exp(np.linspace(0, -0.12, 380))
    return c


def session(tmp_path, timing=None, *, shock=None, at=(54,), vix=18.0, event_at=None, until=390):
    eng, broker, clock, store, open_utc = make(tmp_path, synth_frames(shock=shock))
    eng._uni_cache = (UNI, {})
    if timing is not None:
        store.insert("config", {"run_id": store.tables["run"][0]["id"], "version": 1, "body": {"timing": timing},
                                "status": "applied", "created_at": (open_utc - timedelta(hours=10)).isoformat(),
                                "applies_from": open_utc.isoformat()})
    if event_at is not None:
        eng.events = {date.fromisoformat(DAY): [("test_event", event_at)]}
    fake_candidate(eng, at=at)
    eng.market.regime_at = lambda m: (False, vix)
    run_day(eng, broker, clock, open_utc, until=until)
    assert not eng.day.refused, eng.day.refused
    return eng, store, open_utc


def entry_minutes(store, open_utc):
    """Decision minute of each dip-reversal signal that became a trade."""
    traded = {t["signal_id"] for t in store.tables.get("trade", [])}
    return [s["features"]["m"] for s in store.tables.get("signal", [])
            if s.get("strategy") == "dip_reversal" and s["id"] in traded]


# ---------------- 1. each setting is read from the config ----------------
@pytest.fixture(scope="module")
def streak_default(tmp_path_factory):
    eng, store, open_utc = session(tmp_path_factory.mktemp("d"), shock=slide, at=EVERY_20, until=200)
    return eng, store, open_utc


def test_default_loss_pause_is_three_losses_then_sixty_minutes(streak_default):
    eng, store, open_utc = streak_default
    assert eng.day.rules.version == 0 and eng._pause_rule() == (3, 60)
    assert all(t["exit_reason"] == "stop" and t["pnl_usd"] < 0 for t in store.tables["trade"])
    # three losses, an hour off, three more: exactly today's behaviour
    assert entry_minutes(store, open_utc) == [24, 44, 64, 144, 164, 184]


def test_loss_pause_after_two_losses_from_config(tmp_path):
    eng, store, open_utc = session(tmp_path, {"pause_losses": 2}, shock=slide, at=EVERY_20, until=200)
    assert eng._pause_rule() == (2, 60)
    assert entry_minutes(store, open_utc) == [24, 44, 124, 144]


def test_loss_pause_length_from_config(tmp_path):
    eng, store, open_utc = session(tmp_path, {"pause_minutes": 240}, shock=slide, at=EVERY_20, until=330)
    assert eng._pause_rule() == (3, 240)
    assert entry_minutes(store, open_utc) == [24, 44, 64, 324]


@pytest.mark.parametrize("timing,minute,traded", [
    (None, 164, True), (None, 174, False),  # today: +-30 around minute 200 blocks 170..230
    ({"event_window_min": 90}, 104, True), ({"event_window_min": 90}, 114, False),  # +-90 blocks 110..290
    ({"event_window_min": 90}, 164, False),
])
def test_quiet_window_from_config(tmp_path, timing, minute, traded):
    eng, store, _ = session(tmp_path, timing, at=(minute,), event_at=200, until=minute + 6)
    assert bool(store.tables.get("trade")) is traded


@pytest.mark.parametrize("timing,vix,traded", [
    (None, 34.9, True),  # today: only the 35 crisis gate stops new trades
    (TODAY, 34.9, True),
    ({"vix_max": 20}, 20.0, True), ({"vix_max": 20}, 20.1, False),
    ({"vix_max": 27.5}, 27.4, True), ({"vix_max": 27.5}, 27.6, False),
])
def test_vix_cutoff_from_config(tmp_path, timing, vix, traded):
    eng, store, _ = session(tmp_path, timing, vix=vix, until=60)
    assert bool(store.tables.get("trade")) is traded
    if not traded:
        assert any("VIX above your limit" in (s.get("rules_veto") or "") for s in store.tables["signal"])


def test_the_settings_are_fixed_at_the_open(tmp_path):
    """A config applied during the session does not change the day's safety values."""
    eng, broker, clock, store, open_utc = make(tmp_path, synth_frames())
    eng._uni_cache = (UNI, {})
    fake_candidate(eng)
    run_day(eng, broker, clock, open_utc, until=30)
    store.insert("config", {"run_id": store.tables["run"][0]["id"], "version": 1,
                            "body": {"timing": {"pause_losses": 2, "pause_minutes": 240, "vix_max": 20,
                                                "event_window_min": 90}},
                            "status": "applied", "created_at": (open_utc + timedelta(minutes=10)).isoformat(),
                            "applies_from": (open_utc + timedelta(days=1)).isoformat()})
    run_day(eng, broker, clock, open_utc, until=60)
    r = eng.day.rules
    assert (r.version, r.pause_losses, r.pause_minutes, r.vix_max, r.event_window_min) == (0, 3, 60, 35.0, 30)


# ---------------- 2. defaults reproduce today's behaviour exactly ----------------
def test_level_constants_are_todays_constants():
    assert (userconfig.LEVEL_PAUSE_LOSSES, userconfig.LEVEL_PAUSE_MINUTES) == (core.PAUSE_AFTER_LOSSES,
                                                                               core.PAUSE_MINUTES) == (3, 60)
    assert userconfig.LEVEL_EVENT_WINDOW == events.WINDOW_MIN == 30
    assert userconfig.LEVEL_VIX_MAX == 35.0
    for r in (Rules(0, POL, ("dip_reversal",)), tighten(POL, ("dip_reversal",), {}, version=1, universe=UNI, themes={}),
              tighten(POL, ("dip_reversal",), {"timing": {}}, version=1, universe=UNI, themes={})):
        assert (r.pause_losses, r.pause_minutes, r.vix_max, r.event_window_min) == (3, 60, 35.0, 30)


def _record(store, open_utc):
    """Everything the session decided, without row ids and without the config version/sha tags."""
    # row ids shift by the one extra config_applied event row, and the client order id embeds the signal id
    drop = {"id", "signal_id", "trade_id", "order_id", "db_order_id", "client_order_id", "coid", "config_version",
            "config_sha", "created_at"}

    def clean(v):
        if isinstance(v, dict):
            return {k: clean(x) for k, x in v.items() if k not in drop}
        if isinstance(v, list):
            return [clean(x) for x in v]
        return v

    return {t: clean(store.tables.get(t, [])) for t in ("signal", "gate_run", "order", "fill", "trade", "halt")}


@pytest.mark.parametrize("kw", [
    {"shock": slide, "at": EVERY_20, "until": 200},  # a losing streak: the loss pause decides which entries happen
    {"at": (164, 174, 234), "event_at": 200, "until": 240},  # an event day: the quiet window decides
    {"vix": 34.9, "until": 60},  # just under the crisis gate
    {"vix": 35.1, "until": 60},  # just over the 35 the owner may not raise
], ids=["loss_streak", "event_day", "vix_34_9", "vix_35_1"])
def test_spelled_out_defaults_trade_exactly_like_no_config(tmp_path, kw, streak_default):
    if kw.get("shock") is slide:
        _, base_store, base_open = streak_default
    else:
        _, base_store, base_open = session(tmp_path / "a", **kw)
    eng, store, open_utc = session(tmp_path / "b", TODAY, **kw)
    assert eng.day.rules.version == 1  # the config was really applied
    assert _record(store, open_utc) == _record(base_store, base_open)


# ---------------- 3. properties: nothing loosens past today's constants ----------------
anything = st.one_of(st.integers(-1000, 1000), st.floats(allow_nan=True, allow_infinity=True), st.booleans(),
                     st.none(), st.text(max_size=4), st.lists(st.integers(), max_size=2))


@settings(max_examples=500, deadline=None)
@given(timing=st.fixed_dictionaries({}, optional={"pause_losses": anything, "pause_minutes": anything,
                                                  "vix_max": anything, "event_window_min": anything}))
def test_property_safety_values_never_loosen(timing):
    """Any value at all: either the session is refused (ConfigError) or the value sits inside the tighten-only range."""
    try:
        r = tighten(POL, ("dip_reversal",), {"timing": timing}, version=1, universe=UNI, themes={})
    except ConfigError:
        return
    assert r.pause_losses in (2, 3) and r.pause_losses <= core.PAUSE_AFTER_LOSSES
    assert core.PAUSE_MINUTES <= r.pause_minutes <= 240
    assert 20 <= r.vix_max <= 35.0
    assert events.WINDOW_MIN <= r.event_window_min <= 90
    assert all(type(v) in (int, float) for v in (r.pause_losses, r.pause_minutes, r.vix_max, r.event_window_min))


@settings(max_examples=300, deadline=None)
@given(pl=st.integers(-5, 10), pm=st.integers(-100, 600), vx=st.floats(-10, 100, allow_nan=False),
       ew=st.integers(-100, 300))
def test_property_only_the_tighten_only_range_is_accepted(pl, pm, vx, ew):
    """Accepted exactly when every value is inside its range: nothing looser gets in, nothing legal is refused."""
    ok = pl in (2, 3) and 60 <= pm <= 240 and 20 <= vx <= 35 and 30 <= ew <= 90
    body = {"timing": {"pause_losses": pl, "pause_minutes": pm, "vix_max": vx, "event_window_min": ew}}
    try:
        r = tighten(POL, ("dip_reversal",), body, version=1, universe=UNI, themes={})
    except ConfigError:
        assert not ok
        return
    assert ok and (r.pause_losses, r.pause_minutes, r.vix_max, r.event_window_min) == (pl, pm, vx, ew)


@settings(max_examples=300, deadline=None)
@given(window=st.one_of(st.none(), st.integers(-500, 500)), event_m=st.integers(-120, 480), m=st.integers(0, 389))
def test_property_quiet_window_never_shrinks_below_thirty_minutes(window, event_m, m):
    """Second line of defence in events.in_blackout: whatever window it is handed, today's +-30 min stays blocked."""
    day = date(2026, 8, 4)
    ev = {day: [("e", event_m)]}
    if events.in_blackout(ev, day, m) is not None:
        assert events.in_blackout(ev, day, m, window) is not None
