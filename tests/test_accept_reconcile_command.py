"""M9 ticket 1.14 (#439): the owner accepts a ledger/broker difference from the dashboard.

End to end: a difference appears (buys stop, the heartbeat shows the exact difference), the owner's dashboard queues
`accept_reconcile` through `public.kb_command` with what its screen showed, the engine applies the row from the
command table, and buys resume. A command for a difference that changed or healed after the screen was drawn is
refused and buys stay stopped.

The static and engine tests always run (the engine ones get the payload `kb_command` stores from `stored`, this
file's copy of the function's rule). The SQL ones need a scratch Postgres with the migrations applied
(KAIBOT_TEST_PG, or KAIBOT_TEST_PG_AUTO=1 as in CI) and then feed the engine the row the database really stored."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest
from test_ledger_engine import D2, carried, offer, rows, session

from kaibot.engine.reconcile import ACCEPT_COMMAND, QTY_EPS, Reconciler

ROOT = Path(__file__).resolve().parents[1]
FILES = sorted((ROOT / "migrations").glob("*_accept_reconcile_command.sql"))
PG = os.environ.get("KAIBOT_TEST_PG")
needs_pg = pytest.mark.skipif(not PG, reason="set KAIBOT_TEST_PG to a scratch Postgres with the migrations applied")
OWNER, OTHER = "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"
REASON = "a broker fee the ledger does not model"


# ------------------------------------------------------------------ static: what the migration may touch
def test_the_migration_only_widens_the_command_kinds():
    assert len(FILES) == 1
    sql = re.sub(r"--[^\n]*", "", FILES[0].read_text()).lower()
    assert re.findall(r"create or replace function ([\w.]+)", sql) == ["public.kb_command"]
    assert re.findall(r"alter table ([\w.]+)", sql) == ["kaibot.command", "kaibot.command"]
    assert "drop constraint if exists command_kind_check" in sql
    for word in ("create table", "drop table", "drop function", "truncate", "delete ", "update ", "service_role",
                 "net.http", "cron.", "grant select", "grant insert"):
        assert word not in sql, word
    assert sql.count("insert into") == 1 and "insert into kaibot.command" in sql
    assert "security definer set search_path = ''" in sql and "kaibot.is_owner()" in sql
    assert "from public, anon;" in sql and "to authenticated;" in sql
    assert "on conflict (idempotency_key) do nothing" in sql


def test_every_earlier_kind_is_kept_and_new_run_stays_with_kb_new_run():
    sql = re.sub(r"--[^\n]*", "", FILES[0].read_text())
    table = re.search(r"check \(kind in \(([^)]*)\)\)", sql)[1]
    first = re.search(r"kind text not null check \(kind in \(([^)]*)\)\)",
                      (ROOT / "migrations/0001_init.sql").read_text())
    kinds = lambda s: set(re.findall(r"'([a-z_]+)'", s))  # noqa: E731
    assert kinds(table) == kinds(first[1]) | {ACCEPT_COMMAND}
    fn = re.search(r"p_kind not in \(([^)]*)\)", sql)[1]
    assert kinds(fn) == kinds(table) - {"new_run"}


def test_the_engine_applies_every_kind_the_dashboard_can_queue():
    """Ticket 1.15 whitelists command kinds in the engine: `accept_reconcile` must be in whatever list there is."""
    core = (ROOT / "src/kaibot/engine/core.py").read_text()
    body = core[core.index("def apply_commands"):core.index("def _new_run")]
    assert "kind == ACCEPT_COMMAND" in body
    for name in ("COMMAND_KINDS", "KNOWN_COMMANDS", "ALLOWED_COMMANDS"):  # a whitelist, when one exists
        m = re.search(rf"^{name}\b[^\n]*=\s*(.+?)$", core, re.M | re.S)
        if m:
            block = core[m.start():core.index("\n\n", m.start())]
            assert "ACCEPT_COMMAND" in block or f'"{ACCEPT_COMMAND}"' in block, name


# ------------------------------------------------------------------ the engine, with the row kb_command stores
def stored(reason, shown):
    """What kb_command (0037) writes as the command's payload: the reason trimmed, and only kind, symbol and offset
    of each difference shown."""
    return {"reason": reason.strip(),
            "shown": [{"kind": x["kind"], "symbol": x.get("symbol"), "offset": x["offset"]} for x in shown]}


def drift(eng, cash=-500.0, broker_b=8.0):
    """The ledger says 10 B and no cash change; the broker says `broker_b` B and `cash` dollars."""
    eng._recon_inputs = lambda: {
        "ledger_qty": {"B": 10.0}, "broker_positions": {"B": {"qty": broker_b}} if broker_b else {},
        "ledger_cash": 0.0, "ledger_cash0": 0.0, "broker_cash": cash, "broker_cash0": 0.0, "equity": 5000.0}


def screen(eng):
    """What the dashboard reads from the heartbeat and sends back: the acceptable differences it displayed."""
    rec = eng._daily_status({})["reconcile"]
    return [{"kind": d["kind"], "symbol": d["symbol"], "offset": d["offset"]} for d in rec.get("diffs", [])
            if d["offset"] is not None]


def queue(store, payload, key="k-accept-1"):
    store.insert("command", {"kind": ACCEPT_COMMAND, "payload": payload, "idempotency_key": key, "applied_at": None})


def blocked(eng):
    return bool(eng.recon.last.diffs) and not eng._may_enter(60)


def test_the_difference_appears_the_owner_accepts_and_buys_resume(tmp_path):
    eng, broker, clock, store = carried(tmp_path)
    drift(eng)
    eng.startup()
    assert blocked(eng)
    rec = eng._daily_status({})["reconcile"]  # the heartbeat: exactly what the owner is shown
    assert rec["ok"] is False and rec["ts"]
    qty, cash = (next(d for d in rec["diffs"] if d["kind"] == k) for k in ("quantity", "cash"))
    assert (qty["symbol"], qty["ledger"], qty["broker"], qty["offset"]) == ("B", 10.0, 8.0, -2.0)
    assert (cash["ledger_change"], cash["broker_change"], cash["offset"]) == (0.0, -500.0, -500.0)

    queue(store, stored(f"  {REASON} ", screen(eng)))
    eng.apply_commands()
    assert not eng.recon.last.diffs and eng._may_enter(60)
    assert eng._daily_status({})["reconcile"] == {"ok": True, "checked": True, "ts": eng._recon_ts.isoformat()}
    assert rows(store, "command")[-1]["applied_at"] is not None
    ev = [e for e in rows(store, "event") if e["type"] == f"command_{ACCEPT_COMMAND}"]
    assert len(ev) == 1 and ev[0]["payload"]["reason"] == REASON
    kind, body = eng.journal.entries(kinds=("reconcile_resolved",))[-1][2:]
    assert body["reason"] == REASON and body["by"] == "owner (command)"
    assert {(o["kind"], o["symbol"], o["offset"]) for o in body["offsets"]} == {("quantity", "B", -2.0),
                                                                                ("cash", None, -500.0)}

    offer(eng, D2)  # a dip candidate at 10:24 on D2: bought now, where an open difference would have refused it
    session(eng, broker, clock, D2)
    trades = rows(store, "trade")
    assert len(trades) == 2 and trades[1]["exit_reason"] != "carried_over", "the buy went out after the acceptance"
    assert not eng.recon.last.diffs, "the accepted difference stays accepted through the session start"


@pytest.mark.parametrize("change, why", [
    (dict(cash=-900.0), "changed since it was shown"),
    (dict(broker_b=7.0), "changed since it was shown"),
    (dict(cash=0.0, broker_b=10.0), "healed"),
    (dict(cash=0.0), "healed"),  # one of the two healed: the owner looks again
])
def test_a_difference_that_changed_or_healed_after_the_screen_was_drawn_is_refused(tmp_path, change, why):
    eng, broker, clock, store = carried(tmp_path)
    drift(eng)
    eng.startup()
    shown = screen(eng)
    drift(eng, **change)
    eng._startup_reconcile("recheck")  # the engine sees the new state before the command arrives
    queue(store, stored(REASON, shown))
    eng.apply_commands()
    cmd = rows(store, "command")[-1]
    assert cmd["applied_at"] is not None and why in cmd["payload"]["error"], "applied once, with the refusal"
    assert not eng.journal.entries(kinds=("reconcile_resolved",))
    assert not [e for e in rows(store, "event") if e["type"] == f"command_{ACCEPT_COMMAND}"]
    if eng.recon.last.diffs:
        assert blocked(eng)
        rec = eng._daily_status({})["reconcile"]
        assert why in rec["refused"]["why"], "the dashboard can say why buys are still stopped"
        ev = [e for e in rows(store, "event") if e["type"] == "reconcile_blocked"][-1]
        assert ev["payload"]["when"] == "accept_refused" and why in ev["payload"]["refused"]
    else:
        assert "refused" not in eng._daily_status({})["reconcile"], "nothing left to explain once the books agree"
        assert not [e for e in rows(store, "event") if e["type"] == "reconcile_blocked"
                    and e["payload"].get("when") == "accept_refused"], "nothing is blocked, so no event says so"


def test_a_second_command_after_the_acceptance_changes_nothing(tmp_path):
    """Two devices, or a retry under another key: the first is accepted, the second finds nothing open."""
    eng, broker, clock, store = carried(tmp_path)
    drift(eng)
    eng.startup()
    shown = screen(eng)
    queue(store, stored(REASON, shown))
    queue(store, stored(REASON, shown), key="k-accept-2")
    eng.apply_commands()
    first, second = rows(store, "command")[-2:]
    assert first["applied_at"] is not None and "error" not in first["payload"]
    assert second["applied_at"] is not None and "healed" in second["payload"]["error"]
    assert len(eng.journal.entries(kinds=("reconcile_resolved",))) == 1
    assert len([e for e in rows(store, "event") if e["type"] == f"command_{ACCEPT_COMMAND}"]) == 1
    assert not [e for e in rows(store, "event") if e["type"] == "reconcile_blocked"
                and e["payload"].get("when") == "accept_refused"]
    assert not eng.recon.last.diffs and "refused" not in eng._daily_status({})["reconcile"]


def test_a_change_between_the_engines_last_check_and_the_command_is_refused_too(tmp_path):
    """The engine has not looked since the screen was drawn: `resolve` reads the venue again (#179)."""
    eng, broker, clock, store = carried(tmp_path)
    drift(eng)
    eng.startup()
    shown = screen(eng)
    drift(eng, cash=-900.0)
    queue(store, stored(REASON, shown))
    eng.apply_commands()
    assert blocked(eng) and "changed since it was shown" in rows(store, "command")[-1]["payload"]["error"]
    queue(store, stored(REASON, screen(eng)), key="k-accept-2")  # the owner looks again and accepts the new value
    eng.apply_commands()
    assert not eng.recon.last.diffs and "refused" not in eng._daily_status({})["reconcile"]


@pytest.mark.parametrize("payload", [
    {"reason": REASON, "shown": []},
    {"reason": REASON, "shown": [{"kind": "cash"}]},
    {"reason": REASON, "shown": "everything"},
    {"reason": "", "shown": [{"kind": "cash", "symbol": None, "offset": -500.0}]},
    {"reason": REASON, "shown": [{"kind": "cash", "symbol": None, "offset": -500.0}]},  # B's shares were not shown
])
def test_a_command_that_does_not_name_exactly_what_is_open_accepts_nothing(tmp_path, payload):
    eng, broker, clock, store = carried(tmp_path)
    drift(eng)
    eng.startup()
    queue(store, payload)
    eng.apply_commands()
    assert blocked(eng) and not eng.journal.entries(kinds=("reconcile_resolved",))


def test_a_difference_that_cannot_be_accepted_says_so_in_the_heartbeat(tmp_path):
    eng, broker, clock, store = carried(tmp_path)

    def down():
        raise RuntimeError("broker unreachable")

    eng._recon_inputs = down
    eng.startup()
    rec = eng._daily_status({})["reconcile"]
    assert rec["ok"] is False and [(d["kind"], d["offset"]) for d in rec["diffs"]] == [("unavailable", None)]
    assert screen(eng) == []


def test_the_heartbeat_list_is_bounded():
    r = Reconciler(lambda: {"ledger_qty": {f"S{i:02d}": 1.0 for i in range(30)}, "broker_positions": {}})
    r.check()
    st = r.status()
    assert len(st["diffs"]) == 20 and st["more"] == 10 and all(abs(d["offset"] + 1.0) < QTY_EPS for d in st["diffs"])


def test_the_terminal_command_still_accepts_without_a_screen(tmp_path):
    eng, broker, clock, store = carried(tmp_path)
    drift(eng)
    eng.startup()
    queue(store, {"reason": REASON})  # a row written before 0037, or by hand: no `shown`
    eng.apply_commands()
    assert not eng.recon.last.diffs


# ------------------------------------------------------------------ SQL: kb_command as the dashboard calls it
def psql(sql: str, uid: str | None = OWNER, check: bool = True):
    pre = f"set test.uid = '{uid or ''}';\n"
    r = subprocess.run(["psql", *shlex.split(PG), "-tA", "-q", "-v", "ON_ERROR_STOP=1"], input=pre + sql,
                       capture_output=True, text=True)
    if check and r.returncode:
        raise AssertionError(r.stderr)
    return r


def call(kind, payload, idem="idem-0001", uid=OWNER):
    body = json.dumps(payload).replace("'", "''")
    r = psql(f"select public.kb_command('{kind}', '{idem}', '{body}'::jsonb);", uid, check=False)
    return json.loads(r.stdout.strip().splitlines()[-1]) if not r.returncode else r.stderr


def commands():
    out = psql("select coalesce(jsonb_agg(to_jsonb(c) order by c.id), '[]'::jsonb) from kaibot.command c;", None)
    return json.loads(out.stdout.strip())


@pytest.fixture
def db():
    psql(f"""delete from kaibot.command;
             insert into auth.users (id) values ('{OWNER}'), ('{OTHER}') on conflict do nothing;
             insert into kaibot.owner (user_id) values ('{OWNER}') on conflict do nothing;""", None)


SHOWN = [{"kind": "quantity", "symbol": "B", "offset": -2.0, "ledger": 10, "broker": 8},
         {"kind": "cash", "symbol": None, "offset": -500.0, "tolerance": 2.5}]


@needs_pg
def test_sql_owner_only(db):
    for uid in (OTHER, None):
        assert "not authorised" in call(ACCEPT_COMMAND, {"reason": REASON, "shown": SHOWN}, uid=uid)
    assert commands() == []
    for role in ("anon", "public"):
        has = psql(f"select has_function_privilege('{role}', 'public.kb_command(text, text, jsonb)', 'execute');",
                   None).stdout.strip()
        assert has == "f", role


@needs_pg
@pytest.mark.parametrize("role, uid", [("authenticated", OWNER), ("authenticated", OTHER), ("anon", None)])
def test_sql_the_command_table_cannot_be_written_past_kb_command(db, role, uid):
    """The only way from the dashboard is kb_command: no signed-in account, the owner included, writes a row itself
    (a row written directly could carry no `shown`)."""
    for sql in ("insert into kaibot.command (idempotency_key, kind, payload) values ('idem-direct', "
                "'accept_reconcile', '{\"reason\": \"by hand\"}'::jsonb);",
                "update kaibot.command set applied_at = null;", "select count(*) from kaibot.command;"):
        r = psql(f"set role {role};\n{sql}", uid, check=False)
        assert r.returncode and "permission denied" in r.stderr, (role, sql, r.stdout, r.stderr)
    assert commands() == []


@needs_pg
def test_sql_the_row_holds_the_reason_and_only_what_was_shown_and_the_key_is_idempotent(db):
    assert call(ACCEPT_COMMAND, {"reason": f"  {REASON} ", "shown": SHOWN, "extra": "dropped"}) == {"queued": True}
    assert call(ACCEPT_COMMAND, {"reason": "another reason", "shown": SHOWN[:1]}) == {"queued": True}  # same key
    got = commands()
    assert len(got) == 1 and got[0]["kind"] == ACCEPT_COMMAND and got[0]["applied_at"] is None
    assert got[0]["payload"] == stored(f"  {REASON} ", SHOWN)


@needs_pg
@pytest.mark.parametrize("payload, why", [
    ({"shown": SHOWN}, "a reason"),
    ({"reason": "  x ", "shown": SHOWN}, "a reason"),
    ({"reason": "x" * 201, "shown": SHOWN}, "a reason"),
    ({"reason": REASON}, "differences shown"),
    ({"reason": REASON, "shown": []}, "differences shown"),
    ({"reason": REASON, "shown": {"kind": "cash"}}, "differences shown"),
    ({"reason": REASON, "shown": [SHOWN[1]] * 51}, "differences shown"),
    ({"reason": REASON, "shown": ["cash"]}, "can be accepted"),
    ({"reason": REASON, "shown": [{"kind": "unavailable", "offset": 1}]}, "can be accepted"),
    ({"reason": REASON, "shown": [{"kind": "foreign_fill", "symbol": "B", "offset": 1}]}, "can be accepted"),
    ({"reason": REASON, "shown": [{"kind": "cash", "offset": "-500"}]}, "can be accepted"),
    ({"reason": REASON, "shown": [{"kind": "cash", "symbol": None}]}, "can be accepted"),
    ({"reason": REASON, "shown": [{"kind": "cash", "symbol": "B", "offset": 1}]}, "can be accepted"),
    ({"reason": REASON, "shown": [{"kind": "quantity", "offset": 1}]}, "can be accepted"),
    ({"reason": REASON, "shown": [{"kind": "quantity", "symbol": "b; drop", "offset": 1}]}, "can be accepted"),
])
def test_sql_a_malformed_acceptance_queues_nothing(db, payload, why):
    assert why in call(ACCEPT_COMMAND, payload)
    assert commands() == []


@needs_pg
def test_sql_the_other_commands_are_as_before(db):
    assert call("pause", {"anything": 1}, "idem-pause") == {"queued": True}
    assert call("set_level", {"level": "balanced", "x": 1}, "idem-level") == {"queued": True}
    assert "unknown level" in call("set_level", {"level": "reckless"}, "idem-level2")
    assert "unknown command" in call("new_run", {"amount_usd": 1e7, "level": "balanced"}, "idem-run")
    assert "unknown command" in call("anything", {}, "idem-any")
    assert "idempotency key required" in call("pause", {}, "short")
    assert [(c["kind"], c["payload"]) for c in commands()] == [("pause", {}), ("set_level", {"level": "balanced"})]
    assert "command_kind_check" in psql("insert into kaibot.command (idempotency_key, kind) values ('idem-raw', "
                                        "'sell_the_house');", None, check=False).stderr


@needs_pg
def test_sql_end_to_end_the_stored_row_is_what_the_engine_accepts(db, tmp_path):
    eng, broker, clock, store = carried(tmp_path)
    drift(eng)
    eng.startup()
    assert blocked(eng)
    seen = eng._daily_status({})["reconcile"]["diffs"]  # the dashboard sends the rows as it read them
    assert call(ACCEPT_COMMAND, {"reason": REASON, "shown": seen}) == {"queued": True}
    row = commands()[0]  # the engine reads this row from the command table
    queue(store, row["payload"], key=row["idempotency_key"])
    eng.apply_commands()
    assert not eng.recon.last.diffs and eng._may_enter(60)
    offer(eng, D2)
    session(eng, broker, clock, D2)
    assert len(rows(store, "trade")) == 2, "buys resume"
