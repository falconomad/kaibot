"""Story S37: pump_risk blocks longs on pump-and-dump red flags; liquid large caps practically never flag."""

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from kaibot.features.alt import pump_risk as reexported
from kaibot.features.pump import pump_risk, rules_veto_extra

AS_OF = datetime(2026, 11, 11, 15, 30, tzinfo=UTC)  # 10:30 ET, 1/6.5 of the session


def daily(price, vol, n=21, today=None):
    """n-1 quiet history days, then today's session so far as the last row (o, c, v)."""
    days = pd.bdate_range(end="2026-11-10", periods=n - 1)
    ts = [pd.Timestamp(d.date().isoformat() + " 14:30", tz=UTC) for d in days]
    rows = [{"ts": t, "o": price, "h": price * 1.01, "l": price * 0.99, "c": price, "v": vol} for t in ts]
    if today:
        o, c, v = today
        rows.append({"ts": pd.Timestamp("2026-11-11 14:30", tz=UTC), "o": o, "h": max(o, c), "l": min(o, c),
                     "c": c, "v": v})
    return pd.DataFrame(rows)


def wire(source, minutes_ago=30, kind="news", **kw):
    return {"source": source, "kind": kind, "available_ts": AS_OF - timedelta(minutes=minutes_ago), **kw}


def test_classic_pump_flags_and_blocks_longs():
    # $1.50 microcap trading $1.2M/day; today gaps +40% on 10x volume (pro-rated), only GlobeNewswire/Accesswire PRs
    bars = daily(1.5, 800_000, today=(2.1, 2.3, 1_300_000))  # ~10.6x the pro-rated median
    news = [wire("globenewswire"), wire("accesswire", 60)]
    r = pump_risk("PUMP", AS_OF, bars, news, market_cap=40e6)
    assert r["flag"] and r["score"] >= 0.8
    text = " ".join(r["reasons"])
    assert "penny" in text and "microcap" in text and "volume" in text and "spike" in text and "promotional" in text
    assert rules_veto_extra("PUMP", AS_OF, bars=bars, news=news, market_cap=40e6) == "pump risk"
    assert rules_veto_extra("PUMP", AS_OF, bars=bars, news=news, side="sell") is None  # never blocks an exit
    assert reexported is pump_risk


def test_spike_with_no_news_at_all_still_flags_a_penny_stock():
    bars = daily(3.0, 500_000, today=(3.6, 4.2, 2_000_000))
    r = pump_risk("THIN", AS_OF, bars, [])
    assert r["flag"] and any("no news" in x for x in r["reasons"])


def test_an_8k_and_independent_news_lower_the_score():
    bars = daily(3.0, 500_000, today=(3.6, 4.2, 2_000_000))
    base = pump_risk("THIN", AS_OF, bars, [wire("prnewswire")])["score"]
    with_8k = pump_risk("THIN", AS_OF, bars, [wire("prnewswire"), wire("sec", kind="filing", form="8-K")])
    with_news = pump_risk("THIN", AS_OF, bars, [wire("prnewswire"), wire("benzinga")])
    assert with_8k["score"] < base and with_news["score"] < base


def test_large_cap_never_flags_even_on_a_wild_day():
    # $230 mega cap, 50M shares/day; earnings gap +18% on 6x volume, only a Business Wire release
    bars = daily(230.0, 50_000_000, today=(271.0, 275.0, 50_000_000 * 6 / 6.5))
    r = pump_risk("MEGA", AS_OF, bars, [wire("businesswire")], market_cap=3.4e12)
    assert not r["flag"] and r["score"] <= 0.5  # spike + wire-only news, but no quality flag
    assert rules_veto_extra("MEGA", AS_OF, bars=bars, news=[wire("businesswire")]) is None


def test_universe_like_names_do_not_flag_across_random_days():
    rng = np.random.default_rng(7)
    flags = 0
    for _ in range(300):
        px = float(rng.uniform(20, 900))
        vol = float(rng.uniform(2e6, 80e6))
        gap = float(rng.normal(0, 0.04))
        rv = float(rng.lognormal(0, 0.6))
        bars = daily(px, vol, today=(px * (1 + gap), px * (1 + gap + rng.normal(0, 0.02)), vol * rv / 6.5))
        news = [wire(rng.choice(["benzinga", "businesswire", "prnewswire"]))] if rng.random() < 0.5 else []
        flags += pump_risk("LIQ", AS_OF, bars, news)["flag"]
    assert flags == 0


def test_point_in_time_and_missing_data():
    future = daily(1.5, 800_000, today=(2.1, 2.3, 9_000_000))
    yday_close = datetime(2026, 11, 10, 22, 0, tzinfo=UTC)
    r = pump_risk("PUMP", yday_close, future, [wire("globenewswire", minutes_ago=-60)])
    assert not r["flag"]  # today's bar and today's press release are invisible the evening before
    assert not any("promotional" in x or "spike" in x for x in r["reasons"])
    assert pump_risk("X", AS_OF, None, None) == {"flag": False, "score": 0.0, "reasons": ["insufficient bars"]}
    assert pump_risk("X", AS_OF, pd.DataFrame(), [])["reasons"] == ["insufficient bars"]
    assert pump_risk("X", AS_OF, daily(2.0, 1e5, n=4), [])["flag"] is False
    assert rules_veto_extra("X", AS_OF) is None


def test_veto_fails_closed_on_garbage_input():
    bad = pd.DataFrame({"ts": ["not a time"] * 10, "o": 1, "c": 1, "v": 1})
    assert rules_veto_extra("X", AS_OF, bars=bad) == "pump risk check failed"
