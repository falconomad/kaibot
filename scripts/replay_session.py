"""Replay a recorded session's journal through the code checked out here and compare (M12 ticket 3.4, #466).

The engine is journal-first and deterministic, so a session it recorded can be run again through today's code and
the two compared: the decisions (which session decided for which, late or not, which names), the sends (the targets
and shares of each window, and a late send's `late_of`), the orders (every order intent, its side, size and last
state) and the ledger (shares per symbol and the cash the fills moved, folded by today's `Ledger`). A difference
fails the replay unless an entry of the allowlist acknowledges it, with a reason (normally: the change that made it,
in the same pull request).

    python scripts/replay_session.py export JOURNAL.sqlite --out SESSION.jsonl [--sessions N | --all]
    python scripts/replay_session.py replay SESSION.jsonl|JOURNAL.sqlite ... [--allowlist FILE] [--report OUT.md]
    python scripts/replay_session.py record "late start" --out tests/replay/fixtures/x.jsonl   # the simulator

`export` needs only the Python standard library and opens the journal read-only: it writes nothing next to it, never
connects anywhere, and keeps only the entries and fields the comparison needs (no Supabase row but the published
ledger positions, no broker order id, execution ids hashed). It refuses to write a file in which anything looks
like a key. docs/ops/replay.md has the exact command for the Mac.

What "replay" can re-run depends on what the file says its inputs were (its first line):

    inputs.kind = "dst"   a session of the deterministic simulation (tests/dst): the scenario (seed, plan, outages,
                          faults) is in the file, so the whole session runs again through the engine here
                          (`Engine.tick`, `LiveDaily`, `OrderManager`, `Journal`, `SimBroker` behind
                          `FaultyBroker`, `SimClock`), and decisions, sends, orders and ledger are compared
    inputs.kind = "live"  a session of the engine on the Mac: its market data is not in the journal, so decisions and
                          orders cannot be derived again; the replay folds the recorded ledger entries with today's
                          `Ledger` and compares that with the positions the recorded engine published (and, for a
                          journal file, opens it with today's `Journal` and verifies every checksum). This checks that
                          today's code reads yesterday's book the same way, the upgrade path

Exit codes: 0 no unacknowledged difference, 1 a difference, 2 the file cannot be read or replayed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
FORMAT = "kaibot-session/1"
DEFAULT_ALLOWLIST = ROOT / "tests" / "replay" / "allowlist.toml"

# ---------------------------------------------------------------- what is kept: kinds and their fields
_ANY = None  # every field of the body
KEEP: dict[str, tuple[str, ...] | None] = {
    "run": _ANY,  # the ledger opens the book from it (amount, prior cash and days, the broker's cash at start)
    "md_decision": ("run_id", "strategy", "session", "fill_session", "late", "note", "decided_at", "target"),
    "md_send": ("run_id", "strategy", "session", "decided", "late_of", "targets", "skipped"),
    "md_sent": ("run_id", "strategy", "session", "sent", "skipped"),
    "md_unfilled": _ANY, "md_withheld": _ANY, "md_cancel": _ANY, "md_book_end": _ANY, "md_retired": _ANY,
    "om_intent": ("coid", "strategy", "symbol", "side", "qty", "type", "purpose", "session", "tif", "order_class"),
    "om_state": ("coid", "state", "reason", "cum", "qty"),
    "fill": ("run_id", "coid", "activity", "strategy", "symbol", "side", "qty", "px", "fees", "purpose", "session",
             "ts"),
    # the other ledger entries (kaibot.engine.ledger.LEDGER_KINDS): book entries without any identifier of the account
    "position": _ANY, "dividend": _ANY, "split": _ANY, "spinoff": _ANY, "symbol_change": _ANY, "mark": _ANY,
    "fee": _ANY,
    "reconcile": ("when", "ok"),
}
ROW_TABLES = {"ledger_position": ("run_id", "session", "strategy", "symbol", "qty", "avg_cost")}
SECRETISH = re.compile(r"\b(PK|AK|CK)[A-Z0-9]{16,}\b|sb_secret_|sk-ant-|eyJhbGciOi|-----BEGIN")


def _hash(v) -> str:
    return "h" + hashlib.blake2b(str(v).encode(), digest_size=8).hexdigest()


def sanitise(kind: str, body: dict) -> dict | None:
    """The entry as the export keeps it, or None: the kind is not kept."""
    if kind == "row":
        cols = ROW_TABLES.get(str(body.get("table")))
        if cols is None or not isinstance(body.get("row"), dict):
            return None
        return {"table": body["table"], "op": body.get("op"), "row": {k: body["row"].get(k) for k in cols}}
    if kind not in KEEP:
        return None
    cols = KEEP[kind]
    out = dict(body) if cols is None else {k: body[k] for k in cols if k in body}
    for k in ("order_id", "ids", "account", "account_id", "broker_order_id", "retirement_id"):
        out.pop(k, None)  # retirement_id: the single-use key a re-enable accepts (#211), not needed to compare
    if kind == "fill" and "activity" in out:
        out["activity"] = _hash(out["activity"])
    if kind in ("dividend", "split", "spinoff", "symbol_change", "fee") and out.get("ref"):
        out["ref"] = _hash(out["ref"])  # the broker's activity id ("alpaca:<id>"): only its identity is needed
    if kind == "md_decision" and isinstance(out.get("target"), dict):
        t = out["target"]
        out["target"] = {k: t.get(k) for k in ("weights", "sell", "resize", "session", "strategy") if k in t}
    return out


# ---------------------------------------------------------------- reading a journal file (standard library only)
def open_readonly(path: str | Path) -> sqlite3.Connection:
    """The journal opened as scripts/preview_next_send.py opens it: read-only, through a write-ahead log an engine
    has open, otherwise `immutable` (a read-only connection would leave two empty side files next to it)."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"no journal at {p}")
    extra = "" if Path(str(p) + "-wal").exists() else "&immutable=1"
    return sqlite3.connect("file:" + quote(str(p.resolve())) + "?mode=ro" + extra, uri=True)


def journal_entries(path: str | Path) -> tuple[dict, list[tuple[int, str, str, dict]]]:
    db = open_readonly(path)
    try:
        meta = dict(db.execute("select k, v from meta").fetchall()) if _has(db, "meta") else {}
        rows = [(int(s), ts, k, json.loads(b)) for s, ts, k, b in
                db.execute("select seq, ts, kind, body from entry order by seq")]
    finally:
        db.close()
    return meta, rows


def _has(db: sqlite3.Connection, table: str) -> bool:
    return db.execute("select 1 from sqlite_master where type='table' and name=?", (table,)).fetchone() is not None


def _code_sha(rows: Iterable) -> str | None:
    sha = None
    for _seq, _ts, kind, b in rows:
        if kind == "row" and b.get("table") == "event" and (b.get("row") or {}).get("type") == "engine_start":
            sha = ((b["row"].get("payload") or {}).get("code_sha")) or sha
    return sha


def _sessions(rows: Iterable) -> list[str]:
    seen = set()
    for _seq, _ts, kind, b in rows:
        if kind in ("md_send", "md_decision") and b.get("session"):
            seen.add(str(b["session"]))
    return sorted(seen)


def export(path: str | Path, sessions: int | None = 1, inputs: dict | None = None) -> list[dict]:
    """The sanitised lines of a session file: a header, then one line per kept entry. `sessions`: the last N sessions
    the journal sent or decided for (their entries from the start of the day before the first of them, New York time
    is not needed for that margin), plus every `run` entry; None: everything."""
    meta, rows = journal_entries(path)
    since = None
    days = _sessions(rows)
    if sessions is not None and days:
        first = days[-min(sessions, len(days))]
        since = (datetime.fromisoformat(first) - timedelta(days=1)).date().isoformat()
    now = datetime.now(timezone.utc)  # noqa: UP017 (the Mac's python3 may be older than 3.11)
    header = {"format": FORMAT, "journal": _hash(meta.get("journal_id", "")), "created": meta.get("created"),
              "code_sha": _code_sha(rows), "exported_at": now.isoformat(timespec="seconds"),
              "sessions": days if since is None else [d for d in days if d >= first],
              "inputs": inputs or {"kind": "live"}}
    out = [header]
    for seq, ts, kind, body in rows:
        if since is not None and kind != "run" and ts[:10] < since:
            continue
        kept = sanitise(kind, body)
        if kept is not None:
            out.append({"seq": seq, "ts": ts, "kind": kind, "body": kept})
    text = "\n".join(json.dumps(x, sort_keys=True) for x in out)
    hit = SECRETISH.search(text)
    if hit:
        raise ValueError(f"refusing to export: something in the journal looks like a key ({hit[0][:6]}...)")
    return out


def write_lines(lines: list[dict], out: str | Path) -> None:
    Path(out).write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in lines))


def read_session(path: str | Path) -> tuple[dict, list[tuple[int, str, str, dict]]]:
    """A session file (.jsonl) or a journal file (anything else: exported on the fly, all of it)."""
    p = Path(path)
    if p.suffix != ".jsonl":
        lines = export(p, sessions=None)
    else:
        lines = [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
    if not lines or lines[0].get("format") != FORMAT:
        raise ValueError(f"{p}: not a {FORMAT} file")
    return lines[0], [(e["seq"], e["ts"], e["kind"], e["body"]) for e in lines[1:]]


# ---------------------------------------------------------------- the facts compared
RUN_PREFIX = re.compile(r"^kb\d+-")


def _coid(c: str) -> str:
    return RUN_PREFIX.sub("kb-", str(c))


def facts(entries: list[tuple[int, str, str, dict]], ledger: bool = True) -> dict[str, object]:
    """What the session did, keyed so that the same thing done twice compares equal: `decision <fill session>`,
    `send <session>`, `order <client order id without the run>`, `ledger shares <symbol>`, `ledger cash`."""
    out: dict[str, object] = {}
    for _seq, _ts, kind, b in entries:
        if kind == "md_decision":
            t = b.get("target") or {}
            out[f"decision {b.get('fill_session')}"] = {
                "session": b.get("session"), "late": bool(b.get("late")), "note": b.get("note"),
                "names": sorted(str(w[0]) for w in t.get("weights") or [])}
        elif kind == "md_send":
            out[f"send {b.get('session')}"] = {
                "late_of": b.get("late_of"), "targets": sorted([str(t[0]), int(t[1]), str(t[2])]
                                                                for t in b.get("targets") or [])}
        elif kind == "om_intent":
            out[f"order {_coid(b['coid'])}"] = {"side": b.get("side"), "qty": float(b.get("qty") or 0),
                                                 "type": b.get("type"), "purpose": b.get("purpose"), "state": "intent",
                                                 "filled": 0.0}
        elif kind == "om_state":
            o = out.get(f"order {_coid(b['coid'])}")
            if isinstance(o, dict):
                o["state"] = str(b.get("state"))
        elif kind == "fill":
            o = out.get(f"order {_coid(b.get('coid', ''))}")
            if isinstance(o, dict):
                o["filled"] = round(float(o["filled"]) + float(b.get("qty") or 0), 6)
    if ledger:
        out.update(ledger_facts(entries))
    return out


def ledger_facts(entries: list[tuple[int, str, str, dict]]) -> dict[str, object]:
    """The book of the last run, folded by today's `kaibot.engine.ledger.Ledger` (the code on this checkout)."""
    from kaibot.engine.ledger import LEDGER_KINDS, Ledger

    led = Ledger.fold((s, ts, k, b) for s, ts, k, b in entries if k in LEDGER_KINDS)
    rid = None
    for _s, _ts, k, b in entries:
        if k == "run" and b.get("run_id") is not None:
            rid = b["run_id"]
    book = led.book(rid) if rid is not None else None
    if book is None:
        return {}
    out: dict[str, object] = {f"ledger shares {s}": round(q, 6) for s, q in sorted(book.qty_by_symbol().items())
                              if abs(q) > 1e-9}
    out["ledger cash"] = round(book.cash - book.amount, 2)
    return out


def published_positions(entries: list[tuple[int, str, str, dict]]) -> dict[str, object]:
    """The positions the recorded engine itself published (its last `ledger_position` rows of the last run)."""
    rid = None
    for _s, _ts, k, b in entries:
        if k == "run" and b.get("run_id") is not None:
            rid = b["run_id"]
    last: dict[tuple, dict] = {}
    for _s, _ts, k, b in entries:
        r = b.get("row") or {}
        if k == "row" and b.get("table") == "ledger_position" and r.get("run_id") == rid:
            last[(r.get("strategy"), r.get("symbol"))] = r
    if not last:
        return {}
    day = max(str(r.get("session")) for r in last.values())
    qty: dict[str, float] = {}
    for (_st, sym), r in last.items():
        if str(r.get("session")) == day:
            qty[str(sym)] = qty.get(str(sym), 0.0) + float(r.get("qty") or 0.0)
    return {f"ledger shares {s}": round(q, 6) for s, q in sorted(qty.items()) if abs(q) > 1e-9}


# ---------------------------------------------------------------- the replay
@dataclass
class Outcome:
    name: str
    header: dict
    diffs: list[tuple[str, object, object]] = field(default_factory=list)  # key, recorded, replayed
    acked: list[tuple[str, object, object, str]] = field(default_factory=list)
    stale: list[dict] = field(default_factory=list)  # entries for this file whose difference no longer happens
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.diffs and not self.stale


def compare(recorded: dict, replayed: dict) -> list[tuple[str, object, object]]:
    keys = sorted(set(recorded) | set(replayed))
    return [(k, recorded.get(k), replayed.get(k)) for k in keys if recorded.get(k) != replayed.get(k)]


def replay(path: str | Path, acks: list[dict] | None = None) -> Outcome:
    header, entries = read_session(path)
    name = Path(path).stem
    out = Outcome(name, header)
    kind = (header.get("inputs") or {}).get("kind")
    if kind == "dst":
        recorded = facts(entries)
        replayed = facts(run_dst(header["inputs"]))
        out.notes.append(f"re-ran the simulated session (seed {header['inputs']['scenario']['seed']}) through this "
                         f"checkout; recorded by {header.get('code_sha') or 'unknown code'}")
    elif kind == "live":
        if Path(path).suffix != ".jsonl":
            out.notes.append(f"today's Journal opened the file and verified {verify_journal(path)} entries")
        recorded = published_positions(entries)
        if not recorded:
            out.notes.append("no published ledger positions in the file: only the fold is reported")
        replayed = {k: v for k, v in ledger_facts(entries).items() if k.startswith("ledger shares")}
        if not recorded:
            recorded = dict(replayed)
        out.notes.append("a live session: its market data is not in the journal, so decisions and orders are listed, "
                         f"not derived again ({sum(k.startswith('decision') for k in facts(entries, False))} "
                         "decisions); the ledger is folded by today's code and compared with what was published")
    else:
        raise ValueError(f"{path}: unknown inputs {header.get('inputs')!r}")
    used: set[int] = set()
    for key, a, b in compare(recorded, replayed):
        hit = acknowledged(acks or [], name, key, a, b)
        if hit is None:
            out.diffs.append((key, a, b))
        else:
            used.add(id(hit))
            out.acked.append((key, a, b, str(hit["reason"]).strip()))
    # an acknowledged difference that no longer happens fails too: the change it acknowledges was reverted (or the
    # file was recorded again and the entry is to be removed)
    out.stale = [a for a in acks or [] if a["journal"] == name and id(a) not in used]
    return out


def verify_journal(path: str | Path) -> int:
    """Open a copy with today's `Journal` (it checks the durability settings and every checksum on open)."""
    import shutil

    from kaibot.engine.journal import Journal

    with tempfile.TemporaryDirectory(prefix="kb-replay-") as d:
        dst = Path(d) / "journal.sqlite"
        src = open_readonly(path)
        try:
            with sqlite3.connect(dst) as copy:
                src.backup(copy)
        finally:
            src.close()
        j = Journal(dst)
        try:
            return j.verify()
        finally:
            j.close()
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- the simulator's sessions (tests/dst)
def _dst_path() -> None:
    for p in (ROOT / "tests" / "dst", ROOT / "tests"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))


def _compat() -> None:
    """Names the simulator imports that an older engine (a pre-M9 checkout, to record a fixture) does not have."""
    from kaibot.engine import failsafe, journal

    if not hasattr(journal, "JournalCorrupt"):
        journal.JournalCorrupt = type("JournalCorrupt", (RuntimeError,), {})
    if not hasattr(failsafe, "CANCELS_FILE"):
        failsafe.CANCELS_FILE = "watchdog_cancels.jsonl"


def scenario_to_dict(s) -> dict:
    from dataclasses import asdict

    def t(x):
        return None if x is None else x.isoformat()

    return {"seed": s.seed, "plan": {k: None if v is None else list(v) for k, v in s.plan.items()},
            "ready": {k: t(v) for k, v in s.ready.items()},
            "outages": [{**asdict(o), "arm": t(o.arm)} for o in s.outages], "flatten": t(s.flatten),
            "watchdog": s.watchdog, "profile": asdict(s.profile), "sparse": s.sparse,
            "forced": [asdict(f) for f in s.forced], "heal": s.heal}


def scenario_from_dict(d: dict):
    _dst_path()
    _compat()
    from world import Outage, Scenario

    from kaibot.broker.faulty import FaultProfile, Forced

    def t(x):
        return None if x is None else datetime.fromisoformat(x)

    return Scenario(int(d["seed"]), {k: None if v is None else tuple(v) for k, v in d["plan"].items()},
                    {k: t(v) for k, v in d["ready"].items()},
                    tuple(Outage(**{**o, "arm": t(o["arm"])}) for o in d.get("outages", [])), t(d.get("flatten")),
                    watchdog=bool(d.get("watchdog")), profile=FaultProfile(**d.get("profile", {})),
                    sparse=bool(d.get("sparse", True)), forced=tuple(Forced(**f) for f in d.get("forced", [])),
                    heal=d.get("heal") or "2026-08-05")


def run_dst(inputs: dict, root: Path | None = None) -> list[tuple[int, str, str, dict]]:
    """Run the simulated session of `inputs` through this checkout; its journal's entries, sanitised as an export."""
    _dst_path()
    _compat()
    from world import run_scenario

    with tempfile.TemporaryDirectory(prefix="kb-replay-") as d:
        where = Path(root) if root is not None else Path(d)
        run_scenario(scenario_from_dict(inputs["scenario"]), where)
        _meta, rows = journal_entries(where / "journal.sqlite")
    return [(s, ts, k, kept) for s, ts, k, b in rows if (kept := sanitise(k, b)) is not None]


def record(name: str, out: str | Path) -> list[dict]:
    """Record a fixture: run the named hand-written scenario (tests/dst/scenarios.FINDINGS, or `sample`: one buy, one
    sale, no fault) through this checkout and export its journal with the scenario as its inputs."""
    _dst_path()
    _compat()
    import world
    from scenarios import FINDINGS

    from kaibot.broker.faulty import NO_FAULTS, Forced

    on_time = {d: world.et(d, "16:10") for d in world.EVENINGS}
    own = {
        # one buy, one sale, no fault
        "sample": world.Scenario(7, {world.D1: ("B", "C"), world.D2: ("C",), world.D3: None}, on_time,
                                 profile=NO_FAULTS),
        # the buy is decided on D1's evening (the run exists by then, so an engine before M9 1.3 makes it too); the
        # owner flattens on D3 and half of the sale fills: M9 1.8 (through the order manager) and 1.2 (pending until
        # flat) apart from the late start
        "flatten partial fill on D3": world.Scenario(
            0, {world.D1: None, world.D2: ("B",), world.D3: None}, on_time, profile=NO_FAULTS,
            flatten=world.et(world.D3, "11:00"), forced=(Forced("partial_fill", "|B|sell", 0.5),), heal="2026-08-06"),
    }
    sc = own[name] if name in own else FINDINGS[name][0]
    with tempfile.TemporaryDirectory(prefix="kb-record-") as d:
        world.run_scenario(sc, Path(d))
        lines = export(Path(d) / "journal.sqlite", sessions=None,
                       inputs={"kind": "dst", "scenario": scenario_to_dict(sc), "name": name})
    lines[0]["exported_at"] = None  # a fixture is the same file whenever it is recorded again
    lines[0]["code_sha"] = _engine_sha() or lines[0]["code_sha"]
    write_lines(lines, out)
    return lines


def _engine_sha() -> str | None:
    """The commit of the engine that is imported (a fixture recorded on an older checkout names that checkout)."""
    import subprocess

    import kaibot

    try:
        return subprocess.run(["git", "-C", str(Path(kaibot.__file__).parent), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, check=True, timeout=10).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


# ---------------------------------------------------------------- the allowlist
GLOB = re.compile(r"[*?\[\]]")


def _norm(v):
    """A value as JSON reads it back (so 23 == 23.0 and a tuple is a list)."""
    return json.loads(json.dumps(v, sort_keys=True, default=str))


def load_allowlist(path: str | Path | None) -> list[dict]:
    """Every entry names one session file (`journal`), one difference (`key`, as the report prints it), the value
    recorded (`was`) and the value replayed (`now`), both as JSON (`null`: absent), and a `reason`. No globs: an entry
    acknowledges exactly one difference, so any other difference, or the same one with another value, still fails."""
    if path is None or not Path(path).exists():
        return []
    import tomllib

    acks = tomllib.loads(Path(path).read_text()).get("ack", [])
    for a in acks:
        if not str(a.get("reason") or "").strip() or not a.get("key") or not a.get("journal"):
            raise ValueError(f"{path}: every [[ack]] needs journal, key and a reason: {a}")
        if GLOB.search(str(a["journal"])) or GLOB.search(str(a["key"])):
            raise ValueError(f"{path}: no globs in an [[ack]]: name one session file and one difference: {a}")
        if not isinstance(a.get("was"), str) or not isinstance(a.get("now"), str):
            raise ValueError(f"{path}: every [[ack]] needs `was` and `now` (the values, as JSON): {a}")
        try:
            a["_was"], a["_now"] = json.loads(a["was"]), json.loads(a["now"])
        except json.JSONDecodeError as err:
            raise ValueError(f"{path}: `was`/`now` is not JSON ({err}): {a}") from None
    return acks


def acknowledged(acks: list[dict], name: str, key: str, recorded, replayed) -> dict | None:
    """The entry that acknowledges this difference: same file, same key, same recorded and replayed values."""
    for a in acks:
        if (a["journal"] == name and a["key"] == key and _norm(recorded) == a.get("_was", object())
                and _norm(replayed) == a.get("_now", object())):
            a["_used"] = True
            return a
    return None


# ---------------------------------------------------------------- the report
def _short(v) -> str:
    return "(none)" if v is None else json.dumps(v, sort_keys=True, default=str)[:160]


def report(outcomes: list[Outcome], acks: list[dict]) -> str:
    lines = ["| session file | recorded by | differences | acknowledged | acknowledged, gone |",
             "|---|---|---|---|---|"]
    for o in outcomes:
        lines.append(f"| `{o.name}` | `{o.header.get('code_sha') or '?'}` | {len(o.diffs)} | {len(o.acked)} "
                     f"| {len(o.stale)} |")
    for o in outcomes:
        if not (o.diffs or o.acked or o.stale or o.notes):
            continue
        lines += ["", f"#### `{o.name}`", *[f"- {n}" for n in o.notes]]
        for k, a, b in o.diffs:
            lines.append(f"- **difference** `{k}`: recorded {_short(a)}, now {_short(b)}")
        for k, a, b, why in o.acked:
            lines.append(f"- acknowledged `{k}`: recorded {_short(a)}, now {_short(b)} ({why})")
        for a in o.stale:
            lines.append(f"- **acknowledged difference no longer happens** `{a['key']}`: expected now "
                         f"{a.get('now')} ({a.get('reason', '').strip()}); the change it acknowledges was undone, "
                         "or the file was recorded again and the entry is to be removed")
    stale = [a for a in acks if not a.get("_used")]
    if stale:  # the ones for a replayed file fail it (above); this also lists those for files not replayed
        lines += ["", "Allowlist entries that matched nothing (remove them once the fixture is recorded again):",
                  *[f"- `{a['journal']}` `{a['key']}`" for a in stale]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export", help="a sanitised session file from a journal (read-only, standard library only)")
    e.add_argument("journal")
    e.add_argument("--out", required=True)
    g = e.add_mutually_exclusive_group()
    g.add_argument("--sessions", type=int, default=1, help="the last N sessions (default 1)")
    g.add_argument("--all", action="store_true", help="the whole journal")
    r = sub.add_parser("replay", help="replay session files through this checkout and compare")
    r.add_argument("files", nargs="+")
    r.add_argument("--allowlist", default=str(DEFAULT_ALLOWLIST))
    r.add_argument("--report", help="also write the report (Markdown) here")
    c = sub.add_parser("record", help="record a fixture from the simulator (tests/dst)")
    c.add_argument("name", help="'sample' or a scenario of tests/dst/scenarios.FINDINGS")
    c.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "export":
        try:
            lines = export(a.journal, sessions=None if a.all else a.sessions)
        except (FileNotFoundError, sqlite3.Error, ValueError) as err:
            print(f"export failed: {err}", file=sys.stderr)
            return 2
        write_lines(lines, a.out)
        print(f"wrote {a.out}: {len(lines) - 1} entries, sessions {', '.join(lines[0]['sessions']) or '-'}")
        return 0
    if a.cmd == "record":
        lines = record(a.name, a.out)
        print(f"wrote {a.out}: {len(lines) - 1} entries")
        return 0
    import logging

    logging.basicConfig(level=logging.CRITICAL)
    acks = load_allowlist(a.allowlist)
    outcomes = []
    for f in a.files:
        try:
            outcomes.append(replay(f, acks))
        except (FileNotFoundError, ValueError, KeyError, sqlite3.Error) as err:
            print(f"{f}: cannot be replayed: {err}", file=sys.stderr)
            return 2
    text = report(outcomes, acks)
    print(text)
    if a.report:
        Path(a.report).write_text(text)
    return 1 if any(not o.ok for o in outcomes) else 0


if __name__ == "__main__":
    raise SystemExit(main())
