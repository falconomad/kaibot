"""#256: a throwaway local Postgres with every migration applied, for the SQL tests that need one (KAIBOT_TEST_PG).
Never Supabase, never production: a private cluster in a temp directory, unix socket only, deleted at the end.

Used by tests/conftest.py when KAIBOT_TEST_PG_AUTO=1 (CI) and KAIBOT_TEST_PG is not set already: the main process
builds a template database `kb_all` (Supabase stand-ins, then migrations/0001..NNNN in order) and every xdist worker
gets its own copy, so tests that reset tables never meet across workers. Without Postgres binaries nothing happens
and those tests stay skipped, as before.

What stands in for Supabase (only so the migrations apply; the tests themselves exercise the kaibot SQL):
- roles anon, authenticated, service_role;
- `auth.users` and `auth.uid()`, which reads the session setting `test.uid` (the tests set it per call);
- the legacy public tables 0003 locks;
- Realtime's `realtime.messages` (RLS on) and `realtime.topic()`, which reads `test.topic` (0032);
- pg_net's `net` schema, Vault's `vault`, pg_cron's `cron` (as in tests/pg_harness.py). The two
  `create extension if not exists pg_cron / pg_net` lines of 0006 are left out when applying: those extensions do
  not exist outside Supabase, and their schemas are the stand-ins.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

from pg_harness import _bindir

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = "kb_all"
EXTENSION = re.compile(r"^\s*create extension if not exists pg_(cron|net)\s*;\s*$", re.I | re.M)

STUBS = r"""
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon; end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated; end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then create role service_role; end if;
end $$;
create schema auth;
create table auth.users (id uuid primary key);
create function auth.uid() returns uuid language sql stable as
  $f$ select nullif(current_setting('test.uid', true), '')::uuid $f$;
create function auth.role() returns text language sql stable as $f$ select 'authenticated'::text $f$;
create function auth.jwt() returns jsonb language sql stable as $f$ select '{}'::jsonb $f$;
do $$ declare t text; begin
  foreach t in array array['agent_goal_snapshots', 'decision_engine_meta', 'decision_memory', 'manual_ticker_checks',
    'portfolio', 'position_snapshots', 'recommendation_signals', 'recommendation_trace', 'trade_decisions',
    'transactions', 'worker_runs'] loop
    execute format('create table public.%I (id bigint)', t);
  end loop; end $$;
create schema realtime;
create table realtime.messages (id bigserial primary key, topic text not null, extension text not null,
  event text, payload jsonb, private boolean default true);
alter table realtime.messages enable row level security;
create function realtime.topic() returns text language sql stable as
  $f$ select nullif(current_setting('test.topic', true), '') $f$;
grant usage on schema realtime to anon, authenticated;
grant select on realtime.messages to anon, authenticated;
create schema net;
create table net.calls (id bigserial primary key, method text, url text, params jsonb, body jsonb,
  done boolean not null default false);
create table net._http_response (id bigint primary key, status_code int, content_type text, headers jsonb,
  content text, timed_out boolean, error_msg text, created timestamptz not null default now());
create function net.http_get(url text, params jsonb default '{}', headers jsonb default '{}',
  timeout_milliseconds int default 5000) returns bigint language sql as
  $f$ insert into net.calls (method, url, params) values ('GET', url, params) returning id $f$;
create function net.http_delete(url text, params jsonb default '{}', headers jsonb default '{}',
  timeout_milliseconds int default 5000) returns bigint language sql as
  $f$ insert into net.calls (method, url, params) values ('DELETE', url, params) returning id $f$;
create function net.http_post(url text, body jsonb default '{}', params jsonb default '{}',
  headers jsonb default '{}', timeout_milliseconds int default 5000) returns bigint language sql as
  $f$ insert into net.calls (method, url, params, body) values ('POST', url, params, body) returning id $f$;
create schema vault;
create table vault.secrets (id uuid primary key default gen_random_uuid(), name text unique, secret text);
create view vault.decrypted_secrets as select id, name, secret as decrypted_secret from vault.secrets;
create function vault.create_secret(s text, n text default null, d text default null) returns uuid language sql as
  $f$ insert into vault.secrets (name, secret) values (n, s) returning id $f$;
create schema cron;
create table cron.job (jobid bigserial primary key, jobname text unique, schedule text, command text,
  active boolean default true);
create table cron.job_run_details (runid bigserial primary key, jobid bigint, status text, return_message text,
  start_time timestamptz);
create function cron.schedule(n text, s text, c text) returns bigint language sql as
  $f$ insert into cron.job (jobname, schedule, command) values (n, s, c)
      on conflict (jobname) do update set schedule = excluded.schedule, command = excluded.command returning jobid $f$;
create function cron.unschedule(j bigint) returns boolean language sql as
  $f$ delete from cron.job where jobid = j returning true $f$;
create function cron.unschedule(n text) returns boolean language sql as
  $f$ delete from cron.job where jobname = n returning true $f$;
"""


class ScratchPg:
    """A private cluster: `conn(db)` is the psql-style connection string the SQL tests read from KAIBOT_TEST_PG."""

    def __init__(self) -> None:
        self.bin = _bindir()
        if self.bin is None or not (self.bin / "pg_ctl").exists():
            raise FileNotFoundError("no PostgreSQL binaries")
        self.dir = Path(tempfile.mkdtemp(prefix="kaibot-pgscratch-"))
        os.chmod(self.dir, 0o755)
        self.as_pg: list[str] = []
        if os.geteuid() == 0:  # initdb refuses root: the server runs as the postgres user
            shutil.chown(self.dir, "postgres")
            self.as_pg = ["runuser", "-u", "postgres", "--"]
        self._run([str(self.bin / "initdb"), "-D", str(self.dir / "data"), "-A", "trust", "-U", "postgres",
                   "-E", "UTF8", "--locale=C.UTF-8"])
        self._run([str(self.bin / "pg_ctl"), "-D", str(self.dir / "data"), "-w", "-l", str(self.dir / "log"),
                   "-o", f"-k {self.dir} -p 5432 -c listen_addresses='' -c fsync=off", "start"])
        self.psql = str(self.bin / "psql") if (self.bin / "psql").exists() else shutil.which("psql")

    def _run(self, cmd: list[str]) -> None:
        subprocess.run([*self.as_pg, *cmd], check=True, capture_output=True, text=True)

    def conn(self, db: str) -> str:
        return f"-h {self.dir} -p 5432 -U postgres -d {db}"

    def sql(self, q: str, db: str = "postgres") -> None:
        out = subprocess.run([self.psql, *shlex.split(self.conn(db)), "-X", "-q", "-v", "ON_ERROR_STOP=1"], input=q,
                             capture_output=True, text=True)
        if out.returncode != 0:
            raise RuntimeError(out.stderr[-2000:])

    def build_template(self) -> None:
        """Supabase stand-ins, then every migration file in order, into `kb_all`."""
        self.sql(f"create database {TEMPLATE}")
        self.sql(STUBS, TEMPLATE)
        for path in sorted((ROOT / "migrations").glob("[0-9][0-9][0-9][0-9]_*.sql")):
            try:
                self.sql(EXTENSION.sub("", path.read_text()), TEMPLATE)
            except RuntimeError as e:
                raise RuntimeError(f"{path.name}: {e}") from None

    def copy(self, db: str) -> str:
        self.sql(f"create database {db} template {TEMPLATE}")
        return self.conn(db)

    def stop(self) -> None:
        subprocess.run([*self.as_pg, str(self.bin / "pg_ctl"), "-D", str(self.dir / "data"), "-m", "immediate",
                        "stop"], capture_output=True)
        shutil.rmtree(self.dir, ignore_errors=True)


def attach(base: str, db: str) -> str:
    """In an xdist worker: its own copy of the template, on the cluster the main process started (`base`)."""
    psql = shutil.which("psql") or str((_bindir() or Path("/usr/bin")) / "psql")
    subprocess.run([psql, *shlex.split(base), "-d", "postgres", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-c",
                    f"drop database if exists {db}", "-c", f"create database {db} template {TEMPLATE}"],
                   check=True, capture_output=True, text=True)
    return f"{base} -d {db}"
