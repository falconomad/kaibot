"""Event-sourced view of a replay (story S05): every replay is reduced to an ordered event stream and hashed.

Two digests make a replay reproducible and auditable:
- input_digest: the exact bars, news times, VIX series and parameters the replay consumed;
- events_digest: the ordered entry / exit / day_close events it produced.
Same inputs + same code must give the same events_digest, on any machine. Floats are rounded to 1e-6 before
hashing so a different BLAS or CPU cannot flip the last bit of a P&L sum.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator, Mapping

import numpy as np

from kaibot.replay.data import SymbolDays
from kaibot.replay.sim import Book

ROUND = 6


def _canon(x):
    if isinstance(x, float | np.floating):
        v = round(float(x), ROUND)
        return 0.0 if v == 0 else v  # -0.0 -> 0.0
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, np.bool_):
        return bool(x)
    if isinstance(x, np.datetime64):
        return str(np.datetime64(x, "D"))
    if isinstance(x, Mapping):
        return {str(k): _canon(v) for k, v in sorted(x.items(), key=lambda kv: str(kv[0]))}
    if isinstance(x, list | tuple):
        return [_canon(v) for v in x]
    return x


def events(book: Book) -> Iterator[dict]:
    """Ordered events: per session, entries and exits by minute, then the day's close."""
    by_day: dict[str, list[dict]] = {}
    for t in book.trades:
        d = str(np.datetime64(t.date, "D"))
        by_day.setdefault(d, []).append({"type": "entry", "date": d, "m": t.entry_m, "symbol": t.symbol,
                                         "qty": t.qty, "px": t.entry, "stop": t.stop, "target": t.target})
        by_day[d].append({"type": "exit", "date": d, "m": t.exit_m, "symbol": t.symbol, "qty": t.qty,
                          "px": t.exit, "reason": t.reason, "pnl": t.pnl})
    for row in book.daily:
        d = str(np.datetime64(row["date"], "D"))
        evs = sorted(by_day.get(d, []), key=lambda e: (e["m"], e["type"] != "exit", e["symbol"]))
        yield from evs
        yield {"type": "day_close", "date": d, "start": row["start"], "end": row["end"], "pnl": row["pnl"],
               "trades": row["trades"], "halted": row["halted"]}


def digest_events(evs: Iterable[dict]) -> str:
    h = hashlib.sha256()
    for e in evs:
        h.update(json.dumps(_canon(e), sort_keys=True, separators=(",", ":")).encode())
        h.update(b"\n")
    return h.hexdigest()


def events_digest(book: Book) -> str:
    return digest_events(events(book))


def input_digest(bars: Mapping[str, SymbolDays], news: Mapping[str, np.ndarray] | None = None,
                 series: Mapping[str, object] | None = None, params: Mapping[str, object] | None = None) -> str:
    """Hash of everything the replay read. Bars are hashed after rounding to 1e-6 (prices) so a re-download that
    returns the same prints matches; NaN layout is part of the hash."""
    h = hashlib.sha256()
    for s in sorted(bars):
        sd = bars[s]
        h.update(s.encode())
        h.update(np.asarray(sd.dates, dtype="datetime64[D]").tobytes())
        for k in ("o", "h", "l", "c", "v", "vw"):
            a = np.round(np.asarray(getattr(sd, k), dtype=float), ROUND)
            h.update(np.nan_to_num(a, nan=-1.0).tobytes())
    for s in sorted(news or {}):
        h.update(f"news:{s}".encode())
        h.update(np.asarray(news[s], dtype="datetime64[s]").tobytes())
    h.update(json.dumps(_canon(dict(series or {})), sort_keys=True, default=str).encode())
    h.update(json.dumps(_canon(dict(params or {})), sort_keys=True, default=str).encode())
    return h.hexdigest()
