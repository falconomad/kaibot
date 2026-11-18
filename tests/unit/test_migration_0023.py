"""0023 (stock summaries) is additive: two new tables and owner-only functions, nothing existing changed, nothing
deleted, no grant to anon, and the caps the serverless function relies on are in the SQL itself."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SQL = (ROOT / "migrations" / "0023_stock_summary.sql").read_text()
BODY = re.sub(r"--[^\n]*", "", SQL).lower()


def test_additive_only():
    for word in ("drop ", "truncate", "delete "):
        assert word not in BODY, word
    assert set(re.findall(r"create table if not exists (kaibot\.\w+)", BODY)) == {"kaibot.summary_call",
                                                                               "kaibot.stock_summary"}
    altered = set(re.findall(r"alter table (kaibot\.\w+)", BODY))
    assert altered == {"kaibot.summary_call", "kaibot.stock_summary"}
    assert re.findall(r"update (kaibot\.\w+)", BODY) == ["kaibot.summary_call"]
    fns = re.findall(r"create or replace function ([\w.]+)", BODY)
    assert fns == ["kaibot.summary_spent", "public.kb_stock_summary", "public.kb_summary_facts",
                   "public.kb_summary_reserve", "public.kb_summary_settle", "public.kb_summary_spend"]


def test_every_public_function_is_owner_only_definer_with_an_empty_search_path():
    for name in re.findall(r"create or replace function (public\.\w+)", BODY):
        fn = BODY[BODY.index(f"create or replace function {name}"):]
        fn = fn[:fn.index("end $$;")]
        assert "security definer set search_path = ''" in fn and "kaibot.is_owner()" in fn, name
    assert "from public, anon;" in BODY and " to authenticated;" in BODY and "to anon" not in BODY


def test_the_caps_are_in_the_sql():
    assert "day_cap constant numeric := 0.30" in BODY and "month_cap constant numeric := 5.00" in BODY
    assert "p_reserved > 0.02" in BODY and ">= 2 then" in BODY and "pg_advisory_xact_lock" in BODY
