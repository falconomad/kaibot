"""What would the next send window do? A read-only preview of the live daily path (QA-Ops, follow-up of M9 1.3/1.4).

    python scripts/preview_next_send.py --env .env     # the journal under KAIBOT_DATA_DIR
    python scripts/preview_next_send.py --journal data/live/journal.sqlite --session 2026-11-23

It answers, in plain words and per rule: is the next 15:50-15:57 ET window a decision's own send, a LATE send of a
decision whose window was missed (M9 1.4), a carry-over of buys still short (#363), only sales still pending, or
nothing; and per stock the target, the shares held, the shares working and what would be bought or sold, with every
stock left out and its reason.

Safe to run while the engine is stopped or running:

- the journal is opened read-only (SQLite `mode=ro`); no entry is ever written, and neither the file nor its
  write-ahead log changes. With the engine stopped no file is created or touched at all. With the engine running
  the preview, as any reader, marks its place in SQLite's lock table (the `-shm` file, which holds no entry).
  What the engine would journal on the way (a decision not taken yet) is kept in memory and shown as such;
- no broker and no Supabase: the broker handed to the path raises on any use, no lease is taken, no event is sent;
- the daily store is only read (`StoreLoader`, the engine's own loader and integrity gate).

It is not a second implementation: the plan comes from `LiveDaily._plan`, the engine's own code, on the journal's
records, the ledger folded from the journal and the daily view of the previous close (decision 15: one core).

What it cannot know offline, and says so: the corporate-action guard and the pre-send quote check (both read
Alpaca in the window), entry blocks and reconciliation differences at that minute, fail-safe stops at the venue,
fills or expiries the engine has not journalled yet, the owner's tighter settings of the day, and tomorrow's
decision when the previous close is not in the daily store yet. Sessions come from the NYSE rules
(`ingest/nyse_sessions`), not from the broker's calendar; `--session` overrides.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))  # run from a checkout without installing

from kaibot.common import switches  # noqa: E402  (M10 1.23: KAIBOT_* as declared there)
from kaibot.engine.config import ROOT, load_env_file  # noqa: E402
from kaibot.engine.execution import NY  # noqa: E402
from kaibot.engine.journal import JournalStore, journal_path  # noqa: E402
from kaibot.engine.ledger import LEDGER_KINDS, Ledger  # noqa: E402
from kaibot.ingest import nyse_sessions  # noqa: E402

OFFLINE = (
    "the corporate-action guard (an announced split, spin-off or merger stops a buy; read from Alpaca in the window)",
    "the pre-send quote check (a stale quote or a move of more than 10% since the close withholds a buy)",
    "entry blocks at that minute (kill switch, halt, an unresolved difference between ledger and venue, shorting on)",
    "fills, expiries and cancels the engine has not journalled yet, and fail-safe stops at the venue",
    "the owner's tighter settings of the day; the book is sized here at the run's level from config/profiles.yaml",
)


class ReadOnlyJournal:
    """The engine's journal file, opened read-only, with the journal's read interface (`entries`). `append` never
    reaches the file: the entry is kept in memory (`pending`) and read back after the file's entries, so the path's
    own steps see what they "wrote". The file is read up to the entry that was last when it was opened."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if not Path(self.path).is_file():
            raise FileNotFoundError(f"no journal at {self.path}")
        uri = "file:" + quote(str(Path(self.path).resolve()))
        # A write-ahead log next to the file: an engine has it open (or died with entries in it). Read through it,
        # read-only. None: no engine has the journal open, and a read-only connection would leave two empty side
        # files behind, so the file is read as it is (`immutable`), which creates nothing.
        self.mode = "ro" if Path(self.path + "-wal").exists() else "immutable"
        extra = "" if self.mode == "ro" else "&immutable=1"
        self.db = sqlite3.connect(uri + "?mode=ro" + extra, uri=True, check_same_thread=False)
        self.last = self._last()
        self.pending: list[tuple[int, str, str, dict]] = []

    def _last(self) -> int:
        return int(self.db.execute("select coalesce(max(seq), 0) from entry").fetchone()[0])

    def entries(self, after: int = 0, kinds: tuple[str, ...] | None = None, limit: int | None = None) -> list:
        q, args = "select seq, ts, kind, body from entry where seq > ? and seq <= ?", [after, self.last]
        if kinds:
            q += f" and kind in ({','.join('?' * len(kinds))})"
            args += list(kinds)
        q += " order by seq"
        rows = [(s, ts, k, json.loads(b)) for s, ts, k, b in self.db.execute(q, args).fetchall()]
        rows += [e for e in self.pending if e[0] > after and (not kinds or e[2] in kinds)]
        return rows[:int(limit)] if limit else rows

    def append(self, kind: str, body: dict) -> int:
        seq = self.last + len(self.pending) + 1
        self.pending.append((seq, datetime.now(UTC).isoformat(), kind, json.loads(json.dumps(body, default=str))))
        return seq

    def last_seq(self) -> int:
        return self.last + len(self.pending)

    def close(self) -> None:
        self.db.close()


class NoBroker:
    """The preview's broker: any use raises. Nothing in a preview may reach the venue."""

    def __getattr__(self, name: str):
        raise RuntimeError(f"the preview never talks to the broker (asked for `{name}`)")


class JournalAccount:
    """`live_daily.Account` from the journal alone: the run's ledger folded from its entries, as the engine's is."""

    def __init__(self, journal: ReadOnlyJournal, run_id: int | None) -> None:
        self.journal, self.rid = journal, run_id
        self.ledger = Ledger.fold(journal.entries(kinds=LEDGER_KINDS))

    def _book(self):
        return self.ledger.book(self.rid) if self.rid is not None else None

    def run_id(self) -> int | None:
        return self.rid

    def equity(self) -> float:
        b = self._book()
        if b is None:
            return 0.0
        return float(b.closes[-1].close_equity_venue) if b.closes else float(b.amount)

    def cash(self) -> float:
        b = self._book()
        return float(b.cash) if b is not None else 0.0

    def held(self, strategy: str) -> dict[str, tuple[float, float | None]]:
        b = self._book()
        if b is None:
            return {}
        return {sym: (float(s.qty), s.mark) for (st, sym), s in b.sleeves.items() if st == strategy and s.qty > 1e-9}

    def book_fill(self, body: dict) -> None:  # never called: the preview books nothing
        raise RuntimeError("the preview books no fill")

    def entry_block(self) -> str | None:
        return None  # not known offline (OFFLINE)

    def allows(self, symbol: str, side: str, purpose: str = "target", qty: float | None = None) -> str | None:
        return None

    def book_ended(self) -> bool:
        return self.rid is not None and any(b.get("run_id") == self.rid for *_, b in
                                            self.journal.entries(kinds=("md_book_end",)))

    def adopted(self, strategy: str) -> dict[str, str]:
        b = self._book()
        if b is None:
            return {}
        return {sym: "spin_off_adopted" for (st, sym), s in b.sleeves.items()
                if st == strategy and s.qty > 1e-9 and str(s.origin or "").startswith("spin_off:")}

    def splits(self, day: date) -> dict[str, float]:
        out: dict[str, float] = {}
        for *_, b in self.journal.entries(kinds=("split",)):
            if str(b.get("ex_date"))[:10] == str(day) and b.get("run_id") == self.rid and b.get("ratio"):
                out[str(b["symbol"])] = float(b["ratio"])
        return out


def next_session(d: date) -> date:
    d += timedelta(days=1)
    while not nyse_sessions.is_session(d):
        d += timedelta(days=1)
    return d


def prev_session(d: date) -> date:
    d -= timedelta(days=1)
    while not nyse_sessions.is_session(d):
        d -= timedelta(days=1)
    return d


def window_session(now: datetime, send_until) -> date:
    """The session of the next send window: today while its window has not ended, else the next session."""
    et = now.astimezone(NY)
    if nyse_sessions.is_session(et.date()) and et.time() < send_until(et.date()):
        return et.date()
    return next_session(et.date())


def _working(om, rule: str, symbol: str) -> float:
    from kaibot.engine.targets import working_shares

    return float(working_shares(om, (rule, symbol))) if om is not None else 0.0


def _rule_preview(live, r, session: date, rid: int, journal: ReadOnlyJournal) -> dict:
    """One rule's next window, from the engine's own `_plan`. Never writes; `journal.pending` holds what the engine
    would have journalled on the way (a decision record not taken yet)."""
    name, prev = r.name, live.prev_session(session)
    out: dict = {"rule": name, "session": str(session), "after_close_of": str(prev), "kind": "nothing", "lines": [],
                 "left_out": {}, "notes": []}
    retired = live.retirement(name)
    if retired is not None:
        out["kind"] = "retired"
        out["notes"].append(f"the rule is retired (since {retired.get('session')}): it sends nothing")
        return out
    om = live._manager()  # as `send` does first: the orders folded from the journal (the carry-over reads them)
    before = len(journal.pending)
    if live._decision_for(name, session) is None:
        live.after_close(prev)  # as the engine does after the close, or late at its first tick (M9 1.3)
    took = [b for _, _, k, b in journal.pending[before:] if k == "md_decision" and b.get("strategy") == name]
    planned = live._plan(r, session, rid)
    dec = live._decision_for(name, session)
    if took:
        out["notes"].append(f"the journal has no decision record for {session} yet: the engine takes it after the "
                            f"close of {prev} (or late, at its first tick); it is previewed here, not written")
    if dec is None:
        why = live._waiting.get(name) or (live._missed.get(name) or ("", ""))[1]
        out["kind"] = "no decision"
        out["notes"].append(f"no decision for {session} can be taken now"
                            + (f": {why}" if why else "") + ". Run the preview again once the daily store holds "
                            f"{prev} (./scripts/fetch_daily.sh after 20:15 ET)")
    latest = live._latest(name, session, rid)
    if latest is not None:
        sent = live._sent_plan(name, rid, latest)
        out["latest_decision"] = {"session": latest["session"], "fill_session": latest["fill_session"],
                                  "sent_on": sent["session"] if sent else None,
                                  "late_of": (sent or {}).get("late_of")}
    held = {s: float(q) for s, (q, _) in live.account.held(name).items()}
    for sym, shares, purpose, why in planned.get("targets") or []:
        have, work = held.get(sym, 0.0), _working(om, name, sym)
        diff = int(shares) - have - work
        out["lines"].append({"symbol": sym, "purpose": purpose, "target": int(shares), "held": have,
                             "working": work, "buy": max(diff, 0.0), "sell": max(-diff, 0.0), "why": why})
    carry, late = planned.get("carry") or {}, planned.get("late") or {}
    if planned.get("late_of"):
        out["kind"] = "late send"
        out["late_of"], out["decided"] = planned["late_of"], planned.get("decided")
        out["left_out"] = dict(late.get("skipped") or {})
        out["due"] = [list(x) for x in late.get("due") or []]
        out["risk_off"] = bool(late.get("risk_off"))
    elif carry:
        out["kind"] = "carry-over"
        out["carry_of"] = carry.get("of")
        out["left_out"] = dict(carry.get("skipped") or {})
        out["risk_off"] = bool(carry.get("risk_off"))
        if carry.get("unread"):
            out["notes"].append(f"no carry-over can be worked out now: {carry['unread']}")
        if carry.get("error"):
            out["notes"].append(f"the carry-over failed: {carry['error']}")
    elif dec is not None and dec.get("target") is not None and "equity" in planned:
        out["kind"] = "decision's own send"
        out["decided"] = planned.get("decided")
    elif planned.get("targets"):
        out["kind"] = "pending sales only"
    for x in planned.get("skipped") or []:  # the builder's own skips (cash, caps), as the send record keeps them
        sym, why = getattr(x, "symbol", None), " ".join(filter(None, (getattr(x, "reason", ""),
                                                                      getattr(x, "detail", ""))))
        if sym is not None:
            out["left_out"].setdefault(str(sym), f"the builder: {why}" if why else "left out by the builder")
    if "equity" in planned:
        out["equity"], out["cash"] = planned.get("equity"), planned.get("cash")
    return out


def preview(journal_file: str | Path, daily_root: str | Path, *, now: datetime | None = None,
            session: date | None = None, load: Callable | None = None, rules=None,
            universe_file: str | Path | None = None) -> dict:
    """The next send window of every enabled multi-day rule, as a dict (`render` writes it in plain words).
    `load`, `rules`: the tests' daily view and rules; default: the daily store under `daily_root` and the rules
    enabled in config/profiles.yaml at the run's level."""
    from kaibot.engine import live_daily as ld

    now = now or datetime.now(UTC)
    journal = ReadOnlyJournal(journal_file)
    try:
        store = JournalStore(journal)  # the journal's own tables (run, halt), read only: no Supabase behind it
        run = store.active_run()
        ledger_runs = sorted(Ledger.fold(journal.entries(kinds=("run",))).books)
        rid = int(run["id"]) if run else (ledger_runs[-1] if ledger_runs else None)
        level = (run or {}).get("level") or "balanced"
        flag = bool(switches.get("KAIBOT_MULTIDAY"))
        out: dict = {"journal": journal.path, "journal_mode": journal.mode, "last_entry": journal.last,
                     "multiday_flag": flag,
                     "now": now.isoformat(), "run_id": rid, "level": level, "run_status": (run or {}).get("status"),
                     "rules": [], "events": [], "offline": list(OFFLINE)}
        if rid is None:
            out["note"] = "the journal has no paper run: nothing would be sent"
            return out
        if rules is None:
            from kaibot.risk.profiles import enabled_multi_day, load_profiles

            rules = ld.rules_for(enabled_multi_day(), load_profiles()[level], level)
        if load is None:
            load = ld.StoreLoader(Path(daily_root), Path(universe_file or ROOT / "config" / "universe_history.yaml"))
        account = JournalAccount(journal, rid)
        live = ld.LiveDaily(NoBroker(), journal, lambda: now, list(rules), load, account,
                            next_session=next_session, prev_session=prev_session,
                            event=lambda kind, body: out["events"].append([kind, body]), store=store,
                            session_close=lambda d: nyse_sessions.EARLY_CLOSE if nyse_sessions.early_close(d) else None)
        session = session or window_session(now, live.send_until)
        out.update(session=str(session), window=[str(live.send_from(session)), str(live.send_until(session))],
                   equity=account.equity(), cash=account.cash(), book_ended=account.book_ended())
        if account.book_ended():
            out["note"] = "the multi-day book reached its run end in this run: nothing would be sent"
            return out
        if not rules:
            out["note"] = "no multi-day rule is enabled (config/profiles.yaml): nothing would be sent"
        for r in live.rules:
            try:
                out["rules"].append(_rule_preview(live, r, session, rid, journal))
            except Exception as e:  # a preview never raises half-way: say what failed for this rule
                out["rules"].append({"rule": r.name, "session": str(session), "kind": "unknown", "lines": [],
                                     "left_out": {}, "notes": [f"the preview failed: {type(e).__name__}: {e}"]})
        out["not_written"] = [[k, b] for _, _, k, b in journal.pending]
        return out
    finally:
        journal.close()


def _n(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:g}"


def render(p: dict) -> str:
    """The preview in plain words."""
    ro = ("read-only, next to a write-ahead log: an engine has it open" if p["journal_mode"] == "ro"
          else "read-only; no engine has it open")
    lines = ["Preview of the next send window. Nothing is written and nothing is sent.",
             f"Journal: {p['journal']} ({ro}; entries up to number {p['last_entry']}). Now: {p['now']}."]
    if p.get("run_id") is None or "session" not in p:
        return "\n".join([*lines, p.get("note", "")])
    lines.append(f"Run {p['run_id']} ({p['run_status'] or 'status unknown'}, level {p['level']}): equity at the last "
                 f"close ${p['equity']:,.2f}, cash ${p['cash']:,.2f}.")
    if not p.get("multiday_flag"):
        lines.append("KAIBOT_MULTIDAY is not on in this environment: an engine started with it has no daily path and "
                     "sends none of this. The preview shows what it would do with the flag on.")
    lines.append(f"Next window: session {p['session']}, {p['window'][0][:5]} to {p['window'][1][:5]} ET.")
    if p.get("note"):
        lines.append(p["note"])
    for r in p["rules"]:
        lines.append("")
        kind = r["kind"]
        head = {"late send": f"LATE SEND. The decision taken after {r.get('decided')} was never sent (its window on "
                             f"{r.get('late_of')} was missed). It would be sized now, at the previous close, and "
                             "sent in this window, once.",
                "carry-over": f"CARRY-OVER of the plan sent on {r.get('carry_of')}: buys still short of their "
                              "targets are sent again at the plan's own shares.",
                "decision's own send": f"THE DECISION'S OWN SEND (decided after the close of {r.get('decided')}).",
                "pending sales only": "ONLY SALES STILL PENDING from an earlier session.",
                "no decision": "NO DECISION for this session: nothing new would be sent.",
                "retired": "RETIRED.", "unknown": "NOT KNOWN.",
                "nothing": "NOTHING. No order would go out for this rule."}[kind]
        lines.append(f"{r['rule']}: {head}")
        ld_ = r.get("latest_decision")
        if ld_:
            sent = (f"sent on {ld_['sent_on']}" + (f" (late, for {ld_['late_of']})" if ld_.get("late_of") else "")
                    if ld_["sent_on"] else "NOT sent: no send record with the builder's plan")
            lines.append(f"  Latest decision of this run: after the close of {ld_['session']}, for "
                         f"{ld_['fill_session']}; {sent}.")
        if r.get("risk_off"):
            lines.append("  The market filter is off at the previous close (SPY below its 200-day average): no buy.")
        if r["lines"]:
            lines.append(f"  {'stock':<8}{'what':<8}{'target':>8}{'held':>8}{'working':>9}{'to buy':>8}{'to sell':>9}")
            for x in r["lines"]:
                lines.append(f"  {x['symbol']:<8}{x['purpose']:<8}{x['target']:>8}{_n(x['held']):>8}"
                             f"{_n(x['working']):>9}{_n(x['buy']):>8}{_n(x['sell']):>9}")
            buys = sum(1 for x in r["lines"] if x["buy"] > 0)
            sells = sum(1 for x in r["lines"] if x["sell"] > 0)
            lines.append(f"  {buys} to buy, {sells} to sell. An order is the target less the shares held less the "
                         "shares working: never above the target.")
        elif kind in ("carry-over", "late send"):
            lines.append("  No order would go out in this window.")
        if r["left_out"]:
            lines.append("  Left out this session:")
            lines += [f"    {s}: {why}" for s, why in sorted(r["left_out"].items())]
        if r.get("due"):
            lines.append("  Still due after this window (tried again by the carry-over): "
                         + ", ".join(f"{s} {q}" for s, q, _ in r["due"]))
        lines += [f"  Note: {n}" for n in r["notes"]]
    if p.get("not_written"):
        lines.append("")
        lines.append(f"{len(p['not_written'])} record(s) the engine would journal first are previewed in memory only "
                     "(" + ", ".join(sorted({k for k, _ in p['not_written']})) + ").")
    lines.append("")
    lines.append("Not known offline, so the real window can differ (no order is ever above its target):")
    lines += [f"  - {x}" for x in p["offline"]]
    lines.append("Sessions are worked out from the NYSE rules, not read from the broker.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="scripts/preview_next_send.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--env", help="the engine's .env file (for KAIBOT_DATA_DIR and KAIBOT_DAILY_DATA); no key is used")
    ap.add_argument("--journal", help="the journal file (default: <KAIBOT_DATA_DIR>/journal.sqlite)")
    ap.add_argument("--daily-data", help="the folder holding daily/ (default: KAIBOT_DAILY_DATA or <repo>/data)")
    ap.add_argument("--session", help="the session to preview, YYYY-MM-DD (default: the next send window's)")
    ap.add_argument("--now", help="pretend it is this time (ISO, with offset); default: now")
    ap.add_argument("--json", action="store_true", help="print the preview as JSON")
    a = ap.parse_args(argv)
    load_env_file(a.env or switches.text("KAIBOT_ENV_FILE"))
    jfile = Path(a.journal) if a.journal else journal_path(switches.get("KAIBOT_DATA_DIR") or ROOT / "data" / "live")
    root = Path(a.daily_data or switches.get("KAIBOT_DAILY_DATA") or ROOT / "data")
    now = datetime.fromisoformat(a.now) if a.now else None
    if now is not None and now.tzinfo is None:
        ap.error("--now needs an offset, e.g. 2026-11-23T15:00:00-05:00")
    try:
        p = preview(jfile, root, now=now, session=date.fromisoformat(a.session) if a.session else None)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(json.dumps(p, indent=1, default=str) if a.json else render(p))
    return 0


if __name__ == "__main__":
    sys.exit(main())
