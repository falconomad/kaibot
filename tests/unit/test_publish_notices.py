"""The market-notice publisher (#378, scripts/publish_notices.py): what it builds from the new-listing watch and the
market-event alarm, what it writes and withdraws, and that nothing that trades imports it. Offline."""

from __future__ import annotations

import ast
import importlib.util
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from test_listings import CREDS, N1, net

from kaibot.ingest import listings_pit as lp
from kaibot.ingest.alt import listings as ls

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "publish_notices.py"
MORNING_19 = datetime(2026, 11, 19, 6, 45, tzinfo=UTC)  # the daily job's 07:45 Berlin run on ANT's first day


def _mod():
    spec = importlib.util.spec_from_file_location("publish_notices", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


pn = _mod()


def _listings(tmp_path, now):
    ls.collect(tmp_path, CREDS, now=N1, **net(1).kw)  # the night before: Nasdaq's notice says 19 Nov
    t = lp.Listings.load(tmp_path).table(now, live=True)
    return t.astype(object).where(t.notna(), None).to_dict("records")


def fake_alarm(d: date, now: datetime) -> dict:
    flags = dict.fromkeys(pn.ALARM_FLAGS, False)
    det: dict = {"index": [], "mega_listing": [], "megacaps_reporting": []}
    if d == date(2026, 11, 19):
        flags["mega_listing"], det["mega_listing"] = True, ["ANT"]
    if d == date(2026, 11, 20):
        flags["megacap_report"], det["megacaps_reporting"] = None, []  # unknown: never a notice
    if d == date(2026, 11, 23):
        flags["cpi_day"] = True
    return {"session": d.isoformat(), "flags": flags, "details": det, "n_megacaps": 0, "megacap_major": 3}


def test_the_window_is_four_sessions_back_and_six_ahead():
    back, ahead = pn.window(date(2026, 11, 19))
    assert back == [date(2026, 11, 13), date(2026, 11, 16), date(2026, 11, 17), date(2026, 11, 18)]
    assert ahead[0] == date(2026, 11, 19) and date(2026, 11, 26) not in ahead and len(ahead) == 6


def test_names_lose_their_legal_form():
    assert pn.short_name("Anthropic, PBC", "ANT") == "Anthropic"
    assert pn.short_name("Example Bio Inc.", "EXBI") == "Example Bio"
    assert pn.short_name(None, "XYZ") == "XYZ"


def test_the_19_november_morning_has_the_ant_notice(tmp_path):
    """Acceptance (#378): with 19 Nov 2026 replayed, the notices hold ANT on its first session, major, with the
    paper rule's earliest date and no price."""
    rows, _, _ = pn.build(MORNING_19, _listings(tmp_path, MORNING_19), fake_alarm)
    by = {r["id"]: r for r in rows}
    ant = by["new_listing:ANT:2026-11-19"]
    assert (ant["kind"], ant["flag"], ant["tier"], ant["session"]) == ("new_listing", "ipo", "major", "2026-11-19")
    assert ant["title"] == "Anthropic (ANT) starts trading" and ant["first_eligible"] == "2027-11-19"
    assert ant["detail"]["name"] == "Anthropic" and "offer_price" not in str(ant)
    rules = {x["rule"]: x for x in ant["detail"]["rules"]}
    assert rules["owner_tickers"]["first_eligible"] == "2027-02-17" and rules["trend_momentum"]["needs_sp500"]
    exbi = by["new_listing:EXBI:2026-11-24"]
    assert exbi["tier"] == "notice"  # an ordinary listing: in the list, not on Home
    assert by["big_day:mega_listing:2026-11-19:ANT"]["tier"] == "major"
    cpi = by["big_day:cpi_day:2026-11-23"]
    assert cpi["starts"] == "2026-11-23T13:30:00+00:00" and cpi["tier"] == "notice"
    assert not any(r["flag"] == "megacap_report" for r in rows)


def test_without_the_alarm_module_only_listings_are_built(tmp_path):
    rows, _, _ = pn.build(MORNING_19, _listings(tmp_path, MORNING_19), None)
    assert {r["kind"] for r in rows} == {"new_listing"}
    assert {r["id"]: r["tier"] for r in rows}["new_listing:ANT:2026-11-19"] == "notice"  # no offer price known yet


def test_a_listing_outside_the_window_or_not_new_is_left_out():
    base = {"symbol": "NEWX", "is_new_listing": True, "kind": "ipo", "status": "listed", "company": "New X Inc."}
    back, ahead = pn.window(date(2026, 11, 19))
    now = MORNING_19
    assert pn.listing_rows([{**base, "first_session": date(2026, 11, 12)}], back, ahead, now) == []
    assert pn.listing_rows([{**base, "first_session": date(2026, 11, 19), "kind": "fund"}], back, ahead, now) == []
    reg = {**base, "first_session": date(2026, 11, 19), "status": "registered"}
    assert pn.listing_rows([reg], back, ahead, now) == []
    big = pn.listing_rows([{**base, "first_session": date(2026, 11, 13), "offer_price": 20.0, "shares_offered": 6e8}],
                          back, ahead, now)
    assert len(big) == 1 and big[0]["tier"] == "major"


class FakeStore:
    def __init__(self, existing):
        self.rows = {r["id"]: dict(r) for r in existing}
        self.calls = []

    def upsert(self, table, rows, on_conflict):
        self.calls.append(("upsert", table, on_conflict))
        for r in rows:
            self.rows[r["id"]] = dict(r)

    def select(self, table, params):
        self.calls.append(("select", table, params))
        return [{"id": k} for k, r in self.rows.items() if not r.get("withdrawn")]

    def update(self, table, match, values):
        self.calls.append(("update", table, match))
        self.rows[match["id"]].update(values)


def test_publish_upserts_and_withdraws_what_it_no_longer_finds(tmp_path):
    rows, back, ahead = pn.build(MORNING_19, _listings(tmp_path, MORNING_19), fake_alarm)
    st = FakeStore([{"id": "new_listing:OLDX:2026-11-18", "withdrawn": False}])
    res = pn.publish(st, rows, back, ahead, MORNING_19)
    assert res == {"written": len(rows), "withdrawn": ["new_listing:OLDX:2026-11-18"]}
    assert st.rows["new_listing:OLDX:2026-11-18"]["withdrawn"] is True
    assert {c[1] for c in st.calls} == {"market_notice"}  # the only table it touches
    sel = next(c for c in st.calls if c[0] == "select")[2]
    assert sel["and"] == "(session.gte.2026-11-13,session.lte.2026-11-27)"


def test_a_dry_run_writes_nothing(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(pn, "load_alarm", lambda: None)
    assert pn.main(["--data", str(tmp_path), "--now", "2026-11-19T06:45:00Z"]) == 0
    assert "dry run: nothing written" in capsys.readouterr().out


def _imports(path: Path) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


def test_nothing_that_trades_names_the_publisher_or_its_table():
    src = ROOT / "src" / "kaibot"
    bad = [str(f.relative_to(src)) for pkg in ("engine", "risk", "strategies", "broker", "features", "replay")
           for f in (src / pkg).rglob("*.py") if "publish_notices" in f.read_text() or "market_notice" in f.read_text()]
    assert bad == []
    mods = _imports(SCRIPT)
    assert not any(m.startswith(("kaibot.risk", "kaibot.strategies", "kaibot.broker", "kaibot.replay")) for m in mods)
    assert {m for m in mods if m.startswith("kaibot.engine")} <= {"kaibot.engine.config", "kaibot.engine.store"}


def test_the_daily_job_runs_it_only_when_switched_on():
    sh = (ROOT / "scripts" / "service_run.sh").read_text()
    assert "switch KAIBOT_NOTICES 0" in sh and "publish_notices.py" in sh


@pytest.mark.parametrize("flag,hour", [("fed_day", 19), ("cpi_day", 13)])
def test_times_are_new_york_times_in_utc(flag, hour):
    assert datetime.fromisoformat(pn._starts(flag, date(2026, 12, 9))).hour == hour
