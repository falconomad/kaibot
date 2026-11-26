"""Migration 0052: the stock summaries' licence tag (#722, M19 ticket 8.4 of the second engine audit).

A card holding news text (Benzinga, through Alpaca) is owner_only; only a card without any news item may be public.
The verification the ticket asks for: a member (or any non-owner) RPC cannot return an owner-only gist. The file checks
run everywhere; the SQL checks need the scratch Postgres of tests/pg_scratch.py (KAIBOT_TEST_PG)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from test_invites_sql import MEMBER, OWNER, STRANGER, _member, db, j, needs_pg, psql  # noqa: F401  (db: the fixture)

ROOT = Path(__file__).resolve().parents[1]
M23 = ROOT / "migrations" / "0023_stock_summary.sql"
M52 = ROOT / "migrations" / "0052_stock_summary_licence.sql"
GIST = "Apple raised its dividend to 27 cents, owner-only gist"


def _code(path: Path) -> str:
    return "\n".join(ln for ln in path.read_text().splitlines() if not ln.lstrip().startswith("--")).lower()


def _body(path: Path, fn: str) -> list[str]:
    s = path.read_text()
    i = s.index(f"create or replace function {fn}(")
    return [ln for ln in s[i:s.index("end $$;", i)].splitlines() if not ln.lstrip().startswith("--")]


# ---------------------------------------------------------------- the file


def test_0052_is_additive():
    s = _code(M52)
    assert not re.search(r"\b(drop|truncate|delete from)\b", s)
    assert re.findall(r"\bupdate\s+(kaibot\.\w+)", s) == ["kaibot.summary_call"]  # 0023's, inside kb_summary_settle
    assert re.findall(r"insert into (kaibot\.\w+)", s) == ["kaibot.stock_summary"]  # inside kb_summary_settle only
    assert re.findall(r"alter table (kaibot\.\w+)", s) == ["kaibot.stock_summary", "kaibot.stock_summary"]
    assert "add column if not exists licence text not null default 'owner_only'" in s
    assert re.findall(r"create or replace function (\w+\.\w+)\(", s) == [
        "kaibot.stock_summary_card", "public.kb_stock_summary", "public.kb_summary_settle"]
    assert "revoke all on function kaibot.stock_summary_card(text, boolean) from public, anon, authenticated;" in s
    assert " to anon" not in s and "grant execute on function kaibot." not in s
    assert not any(re.match(r"^--\s*(NOT )?APPLIED\b", ln) for ln in M52.read_text().splitlines())


def test_0052_every_function_is_definer_with_an_empty_search_path_and_the_public_ones_owner_only():
    s = _code(M52)
    for body in re.split(r"create or replace function ", s)[1:]:
        assert "security definer set search_path = ''" in body.split("as $$")[0], body[:60]
    for fn in ("public.kb_stock_summary", "public.kb_summary_settle"):
        body = s.split(f"create or replace function {fn}(")[1].split("end $$;")[0]
        assert "kaibot.is_owner()" in body and "raise exception 'not authorised'" in body, fn


def test_0052_settle_is_0023s_but_for_the_licence():
    old, new = _body(M23, "public.kb_summary_settle"), _body(M52, "public.kb_summary_settle")
    changed = [ln for ln in new if ln not in old]
    gone = [ln for ln in old if ln not in new]
    assert gone == ["  insert into kaibot.stock_summary (symbol, day, text, body, sources, model, cost_usd, call_id)",
                    "            case when coalesce(p_ai, false) then c.model end, c.actual_usd, c.id)"]
    assert any("'public' else 'owner_only' end)" in ln for ln in changed) and len(changed) == 4


# ---------------------------------------------------------------- the database

def _seed(licence: str | None, news: list, symbol: str = "AAPL", days_ago: int = 0) -> None:
    body = json.dumps({"about": "Apple Inc: Technology.", "news": news}).replace("'", "''")
    lic = f", '{licence}'" if licence else ""
    col = ", licence" if licence else ""
    text = "Apple Inc: Technology." + (f" {GIST}" if news else "")
    day = f"(now() at time zone 'America/New_York')::date - {days_ago}"
    psql(f"""insert into kaibot.stock_summary (symbol, day, text, body{col})
      values ('{symbol}', {day}, '{text}', '{body}'::jsonb{lic});""", role=None)


@pytest.fixture
def cards(db):  # noqa: F811
    psql("delete from kaibot.stock_summary; delete from kaibot.summary_call;", role=None)
    yield
    psql("delete from kaibot.stock_summary; delete from kaibot.summary_call;", role=None)


NEWS = [{"headline": "Apple raises dividend", "gist": GIST, "date": "2026-11-18", "source": "benzinga"}]


@needs_pg
def test_a_card_is_owner_only_unless_it_holds_no_news(cards):
    _seed(None, NEWS)
    assert psql("select licence from kaibot.stock_summary;", role=None) == "owner_only"
    with pytest.raises(AssertionError, match="stock_summary_licence_by_content"):
        _seed("public", NEWS, symbol="MSFT")
    with pytest.raises(AssertionError, match="stock_summary_licence_by_content"):
        psql("""insert into kaibot.stock_summary (symbol, day, text, body, licence)
          values ('MSFT', current_date, 'x', '{}'::jsonb, 'public');""", role=None)  # no news list: not public
    with pytest.raises(AssertionError, match="stock_summary_licence_by_content"):
        _seed("members", [], symbol="MSFT")
    _seed("public", [], symbol="MSFT")


def _settle(body: dict) -> dict:
    cid = j("select public.kb_summary_reserve('AAPL', 'claude-haiku-5-5', 0.001);")["call_id"]
    b = json.dumps(body).replace("'", "''")
    return j(f"""select public.kb_summary_settle({cid}, 0.0003, 900, 120, null, 'Apple Inc: Technology. {GIST}',
      '{b}'::jsonb, '[]'::jsonb, true);""")


@needs_pg
def test_settle_stores_public_only_when_asked_and_the_body_lists_no_news(cards):
    assert _settle({"about": "a", "news": NEWS, "licence": "public"})["licence"] == "owner_only"
    psql("delete from kaibot.stock_summary; delete from kaibot.summary_call;", role=None)
    assert _settle({"about": "a", "news": [], "licence": "owner_only"})["licence"] == "owner_only"
    psql("delete from kaibot.stock_summary; delete from kaibot.summary_call;", role=None)
    assert _settle({"about": "a", "news": []})["licence"] == "owner_only"  # an older function's body: no tag
    psql("delete from kaibot.stock_summary; delete from kaibot.summary_call;", role=None)
    assert _settle({"about": "a", "news": [], "licence": "public"})["licence"] == "public"


@needs_pg
def test_the_guard_gives_owner_only_cards_to_the_owner_path_only(cards):
    _seed(None, NEWS)                      # today's card: owner_only, with the gist
    _seed("public", [], days_ago=1)        # yesterday's: facts only
    owner = j("select kaibot.stock_summary_card('AAPL', true);", role=None)
    assert owner["licence"] == "owner_only" and GIST in owner["text"]
    other = j("select kaibot.stock_summary_card('AAPL', false);", role=None)
    assert other["licence"] == "public" and other["today"] is False and GIST not in json.dumps(other)
    assert psql("select kaibot.stock_summary_card('AAPL', null);", role=None) == psql(
        "select kaibot.stock_summary_card('AAPL', false);", role=None)
    assert j("select public.kb_stock_summary('AAPL');")["licence"] == "owner_only"
    assert psql("select has_function_privilege('authenticated', 'kaibot.stock_summary_card(text, boolean)', "
                "'execute');", role=None) == "f"
    assert psql("select has_function_privilege('anon', 'kaibot.stock_summary_card(text, boolean)', 'execute');",
                role=None) == "f"


def _readers() -> list[tuple[str, int, bool]]:
    """Every function the authenticated role may call whose body reads the cards, directly or through the guard."""
    rows = psql("""select n.nspname || '.' || p.proname, p.pronargs,
             coalesce(p.proargtypes[0] = 'text'::regtype, false) from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
       where n.nspname in ('public', 'kaibot') and has_function_privilege('authenticated', p.oid, 'execute')
         and p.prosrc ~ 'stock_summary' order by 1;""", role=None)
    return [(r.split("|")[0], int(r.split("|")[1]), r.split("|")[2] == "t") for r in rows.splitlines()]


@needs_pg
def test_no_member_or_stranger_rpc_returns_an_owner_only_gist(cards):
    """The ticket's check: with an owner_only card holding a gist in the table, every function the authenticated role
    can call that reads the cards, called by an invited member and by a signed-in stranger, refuses or returns nothing
    with the gist. The owner, through the same function, gets it."""
    _seed(None, NEWS)
    _member()
    readers = _readers()
    names = {f for f, _, _ in readers}
    assert {"public.kb_stock_summary", "public.kb_summary_settle", "public.kb_summary_spend"} <= names, names
    assert "kaibot.stock_summary_card" not in names
    psql(f"insert into auth.users (id) values ('{STRANGER}') on conflict do nothing;", role=None)
    for uid in (MEMBER, STRANGER):
        for fn, nargs, text_first in readers:
            for arg in ("null", "'AAPL'") if text_first else ("null",):
                args = ", ".join([arg if i == 0 else "null" for i in range(nargs)])
                try:
                    out = psql(f"select {fn}({args});", uid=uid)
                except AssertionError as e:
                    assert "not authorised" in str(e) or "permission denied" in str(e), (fn, uid, str(e))
                    continue
                assert GIST not in out, (fn, uid)
        with pytest.raises(AssertionError, match="permission denied"):
            psql("select text from kaibot.stock_summary;", uid=uid)
    assert GIST in j("select public.kb_stock_summary('AAPL');")["text"]
