"""Audit 2 ticket 7.7 (#717): a failure of the dead-man itself alerts somebody.

Migration 0051: the pg_cron job runs `kaibot.deadman_tick()`, which runs `kaibot.deadman_check()` unchanged and pings
the dead-man's own Healthchecks.io check (URL in Vault, `kaibot_deadman_healthchecks_url`): success when all is
well, `/fail` with the reason for a dead-man halt, for the dead-man acting on a silent engine, for missing paper
keys (checked on every run, the engine alive or not) and for the check raising; no ping at all when the job does not
run, which Healthchecks turns into an alert by itself.

The drill of the ticket, "disabling the Vault key produces an alert": `test_drill_disabling_a_vault_key_alerts`.
The database is a throwaway local Postgres (tests/pg_harness.py), never Supabase; pg_net is the harness's table.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "unit"))
from pg_harness import DeadmanDb  # noqa: E402
from test_order_manager import DAY, NY, venue  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
M51 = ROOT / "migrations" / "0051_deadman_alerts.sql"
HC = "https://hc-ping.com/0b1c2d3e-test"
R = 7


def at(hhmm: str, day: str = DAY):
    return pd.Timestamp(f"{day} {hhmm}", tz=NY).to_pydatetime()


class Db(DeadmanDb):
    def tick(self, now) -> str:
        return self.one(f"select kaibot.deadman_tick('{now.isoformat()}')")

    def pings(self) -> list[tuple[str, str]]:
        """(url suffix, message) of the Healthchecks pings since the last call, oldest first; marked done so `pump`
        skips them."""
        rows = [json.loads(r) for r in self.sql(
            "select row_to_json(c) from net.calls c where url like 'https://hc-ping.com/%' and not done order by id")]
        self.sql("update net.calls set done = true where url like 'https://hc-ping.com/%'")
        return [(c["url"].removeprefix(HC), (c["body"] or {}).get("msg", "")) for c in rows]

    new_pings = pings


@pytest.fixture
def db(tmp_path):
    d = Db(tmp_path / "pg")
    d.file(M51)
    d.run(R)
    d.sql(f"insert into vault.decrypted_secrets values ('kaibot_deadman_healthchecks_url', '{HC}')")
    yield d
    d.stop()


# ---------------------------------------------------------------- the file


def test_0051_is_additive_and_keeps_the_one_job():
    s = "\n".join(ln for ln in M51.read_text().splitlines() if not ln.lstrip().startswith("--")).lower()
    assert not re.search(r"\b(drop|truncate|delete from|update)\b", s)
    assert "function kaibot.deadman_check" not in s and "function kaibot.deadman_act" not in s
    assert re.findall(r"create or replace function (kaibot\.\w+)", s) == ["kaibot.deadman_ping", "kaibot.deadman_tick"]
    assert "select cron.schedule('kaibot_deadman', '* 13-21 * * 1-5', 'select kaibot.deadman_tick()');" in s
    assert s.count("security definer") == 2 and "hc-ping.com" not in s  # the URL lives in Vault only


def test_the_job_runs_the_tick_on_0019s_schedule(db):
    assert db.sql("select jobname || ' | ' || schedule || ' | ' || command from cron.job") == [
        "kaibot_deadman | * 13-21 * * 1-5 | select kaibot.deadman_tick()"]
    assert db.one("select has_function_privilege('authenticated', 'kaibot.deadman_tick(timestamptz)', "
                  "'execute')") == "f"
    assert db.one("select has_function_privilege('anon', 'kaibot.deadman_ping(text, text)', 'execute')") == "f"


# ---------------------------------------------------------------- what each minute sends


def test_all_well_is_a_success_ping_every_minute(db):
    db.beat(at("10:00"))
    assert db.tick(at("10:01")) == "engine alive"
    assert db.tick(at("16:30")) == "market closed"
    assert db.new_pings() == [("", "kaibot dead-man: engine alive"), ("", "kaibot dead-man: market closed")]


def test_without_the_url_nothing_is_sent_and_the_tick_is_the_check(db):
    db.sql("delete from vault.decrypted_secrets where name = 'kaibot_deadman_healthchecks_url'")
    db.beat(at("10:00"))
    assert db.tick(at("10:01")) == db.check(at("10:01")) == "engine alive"
    db.sql("insert into vault.decrypted_secrets values ('kaibot_deadman_healthchecks_url', 'http://plain/x')")
    assert db.tick(at("10:02")) == "engine alive"
    assert db.one("select count(*) from net.calls") == "0"


def test_a_dead_man_halt_and_its_acting_are_fail_pings_until_the_engine_beats_again(db):
    b, _ = venue()
    db.beat(at("10:00"))
    assert db.tick(at("10:30")) == "reading the account"
    assert db.new_pings() == [("/fail", "kaibot dead-man acting, the engine heartbeat is stale: reading the account")]
    db.pump(b)
    said = db.tick(at("10:31"))
    assert said.startswith("acted:")
    ((path, msg),) = db.new_pings()
    assert path == "/fail" and msg.startswith("kaibot dead-man halt: engine silent (heartbeat ")
    assert db.one("select count(*) from kaibot.halt where kind = 'deadman'") == "1"
    db.tick(at("10:32"))
    assert [p for p, _ in db.new_pings()] == ["/fail"]  # still acting: the check stays red
    db.beat(at("10:33"))
    assert db.tick(at("10:33")) == "engine alive"
    assert db.new_pings() == [("", "kaibot dead-man: engine alive")]  # green again


def test_the_check_raising_is_a_fail_ping_and_its_work_is_rolled_back(db):
    db.beat(at("10:00"))
    db.sql("alter table kaibot.deadman_pass rename to deadman_pass_gone")  # a broken dead-man
    said = db.tick(at("10:30"))
    assert said.startswith("failed: ")
    ((path, msg),) = db.new_pings()
    assert path == "/fail" and msg.startswith("kaibot dead-man FAILED: kaibot.deadman_check raised")
    assert db.one("select count(*) from net.calls where url like 'https://paper-api%'") == "0"


def test_a_ping_that_cannot_be_queued_never_stops_the_dead_man(db):
    db.sql("alter table net.calls rename to calls_gone")  # pg_net's queue unusable
    db.beat(at("10:00"))
    assert db.tick(at("10:01")) == "engine alive"


# ---------------------------------------------------------------- the drill of the ticket


def test_drill_disabling_a_vault_key_alerts(db):
    """#717's check: the engine alive, after the close; the paper secret is renamed in Vault (disabled), and the
    next minute's run sends a fail ping naming it. Put back, the next run is green."""
    db.beat(at("16:05"))
    assert db.tick(at("16:06")) == "market closed"
    assert db.new_pings() == [("", "kaibot dead-man: market closed")]
    db.sql("update vault.decrypted_secrets set name = 'kaibot_alpaca_paper_secret_drill' "
           "where name = 'kaibot_alpaca_paper_secret'")
    assert db.tick(at("16:07")) == "market closed; no paper keys in Vault"
    ((path, msg),) = db.new_pings()
    assert path == "/fail" and "no Alpaca paper keys in Vault" in msg
    db.beat(at("10:00", "2026-09-16"))
    assert db.tick(at("10:01", "2026-09-16")) == "engine alive; no paper keys in Vault"  # engine alive: still red
    assert [p for p, _ in db.new_pings()] == ["/fail"]
    db.sql("update vault.decrypted_secrets set name = 'kaibot_alpaca_paper_secret' "
           "where name = 'kaibot_alpaca_paper_secret_drill'")
    assert db.tick(at("10:02", "2026-09-16")) == "engine alive"
    assert db.new_pings() == [("", "kaibot dead-man: engine alive")]


def test_a_missing_key_with_the_engine_silent_is_the_halt_alert(db):
    db.sql("delete from vault.decrypted_secrets where name = 'kaibot_alpaca_paper_key_id'")
    db.beat(at("10:00"))
    assert db.tick(at("10:30")) == "no keys"
    ((path, msg),) = db.new_pings()
    assert path == "/fail" and "no paper keys in Vault: nothing checked" in msg
