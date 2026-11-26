"""Migration 0055 (audit 2, 5.13, #693; #529): unregistered event names become a registered `warn` event that reaches
the dashboard and the dead-man's alert path.

- kb_health lists the health events that waited in the registry's PENDING (`unregistered_event` among them) and
  `watchdog_close_booked`: 0039's function, byte for byte, with only those types added at the end of the list.
- kaibot.deadman_tick (0051's) sends one Healthchecks.io `/fail` ping per batch of new `unregistered_event` rows,
  naming them; the next run is green again. A tick that pings fail for its own reason adds the names to it.

The file checks run everywhere. The dead-man checks use the throwaway local Postgres of tests/pg_harness.py (never
Supabase); the kb_health checks need KAIBOT_TEST_PG (CI builds one with every migration applied)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pandas as pd
import pytest
from test_kb_health_news_events_sql import OTHER, PG, _sql, _types, owner, psql  # noqa: F401 (owner: autouse)

from kaibot.engine import event_names as en

sys.path.insert(0, str(Path(__file__).resolve().parent / "unit"))
from pg_harness import DeadmanDb  # noqa: E402
from test_order_manager import DAY, NY  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
M39 = ROOT / "migrations" / "0039_kb_health_m9_events.sql"
M51 = ROOT / "migrations" / "0051_deadman_alerts.sql"
M55 = ROOT / "migrations" / "0055_kb_health_unregistered_event_alert.sql"
NEW = ("unregistered_event", "journal_snapshot_mismatch", "journal_snapshot_unused", "engine_degraded_start",
       "engine_lease_confirmed", "daily_fetch_failed", "multi_day_data_late", "data_entry_block",
       "data_entry_unblocked", "engine_gap", "multi_day_close_unknown", "multi_day_sale_refused",
       "multi_day_decision_lost", "multi_day_no_daily_bar", "calendar_local", "watchdog_close_booked")
HC = "https://hc-ping.com/0b1c2d3e-test"
needs_pg = pytest.mark.skipif(not PG, reason="set KAIBOT_TEST_PG to a scratch Postgres with migration 0055 applied")


def _kb_health(path: Path) -> str:
    s = _sql(path)
    i = s.index("create or replace function public.kb_health()")
    j = s.index("grant execute on function public.kb_health() to authenticated;") + len("grant execute on function "
                                                                                       "public.kb_health() to "
                                                                                       "authenticated;")
    return s[i:j]


# ---------------------------------------------------------------- the file
def test_0055_kb_health_is_0039s_with_only_the_new_types_added():
    old, new = _kb_health(M39), _kb_health(M55)
    assert _types(new) == _types(old) + list(NEW)
    head_old, tail_old = old.split("where type in (", 1)[0], old.split("order by ts desc limit 80", 1)[1]
    head_new, tail_new = new.split("where type in (", 1)[0], new.split("order by ts desc limit 80", 1)[1]
    assert (head_new, tail_new) == (head_old, tail_old)


def test_0055_lists_exactly_the_registry_and_unregistered_event_is_a_warning():
    assert _types(_kb_health(M55)) == list(en.HEALTH_NAMES)
    assert en.severity("unregistered_event") == "warn" and "unregistered_event" in en.HEALTH_NAMES
    assert en.PENDING == ()


def test_0055_is_additive():
    s = _sql(M55).lower()
    assert not re.search(r"\b(drop|truncate|delete from|update kaibot|alter table kaibot\.(?!deadman_mark))\b", s)
    assert "function kaibot.deadman_check" not in s and "function kaibot.deadman_act" not in s
    assert re.findall(r"create or replace function ([\w.]+)", s) == ["public.kb_health", "kaibot.deadman_tick"]
    assert "cron.schedule" not in s and "hc-ping.com" not in s


def test_0055_tick_is_0051s_with_only_the_alert_added():
    """Every line of 0051's tick is still there, in order (the pings that now carry the names aside)."""
    def tick(p):
        s = _sql(p)
        i = s.index("create or replace function kaibot.deadman_tick")
        return s[i:s.index("$$;", i)].splitlines()
    old, new = tick(M51), tick(M55)
    it = iter(new)
    assert all(any(n == o for n in it) for o in old if "return 'failed: '" not in o and "said);" not in o
               and "goes silent');" not in o and "|| halts);" not in o and "err));" not in o)


def test_the_header_claims_no_status():
    assert not re.search(r"^--\s*(NOT APPLIED|APPLIED)\b", M55.read_text(), re.M)


def test_migrations_pending_records_0055():
    doc = (ROOT / "docs" / "ops" / "migrations_pending.md").read_text()
    assert "0055" in doc and "0055_kb_health_unregistered_event_alert.sql" in doc


# ---------------------------------------------------------------- kb_health on a scratch Postgres
INSERT = """insert into kaibot.event (ts, type, payload, hash) values
  (now() - interval '2 minutes', 'unregistered_event', '{"name": "brand_new"}', 't0055-a'),
  (now() - interval '1 minute', 'watchdog_close_booked', '{"symbol": "B"}', 't0055-b');"""


@needs_pg
def test_kb_health_returns_the_unregistered_event_warning():
    out = psql(f"begin;\n{INSERT}\nselect public.kb_health();\nrollback;").stdout.strip().splitlines()
    got = [e["type"] for e in json.loads(out[-1])["events"] if e["type"] in ("unregistered_event",
                                                                            "watchdog_close_booked")]
    assert got[:2] == ["watchdog_close_booked", "unregistered_event"]


# ---------------------------------------------------------------- the dead-man's alert
def at(hhmm: str, day: str = DAY):
    return pd.Timestamp(f"{day} {hhmm}", tz=NY).to_pydatetime()


class Db(DeadmanDb):
    def tick(self, now) -> str:
        return self.one(f"select kaibot.deadman_tick('{now.isoformat()}')")

    def new_pings(self) -> list[tuple[str, str]]:
        rows = [json.loads(r) for r in self.sql(
            "select row_to_json(c) from net.calls c where url like 'https://hc-ping.com/%' and not done order by id")]
        self.sql("update net.calls set done = true where url like 'https://hc-ping.com/%'")
        return [(c["url"].removeprefix(HC), (c["body"] or {}).get("msg", "")) for c in rows]

    def unregistered(self, name: str) -> None:
        self.sql("insert into kaibot.event (type, payload, hash) values ('unregistered_event', "
                 f"'{json.dumps(en.unregistered(name))}', 'h')")


@pytest.fixture
def db(tmp_path):
    d = Db(tmp_path / "pg")
    d.sql("create table kaibot.event (id bigint generated always as identity primary key, ts timestamptz not null "
          "default now(), run_id bigint, type text not null, payload jsonb not null, prev_hash text, "
          "hash text not null)")
    d.unregistered("old_name")  # on the Health page before 0055: not alerted again
    d.file(M51)
    d.file(M55)
    d.run(7)
    d.sql(f"insert into vault.decrypted_secrets values ('kaibot_deadman_healthchecks_url', '{HC}')")
    yield d
    d.stop()


def test_an_unregistered_event_is_one_fail_ping_naming_it_then_green(db):
    db.beat(at("10:00"))
    assert db.tick(at("10:01")) == "engine alive"
    assert db.new_pings() == [("", "kaibot dead-man: engine alive")]  # the row from before 0055 is not alerted
    db.unregistered("brand_new")
    db.unregistered("brand_new")
    db.unregistered("other_new")
    db.beat(at("10:02"))
    assert db.tick(at("10:02")) == "engine alive"
    ((path, msg),) = db.new_pings()
    assert path == "/fail" and "brand_new, other_new" in msg and "event_names.py" in msg
    assert db.tick(at("10:03")) == "engine alive"
    assert db.new_pings() == [("", "kaibot dead-man: engine alive")]  # alerted once: green again


def test_the_names_join_a_fail_ping_the_tick_sends_anyway(db):
    db.sql("delete from vault.decrypted_secrets where name = 'kaibot_alpaca_paper_secret'")
    db.beat(at("10:00"))
    db.unregistered("brand_new")
    db.tick(at("10:01"))
    ((path, msg),) = db.new_pings()
    assert path == "/fail" and "no Alpaca paper keys" in msg and "brand_new" in msg


def test_a_failing_lookup_never_stops_the_dead_man(db):
    db.sql("alter table kaibot.deadman_mark rename to deadman_mark_gone")
    db.beat(at("10:00"))
    db.unregistered("brand_new")
    assert db.tick(at("10:01")) == "engine alive"
    assert db.new_pings() == [("", "kaibot dead-man: engine alive")]
