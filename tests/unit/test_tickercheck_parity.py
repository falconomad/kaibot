"""The ticker check has two implementations that must agree: engine/tickercheck.py (the Mac worker and the engine's
re-check at every open) and apps/web/api/check-ticker.ts (the Vercel function behind 'My lists'). Both are pinned to
one shared file, apps/web/api/_tickercheck_parity.json: its thresholds, and the verdict, stats and sector-fund cases
with their expected answers. The TypeScript twin of this test is apps/web/src/apiCheckTicker.test.ts.

If this test fails, the Python behaviour changed: update the JSON, then make check-ticker.ts pass its test too."""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from kaibot.engine import tickercheck as tc
from kaibot.engine.userconfig import LEVEL_MIN_ADV, LEVEL_MIN_PRICE, REFERENCE_ETFS, TICKER_RE

ROOT = Path(__file__).resolve().parents[2]
SHARED = json.loads((ROOT / "apps" / "web" / "api" / "_tickercheck_parity.json").read_text())


def test_the_thresholds_are_the_shared_ones():
    r = SHARED["rules"]
    assert r["LEVEL_MIN_ADV"] == LEVEL_MIN_ADV and r["LEVEL_MIN_PRICE"] == LEVEL_MIN_PRICE
    assert r["MIN_SESSIONS"] == tc.MIN_SESSIONS and r["ADV_SESSIONS"] == tc.ADV_SESSIONS
    assert r["MAX_ATTEMPTS"] == tc.MAX_ATTEMPTS and r["FALLBACK_ETF"] == tc.FALLBACK_ETF
    assert r["TICKER_RE"] == TICKER_RE.pattern
    assert set(r["MAJOR_EXCHANGES"]) == set(tc.MAJOR_EXCHANGES)
    assert set(r["REFERENCE_ETFS"]) == set(REFERENCE_ETFS)
    assert r["INDUSTRY_ETF"] == tc.INDUSTRY_ETF


@pytest.mark.parametrize("case", SHARED["verdict"], ids=lambda c: c["name"])
def test_verdict(case):
    floors = case.get("floors", {})
    ok, reason = tc.verdict(case["symbol"], case["asset"], case["stats"], **floors)
    assert {"ok": ok, "reason": reason} == case["expect"]


def test_every_refusal_reason_is_covered():
    reasons = {c["expect"]["reason"].split(" ")[1] if c["expect"]["reason"] else None for c in SHARED["verdict"]}
    assert len(SHARED["verdict"]) >= 30 and None in reasons
    words = " ".join(c["expect"]["reason"] or "" for c in SHARED["verdict"])
    for bit in ("does not look like", "reference fund", "could not find", "major US exchange", "cannot be traded at",
                "trading days", "unknown price", "closed at $", "trades about $"):
        assert bit in words, bit


@pytest.mark.parametrize("case", SHARED["stats"], ids=lambda c: c["name"])
def test_stats(case):
    bars = pd.DataFrame(case["bars"], columns=["t", "c", "v"]).rename(columns={"t": "ts"})
    got = tc.stats(bars if len(bars) else None, date.fromisoformat(case["before"]))
    want = case["expect"]
    assert got["sessions"] == want["sessions"] and got["last_close"] == want["last_close"]
    assert math.isclose(got["adv20_usd"], want["adv20_usd"], rel_tol=1e-12, abs_tol=1e-6)


@pytest.mark.parametrize("case", SHARED["sector"], ids=lambda c: repr(c["industry"]))
def test_sector_etf(case):
    assert tc.sector_etf(case["industry"]) == case["expect"]


def test_number_formats():
    """The reasons format numbers with Python's `,.2f` / `,.0f` (exact binary value, ties to even); the TypeScript
    twin must print the same, so the expected strings live in the shared file too."""
    assert len(SHARED["formats"]) > 300
    for x, digits, want in SHARED["formats"]:
        assert format(x, f",.{digits}f") == want
