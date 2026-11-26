"""Audit 2 ticket 5.6 (#686): scripts/backfill_migration_history.sql records 0041-0047 in Supabase's history.

The file checks run everywhere. The SQL checks need a scratch Postgres (KAIBOT_TEST_PG; CI and KAIBOT_TEST_PG_AUTO=1
build one, tests/pg_scratch.py; skipped otherwise). They build Supabase's `supabase_migrations.schema_migrations`
with a history that ends at 0040, in each of the two forms the table can have (version '0040' or a 14-digit
timestamp; name with or without the number), run the file the way the owner does (`psql -X -1 -v ON_ERROR_STOP=1
-f`), and run it again."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SQL = ROOT / "scripts" / "backfill_migration_history.sql"
PG = os.environ.get("KAIBOT_TEST_PG")
needs_pg = pytest.mark.skipif(not PG, reason="set KAIBOT_TEST_PG to a scratch Postgres")
TABLE = "supabase_migrations.schema_migrations"


def _code() -> str:
    return "\n".join(ln for ln in SQL.read_text().splitlines() if not ln.lstrip().startswith("--")).lower()


# ---------------------------------------------------------------- the file


def test_the_file_names_exactly_0041_to_0047_as_they_are_in_migrations():
    listed = re.findall(r"\('(\d{4})', '([a-z0-9_]+)',", SQL.read_text())
    files = sorted(p.stem for p in (ROOT / "migrations").glob("004[1-7]_*.sql"))
    assert [f"{n}_{s}" for n, s in listed] == files and len(files) == 7


def test_the_file_writes_only_the_history_table():
    s = _code()
    assert re.findall(r"insert into ([\w.]+)", s) == [TABLE]
    assert not re.search(r"\b(update|delete|truncate|drop|alter|create|grant|revoke|copy|execute)\b", s)


def test_the_header_gives_the_owners_psql_command():
    assert ('psql "$DATABASE_URL" -X -1 -v ON_ERROR_STOP=1 -P pager=off -f scripts/backfill_migration_history.sql'
            in SQL.read_text())


# ---------------------------------------------------------------- the database


def psql(sql: str = "", file: Path | None = None) -> subprocess.CompletedProcess:
    args = ["psql", *shlex.split(PG or ""), "-X", "-1", "-q", "-tA", "-v", "ON_ERROR_STOP=1", "-P", "pager=off"]
    if file is not None:
        args += ["-f", str(file)]
    return subprocess.run(args, input=sql, capture_output=True, text=True)


def ok(sql: str = "", file: Path | None = None) -> str:
    r = psql(sql, file)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def history() -> list[tuple[str, str]]:
    out = ok(f"select version || '|' || name from {TABLE} order by version;")
    return [tuple(line.split("|", 1)) for line in out.splitlines()]


NAMES = [p.stem for p in sorted((ROOT / "migrations").glob("00[0-4][0-9]_*.sql"))]


def seed(form: str, upto: int = 40) -> None:
    """A history like Supabase's, 0001..upto. form: 'num' ('0040', 'accept_unbooked') or 'ts'
    ('202611...', '0040_accept_unbooked')."""
    rows = []
    for i, stem in enumerate(NAMES[:upto], 1):
        if form == "num":
            rows.append(f"('{i:04d}', '{stem[5:]}')")
        else:  # one a minute from 13 Nov, the last (0040) at 23 Nov 21:05 UTC as Product applied it
            rows.append(f"(to_char(timestamp '2026-11-23 21:05:00' - interval '1 minute' * ({upto} - {i}), "
                        f"'YYYYMMDDHH24MISS'), '{stem}')")
    ok(f"""drop schema if exists supabase_migrations cascade;
      create schema supabase_migrations;
      create table {TABLE} (version text primary key, statements text[], name text);
      insert into {TABLE} (version, name) values {", ".join(rows)};""")


@pytest.fixture(autouse=True)
def _clean():
    yield
    if PG:
        psql("drop schema if exists supabase_migrations cascade;")


@needs_pg
def test_number_form_gets_0041_to_0047_and_a_second_run_adds_nothing():
    seed("num")
    out = ok(file=SQL)
    rows = history()
    assert rows[-7:] == [(f"{n:04d}", NAMES[n - 1][5:]) for n in range(41, 48)]
    assert rows[-1] == ("0047", "helper_shadow_results") and len(rows) == 47
    assert out.splitlines()[0] == "0047|helper_shadow_results"  # the closing query shows the end
    ok(file=SQL)
    assert history() == rows


@needs_pg
def test_timestamp_form_gets_ordered_versions_after_0040():
    seed("ts")
    ok(file=SQL)
    rows = history()
    assert [n for _, n in rows[-7:]] == NAMES[40:47]
    versions = [v for v, _ in rows]
    assert versions == sorted(versions) and len(set(versions)) == 47
    assert all(re.fullmatch(r"\d{14}", v) for v in versions)
    assert dict((n, v) for v, n in rows)["0047_helper_shadow_results"] == "20261125194230"  # 20:42:30 CET
    ok(file=SQL)
    assert history() == rows


@needs_pg
def test_timestamps_stay_after_a_0040_written_later_than_0041s_time():
    seed("ts")
    ok(f"update {TABLE} set version = '20261124000000' where name = '0040_accept_unbooked';")  # a later clock
    ok(file=SQL)
    versions = [v for v, _ in history()]
    assert versions == sorted(versions) and versions[-8] == "20261124000000"
    assert versions[-7] == "20261124000001"


@needs_pg
def test_a_partly_recorded_history_gets_only_the_missing_rows():
    seed("num", upto=43)
    ok(file=SQL)
    rows = history()
    assert [v for v, _ in rows] == [f"{n:04d}" for n in range(1, 48)]


@needs_pg
def test_it_refuses_without_a_0040_row_and_writes_nothing():
    seed("num", upto=39)
    r = psql(file=SQL)
    assert r.returncode != 0 and "no history row for 0040" in r.stderr
    assert len(history()) == 39


@needs_pg
def test_it_refuses_without_the_table():
    psql("drop schema if exists supabase_migrations cascade;")
    r = psql(file=SQL)
    assert r.returncode != 0 and "does not exist" in r.stderr
