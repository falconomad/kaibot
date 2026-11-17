"""0021 (#266): kb_health returns the live daily path's events, the corporate-action skips first. The function is
0007's with only the event-type list longer; still owner-only and stable; execute for `authenticated` only."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NEW = (ROOT / "migrations" / "0021_kb_health_multiday_events.sql").read_text()
OLD = (ROOT / "migrations" / "0007_m2_dashboard.sql").read_text()


def _fn(sql: str) -> str:
    i = sql.index("create or replace function public.kb_health()")
    return sql[i:sql.index("end $$;", i)]


def _types(fn: str) -> list[str]:
    fn = re.sub(r"--[^\n]*", "", fn)
    block = fn[fn.index("where type in ("):fn.index(")", fn.index("where type in ("))]
    return re.findall(r"'([a-z_]+)'", block)


def test_the_corporate_action_events_are_listed():
    t = _types(_fn(NEW))
    assert "multi_day_ca_skip" in t and "multi_day_ca_unread" in t
    assert set(_types(_fn(OLD))) <= set(t)  # nothing 0007 returned is dropped
    assert len(t) == len(set(t))


def test_only_the_list_changes():
    strip = re.compile(r"\(\s*'engine_start'.*?\)\s*order by ts desc", re.S)
    norm = lambda s: re.sub(r"\s+", " ", strip.sub("LIST", re.sub(r"--[^\n]*", "", s)))  # noqa: E731
    assert norm(_fn(NEW)) == norm(_fn(OLD))
    assert "kaibot.is_owner()" in _fn(NEW) and "stable security definer" in _fn(NEW)


def test_read_only_and_granted_as_before():
    body = re.sub(r"--[^\n]*", "", NEW).lower()
    for word in ("delete ", "drop ", "truncate", "insert ", "update ", "alter "):
        assert word not in body, word
    assert "revoke all on function public.kb_health() from public, anon;" in NEW
    assert "grant execute on function public.kb_health() to authenticated;" in NEW
