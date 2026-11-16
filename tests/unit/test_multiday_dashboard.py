"""Issue #229: the multi-day dashboard's data (contract in the issue). Reporting only.

1. The rule's sell-condition readings (`TargetList.features["sell_when"]`) agree with its own sell decisions at every
   decision of the committed fixture, and adding them changes no decision.
2. The engine's events carry what the RPCs read (`multi_day_decision`: decided_at, held, buy candidates, sell
   readings, sectors; `multi_day_plan`: the sized targets), and the heartbeat carries the book's stop line
   (`multi_day.book_line`), also after the close.
3. Migration 0020 in a throwaway Postgres (never Supabase): kb_book's `sell_when` and `sector`, kb_multiday_plan's
   orders and their states, kb_book_closed's sold holding; owner-only, nothing for anon."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------- 1. the readings agree with the decisions


def test_the_sell_readings_agree_with_the_rules_own_sell_decisions_and_change_none():
    from kaibot.engine import daily_report as dr
    from kaibot.engine.daily import DailyData, decide_after_close
    from kaibot.strategies import momentum as mom

    market, info = dr.load_fixture()
    data = DailyData.from_market(market)
    for name in ("trend_momentum", "mom_12_1_band"):
        rule = mom.VERSIONS[name]
        held: dict[str, float] = {}
        seen = {"rank": 0, "trend": 0, "gap": 0, "kept": 0}
        for k in range(300, len(data.dates) - 1):
            tl = decide_after_close(rule, data, k, data.day(k + 1), held)
            if tl is None:
                continue
            with pytest.MonkeyPatch.context() as mp:  # the same decision without the readings
                mp.setattr(type(rule), "sell_when", lambda self, *a, **kw: {})
                bare = decide_after_close(rule, data, k, data.day(k + 1), held)
            assert list(bare.weights.items()) == list(tl.weights.items())
            assert bare.features["sell"] == tl.features["sell"]
            sw, sell = tl.features["sell_when"], tl.features["sell"]
            assert set(sw) == set(held)
            for sym, rows in sw.items():
                met = {r["code"] for r in rows if r["met"]}
                why = sell.get(sym)
                if why is None:
                    assert not met, (name, k, sym, rows)
                elif why == "rank":
                    assert "rank" in met and not ({"trend", "gap"} & met), (name, k, sym, rows)
                else:  # trend_momentum: its own sell_if (gap, then trend) comes before the rank
                    assert why in met, (name, k, sym, rows)
                seen[why or "kept"] += 1
                assert rows[0]["code"] == "rank" and rows[0]["of"] == tl.features["ranked"]
                assert rows[0]["as_of"] == str(data.day(k))
            held = {s: 1.0 for s in tl.weights}
        assert seen["kept"] > 0 and seen["rank"] > 0, (name, seen)


# ---------------------------------------------------------------- 2. the engine's events and heartbeat


def test_the_events_and_the_heartbeat_carry_what_the_dashboard_reads(tmp_path, monkeypatch):
    from test_live_daily_engine import MD, DailyAccount

    a = DailyAccount(tmp_path, monkeypatch)
    a.ticks(a.open1, 0, 390)
    a.after_close(a.open1)
    ev = {e["type"]: e["payload"] for e in a.store.tables["event"]}
    dec = ev["multi_day_decision"]
    assert dec["strategy"] == MD and dec["decides"] and dec["decided_at"] and dec["held"] == {}
    assert dec["buy_candidates"] == ["B"] and dec["sectors"] == {"B": None} and dec["sell_when"] == {}
    a.ticks(a.open2, 0, 2)  # 09:31: the plan is sized and sent
    plans = [e["payload"] for e in a.store.tables["event"] if e["type"] == "multi_day_plan"]
    assert [x["note"] for x in plans if x["session"] != dec["fill_session"]] == ["no decision"]  # DAY1: none yet
    (p,) = [x for x in plans if x["session"] == dec["fill_session"]]
    (t,) = p["targets"]
    assert p["session"] == dec["fill_session"] and t[0] == "B" and t[1] == 0.0 and t[2] >= 1 and t[3] == "buy"
    a.ticks(a.open2, 2, 390)
    a.after_close(a.open2)
    st = a.eng._daily_status({"state": "market_closed"})
    line = st["multi_day"].get("book_line")
    assert line is not None and line["run_id"] == a.rid and not line["ended"]
    assert line["peak"] >= line["equity"] - 0.01 and line["end_pct"] > 0 and line["peak_ts"]
    assert line["drawdown_pct"] == pytest.approx(max(0.0, (line["peak"] - line["equity"]) / line["peak"] * 100),
                                                 abs=0.01)
    assert st["multi_day"]["book"] == "open"  # the old field is unchanged


# ---------------------------------------------------------------- 3. migration 0020 in a throwaway Postgres


def _bindir() -> Path | None:
    for d in sorted(Path("/usr/lib/postgresql").glob("*/bin"), reverse=True):
        if (d / "initdb").exists():
            return d
    found = shutil.which("initdb")
    return Path(found).parent if found else None


class Pg:
    def __init__(self) -> None:
        self.bin = _bindir()
        if self.bin is None:
            pytest.skip("no PostgreSQL binaries")
        self.dir = Path(tempfile.mkdtemp(prefix="kaibot-pg229-"))
        os.chmod(self.dir, 0o755)
        self.as_pg: list[str] = []
        if os.geteuid() == 0:
            if shutil.which("runuser") is None:
                pytest.skip("running as root without runuser")
            shutil.chown(self.dir, "postgres")
            self.as_pg = ["runuser", "-u", "postgres", "--"]
        subprocess.run([*self.as_pg, str(self.bin / "initdb"), "-D", str(self.dir / "data"), "-A", "trust", "-U",
                        "postgres"], check=True, capture_output=True)
        subprocess.run([*self.as_pg, str(self.bin / "pg_ctl"), "-D", str(self.dir / "data"), "-w", "-l",
                        str(self.dir / "log"), "-o", f"-k {self.dir} -p 5499 -c listen_addresses='' -c fsync=off",
                        "start"], check=True, capture_output=True)
        self.psql = str(self.bin / "psql")

    def sql(self, q: str, role: str | None = None) -> list[str]:
        if role:
            q = f"set role {role};\n{q}"
        out = subprocess.run([self.psql, "-h", str(self.dir), "-p", "5499", "-U", "postgres", "-d", "postgres", "-X",
                              "-q", "-At", "-v", "ON_ERROR_STOP=1"], input=q, capture_output=True, text=True)
        if out.returncode != 0:
            raise RuntimeError(out.stderr)
        return [x for x in out.stdout.splitlines() if x]

    def json(self, q: str, role: str | None = None):
        return json.loads(self.sql(q, role)[-1])

    def stop(self) -> None:
        subprocess.run([*self.as_pg, str(self.bin / "pg_ctl"), "-D", str(self.dir / "data"), "-m", "immediate",
                        "stop"], capture_output=True)
        shutil.rmtree(self.dir, ignore_errors=True)


STUBS = """
create role anon; create role authenticated;
create schema kaibot;
grant usage on schema kaibot to authenticated;
create table kaibot.owner_flag (owner boolean not null);
insert into kaibot.owner_flag values (true);
create function kaibot.is_owner() returns boolean language sql stable as $$ select owner from kaibot.owner_flag $$;
create table kaibot.run (id bigint primary key, amount_usd numeric not null);
create table kaibot.event (id bigint generated always as identity primary key, ts timestamptz not null default now(),
  run_id bigint references kaibot.run(id), type text not null, payload jsonb not null, prev_hash text,
  hash text not null default '');
"""


def _ledger_ddl() -> str:
    text = (ROOT / "migrations" / "0018_m7_ledger.sql").read_text()
    return "\n".join(re.findall(r"create table if not exists kaibot\.ledger_\w+ \(.*?\n\);", text, re.S))


R = 7
DAYS = ("2026-11-18", "2026-11-19", "2026-11-20")


@pytest.fixture(scope="module")
def pg():
    db = Pg()
    try:
        db.sql(STUBS)
        db.sql(_ledger_ddl())
        db.sql((ROOT / "migrations" / "0020_m7_multiday_dashboard.sql").read_text())
        db.sql((ROOT / "migrations" / "0020_m7_multiday_dashboard.sql").read_text())  # idempotent
        db.sql(f"insert into kaibot.run values ({R}, 10000)")
        for d in DAYS:
            db.sql(f"insert into kaibot.ledger_day (run_id, session, start_equity, close_equity, close_equity_venue, "
                   f"cash, invested, realized_venue, pnl_venue, pnl_shadow, gross, overnight, positions, ts) values "
                   f"({R}, '{d}', 10000, 10000, 10000, 9000, 1000, 0, 0, 0, 0.1, 0.1, 1, '{d}T21:00:00Z')")
        rows = [("2026-11-18", "XOM", 2, 116.2), ("2026-11-19", "XOM", 2, 116.2)] + [
            (d, "AAPL", 4, 238.1) for d in DAYS]
        for d, s, q, c in rows:
            db.sql(f"insert into kaibot.ledger_position (run_id, session, strategy, symbol, qty, avg_cost, mark, "
                   f"priced, opened_session, dividends, stop_state) values ({R}, '{d}', 'trend_momentum', '{s}', {q}, "
                   f"{c}, {c}, true, '2026-11-18', 0, 'missing')")

        def ev(kind, payload, ts):
            db.sql(f"insert into kaibot.event (run_id, type, payload, ts) values ({R}, '{kind}', "
                   f"'{json.dumps(payload)}'::jsonb, '{ts}')")

        sw = {"XOM": [{"code": "rank", "reading": 131, "threshold": 100, "of": 503, "met": True, "as_of": DAYS[1]}],
              "AAPL": [{"code": "rank", "reading": 12, "threshold": 100, "of": 503, "met": False, "as_of": DAYS[1]}]}
        ev("multi_day_decision", {"strategy": "trend_momentum", "session": DAYS[0], "fill_session": DAYS[1],
                                  "decides": True, "sell": {}, "buy": [], "sell_when": {}, "sectors": {}},
           "2026-11-18T21:30:00Z")
        ev("multi_day_decision", {"strategy": "trend_momentum", "session": DAYS[1], "fill_session": DAYS[2],
                                  "decides": True, "sell": {"XOM": "rank"}, "buy": ["MSFT"],
                                  "buy_candidates": ["MSFT", "NVR"], "decided_at": "2026-11-19T16:31:00-05:00",
                                  "held": {"XOM": 2, "AAPL": 4}, "ranked": 503, "sell_when": sw,
                                  "sectors": {"XOM": "Energy", "AAPL": "Information Technology", "MSFT": None}},
           "2026-11-19T21:31:00Z")
        ev("multi_day_plan", {"strategy": "trend_momentum", "session": DAYS[2], "decided": DAYS[1], "skipped": 1,
                              "targets": [["XOM", 2.0, 0, "sell", "rank"], ["AAPL", 4.0, 4, "resize", 1.0],
                                          ["MSFT", 0.0, 3, "buy", 1.0], ["NVDA", 0.0, 2, "buy", 1.0]]},
           "2026-11-20T14:00:00Z")
        ev("multi_day_orders", {"strategy": "trend_momentum", "session": DAYS[2], "deferred": ["NVDA"],
                                "sent": [{"coid": "c-xom", "symbol": "XOM", "side": "sell", "qty": 2,
                                          "state": "accepted"},
                                         {"coid": "c-msft", "symbol": "MSFT", "side": "buy", "qty": 3,
                                          "state": "accepted"}], "skipped": [], "canceled": [], "retry": True},
           "2026-11-20T14:31:00Z")
        ev("multi_day_fill", {"strategy": "trend_momentum", "symbol": "XOM", "side": "sell", "qty": 2, "px": 113.1,
                              "coid": "c-xom"}, "2026-11-20T21:01:00Z")
        yield db
    finally:
        db.stop()


def test_kb_book_carries_the_sell_readings_and_the_sector_of_each_position(pg):
    b = pg.json(f"select public.kb_book({R}, '{DAYS[1]}')")
    by = {p["symbol"]: p for p in b["positions"]}
    assert by["XOM"]["sell_when"][0] == {"code": "rank", "reading": 131, "threshold": 100, "of": 503, "met": True,
                                         "as_of": DAYS[1]}
    assert by["XOM"]["sector"] == "Energy" and by["AAPL"]["sector"] == "Information Technology"
    assert by["AAPL"]["qty"] == 4 and b["mtm"]["equity"] == 10000  # 0018's fields unchanged
    first = pg.json(f"select public.kb_book({R}, '{DAYS[0]}')")
    assert {p["symbol"]: p["sell_when"] for p in first["positions"]} == {"AAPL": None, "XOM": None}


def test_kb_multiday_plan_gives_the_next_close_and_each_orders_state(pg):
    plan = pg.json(f"select public.kb_multiday_plan({R})")
    (r,) = plan["rules"]
    assert (r["rule"], r["decided_after_close_of"], r["fills_at_close_of"]) == ("trend_momentum", DAYS[1], DAYS[2])
    assert r["sell"] == [{"symbol": "XOM", "why": "rank"}] and r["buy_candidates"] == ["MSFT", "NVR"]
    by = {o["symbol"]: o for o in r["orders"]}
    assert set(by) == {"XOM", "MSFT", "NVDA"}  # AAPL keeps its shares: no order
    assert (by["XOM"]["action"], by["XOM"]["state"], by["XOM"]["why"]) == ("sell", "filled", "rank")
    assert (by["MSFT"]["action"], by["MSFT"]["state"], by["MSFT"]["coid"]) == ("buy", "working", "c-msft")
    assert by["NVDA"]["state"] == "deferred" and by["NVDA"]["shares_target"] == 2
    assert r["skipped"] == 1 and r["note"] is None


def test_kb_book_closed_lists_the_sold_holding_with_its_exit_and_reason(pg):
    (c,) = pg.json(f"select public.kb_book_closed({R})")
    assert c == {"rule": "trend_momentum", "symbol": "XOM", "opened_session": DAYS[0], "closed_session": DAYS[2],
                 "qty": 2, "avg_cost": 116.2, "exit_px": 113.1, "pnl_usd": -6.2, "dividends": 0, "days_held": 2,
                 "reason": "rank"}
    assert pg.json(f"select public.kb_book_closed({R}, 0)") == []


def test_the_rpcs_are_owner_only_and_closed_to_anon(pg):
    pg.sql("update kaibot.owner_flag set owner = false")
    try:
        for q in (f"select public.kb_multiday_plan({R})", f"select public.kb_book_closed({R})",
                  f"select public.kb_book({R})"):
            with pytest.raises(RuntimeError, match="not authorised"):
                pg.sql(q, role="authenticated")
    finally:
        pg.sql("update kaibot.owner_flag set owner = true")
    for q in (f"select public.kb_multiday_plan({R})", f"select public.kb_book_closed({R})",
              f"select kaibot.md_decision_at({R}, 'trend_momentum', '{DAYS[2]}')"):
        with pytest.raises(RuntimeError, match="permission denied"):
            pg.sql(q, role="anon")


def test_the_unchecked_helper_and_kb_book_are_closed_to_callers(pg):
    """QA-Ops (#247): `kaibot.md_decision_at` has no owner check of its own, so no caller role may run it directly
    (only the owner-checked RPCs, as security definer, do); `kb_book` stays closed to anon after its replacement."""
    q = f"select kaibot.md_decision_at({R}, 'trend_momentum', '{DAYS[2]}')"
    for role in ("authenticated", "anon"):
        with pytest.raises(RuntimeError, match="permission denied"):
            pg.sql(q, role=role)
    with pytest.raises(RuntimeError, match="permission denied"):
        pg.sql(f"select public.kb_book({R})", role="anon")
    assert pg.json(f"select public.kb_book({R})", role="authenticated")["run_id"] == R  # the owner still reads it
