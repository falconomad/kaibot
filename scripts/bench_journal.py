"""M10 1.24 (#449): how long the engine takes to start and to tick on a year of journal, and how much memory the
journal's state holds. Run it before and after a change to engine/journal.py and compare.

    PYTHONPATH=src python scripts/bench_journal.py --out /tmp/bench            # build a year, then measure it
    PYTHONPATH=src python scripts/bench_journal.py --out /tmp/bench --sessions 21 --positions 5   # a small one

The journal is synthetic and deterministic (same arguments: the same bytes, identity included): 252 sessions of a
multi-day book with `--positions` open positions and two rules. Per session it holds what the paper engine writes
(shapes taken from a session of the go-live drill, tests/test_outage_drill_multiday.py):

- 390 minute rows (`row` entries, table `ledger`, each with the multi-day marks of every open position);
- about a dozen events, the ledger's day and position rows, the daily result, the gate's random baseline (1000
  totals per rule) and its report, three shadow signals;
- the rule's own entries: `md_decision`, `md_send`, `md_sent`, ten `md_book_peak`, the ledger's `mark`, one
  `reconcile`; every 21st session a rebalance of a quarter of the book through the order manager (`om_intent`,
  `om_state`, `fill`).

What is measured, each in a process of its own (so the memory figure is that step's):

- `open`: `Journal(path)`, which checks every entry's checksum (#496);
- `store`: `JournalStore(journal, remote, live=True)`, the fold of the engine's tables;
- `folds`: the other folds a start makes (the ledger, the order manager, the reads of `Engine.startup`);
- `start_total`: the three together, and the process's peak memory after them (`rss_mb`);
- `snapshot`: after 1.24, the snapshot the engine's upkeep writes with the market closed (the check against a fold
  of the whole journal included), which the `next_start` then folds from;
- `tick_mid_ms` / `tick_send_ms`: the journal reads ONE tick makes outside and inside the 15:50-15:57 ET send
  window (the reads recorded from the drill with one rule: TICK_MID, TICK_SEND), median of `--ticks` rounds,
  with the minute row's own append left out (it is one durable write either way).

Nothing here touches a broker or Supabase.
"""

from __future__ import annotations

import argparse
import json
import resource
import sqlite3
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from kaibot.engine import journal as J
from kaibot.engine.ledger import LEDGER_KINDS, Ledger
from kaibot.engine.orders import OM_KINDS, OrderManager

RULES = ("trend_momentum", "low_vol_quality")
RUN_ID = (7 << 32) + 1
# the journal reads of one tick, as recorded from the go-live drill (kinds, times): outside the send window ...
TICK_MID = ((("md_book_end",), 4), (("md_book_peak",), 1), (("md_decision",), 3), (("md_retired", "md_reenabled"), 3))
# ... and at 15:50:07 ET, the tick that sends the rule's orders
TICK_SEND = ((("md_book_end",), 4), (("md_book_peak",), 1), (("md_decision",), 5),
             (("md_retired", "md_reenabled"), 4), (("md_send",), 2), (("md_sent",), 1))
# what `Engine.startup` (and the daily path's first tick) read besides the ledger and the order manager
STARTUP_READS = (("command_waiting", "command_done"), ("div_accrual", "div_paid"), ("intent",), ("md_exit",),
                 ("md_exit",), ("md_exit_done",), ("md_exit_done",), ("md_exit_done",), ("md_exit_intent",),
                 ("md_unfilled",), ("om_intent",), ("own_order_ids", "md_exit"), ("recon_fills_since",),
                 ("reconcile_resolved", "reconcile_expired"), ("reconcile_resolved",), ("md_book_peak",),
                 ("md_book_breach", "md_book_breach_clear"), ("reconcile",))


class Remote:
    """A Supabase that is never asked (the store only needs one to be `live`)."""


def sessions(n: int) -> list[datetime]:
    """The 09:30 ET opens (13:30 UTC, summer and winter alike: the bench does not care) of `n` weekdays."""
    out, d = [], datetime(2026, 1, 5, 13, 30, tzinfo=UTC)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


class Writer:
    """Appends in bulk with the journal's own checksum chain (`journal.entry_sum`), one transaction per session.
    `Journal.append` would give the same entries, one fsync each: minutes for a year."""

    def __init__(self, path: Path):
        self.db = sqlite3.connect(str(path), isolation_level=None)
        self.db.execute("pragma journal_mode=wal")
        self.db.executescript(J.SCHEMA)
        # a fixed identity: the same arguments give the same file
        self.db.execute("insert into meta (k, v) values ('journal_id', ?), ('epoch', '7'), ('created', ?), "
                        "('checksum_from', '1')", ("0" * 31 + "7", "2026-01-05T13:00:00+00:00"))
        self.seq, self.prev, self.ts, self.id = 0, None, "", (7 << 32) + 1
        self.rows: list[tuple] = []

    def add(self, kind: str, body: dict) -> int:
        self.seq += 1
        text = json.dumps(body, sort_keys=True, separators=(",", ":"))
        s = J.entry_sum(self.prev, self.seq, self.ts, kind, text)
        self.rows.append((self.seq, self.ts, kind, text, s))
        self.prev = s
        return self.seq

    def insert(self, table: str, row: dict) -> None:
        self.id += 1
        self.add("row", {"op": "insert", "table": table, "row": {**row, "id": self.id}})

    def upsert(self, table: str, row: dict, on: str) -> None:
        self.add("row", {"op": "upsert", "table": table, "row": row, "on_conflict": on})

    def event(self, kind: str, payload: dict) -> None:
        self.insert("event", {"ts": self.ts, "run_id": RUN_ID, "type": kind, "payload": payload,
                              "prev_hash": "0" * 64, "hash": f"{self.seq:064x}"})

    def flush(self) -> None:
        self.db.execute("begin")
        self.db.executemany("insert into entry (seq, ts, kind, body, sum) values (?, ?, ?, ?, ?)", self.rows)
        self.db.execute("commit")
        self.rows = []

    def close(self) -> None:
        self.flush()
        self.db.execute("pragma wal_checkpoint(truncate)")
        self.db.close()


def build(path: Path, n_sessions: int = 252, positions: int = 15) -> dict:
    """Write the synthetic journal. Returns its size."""
    if path.exists():
        path.unlink()
    w = Writer(path)
    held = [(RULES[i % 2], f"S{i:03d}", 10.0 + i, 50.0 + 3 * i) for i in range(positions)]
    nxt, act = positions, 0
    days = sessions(n_sessions)
    w.ts = (days[0] - timedelta(minutes=30)).isoformat()
    w.add("reconcile", {"accepted": [], "diffs": [], "ok": True, "when": "startup"})
    w.add("row", {"op": "insert", "table": "run", "row": {
        "id": RUN_ID, "amount_usd": 20000.0, "status": "running", "kind": "paper", "level": "balanced",
        "label": "Paper · Balanced · $20,000", "peak_equity": 20000.0, "started_on": str(days[0].date())}})
    w.add("run", {"amount": 20000.0, "broker_cash0": 20000.0, "run_id": RUN_ID, "started": w.ts})
    w.insert("segment", {"run_id": RUN_ID, "level": "balanced", "from_session": str(days[0].date()),
                         "policy_hash": "f2c73db0dc43"})
    w.add("recon_fills_since", {"ts": w.ts})
    for st, sym, q, px in held:
        act += 1
        w.add("fill", {"activity": f"a{act}", "coid": f"kb1-{st}-{sym}-20260102-target-1", "fees": 0.0, "px": px,
                       "qty": q, "run_id": RUN_ID, "session": "2026-01-02", "side": "buy", "strategy": st,
                       "symbol": sym})
    for k, d in enumerate(days):
        day = str(d.date())
        w.ts = (d - timedelta(minutes=5)).isoformat()
        w.event("session_start", {"day": day, "start_equity": 20000.0 + k, "level": "balanced", "minutes": 390})
        for m in range(390):
            t = d + timedelta(minutes=m, seconds=67)
            w.ts = t.isoformat()
            marks = [{"rule": st, "symbol": sym, "qty": q, "avg_cost": px, "mark": round(px * (1 + m / 1e5), 4),
                      "mark_ts": (t - timedelta(seconds=7)).isoformat(), "mark_from": "minute_bar",
                      "value": round(q * px, 2)} for st, sym, q, px in held]
            w.insert("ledger", {"run_id": RUN_ID, "ts": str(t), "cash": 5000.0, "positions": {},
                                "equity_venue": 20000.0 + k + m / 100, "equity_shadow": 20000.0 + k + m / 100,
                                "multi_day": {"v": 1, "cash_ledger": 5000.0, "invested": 15000.0, "settling": [],
                                              "positions": marks}})
            if m % 39 == 0:
                w.add("md_book_peak", {"peak": 20000.0 + k + m / 100, "run_id": RUN_ID, "ts": w.ts})
            if m % 130 == 26:  # a shadow rule's signal, with its counterfactual
                w.insert("signal", {"run_id": RUN_ID, "strategy": "orb_stocks_in_play", "mode": "shadow",
                                    "symbol": held[m % len(held)][1], "ts": str(t), "features_hash": f"{w.seq:016x}",
                                    "features": {"rel_vol_5m": 1.0, "or_high": 61.98, "atr14": 0.55, "m": m,
                                                 "exit_rule": "time_1555", "would_be_qty": 48, "never_sent": True,
                                                 "counterfactual": {"fill_m": m + 1, "fill": 62.0, "stop": 61.9,
                                                                    "exit_m": m + 1, "exit": 61.9, "reason": "stop",
                                                                    "qty": 48, "pnl_usd": -3.19, "r_multiple": -1.2}},
                                    "entry": 61.99, "stop": 61.94, "target": None,
                                    "reasons": ["stock_in_play", "5m_range_up"], "rules_veto": None})
            if m == 380:  # 15:50 ET: the send
                for st in RULES:
                    w.add("md_send", {"decided": day, "note": None, "run_id": RUN_ID, "session": day,
                                      "strategy": st, "targets": [[s[1], s[2]] for s in held if s[0] == st]})
                    w.event("multi_day_plan", {"strategy": st, "session": day, "decided": day, "targets": []})
                if k % 21 == 20:  # the monthly rebalance: a quarter of the book is sold and bought anew
                    for i in range(max(positions // 4, 1)):
                        st, sym, q, px = held[i]
                        new = (st, f"S{nxt:03d}", q, px + 1)
                        nxt += 1
                        for side, (_, s2, q2, p2) in (("sell", held[i]), ("buy", new)):
                            coid = f"kb1-{st}-{s2}-{day.replace('-', '')}-target-1"
                            act += 1
                            w.add("om_intent", {"coid": coid, "strategy": st, "symbol": s2, "side": side, "qty": q2,
                                                "type": "market", "tif": "day", "purpose": "target", "session": day,
                                                "meta": {}, "created": w.ts})
                            for state in ("submitted", "accepted"):
                                w.add("om_state", {"coid": coid, "state": state, "order_id": f"o{act}",
                                                   "venue": "new"})
                            w.add("fill", {"activity": f"a{act}", "coid": coid, "fees": 0.01, "px": p2, "qty": q2,
                                           "run_id": RUN_ID, "session": day, "side": side, "strategy": st,
                                           "symbol": s2, "ts": w.ts, "purpose": "target"})
                            w.add("om_state", {"coid": coid, "state": "filled", "venue": "filled", "cum": q2})
                            w.event("multi_day_fill", {"strategy": st, "symbol": s2, "side": side, "qty": q2,
                                                       "px": p2, "coid": coid})
                        held[i] = new
                for st in RULES:
                    w.add("md_sent", {"run_id": RUN_ID, "sent": [], "session": day, "skipped": [], "strategy": st})
        w.ts = (d + timedelta(minutes=390, seconds=7)).isoformat()
        w.add("mark", {"pnl_shadow": 0.0, "pnl_venue": 0.0, "run_id": RUN_ID, "session": day, "ts": w.ts,
                       "prices": {f"{st}|{sym}": px for st, sym, _, px in held}})
        w.add("reconcile", {"accepted": [], "diffs": [], "ok": True, "when": "session_end"})
        w.insert("daily_result", {"run_id": RUN_ID, "session": day, "pnl_venue": 0.0, "pnl_shadow": 0.0,
                                  "spy_ret": 0.001, "trades": 0, "config_version": 0})
        w.upsert("ledger_day", {"run_id": RUN_ID, "session": day, "start_equity": 20000.0 + k,
                                "close_equity": 20001.0 + k, "close_equity_venue": 20001.0 + k, "cash": 5000.0,
                                "invested": 15000.0, "realized_venue": 0.0, "pnl_venue": 0.0, "pnl_shadow": 0.0,
                                "dividends": 0.0, "gross": 0.75, "overnight": 0.75, "positions": len(held),
                                "reconciled": {"ok": True, "positions": {}, "cash_diff": 0.0, "tolerance": 10.0,
                                               "ts": w.ts}, "ts": w.ts}, "run_id,session")
        for st, sym, q, px in held:
            w.upsert("ledger_position", {"run_id": RUN_ID, "session": day, "strategy": st, "symbol": sym, "qty": q,
                                         "avg_cost": px, "mark": px, "priced": True, "opened_session": "2026-01-02",
                                         "dividends": 0.0, "stop_px": None, "stop_state": "none_by_design"},
                     "run_id,session,strategy,symbol")
        for st in ("dip_reversal", "orb_stocks_in_play"):
            w.insert("gate_random", {"run_id": RUN_ID, "strategy": st, "session": day, "n_trades": 0, "seed": k,
                                     "config_version": 0, "totals": [0.0] * 1000})
        w.insert("gate_report", {"run_id": RUN_ID, "session": day, "bar_sha": "c" * 64, "fingerprint": "f" * 180,
                                 "overall": "keep collecting",
                                 "report": {"strategies": {s: {"status": "provisional", "kills": [], "criteria": {
                                     c: {"value": 0, "limit": 900, "met": False} for c in (
                                         "trades", "months", "t", "dsr", "random_pct", "beat_spy", "drawdown")}}
                                     for s in ("dip_reversal", "orb_stocks_in_play", "news_continuation",
                                               "spy_late_momentum")}}})
        w.event("session_end", {"day": day, "pnl_reported": 0.0, "trades": 0, "halted": False})
        w.event("shadow_session_end", {"day": day, "orb_stocks_in_play": {"signals": 3, "sized": 3, "pnl_usd": -9.6}})
        for st in RULES:
            w.add("md_decision", {"decided_at": w.ts, "fill_session": day, "held": {s[1]: s[2] for s in held
                                                                                    if s[0] == st},
                                  "run_id": RUN_ID, "session": day, "strategy": st,
                                  "target": {"features": {"buy": [], "keep": [s[1] for s in held if s[0] == st]},
                                             "resize": False, "sell": [], "session": day, "strategy": st,
                                             "weights": [[s[1], 1.0] for s in held if s[0] == st]}})
            w.event("multi_day_decision", {"strategy": st, "session": day, "decides": True, "buy": [], "sell": {},
                                           "held": {s[1]: s[2] for s in held if s[0] == st}})
        w.flush()
    n = w.seq
    w.close()
    return {"entries": n, "sessions": n_sessions, "positions": positions,
            "file_mb": round(path.stat().st_size / 1e6, 1)}


def rss_mb() -> float:
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)  # Linux: kB


def start(path: Path) -> tuple:
    """What a start reads, in the engine's order. Returns the journal, the store and the timings."""
    out: dict = {}
    t0 = time.perf_counter()
    j = J.Journal(path)
    out["open_s"] = time.perf_counter() - t0
    t = time.perf_counter()
    store = store_for(j)
    out["store_s"] = time.perf_counter() - t
    t = time.perf_counter()
    Ledger.fold(j.entries(kinds=LEDGER_KINDS))
    OrderManager(None, j, lambda: None)
    assert OM_KINDS
    for kinds in STARTUP_READS:
        j.entries(kinds=kinds)
    out["folds_s"] = time.perf_counter() - t
    out["start_total_s"] = time.perf_counter() - t0
    return j, store, {k: round(v, 3) for k, v in out.items()}


def store_for(j):
    """The store as the live engine builds it (engine/__main__.py `build`)."""
    live = getattr(J, "live_store", None)  # after 1.24: minute rows are not kept in memory
    return live(j, Remote()) if live is not None else J.JournalStore(j, Remote(), live=True)


def snapshot(store) -> dict | None:
    """What the engine's upkeep does once the market is closed (engine/__main__.py `journal_upkeep`): a snapshot
    when one is due, so the NEXT start folds from it. None before 1.24 (no snapshots)."""
    if not hasattr(store, "snapshot_due"):
        return None
    why = store.snapshot_due(datetime.now(UTC))
    if why is None:
        return {"written": None, "used_at_this_start": store.snapshot_from}
    t = time.perf_counter()
    got = store.snapshot(why)
    return {"written": why, "check_and_write_s": round(time.perf_counter() - t, 2), "rows": got["rows"],
            "used_at_this_start": store.snapshot_from}


def tick(j, reads) -> float:
    t = time.perf_counter()
    for kinds, n in reads:
        for _ in range(n):
            j.entries(kinds=kinds)
    return (time.perf_counter() - t) * 1000


def measure(path: Path, ticks: int = 15) -> dict:
    j, store, out = start(path)
    out["rss_mb"] = rss_mb()
    out["snapshot"] = snapshot(store)
    out["rows_in_memory"] = {t: len(r) for t, r in sorted(store.tables.items())}
    out["tick_mid_ms"] = round(statistics.median(tick(j, TICK_MID) for _ in range(ticks)), 2)
    out["tick_send_ms"] = round(statistics.median(tick(j, TICK_SEND) for _ in range(ticks)), 2)
    out["tick_send_reads"] = sum(n for _, n in TICK_SEND)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, required=True, help="a folder for the synthetic journal")
    ap.add_argument("--sessions", type=int, default=252)
    ap.add_argument("--positions", type=int, default=15)
    ap.add_argument("--ticks", type=int, default=15)
    ap.add_argument("--keep", action="store_true", help="measure the journal that is already in --out")
    ap.add_argument("--measure-only", action="store_true", help=argparse.SUPPRESS)  # the child process
    a = ap.parse_args(argv)
    path = a.out / "journal.sqlite"
    if a.measure_only:
        print(json.dumps(measure(path, a.ticks)))
        return 0
    a.out.mkdir(parents=True, exist_ok=True)
    report: dict = {}
    if not (a.keep and path.exists()):
        t = time.perf_counter()
        report["journal"] = {**build(path, a.sessions, a.positions), "built_in_s": round(time.perf_counter() - t, 1)}
    # twice, each in its own process: the second start is the one a snapshot (if the code writes one) serves
    for name in ("first_start", "next_start"):
        got = subprocess.run([sys.executable, __file__, "--out", str(a.out), "--ticks", str(a.ticks),
                              "--measure-only"], capture_output=True, text=True, check=False)
        if got.returncode:
            print(got.stderr, file=sys.stderr)
            return 1
        report[name] = json.loads(got.stdout.strip().splitlines()[-1])
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
