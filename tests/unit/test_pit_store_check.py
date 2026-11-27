"""#812: the read-only pit store check (`scripts/check_pit_store.sh`): keys expected, matched, missing, the verdict."""

from __future__ import annotations

import json
import shutil
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from kaibot.engine import daily_report as dr
from kaibot.ingest import nyse_sessions
from kaibot.ingest import pit_store_check as psc
from kaibot.ingest import sp500_pit as pit
from kaibot.ingest.daily_bars import COMPLETE_AT

ROOT = Path(__file__).resolve().parents[2]
NY = ZoneInfo("America/New_York")
OUTSIDE = "P29"  # a current member outside the universe file: stored as SYMBOL@ASOF
KEY = f"{OUTSIDE}@2026-11-05"  # STORE_DAY - 7 days


def _store(tmp_path: Path, key: str | None) -> tuple[Path, list[str]]:
    """The committed fixture store, NYSE sessions only and stamped available at 20:15 ET (as fetch_daily writes it);
    OUTSIDE's file renamed to `key` (None: removed)."""
    shutil.copytree(dr.FIXTURE, tmp_path / "daily")
    meta = json.loads((dr.FIXTURE / "fixture.json").read_text())
    for sym in meta["symbols"]:
        f = tmp_path / "daily" / "bars" / f"symbol={sym}.parquet"
        t = pq.read_table(f)
        days = t.column("date").to_pylist()
        cal = set(nyse_sessions.sessions(min(days), max(days)))
        t = t.filter(pa.array([d in cal for d in days]))
        ts = [datetime.combine(d, COMPLETE_AT, NY).astimezone(UTC) for d in t.column("date").to_pylist()]
        pq.write_table(t.append_column("available_ts", pa.array(ts, pa.timestamp("s", tz="UTC"))), f)
    src = tmp_path / "daily" / "bars" / f"symbol={OUTSIDE}.parquet"
    if key is None:
        src.unlink()
    else:
        src.rename(src.with_name(f"symbol={key}.parquet"))
    listed = [s for s in meta["stocks"] if s != OUTSIDE]
    uni = tmp_path / "universe.yaml"
    uni.write_text(json.dumps({"sectors": {"XLK": listed}, "market": "SPY"}))
    return uni, meta["stocks"]


@pytest.fixture
def world(tmp_path, monkeypatch):
    def make(key, today=date(2026, 11, 27)):
        uni, stocks = _store(tmp_path, key)
        listed = [s for s in stocks if s != OUTSIDE]
        ms = pit.Membership([pit.Change(date(2016, 1, 1), tuple(stocks))], {}, listed, today)
        monkeypatch.setattr(pit.Membership, "load", classmethod(lambda cls, *a, **k: ms))
        return uni, ms
    return make


def test_ok_when_the_store_holds_every_key(world, tmp_path):
    uni, ms = world(KEY)
    assert ms.keys(OUTSIDE) == [KEY]
    rep = psc.check(tmp_path, uni)
    assert rep["companies_expected"] == rep["companies_matched"] == 30 and rep["keys_expected"] == 32
    # the fixture store has no sector ETF: XLK is reported, the live view loads without it
    assert rep["keys_matched"] == 31 and rep["missing_plain"] == ["XLK"]
    assert not rep["missing_current"] and rep["mismatch"] is None
    live = rep["live"]
    assert "error" not in live and live["stocks"] == 30 and live["tradable"] > 0
    assert rep["ok"] and rep["verdict"] == f"OK: live path would trade {live['tradable']} stocks"
    assert psc.render(rep).splitlines()[-1] == rep["verdict"]


def test_the_key_is_the_same_on_a_later_run_date(world, tmp_path):
    uni, _ = world(KEY, today=date(2027, 4, 1))
    assert psc.check(tmp_path, uni)["ok"]


def test_fail_when_the_store_holds_the_member_under_another_key(world, tmp_path):
    uni, _ = world(f"{OUTSIDE}@2026-11-19")  # the #812 drift
    rep = psc.check(tmp_path, uni)
    assert not rep["ok"] and rep["verdict"] == "FAIL: live path would stop (no orders) — missing keys"
    assert "no matching store key" in rep["live"]["error"]  # the live loader itself refuses: DataNotReady
    (row,) = rep["missing_current"]
    assert row == {"company": OUTSIDE, "expects": [KEY], "store_has": [f"{OUTSIDE}@2026-11-19"]}
    text = psc.render(rep)
    assert f"  - {OUTSIDE}: expects {KEY}; the store has {OUTSIDE}@2026-11-19" in text


def test_fail_when_a_current_member_has_no_key_at_all(world, tmp_path):
    uni, _ = world(None)
    rep = psc.check(tmp_path, uni)
    assert not rep["ok"] and rep["companies_matched"] == 29 and len(rep["missing_current"]) == 1
    assert rep["verdict"] == (f"FAIL: live path would trade only {rep['live']['tradable']} stocks — "
                              "1 current members have no key in the store")


def test_fail_without_a_store(tmp_path):
    rep = psc.check(tmp_path, ROOT / "config" / "universe_history.yaml")
    assert not rep["ok"] and rep["verdict"].startswith("FAIL: live path would stop (no orders) — no daily store")


def test_the_check_writes_nothing(world, tmp_path):
    uni, _ = world(KEY)
    before = sorted((p, p.stat().st_mtime_ns) for p in tmp_path.rglob("*"))
    psc.check(tmp_path, uni)
    assert sorted((p, p.stat().st_mtime_ns) for p in tmp_path.rglob("*")) == before


def test_engine_warning_is_a_warning_never_a_failed_line(world, tmp_path):
    uni, _ = world(f"{OUTSIDE}@2026-11-19")
    w = psc.engine_warning(tmp_path, uni)
    assert isinstance(w, str) and w.startswith("WARNING: FAIL: live path would stop") and "FAILED" not in w


def test_engine_warning_summary_when_ok(world, tmp_path):
    uni, _ = world(KEY)
    w = psc.engine_warning(tmp_path, uni)
    assert isinstance(w, dict) and w["verdict"].startswith("OK:") and w["keys_matched"] == 31


def test_the_script_exits_1_on_fail_and_prints_the_verdict_last(world, tmp_path, capsys):
    sys.path.insert(0, str(ROOT / "scripts"))
    import check_pit_store

    uni, _ = world(f"{OUTSIDE}@2026-11-19")
    assert check_pit_store.main(["--data", str(tmp_path), "--universe-file", str(uni)]) == 1
    assert capsys.readouterr().out.strip().splitlines()[-1].startswith("FAIL: live path would stop (no orders)")
    wrapper = (ROOT / "scripts" / "check_pit_store.sh").read_text()
    assert "scripts/check_pit_store.py" in wrapper and "pip install" not in wrapper and "curl" not in wrapper
