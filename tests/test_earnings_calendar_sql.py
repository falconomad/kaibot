"""Migration 0054: the upcoming-earnings calendar of the held stocks (#797 follow-up). DISPLAY ONLY.

The file checks run everywhere; the SQL checks need the scratch Postgres of tests/pg_scratch.py (KAIBOT_TEST_PG)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from test_helper_role_sql import HELPER, OTHER, OWNER, needs_pg, psql, refused, seeded  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[1]
M54 = ROOT / "migrations" / "0054_earnings_calendar.sql"
TRADING = ["engine", "features", "strategies", "risk", "broker", "agents", "replay", "news"]


def _code() -> str:
    return "\n".join(ln for ln in M54.read_text().splitlines() if not ln.lstrip().startswith("--")).lower()


# ---------------------------------------------------------------- the file


def test_0054_is_additive_and_deletes_only_past_rows_of_its_own_table():
    s = _code()
    assert not re.search(r"\b(drop|truncate|alter function)\b", s)
    assert re.findall(r"delete from (kaibot\.\w+)", s) == ["kaibot.earnings_calendar"]
    assert "where c.report_date < today" in s
    assert re.findall(r"insert into (kaibot\.\w+)", s) == ["kaibot.earnings_calendar"]
    assert "enable row level security" in s
    assert "revoke all on kaibot.earnings_calendar from public, anon, authenticated;" in s
    assert re.findall(r"create or replace function (\w+\.\w+)\(", s) == [
        "kaibot.earnings_calendar_refresh", "public.kb_upcoming_earnings"]
    assert "grant execute on function kaibot.earnings_calendar_refresh(jsonb, timestamptz) to service_role;" in s
    assert " to anon" not in s
    assert not any(re.match(r"^--\s*(NOT )?APPLIED\b", ln) for ln in M54.read_text().splitlines())


def test_0054_functions_are_definer_with_empty_search_path_and_the_read_is_owner_first():
    s = _code()
    for body in re.split(r"create or replace function ", s)[1:]:
        assert "security definer set search_path = ''" in body.split("as $$")[0], body[:60]
    body = s.split("create or replace function public.kb_upcoming_earnings(")[1].split("end $$;")[0]
    assert body.index("if not kaibot.is_owner() then raise exception 'not authorised'") < body.index("from kaibot.run")


def test_the_engine_never_reads_the_calendar():
    """Display only until the pre-registered filter of decision 20 passes gate v3.1 and the stakeholder approves."""
    src = ROOT / "src" / "kaibot"
    hits = [str(f.relative_to(src)) for pkg in TRADING for f in (src / pkg).rglob("*.py")
            if re.search(r"kb_upcoming_earnings|earnings_calendar_refresh|kaibot\.earnings_calendar\b|"
                         r"ingest\.alphavantage|ingest import alphavantage", f.read_text())]
    assert hits == []


# ---------------------------------------------------------------- the database

ROWS = [  # today + n days, New York
    ("ZZA", 3, "2026-12-31", "pre-market", 1.25), ("ZZB", 40, "2026-12-31", None, None),
    ("ZZC", 5, "2026-12-31", "post-market", 0.5), ("ZZA", -2, "2026-09-30", None, 1.0),
]


def _rows(rows=ROWS) -> str:
    out = []
    for sym, n, fde, t, est in rows:
        out.append(f"jsonb_build_object('symbol', '{sym}', 'report_date', "
                   f"((now() at time zone 'America/New_York')::date + {n})::text, 'fiscal_date_ending', '{fde}', "
                   f"'report_time', {'null' if t is None else repr(t)}, 'estimate', {'null' if est is None else est}, "
                   "'currency', 'USD')")
    return "jsonb_build_array(" + ", ".join(out) + ")"


def _refresh(rows=ROWS) -> dict:
    return json.loads(psql(f"select kaibot.earnings_calendar_refresh({_rows(rows)}, now());", role="service_role"))


def _hold(*symbols: str) -> None:
    vals = ", ".join(f"('2099-01-02'::date, 'a', '{s}', 3::numeric, 10::numeric)" for s in symbols)
    # a fixed id (overriding the identity): the run sequence is left as it was for the other SQL tests
    _drop_runs()
    psql(f"""with r as (insert into kaibot.run (id, amount_usd, status, kind) overriding system value
                        values (9054054, 54054.54, 'running', 'paper') returning id)
      insert into kaibot.ledger_position (run_id, session, strategy, symbol, qty, avg_cost, mark, priced, stop_state)
      select r.id, s.session, s.strategy, s.symbol, s.qty, 10, s.mark, true, 'resting' from r, (values {vals},
        ('2099-01-01'::date, 'a', 'ZZB', 1::numeric, 10::numeric)) s(session, strategy, symbol, qty, mark);""",
         role=None)


def _drop_runs() -> None:
    """This file's own runs (amount 54054.54) and their rows, so later SQL tests in the same database see none."""
    psql("""delete from kaibot.ledger_position where run_id in (select id from kaibot.run where amount_usd = 54054.54);
      delete from kaibot.run where amount_usd = 54054.54;""", role=None)


@pytest.fixture
def cal(seeded):  # noqa: F811
    psql("delete from kaibot.earnings_calendar;", role=None)
    yield
    psql("delete from kaibot.earnings_calendar;", role=None)
    _drop_runs()


@needs_pg
def test_refresh_upserts_by_symbol_and_quarter_and_prunes_past_reports(cal):
    out = _refresh()
    assert out["upserted"] == 4 and out["pruned"] == 1 and out["rows"] == 3  # ZZA's past quarter is gone
    moved = [("ZZA", 4, "2026-12-31", "post-market", 1.3)]
    out = _refresh(moved)
    assert out == {"upserted": 1, "pruned": 0, "rows": 3}
    got = psql("select report_time || ' ' || estimate from kaibot.earnings_calendar where symbol = 'ZZA';", role=None)
    assert got == "post-market 1.3000"
    refused(f"select kaibot.earnings_calendar_refresh({_rows()}, now());", "permission denied", uid=OWNER)
    refused("select kaibot.earnings_calendar_refresh('{}'::jsonb, now());", "array", role="service_role")


@needs_pg
def test_the_owner_sees_the_held_stocks_reports_in_the_window_only(cal):
    _refresh()
    _hold("ZZA", "ZZB", "ZZC")  # ZZB's report is 40 days out
    out = json.loads(psql("select public.kb_upcoming_earnings(30);", uid=OWNER))
    assert out["source"] == "alphavantage" and out["fetched_at"]
    assert [(i["symbol"], i["days_until"], i["report_time"]) for i in out["items"]] == [
        ("ZZA", 3, "pre-market"), ("ZZC", 5, "post-market")]
    assert out["items"][0]["estimate"] == 1.25
    _hold("ZZC")  # ZZA sold: no longer shown
    out = json.loads(psql("select public.kb_upcoming_earnings(60);", uid=OWNER))
    assert [i["symbol"] for i in out["items"]] == ["ZZC"]
    for uid in (HELPER, OTHER):
        refused("select public.kb_upcoming_earnings(30);", "not authorised", uid=uid)
    refused("select public.kb_upcoming_earnings(30);", "permission denied", role="anon")
    refused("select * from kaibot.earnings_calendar;", "permission denied", uid=OWNER)
