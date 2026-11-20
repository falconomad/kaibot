"""S97 (#371): the shadow rules' results to date, by four read-only queries (scripts/shadow_results.sql, run by
scripts/shadow_results.sh; the text is docs/research/event_driven/design.md section 5, the same as the issue's).

The file checks run everywhere: the script's queries are the design's, byte for byte; they are plain SELECTs; the
rows they read are the ones engine/shadow.py writes (type, payload keys, signal mode, counterfactual keys). The SQL
checks need a scratch Postgres with every migration applied (KAIBOT_TEST_PG; CI and KAIBOT_TEST_PG_AUTO=1 build
one; skipped otherwise): each query runs on rows shaped like the engine's (a restart that wrote a second summary and
a second signal, a signal the gate sized to zero, a second run with another pot, a session without a summary, a
shadow error) and gives the numbers worked out by hand below; and the whole file runs in a read-only session."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SQL = ROOT / "scripts" / "shadow_results.sql"
SH = ROOT / "scripts" / "shadow_results.sh"
DESIGN = ROOT / "docs" / "research" / "event_driven" / "design.md"
SHADOW = ROOT / "src" / "kaibot" / "engine" / "shadow.py"
PG = os.environ.get("KAIBOT_TEST_PG")
needs_pg = pytest.mark.skipif(not PG, reason="set KAIBOT_TEST_PG to a scratch Postgres with every migration applied")


def _blocks(text: str) -> list[str]:
    return re.findall(r"```sql\n(.*?)```", text, re.S)


def queries() -> list[str]:
    """The script's four statements: the text after each `-- Qn.` header line, up to the next one."""
    parts = re.split(r"^-- Q\d\..*\n", SQL.read_text(), flags=re.M)[1:]
    return [p.strip() for p in parts]


def test_the_script_runs_the_designs_four_queries_unchanged():
    section = DESIGN.read_text().split("## 5. Evidence we already have", 1)[1].split("\n## 6.", 1)[0]
    design = [q.strip() for q in _blocks(section)]
    assert len(design) == 4
    assert [q.strip() for q in queries()] == design


def test_the_queries_only_read():
    for q in queries():
        code = re.sub(r"--[^\n]*", "", q).lower()
        assert re.match(r"\s*(with|select)\b", code)
        assert not re.search(r"\b(insert|update|delete|truncate|drop|alter|create|grant|revoke|copy|call|do)\b"
                             r"|\bset\s+(role|session)|pg_sleep|dblink|lo_", code)
    sh = SH.read_text()
    assert "default_transaction_read_only=on" in sh and "set default_transaction_read_only = on" in sh
    assert os.access(SH, os.X_OK)


def test_the_rows_read_are_the_ones_the_engine_writes():
    src = SHADOW.read_text()
    assert '"shadow_session_end", {"day": str(self.day), **summary}' in src
    assert all(k in src for k in ('"signals":', '"sized":', '"pnl_usd":', '"avg_r":', '"symbols": len('))
    assert '"mode": "shadow"' in src and '"counterfactual": r' in src
    assert '"r_multiple":' in src and '"reason": why' in src
    assert 'store.event("shadow_error", {"where": where, "error":' in src
    names = {(ROOT / "src" / "kaibot" / "strategies" / f).read_text().split('NAME = "', 1)[1].split('"', 1)[0]
             for f in ("orb.py", "news_continuation.py")}
    assert names == {"orb_stocks_in_play", "news_continuation"}
    sim = (ROOT / "src" / "kaibot" / "replay" / "sim.py").read_text()  # Q3 counts 'stop' and 'time' exits
    assert '"time"' in sim and '"stop"' in sim


# ---------------------------------------------------------------- on a scratch Postgres

def summary(orb=None, news=None, symbols=75):
    import json

    p = {"cost_ms": {"setup": 900.1, "p50": 3.2, "max": 40.0, "symbols": symbols}}
    for name, v in (("orb_stocks_in_play", orb), ("news_continuation", news)):
        if v is not None:
            p[name] = dict(zip(("signals", "sized", "pnl_usd", "avg_r"), v, strict=True))
    return json.dumps(p)


def fixture() -> str:
    """Two runs (pot 10,000 then 20,000). Sessions 13, 16, 17 Nov have a summary; on 16 Nov the engine restarted and
    wrote a second one (the latest wins); 17 Nov's has no news_continuation key; 18 Nov started without one."""
    import json

    ev = []

    def end(day, run, orb, news, ts):
        p = json.loads(summary(orb, news))
        ev.append((ts, run, "shadow_session_end", json.dumps({"day": day, **p})))

    end("2026-11-13", ":r1", (2, 1, 10.0, 0.5), (1, 1, -5.0, -0.25), "2026-11-13 21:01+00")
    end("2026-11-16", ":r1", (9, 9, 99.0, 9.0), (5, 5, 50.0, 5.0), "2026-11-16 21:01+00")  # replaced below
    end("2026-11-16", ":r1", (3, 2, 20.0, 0.5), (0, 0, 0, None), "2026-11-16 21:20+00")
    end("2026-11-17", ":r2", (3, 0, 0, None), None, "2026-11-17 21:01+00")
    for d in ("13", "16", "17", "18"):
        ev.append((f"2026-11-{d} 14:31+00", ":r1" if d < "17" else ":r2", "session_start", "{}"))
    ev.append(("2026-11-17 16:00+00", ":r2", "shadow_error",
               json.dumps({"where": "minute", "error": "KeyError: XYZ"})))
    rows = ",\n".join(f"('{ts}', {run}, '{t}', '{p}'::jsonb, 'shadow-{i}')" for i, (ts, run, t, p) in enumerate(ev))

    def sig(strategy, sym, ts, cf, run=":r1"):
        f = {"m": 30, "never_sent": True, "counterfactual": cf}
        return f"({run}, '{strategy}', 'shadow', '{sym}', '{ts}', 'h', '{json.dumps(f)}'::jsonb)"

    cf = lambda r, why: {"fill": 10.0, "reason": why, "qty": 5, "pnl_usd": r * 10, "r_multiple": r}  # noqa: E731
    sigs = ",\n".join([
        sig("orb_stocks_in_play", "AAA", "2026-11-13 15:00+00", cf(0.5, "target")),
        sig("orb_stocks_in_play", "BBB", "2026-11-16 15:00+00", cf(7.0, "time")),  # before the restart: replaced
        sig("orb_stocks_in_play", "BBB", "2026-11-16 15:30+00", cf(1.0, "time")),
        sig("orb_stocks_in_play", "CCC", "2026-11-16 15:05+00", cf(0.0, "stop")),
        sig("orb_stocks_in_play", "DDD", "2026-11-16 15:10+00", None),  # sized to zero by the gate
        sig("news_continuation", "EEE", "2026-11-13 16:00+00", cf(-0.25, "stop")),
        sig("news_continuation", "FFF", "2026-11-14 01:00+00", {"r_multiple": None}),  # no result: left out
    ])
    return f"""begin;
set local client_min_messages = warning;
truncate kaibot.event, kaibot.signal cascade;
insert into kaibot.run (amount_usd, status) values (10000, 'stopped') returning id as r1 \\gset
insert into kaibot.run (amount_usd, status) values (20000, 'running') returning id as r2 \\gset
insert into kaibot.event (ts, run_id, type, payload, hash) values
{rows};
insert into kaibot.signal (run_id, strategy, mode, symbol, ts, features_hash, features) values
{sigs};
"""


def run_queries() -> list[list[list[str]]]:
    """Each query's rows (fields split on |), on the fixture, inside one transaction that is rolled back."""
    marks = "\n".join(f"\\echo '@@Q{i}'\n{q}" for i, q in enumerate(queries(), 1))
    r = subprocess.run(["psql", *shlex.split(PG), "-tA", "-q", "-v", "ON_ERROR_STOP=1"],
                       input=fixture() + marks + "\nrollback;\n", capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    parts = r.stdout.split("@@Q")[1:]
    return [[line.split("|") for line in p.splitlines()[1:] if line] for p in parts]


@needs_pg
def test_q1_one_row_per_rule():
    q1 = {row[0]: row for row in run_queries()[0]}
    cols = ["rule", "sessions", "first_day", "last_day", "signals", "sized_trades", "sessions_with_a_trade",
            "trades_per_session", "mean_r_per_trade", "up_sessions", "down_sessions", "mean_daily_bp_of_pot",
            "sd_daily_bp_of_pot", "t_daily", "sessions_for_900_trades_at_this_rate"]
    orb = dict(zip(cols, q1["orb_stocks_in_play"], strict=True))
    news = dict(zip(cols, q1["news_continuation"], strict=True))
    # orb: 13 Nov (1 trade, +10 on 10,000 = 10 bp), 16 Nov's later summary (2 trades, +20 = 20 bp), 17 Nov (none,
    # 0 bp on 20,000); mean R weighted by trades: (0.5 x 1 + 0.5 x 2) / 3
    assert orb == {"rule": "orb_stocks_in_play", "sessions": "3", "first_day": "2026-11-13", "last_day": "2026-11-17",
                   "signals": "8", "sized_trades": "3", "sessions_with_a_trade": "2", "trades_per_session": "1.00",
                   "mean_r_per_trade": "0.500", "up_sessions": "2", "down_sessions": "0",
                   "mean_daily_bp_of_pot": "10.00", "sd_daily_bp_of_pot": "10.00", "t_daily": "1.73",
                   "sessions_for_900_trades_at_this_rate": "900"}
    # news: 17 Nov has no key for it, so 2 sessions; -5 bp and 0 bp
    assert news == {"rule": "news_continuation", "sessions": "2", "first_day": "2026-11-13", "last_day": "2026-11-16",
                    "signals": "1", "sized_trades": "1", "sessions_with_a_trade": "1", "trades_per_session": "0.50",
                    "mean_r_per_trade": "-0.250", "up_sessions": "0", "down_sessions": "1",
                    "mean_daily_bp_of_pot": "-2.50", "sd_daily_bp_of_pot": "3.54", "t_daily": "-1.00",
                    "sessions_for_900_trades_at_this_rate": "1800"}


@needs_pg
def test_q2_day_by_day_with_each_runs_pot():
    q2 = run_queries()[1]
    assert [(r[0], r[2], r[3], r[4], r[5], r[6], r[8]) for r in q2] == [
        ("2026-11-13", "10000.00", "news_continuation", "1", "1", "-5.0", "75"),
        ("2026-11-13", "10000.00", "orb_stocks_in_play", "2", "1", "10.0", "75"),
        ("2026-11-16", "10000.00", "news_continuation", "0", "0", "0", "75"),
        ("2026-11-16", "10000.00", "orb_stocks_in_play", "3", "2", "20.0", "75"),
        ("2026-11-17", "20000.00", "orb_stocks_in_play", "3", "0", "0", "75"),
    ]
    assert q2[2][7] == "" and q2[1][7] == "0.5"  # avg_r null on a day without a trade


@needs_pg
def test_q3_per_would_be_trade():
    q3 = {row[0]: row for row in run_queries()[2]}
    cols = ["rule", "trades", "sessions_with_a_trade", "symbols", "mean_r", "sd_r", "t_per_trade", "win_pct",
            "stopped_out", "held_to_1555", "best_r", "worst_r", "total_r", "total_r_without_best_5",
            "trades_for_t3_if_this_mean_were_true"]
    orb = dict(zip(cols, q3["orb_stocks_in_play"], strict=True))
    news = dict(zip(cols, q3["news_continuation"], strict=True))
    # orb: AAA 0.5 (target), BBB 1.0 (the row after the restart, time), CCC 0.0 (stop); DDD was sized to zero
    assert orb == {"rule": "orb_stocks_in_play", "trades": "3", "sessions_with_a_trade": "2", "symbols": "3",
                   "mean_r": "0.500", "sd_r": "0.500", "t_per_trade": "1.73", "win_pct": "66.7", "stopped_out": "1",
                   "held_to_1555": "1", "best_r": "1.00", "worst_r": "0.00", "total_r": "1.50",
                   "total_r_without_best_5": "0.00", "trades_for_t3_if_this_mean_were_true": "9"}
    # news: one trade (FFF has no result); 14 Nov 01:00 UTC is still 13 Nov in New York
    assert news == {"rule": "news_continuation", "trades": "1", "sessions_with_a_trade": "1", "symbols": "1",
                    "mean_r": "-0.250", "sd_r": "", "t_per_trade": "", "win_pct": "0.0", "stopped_out": "1",
                    "held_to_1555": "0", "best_r": "-0.25", "worst_r": "-0.25", "total_r": "-0.25",
                    "total_r_without_best_5": "0.00", "trades_for_t3_if_this_mean_were_true": ""}


@needs_pg
def test_q4_sessions_without_a_summary_and_shadow_errors():
    assert run_queries()[3] == [["shadow error", "2026-11-17", "minute: KeyError: XYZ"],
                                ["session without a shadow summary", "2026-11-18", ""]]


@needs_pg
def test_the_whole_file_runs_in_a_read_only_session():
    """What scripts/shadow_results.sh does: the server refuses any write in the session, and the file runs."""
    env = {**os.environ, "PGOPTIONS": "-c default_transaction_read_only=on"}
    r = subprocess.run(["psql", *shlex.split(PG), "-X", "-q", "-v", "ON_ERROR_STOP=1", "-c",
                        "set default_transaction_read_only = on", "-f", str(SQL)],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    w = subprocess.run(["psql", *shlex.split(PG), "-X", "-q", "-v", "ON_ERROR_STOP=1", "-c",
                        "insert into kaibot.run (amount_usd) values (5000)"], capture_output=True, text=True, env=env)
    assert w.returncode and "read-only transaction" in w.stderr
