"""#529, migration 0040: `kb_command`'s `accept_reconcile` also takes the owner's word on an unbooked fill (a row of
kind "unbooked": symbol, coid, venue, booked, action book | accept). Static checks always run; the SQL ones need a
scratch Postgres with the migrations applied (KAIBOT_TEST_PG, or KAIBOT_TEST_PG_AUTO=1 as in CI). The engine side
is tests/unit/test_unbooked_owner.py and, end to end, tests/dst/test_unbooked_owner_dst.py."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from test_accept_reconcile_command import OTHER, REASON, SHOWN, call, commands, db, needs_pg  # noqa: F401

from kaibot.engine.reconcile import ACCEPT_COMMAND

ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT / "migrations" / "0040_accept_unbooked.sql"
COID = "kb638316751041-trend_momentum-B-20260803-buy-1"
ROW = {"kind": "unbooked", "symbol": "B", "coid": COID, "venue": 23, "booked": 0, "action": "book"}


def _code(p: Path) -> str:
    return re.sub(r"--[^\n]*", "", p.read_text())


def test_the_migration_only_redefines_kb_command():
    sql = _code(FILE).lower()
    assert re.findall(r"create or replace function ([\w.]+)", sql) == ["public.kb_command"]
    for word in ("alter table", "create table", "drop ", "truncate", "delete ", "update ", "service_role",
                 "net.http", "cron.", "grant select", "grant insert"):
        assert word not in sql, word
    assert sql.count("insert into") == 1 and "insert into kaibot.command" in sql
    assert "security definer set search_path = ''" in sql and "kaibot.is_owner()" in sql
    assert "from public, anon;" in sql and "to authenticated;" in sql


def test_it_is_0037s_function_plus_the_unbooked_rows():
    """Everything outside the `shown` check and the stored row is 0037's text, character for character."""
    old, new = _code(ROOT / "migrations/0037_accept_reconcile_command.sql"), _code(FILE)
    fn = lambda s: s[s.index("create or replace function public.kb_command"):]  # noqa: E731
    cut = lambda s: re.sub(r"if exists \(.*?jsonb_array_elements\(shown\) x\)\);", "", s, flags=re.S)  # noqa: E731
    assert cut(fn(old)) == cut(fn(new))
    assert "'unbooked'" in new and "'book', 'accept'" in new
    assert "-- APPLIED" not in FILE.read_text() and "-- NOT APPLIED" not in FILE.read_text()


@needs_pg
def test_sql_an_unbooked_row_is_stored_with_only_its_keys(db):  # noqa: F811
    assert call(ACCEPT_COMMAND, {"reason": REASON, "shown": [{**ROW, "px": 1, "extra": "x"}, SHOWN[0]]}) \
        == {"queued": True}
    (c,) = commands()
    assert c["payload"]["shown"] == [ROW, {"kind": "quantity", "symbol": "B", "offset": -2.0}]


@needs_pg
@pytest.mark.parametrize("bad", [
    {"action": "sell"}, {"action": None}, {"coid": "x; drop"}, {"coid": "kbX-1"}, {"venue": 0}, {"venue": "23"},
    {"booked": -1}, {"booked": None}, {"symbol": "b"}, {"symbol": None},
])
def test_sql_a_malformed_unbooked_row_queues_nothing(db, bad):  # noqa: F811
    assert "can be accepted" in call(ACCEPT_COMMAND, {"reason": REASON, "shown": [{**ROW, **bad}]})
    assert commands() == []


@needs_pg
def test_sql_still_owner_only(db):  # noqa: F811
    assert "not authorised" in call(ACCEPT_COMMAND, {"reason": REASON, "shown": [ROW]}, uid=OTHER)
    assert commands() == []
