"""A throwaway local Postgres for the dead-man SQL (S80, #120): never Supabase, never production.

`DeadmanDb` starts a private cluster in a temp directory (unix socket only), creates stand-ins for what Supabase
provides (pg_net's `net` schema, Vault's `vault.decrypted_secrets`, pg_cron's `cron` schema, the kaibot tables the
dead-man reads) and applies migrations 0017 and 0019 as files. `pump(broker)` plays Alpaca for pg_net: it answers
the GETs from a `SimBroker` and carries out the POSTs and DELETEs on it, as the paper venue would.

Skipped when no Postgres binaries are installed (GitHub's ubuntu runners have PostgreSQL 16 preinstalled).
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

import pytest

from kaibot.broker.protocol import BrokerError, OrderRequest

ROOT = Path(__file__).resolve().parents[1]

STUBS = r"""
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon; end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated; end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then create role service_role; end if;
end $$;
create schema kaibot;
create table kaibot.run (id bigint primary key, status text not null, kind text not null default 'paper');
create table kaibot.heartbeat (process text primary key, ts timestamptz not null, detail jsonb);
create table kaibot.halt (id bigint generated always as identity primary key, run_id bigint, ts timestamptz not null
  default now(), kind text not null, reason text not null, session date);
create table kaibot.ledger_position (run_id bigint not null, session date not null, strategy text not null,
  symbol text not null, qty numeric(14,4) not null, stop_px numeric(14,4), stop_state text not null default 'resting',
  primary key (run_id, session, strategy, symbol));
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
create function net.http_post(url text, body jsonb default '{}', params jsonb default '{}', headers jsonb default '{}',
  timeout_milliseconds int default 5000) returns bigint language sql as
  $f$ insert into net.calls (method, url, params, body) values ('POST', url, params, body) returning id $f$;
create schema vault;
create table vault.decrypted_secrets (name text primary key, decrypted_secret text);
create schema cron;
create table cron.job (jobid bigserial primary key, jobname text unique, schedule text, command text);
create function cron.schedule(n text, s text, c text) returns bigint language sql as
  $f$ insert into cron.job (jobname, schedule, command) values (n, s, c)
      on conflict (jobname) do update set schedule = excluded.schedule, command = excluded.command returning jobid $f$;
create function cron.unschedule(j bigint) returns boolean language sql as
  $f$ delete from cron.job where jobid = j returning true $f$;
"""


def _bindir() -> Path | None:
    exe = shutil.which("initdb")
    if exe:
        return Path(exe).parent
    found = sorted(glob.glob("/usr/lib/postgresql/*/bin/initdb"))
    return Path(found[-1]).parent if found else None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class DeadmanDb:
    def __init__(self, tmp: Path) -> None:
        self.bin = _bindir()
        if self.bin is None or shutil.which("psql") is None and not (self.bin / "psql").exists():
            pytest.skip("no PostgreSQL binaries")
        # its own directory under the system temp (the postgres user must be able to reach it when we are root)
        self.dir = Path(tempfile.mkdtemp(prefix="kaibot-pg-"))
        os.chmod(self.dir, 0o755)
        self.port = _free_port()
        self.as_pg: list[str] = []
        if os.geteuid() == 0:  # initdb refuses root: run the server as the postgres user
            if shutil.which("runuser") is None:
                pytest.skip("running as root without runuser")
            shutil.chown(self.dir, "postgres")
            self.as_pg = ["runuser", "-u", "postgres", "--"]
        self._run([str(self.bin / "initdb"), "-D", str(self.dir / "data"), "-A", "trust", "-U", "postgres"])
        self._run([str(self.bin / "pg_ctl"), "-D", str(self.dir / "data"), "-w", "-l", str(self.dir / "log"),
                   "-o", f"-k {self.dir} -p {self.port} -c listen_addresses='' -c fsync=off", "start"])
        self.psql = str(self.bin / "psql") if (self.bin / "psql").exists() else shutil.which("psql")
        # the timestamp, not the wall clock, is what the dead-man reads (p_now), but now() must be in UTC
        self.sql("alter database postgres set timezone to 'UTC'")
        self.sql(STUBS)
        self.file(ROOT / "migrations" / "0017_deadman_early_close.sql")
        self.file(ROOT / "migrations" / "0019_deadman_overnight.sql")
        self.sql("insert into vault.decrypted_secrets values ('kaibot_alpaca_paper_key_id', 'k'), "
                 "('kaibot_alpaca_paper_secret', 's')")

    def _run(self, cmd: list[str]) -> None:
        subprocess.run([*self.as_pg, *cmd], check=True, capture_output=True, text=True)

    def stop(self) -> None:
        subprocess.run([*self.as_pg, str(self.bin / "pg_ctl"), "-D", str(self.dir / "data"), "-m", "immediate",
                        "stop"], capture_output=True)
        shutil.rmtree(self.dir, ignore_errors=True)

    def sql(self, q: str) -> list[str]:
        out = subprocess.run([self.psql, "-h", str(self.dir), "-p", str(self.port), "-U", "postgres", "-d",
                              "postgres", "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1"], input=q, check=False,
                             capture_output=True, text=True)
        if out.returncode != 0:
            raise RuntimeError(out.stderr)
        return [x for x in out.stdout.splitlines() if x != ""]

    def file(self, path: Path) -> None:
        self.sql(path.read_text())

    def one(self, q: str) -> str:
        rows = self.sql(q)
        return rows[0] if rows else ""

    # ---------------------------------------------------------------- the account as the dead-man sees it
    def beat(self, ts) -> None:
        self.sql(f"insert into kaibot.heartbeat values ('engine', '{ts.isoformat()}', '{{}}') "
                 "on conflict (process) do update set ts = excluded.ts")

    def run(self, rid: int, status: str = "running") -> None:
        self.sql(f"insert into kaibot.run values ({rid}, '{status}') on conflict (id) do update set status = "
                 "excluded.status")

    def ledger(self, rid: int, session: str, rows: list[tuple[str, str, float, float | None]]) -> None:
        for strategy, symbol, qty, stop in rows:
            self.sql(f"insert into kaibot.ledger_position (run_id, session, strategy, symbol, qty, stop_px) values "
                     f"({rid}, '{session}', '{strategy}', '{symbol}', {qty}, "
                     f"{'null' if stop is None else stop})")

    def check(self, now, force: bool = False, flatten: bool | None = None) -> str:
        f = "null" if flatten is None else str(flatten).lower()
        return self.one(f"select kaibot.deadman_check({str(force).lower()}, {f}, '{now.isoformat()}')")

    def last_result(self) -> dict:
        r = self.one("select result from kaibot.deadman_pass where state = 'done' order by id desc limit 1")
        return json.loads(r) if r else {}

    def pump(self, broker, prices: dict | None = None) -> list[tuple[str, str]]:
        """Alpaca for pg_net: answer the reads from `broker` (with its network up: Supabase has its own) and
        carry out the writes. Returns the writes done, as (method, url)."""
        down, broker.down = broker.down, False
        done = []
        try:
            for row in self.sql("select row_to_json(c) from net.calls c where not done order by id"):
                c = json.loads(row)
                path = c["url"].split("paper-api.alpaca.markets", 1)[1]
                status, content = 200, None
                try:
                    if c["method"] == "GET" and path == "/v2/positions":
                        content = positions_json(broker, prices)
                    elif c["method"] == "GET" and path == "/v2/orders":
                        content = orders_json(broker)
                    elif c["method"] == "DELETE" and path == "/v2/positions":
                        for s in list(broker.positions()):
                            broker.close_position(s)
                    elif c["method"] == "DELETE" and path.startswith("/v2/positions/"):
                        broker.close_position(path.rsplit("/", 1)[1])
                    elif c["method"] == "DELETE" and path.startswith("/v2/orders/"):
                        broker.cancel(path.rsplit("/", 1)[1])
                    elif c["method"] == "POST" and path == "/v2/orders":
                        b = c["body"]
                        broker.submit(OrderRequest(b["symbol"], b["side"], float(b["qty"]), b["type"],
                                                   b["time_in_force"], b.get("client_order_id"),
                                                   stop_price=float(b["stop_price"]) if b.get("stop_price") else None))
                    else:
                        status = 404
                except BrokerError as e:
                    status = e.status or 500
                if c["method"] != "GET":
                    done.append((c["method"], path))
                body = "null" if content is None else json.dumps(content).replace("'", "''")
                self.sql(f"insert into net._http_response (id, status_code, content) values ({c['id']}, {status}, "
                         f"'{body}'); update net.calls set done = true where id = {c['id']}")
        finally:
            broker.down = down
        return done


def positions_json(broker, prices: dict | None = None) -> list[dict]:
    out = []
    for s, p in broker.positions().items():
        px = (prices or {}).get(s) or p.get("current_price") or p.get("avg_entry_price")
        out.append({"symbol": s, "qty": str(p["qty"]), "current_price": None if px is None else str(px)})
    return out


def orders_json(broker) -> list[dict]:
    """GET /v2/orders?status=open&nested=true: working orders, and filled brackets whose legs still work."""
    rows = list(broker.open_orders())
    rows += [o for o in broker.orders.values() if o["status"] == "filled"
             and any(leg["status"] in ("new", "held") for leg in o.get("legs") or [])]
    return json.loads(json.dumps(rows, default=str))
