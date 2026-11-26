"""Migration 0050: the Activity tape (kb_tape) lists the multi-day rule's fills (stakeholder bug, 26 Nov 2026).

kb_tape (0009) read fills only from kaibot.fill, the retired intraday path's table; the live multi-day rule books its
fills as `multi_day_fill` engine events. 0050 adds one 'fill' item per such event of the run.

The file checks run everywhere: 0050's kb_tape is 0009's with only the new union branch added, it reads exactly the
payload keys the engine writes, and it is additive. The SQL checks need the scratch Postgres of tests/pg_scratch.py
(KAIBOT_TEST_PG; CI and KAIBOT_TEST_PG_AUTO=1 build one; skipped otherwise)."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
M09 = ROOT / "migrations" / "0009_m3_dashboard.sql"
M50 = ROOT / "migrations" / "0050_tape_multiday_fills.sql"
ENGINE = ROOT / "src" / "kaibot" / "engine" / "live_daily.py"
PG = os.environ.get("KAIBOT_TEST_PG")
needs_pg = pytest.mark.skipif(not PG, reason="set KAIBOT_TEST_PG to a scratch Postgres with migration 0050 applied")
OWNER, OTHER = "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"
FN = "public.kb_tape(bigint, int, timestamptz)"


def _body(path: Path) -> list[str]:
    s = path.read_text()
    i = s.index("create or replace function public.kb_tape")
    return [ln for ln in s[i:s.index("end $$;", i)].splitlines() if not ln.lstrip().startswith("--")]


def _code(path: Path) -> str:
    return "\n".join(ln for ln in path.read_text().splitlines() if not ln.lstrip().startswith("--")).lower()


# ---------------------------------------------------------------- the file


def test_0050_is_0009s_kb_tape_with_only_the_multi_day_branch_added():
    old, new = _body(M09), _body(M50)
    intraday = new.index('      from kaibot.fill f join kaibot."order" o on o.id = f.order_id')
    start = new.index("      union all", intraday)
    end = new.index("    ) u order by u.ts desc limit lim")
    added = new[start:end]
    assert new[:start] + new[end:] == old
    text = "\n".join(added)
    assert "e.type = 'multi_day_fill'" in text and "e.run_id = p_run" in text and "e.ts < p_before" in text
    assert "o2.client_order_id = m.coid" in text  # never shows a fill twice


def test_0050_reads_the_payload_keys_the_engine_writes():
    src = ENGINE.read_text()
    m = re.search(r'self\.event\("multi_day_fill", \{(.*?)\}\)', src, re.S)
    assert m, "live_daily.py no longer emits multi_day_fill"
    sent = set(re.findall(r'"(\w+)":', m.group(1)))
    assert sent == {"strategy", "symbol", "side", "qty", "px", "coid"}
    read = set(re.findall(r"e\.payload->>?'(\w+)'", "\n".join(_body(M50))))
    # every key the engine sends is read; the optional ones are read only if a later engine adds them
    assert sent <= read
    assert read - sent == {"shadow_px", "fees", "exit_reason", "reason"}


def test_0050_is_additive_and_keeps_the_grants():
    s = _code(M50)
    assert not re.search(r"\b(drop|truncate|alter|delete from|insert into|update)\b", s)
    assert re.findall(r"create or replace function (\w+\.\w+)\(", s) == ["public.kb_tape"]
    assert "language plpgsql stable security definer set search_path = '' as $$" in s
    assert "if not kaibot.is_owner() then raise exception 'not authorised'" in s
    assert f"revoke all on function {FN} from public, anon;" in s
    assert f"grant execute on function {FN} to authenticated;" in s
    assert " to anon" not in s
    assert not any(re.match(r"^--\s*(NOT )?APPLIED\b", ln) for ln in M50.read_text().splitlines())


def test_0050_is_the_latest_definition_of_kb_tape():
    later = sorted(p.name for p in (ROOT / "migrations").glob("*.sql")
                   if p.name > M50.name and "function public.kb_tape(" in p.read_text())
    assert later == []


# ---------------------------------------------------------------- on a scratch Postgres


def psql(sql: str, uid: str = "") -> str:
    r = subprocess.run(["psql", *shlex.split(PG), "-tA", "-q", "-v", "ON_ERROR_STOP=1"],
                       input=f"set test.uid = '{uid}';\n" + sql, capture_output=True, text=True)
    if r.returncode:
        raise AssertionError(r.stderr)
    return r.stdout.strip()


@pytest.fixture(autouse=True)
def owner():
    if PG:
        psql(f"""insert into auth.users (id) values ('{OWNER}'), ('{OTHER}') on conflict do nothing;
          insert into kaibot.owner (user_id) values ('{OWNER}') on conflict do nothing;""")


XOM = {"strategy": "trend_momentum", "symbol": "XOM", "side": "sell", "qty": 20, "px": 158.862,
       "coid": "kb128381296941-trend_momentum-XOM-20261125-sell-1"}


def _ev(ts: str, payload: dict, run: str = ":r1", typ: str = "multi_day_fill") -> str:
    body = json.dumps(payload).replace("'", "''")
    return f"('{ts}'::timestamptz, {run}, '{typ}', '{body}'::jsonb, md5(random()::text))"


def tape(events: list[str], calls: list[str], extra: str = "") -> list[list[dict]]:
    """Seed two runs and the events in one transaction, call kb_tape once per `calls` (as the owner, with :r1 / :r2
    substituted) and roll back (kaibot.event is append-only). Returns each call's items."""
    sel = "\n".join(f"select coalesce({c}, 'null'::jsonb)::text;" for c in calls)
    out = psql(f"""begin;
      insert into kaibot.run (amount_usd, status) values (10000, 'running') returning id as r1 \\gset
      insert into kaibot.run (amount_usd, status) values (10000, 'stopped') returning id as r2 \\gset
      insert into kaibot.event (ts, run_id, type, payload, hash) values
      {",".join(events)};
      {extra}
      set local test.uid = '{OWNER}'; set local role authenticated;
      {sel}
      rollback;""").splitlines()
    return [json.loads(x) for x in out[-len(calls):]]


@needs_pg
def test_a_multi_day_fill_event_is_a_fill_item_and_another_runs_is_not():
    buy = {"strategy": "trend_momentum", "symbol": "AAPL", "side": "buy", "qty": 3, "px": 231.4,
           "coid": "kb128381296941-trend_momentum-AAPL-20261124-buy-1"}
    [items] = tape([_ev("2026-11-25 20:51:00+00", XOM), _ev("2026-11-24 20:50:00+00", buy),
                    _ev("2026-11-25 20:52:00+00", {**XOM, "symbol": "CVX"}, run=":r2")],
                   ["public.kb_tape(:r1)"])
    assert [(i["kind"], i["symbol"], i["side"]) for i in items] == [("fill", "XOM", "sell"), ("fill", "AAPL", "buy")]
    x = items[0]
    assert datetime.fromisoformat(x.pop("ts")) == datetime(2026, 11, 25, 20, 51, tzinfo=UTC)
    assert x == {"kind": "fill", "source": "multi_day", "side": "sell", "qty": 20,
                 "venue_px": 158.862, "shadow_px": None, "fees": None, "tag": None, "symbol": "XOM",
                 "strategy": "trend_momentum", "coid": XOM["coid"], "trade_id": None, "exit_reason": None,
                 "pnl_usd": None}
    assert items[1]["exit_reason"] is None  # a buy has no exit reason


@needs_pg
def test_a_sales_reason_comes_from_the_payload_the_decision_or_the_order_id():
    stop = {**XOM, "symbol": "MRO", "coid": "kb1-trend_momentum-MRO-20261125-stop-1"}
    flat = {**XOM, "symbol": "OXY", "coid": "kb1-trend_momentum-OXY-20261125-flatten-2"}
    said = {**XOM, "symbol": "HAL", "reason": "gap", "shadow_px": 50.1, "fees": 0.02}
    decision = {"strategy": "trend_momentum", "session": "2026-11-24", "fill_session": "2026-11-25", "decides": True,
                "sell": {"XOM": "below_average", "MRO": "below_average"}}
    [items] = tape([_ev("2026-11-25 20:51:00+00", XOM), _ev("2026-11-25 20:40:00+00", stop),
                    _ev("2026-11-25 20:30:00+00", flat), _ev("2026-11-25 20:20:00+00", said),
                    _ev("2026-11-24 22:00:00+00", decision, typ="multi_day_decision")],
                   ["public.kb_tape(:r1)"])
    why = {i["symbol"]: i["exit_reason"] for i in items}
    # the order's own purpose (a resting stop that fired) wins over the decision's code for the same symbol
    assert why == {"XOM": "below_average", "MRO": "stop", "OXY": "flatten", "HAL": "gap"}
    hal = next(i for i in items if i["symbol"] == "HAL")
    assert (hal["shadow_px"], hal["fees"]) == (50.1, 0.02)


@needs_pg
def test_malformed_events_are_skipped_not_an_error():
    bad = [{**XOM, "side": "short"}, {**XOM, "qty": "20"}, {**XOM, "px": None}, {**XOM, "qty": 0},
           {k: v for k, v in XOM.items() if k != "symbol"}, {"symbol": "AAPL"}]
    [items] = tape([_ev(f"2026-11-25 20:{10 + n}:00+00", b) for n, b in enumerate(bad)]
                   + [_ev("2026-11-25 20:51:00+00", {k: v for k, v in XOM.items() if k != "coid"})],
                   ["public.kb_tape(:r1)"])
    assert [i["symbol"] for i in items] == ["XOM"]  # without a coid it is still a fill (nothing to match it to)
    assert items[0]["coid"] is None and items[0]["exit_reason"] is None


@needs_pg
def test_a_fill_already_in_kaibot_fill_is_not_shown_twice():
    extra = f"""insert into kaibot.signal (run_id, strategy, mode, symbol, ts, features_hash, features)
        values (:r1, 'trend_momentum', 'live', 'XOM', '2026-11-25 20:00:00+00', 'h', '{{}}') returning id as s1 \\gset
      insert into kaibot."order" (signal_id, client_order_id, legs, status)
        values (:s1, '{XOM["coid"]}', '[]', 'filled') returning id as o1 \\gset
      insert into kaibot.fill (order_id, ts, side, qty, venue_px, shadow_px)
        values (:o1, '2026-11-25 20:51:00+00', 'sell', 20, 158.862, 158.8);"""
    other = {**XOM, "symbol": "CVX", "coid": "kb1-trend_momentum-CVX-20261125-sell-1"}
    [items] = tape([_ev("2026-11-25 20:51:00+00", XOM), _ev("2026-11-25 20:50:00+00", other)],
                   ["public.kb_tape(:r1)"], extra)
    fills = [(i["symbol"], i.get("source")) for i in items if i["kind"] == "fill"]
    assert fills == [("XOM", None), ("CVX", "multi_day")]  # XOM once, from kaibot.fill (with its shadow price)
    assert next(i for i in items if i["kind"] == "fill" and i["symbol"] == "XOM")["shadow_px"] == 158.8


@needs_pg
def test_paging_by_ts_walks_every_multi_day_fill_once():
    evs = [_ev(f"2026-11-{17 + n:02d} 20:5{n % 10}:00+00", {**XOM, "symbol": f"S{n}", "coid": f"kb1-r-S{n}-x-sell-1"})
           for n in range(7)]
    first, second, third, big, tiny = tape(evs, [
        "public.kb_tape(:r1, 3)",
        "public.kb_tape(:r1, 3, '2026-11-21 20:54:00+00')",
        "public.kb_tape(:r1, 3, '2026-11-18 20:51:00+00')",
        "public.kb_tape(:r1, 9999)",
        "public.kb_tape(:r1, 0)"])
    assert [i["symbol"] for i in first] == ["S6", "S5", "S4"]
    assert [i["symbol"] for i in second] == ["S3", "S2", "S1"]
    assert [i["symbol"] for i in third] == ["S0"]
    assert [i["symbol"] for i in big] == [f"S{n}" for n in range(6, -1, -1)]
    assert len(tiny) == 1  # the limit is clamped to 1-500, as 0009's


@needs_pg
def test_kb_tape_stays_owner_only():
    with pytest.raises(AssertionError, match="not authorised"):
        psql("set role authenticated; select public.kb_tape(-1);", uid=OTHER)
    with pytest.raises(AssertionError, match="permission denied"):
        psql("set role anon; select public.kb_tape(-1);", uid=OWNER)
    assert psql("select prosrc like '%multi_day_fill%' and provolatile = 's' and prosecdef and "
                "array_to_string(proconfig, ',') = 'search_path=\"\"' from pg_proc where proname = 'kb_tape';") == "t"
