"""Fuzz targets of M12 ticket 3.5 (#467): the decoders of untrusted bytes, each with the property it must keep.

Every target is a plain function of one `bytes` argument. It returns normally when the property holds and raises
`Violation` when it does not; any other exception escaping a target is a crash and also a finding. The same
functions run three ways:

    pytest tests/fuzz                         a fixed corpus and a few hundred Hypothesis inputs per target (every PR)
    python tests/fuzz/run_fuzz.py TARGET -t S  Atheris, coverage-guided, for S seconds (nightly on Linux, `fuzz` job)
    python tests/fuzz/run_fuzz.py TARGET --replay FILE   one crashing input again, with its traceback

The targets (the decoders the engine trusts):

    broker_payloads  Alpaca's order objects and FILL activities as the order manager decodes them
                     (OrderManager._poll/_adopt, read_fills): a payload is either refused loudly (an exception: the
                     tick fails closed) or what it journals is sane: fills of a finite positive quantity and price,
                     buy or sell, a finite non-negative filled quantity; and the ledger folds the journal to finite
                     cash. The account decoders beside it (fees_from_activities, actions_from_activities) the same.
    news_payloads    a provider article through news.desk.normalise: never raises, returns None or a clean item
                     (a timezone-aware time not after now, valid tickers, at most MAX_TICKERS, http(s) URL or None)
    journal_entries  the checksum chain of #496/#515: a journal whose entries were changed after they were written
                     (a body, a time, a kind, a checksum; an entry removed from the middle) fails Journal.verify()
                     with JournalCorrupt. Removing the LAST entry is not detectable by a chain and is not counted.
    journal_file     one damaged byte anywhere in a journal file: opening it raises (JournalCorrupt or SQLite's own
                     DatabaseError: the engine does not start), or every read gives what was written (the
                     identity in `meta` is checked at start against Supabase by JournalStore.verify_identity)

Known engine defects the targets step around (each has a strict xfail in test_fuzz_targets.py; remove the entry
when the issue is fixed):
"""

from __future__ import annotations

import contextlib
import json
import math
import sqlite3
import struct
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

KNOWN: dict[int, str] = {}  # #543 fixed (pragma integrity_check at open): journal_file compares reads by kind again
GOOD_NUMBERS = ["1", "2", "10", "0.5", "3", 3, 2.5, "100.25"]


class Violation(AssertionError):
    """The decoder broke its property for this input."""


# ---------------------------------------------------------------- a small, deterministic data provider
class Data:
    """Consumes the fuzz input. The same bytes always give the same values; when they run out, zeros."""

    def __init__(self, raw: bytes) -> None:
        self.raw, self.i = raw, 0

    def byte(self) -> int:
        if self.i >= len(self.raw):
            return 0
        self.i += 1
        return self.raw[self.i - 1]

    def int(self, n: int) -> int:
        """0 <= x < n."""
        v = 0
        for _ in range(4 if n > 255 else 1):
            v = v * 256 + self.byte()
        return v % n if n > 0 else 0

    def pick(self, seq):
        return seq[self.int(len(seq))]

    def text(self, max_len: int = 40) -> str:
        n = self.int(max_len + 1)
        chunk = bytes(self.byte() for _ in range(n))
        return chunk.decode("utf-8", errors="replace")

    def rest(self) -> bytes:
        return self.raw[self.i:]


NUMBERS = ["1", "2", "10", "0", "-1", "0.5", "1e308", "1e-320", "nan", "NaN", "inf", "-inf", "Infinity", "", " 3 ",
           "abc", None, 3, 2.5, -4, 0, True, float("nan"), float("inf"), [], {}, "1_000", "0x10", "١٢"]
STRINGS = ["", "B", "C", "buy", "sell", "short", "BUY", None, 7, "2026-08-03T19:50:00Z", "2026-08-03T19:50:00",
           "not a time", "2026-02-30T00:00:00Z", "x" * 300, "\x00", "😀", [], {}]


def _num(d: Data):
    return d.pick(NUMBERS) if d.byte() % 4 else d.text(12)


def _str(d: Data):
    return d.pick(STRINGS) if d.byte() % 4 else d.text(20)


# ---------------------------------------------------------------- broker payloads
def _finite_pos(v) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v) and v > 0


def broker_payloads(raw: bytes) -> None:
    from kaibot.broker.protocol import BrokerError, OrderRequest
    from kaibot.engine.journal import Journal
    from kaibot.engine.ledger import LEDGER_KINDS, Ledger, actions_from_activities, fees_from_activities
    from kaibot.engine.orders import OrderManager

    d = Data(raw)
    now = datetime(2026, 8, 3, 19, 50, tzinfo=UTC)
    order_id = "o1"

    class Venue:
        """Answers the submit correctly, then everything from the fuzz input."""

        def __init__(self) -> None:
            self.order_view: object = None
            self.acts: object = []

        def submit(self, req):
            return {"id": order_id, "client_order_id": req.client_order_id, "status": "new", "qty": str(req.qty),
                    "filled_qty": "0", "symbol": req.symbol, "side": req.side, "type": req.type}

        def order(self, oid):
            if isinstance(self.order_view, BrokerError):
                raise self.order_view
            return self.order_view

        def order_by_client_id(self, coid):
            return self.order(None)

        def open_orders(self):
            return [self.order_view] if isinstance(self.order_view, dict) else []

        def activities(self, after=None, types=("FILL",)):
            return self.acts

        def positions(self):
            return {}

        def cancel(self, oid):
            return None

    v = Venue()
    journal = Journal(":memory:", now=lambda: now)
    journal.append("run", {"run_id": 1, "amount": 10_000.0, "started": now.isoformat()})
    om = OrderManager(v, journal, lambda: now, run_id=1)
    om.submit(OrderRequest("B", "buy", 10, client_order_id=om.next_coid(1, "s", "B", "2026-08-03", "buy")),
              strategy="s", purpose="buy", session="2026-08-03")

    def order_obj() -> object:
        k = d.byte() % 8
        if k == 0:
            return d.pick([None, [], "x", 3])
        o: dict = {}
        for key, gen in (("id", lambda: d.pick([order_id, order_id, "o2", None, 5])),
                         ("status", lambda: d.pick(["new", "accepted", "partially_filled", "filled", "canceled",
                                                    "expired", "rejected", "replaced", "pending_cancel", "done_for_day",
                                                    "weird", None, 3])),
                         ("filled_qty", lambda: d.pick(GOOD_NUMBERS + ["0", 0]) if 545 in KNOWN else _num(d)),
                         ("qty", lambda: _num(d)), ("limit_price", lambda: _num(d)),
                         ("stop_price", lambda: _num(d)), ("client_order_id", lambda: _str(d)),
                         ("replaced_by", lambda: d.pick([None, None, None, "o1", "o2"])),
                         ("legs", lambda: d.pick([None, [], [{"id": "l1"}], ["x"], "x"])),
                         ("expires_at", lambda: _str(d))):
            if d.byte() % 3:
                o[key] = gen()
        return o

    def activity() -> object:
        if d.byte() % 9 == 0:
            return d.pick([None, "x", [], 1])
        a: dict = {}
        for key, gen in (("id", lambda: d.pick(["a1", "a2", "a1", None, 7, ""])),
                         ("order_id", lambda: d.pick([order_id, order_id, order_id, "o2", None])),
                         ("symbol", lambda: _str(d)),
                         ("side", lambda: d.pick(["buy", "sell"]) if 545 in KNOWN else _str(d)),
                         ("qty", lambda: d.pick(GOOD_NUMBERS) if 545 in KNOWN else _num(d)),
                         ("price", lambda: d.pick(GOOD_NUMBERS) if 545 in KNOWN else _num(d)),
                         ("fees", lambda: d.pick(["0", 0, None, "0.01"]) if 545 in KNOWN else _num(d)),
                         ("transaction_time", lambda: _str(d)), ("activity_type", lambda: d.pick(["FILL", "FEE", "DIV",
                                                                                                    "SPLIT", None])),
                         ("net_amount", lambda: d.pick(GOOD_NUMBERS + ["-0.5", ""]) if 545 in KNOWN else _num(d)),
                         ("date", lambda: _str(d)), ("status", lambda: _str(d)),
                         ("created_at", lambda: _str(d)), ("activity_sub_type", lambda: _str(d))):
            if d.byte() % 4:
                a[key] = gen()
        return a

    for _ in range(1 + d.int(4)):
        v.order_view = order_obj() if d.byte() % 7 else BrokerError(d.pick([0, 404, 500, 422]), d.text(10))
        v.acts = [activity() for _ in range(d.int(4))] if d.byte() % 9 else d.pick([None, "x", {"a": 1}])
        # refused loudly (an exception): the tick fails; what was journalled before it is checked below
        with contextlib.suppress(ValueError, TypeError, KeyError, AttributeError, BrokerError):
            om.sync()
        rows = v.acts if isinstance(v.acts, list) else []
        try:
            fees = fees_from_activities([r for r in rows if isinstance(r, dict)])
        except (ValueError, TypeError, KeyError):
            fees = []
        for f in fees:
            if not (isinstance(f["amount"], float) and math.isfinite(f["amount"]) and f["amount"] != 0 and f["ref"]):
                raise Violation(f"fees_from_activities gave {f}")
        try:
            acts = actions_from_activities([r for r in rows if isinstance(r, dict)], {"B": 10.0}, {"B": 20.0})
        except (ValueError, TypeError, KeyError):
            acts = []
        for a in acts:
            for name in ("amount", "ratio", "per_share"):
                x = getattr(a, name, None)
                if x is not None and not (isinstance(x, float) and math.isfinite(x)):
                    raise Violation(f"actions_from_activities gave a non-finite {name}: {a}")

    entries = journal.entries()
    for _s, _ts, kind, b in entries:
        if kind == "fill":
            if b.get("side") not in ("buy", "sell"):
                raise Violation(f"a fill was journalled with side {b.get('side')!r}: {b}")
            if not (_finite_pos(b.get("qty")) and _finite_pos(b.get("px"))):
                raise Violation(f"a fill was journalled with qty {b.get('qty')!r} and price {b.get('px')!r}: {b}")
            if not (isinstance(b.get("fees"), float) and math.isfinite(b["fees"]) and b["fees"] >= 0):
                raise Violation(f"a fill was journalled with fees {b.get('fees')!r}: {b}")
        if kind == "om_state" and b.get("cum") is not None:
            c = b["cum"]
            if not (isinstance(c, float) and math.isfinite(c) and c >= 0):
                raise Violation(f"an order state was journalled with filled quantity {c!r}: {b}")
    book = Ledger.fold(e for e in entries if e[2] in LEDGER_KINDS).book(1)
    if book is not None and not math.isfinite(book.cash):
        raise Violation(f"the ledger's cash is {book.cash} after these payloads")
    journal.close()


# ---------------------------------------------------------------- news payloads
def news_payloads(raw: bytes) -> None:
    from kaibot.news import desk

    now = datetime(2026, 8, 3, 13, 0, tzinfo=UTC)
    try:
        art = json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, RecursionError):
        d = Data(raw)
        art = {k: (_str(d) if d.byte() % 2 else _num(d)) for k in ("headline", "created_at", "published_at", "url",
                                                                       "summary", "author", "source")}
        art["symbols"] = d.pick([None, [], ["B"], ["b", " c ", 3, None, "TOO-LONG-TICKER", "ok.A"], "B", [[]]])
        art["created_at"] = d.pick(["2026-08-03T12:00:00Z", "2026-08-03T12:00:00", "2099-01-01T00:00:00Z",
                                    art["created_at"]])
        art["headline"] = d.pick(["Company beats estimates", art["headline"], "   ", "a" * 2000])
    try:
        item = desk.normalise(art, now)
    except RecursionError:
        return  # a pathologically nested document: Python's own limit, not the decoder's
    except Exception as e:  # the docstring's promise: None for anything unusable
        raise Violation(f"normalise raised {type(e).__name__}: {e} on {str(art)[:200]}") from e
    if item is None:
        return
    t = item["published_at"]
    if not isinstance(t, datetime) or t.tzinfo is None or t > now:
        raise Violation(f"published_at {t!r} (now {now})")
    if not isinstance(item["tickers"], list) or len(item["tickers"]) > desk.MAX_TICKERS or \
            any(not isinstance(s, str) or not desk._TICKER.match(s) for s in item["tickers"]):
        raise Violation(f"tickers {item['tickers']!r}")
    if item["url"] is not None and not str(item["url"]).startswith(("http://", "https://")):
        raise Violation(f"url {item['url']!r}")
    if not item["headline"] or len(item["headline"]) > 500 or not item["canonical"]:
        raise Violation(f"headline {item['headline']!r}")


# ---------------------------------------------------------------- the journal: the checksum chain
def _small_journal(d: Data, path=":memory:"):
    from kaibot.engine.journal import Journal

    t0 = datetime(2026, 8, 3, 13, 30, tzinfo=UTC)
    clock = [t0]
    j = Journal(path, now=lambda: clock[0])
    for k in range(2 + d.int(6)):
        clock[0] = t0 + timedelta(seconds=k)
        kind = d.pick(["fill", "om_state", "md_send", "mark", "run"])
        j.append(kind, {"k": k, "v": d.pick([1, 2.5, "x", None, [1], {"a": "b"}, "😀"])})
    return j


def journal_entries(raw: bytes) -> None:
    from kaibot.engine.journal import JournalCorrupt

    d = Data(raw)
    j = _small_journal(d)
    # the damage comes from outside SQLite (a disk, a copy): the journal's append-only triggers are not in its way
    j.db.execute("drop trigger if exists entry_no_update")
    j.db.execute("drop trigger if exists entry_no_delete")
    rows = j.db.execute("select seq, ts, kind, body, sum from entry order by seq").fetchall()
    last = rows[-1][0]
    victim = rows[d.int(len(rows))]
    seq = victim[0]
    what = d.pick(["body", "ts", "kind", "sum", "delete", "null_sum", "swap"])
    if what == "delete":
        if seq == last:
            return  # a chain cannot see that its last entry is gone (the crash tests cover a lost tail)
        j.db.execute("delete from entry where seq = ?", (seq,))
    elif what == "null_sum":
        j.db.execute("update entry set sum = null where seq = ?", (seq,))
    elif what == "swap" and len(rows) > 1:
        other = rows[(rows.index(victim) + 1) % len(rows)]
        if (victim[1], victim[2], victim[3]) == (other[1], other[2], other[3]):
            return
        j.db.execute("update entry set ts = ?, kind = ?, body = ? where seq = ?", (other[1], other[2], other[3], seq))
    else:
        col = {"body": 3, "ts": 1, "kind": 2, "sum": 4}.get(what, 3)
        old = victim[col]
        new = d.text(30) if d.byte() % 2 else old[:-1] + chr((ord(old[-1]) + 1 + d.int(50)) % 0x7F or 65)
        if new == old:
            return
        j.db.execute(f"update entry set {['seq', 'ts', 'kind', 'body', 'sum'][col]} = ? where seq = ?", (new, seq))
    try:
        j.verify()
    except JournalCorrupt:
        return
    finally:
        j.close()
    raise Violation(f"entry {seq} was changed ({what}) and Journal.verify() did not notice")


# ---------------------------------------------------------------- the journal: one damaged byte in the file
_TEMPLATE: dict[str, object] = {}


def _template() -> tuple[bytes, list, dict]:
    if not _TEMPLATE:
        with tempfile.TemporaryDirectory(prefix="kb-fuzz-") as td:
            p = Path(td) / "journal.sqlite"
            j = _small_journal(Data(bytes(range(7, 250, 3))), p)
            j.db.execute("pragma wal_checkpoint(truncate)")
            entries = j.entries()
            meta = {k: j.meta(k) for k in ("journal_id", "epoch", "checksum_from")}
            j.close()
            _TEMPLATE.update(raw=p.read_bytes(), entries=entries, meta=meta)
    return _TEMPLATE["raw"], _TEMPLATE["entries"], _TEMPLATE["meta"]  # type: ignore[return-value]


def journal_file(raw: bytes) -> None:
    from kaibot.engine.journal import DurabilityError, Journal, JournalCorrupt

    template, want, meta = _template()
    d = Data(raw)
    data = bytearray(template)
    for _ in range(1 + d.int(2)):
        off = struct.unpack(">I", bytes(d.byte() for _ in range(4)))[0] % len(data)
        data[off] ^= d.byte() or 1
    with tempfile.TemporaryDirectory(prefix="kb-fuzz-") as td:
        p = Path(td) / "journal.sqlite"
        p.write_bytes(bytes(data))
        try:
            j = Journal(p)
        except (JournalCorrupt, DurabilityError, sqlite3.DatabaseError, ValueError):
            # refused: the engine does not start on it. A damaged byte inside a text value can surface as
            # UnicodeDecodeError, and a damaged `meta.checksum_from` as ValueError (int()), instead of
            # JournalCorrupt: still a refusal (noted on #543)
            return
        try:
            got = j.entries()
            if got != want:
                raise Violation(f"the journal opened and its entries are not what was written ({len(got)} entries, "
                                f"{len(want)} written)")
            if 543 not in KNOWN:
                for kind in sorted({e[2] for e in want}):
                    if j.entries(kinds=(kind,)) != [e for e in want if e[2] == kind]:
                        raise Violation(f"the journal opened and verified, and its {kind} entries read by kind are "
                                        "not what was written")
            # `meta` (the identity, the checksum cut-over) carries no checksum: a damaged journal_id or epoch is
            # refused at start by JournalStore.verify_identity against Supabase, not here; a damaged cut-over only
            # matters for entries without a checksum, and every entry here has one (their reads are compared above)
        except (JournalCorrupt, sqlite3.DatabaseError, ValueError):
            return  # refused on the first read: also fails closed
        finally:
            j.close()


TARGETS = {"broker_payloads": broker_payloads, "news_payloads": news_payloads, "journal_entries": journal_entries,
           "journal_file": journal_file}
