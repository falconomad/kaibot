"""Live-safety exclusion for the multi-day path (Product, 16 Nov 2026; issue #266; trend_momentum.md Amendment 6):
no BUY, and no add to a holding, of a stock with an announced split, reverse split, spin-off, merger or symbol
change whose ex or effective date falls within the next GUARD_SESSIONS sessions. The engine does not yet carry a
held position through these actions safely (the written answer on #266), so it does not walk into one.

Read from Alpaca's corporate-actions endpoint (GET /v1/corporate-actions on the free market-data API; the same
endpoint the daily store and the ex-date dividends read. The older /v2/corporate_actions/announcements of the
trading API is deprecated). Cached per decision evening and symbol: each symbol is asked once per evening. A failed
read is not cached: the read at the send step is the one that counts, and if it fails no stock is bought that
session (fail closed) and the heartbeat says why. Held stocks are never sold because of this guard; a held stock
with an announced action gets a plain heartbeat note instead.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np

CA_PATH = "/v1/corporate-actions"
GUARD_SESSIONS = 35
# 35 sessions counted as weekdays plus two, so market holidays (at most two in any seven weeks) never shorten it
WINDOW_WEEKDAYS = GUARD_SESSIONS + 2
CHUNK = 50  # symbols per request (as the daily store's fetch)
MAX_PAGES = 10

# Alpaca's request type -> (response list, the plain name, the fields naming the stock(s) it concerns, the date
# fields in order of preference). Field names as engine/dividends.py and ingest/daily_bars.py read them, plus the
# merger fields of Alpaca's corporate-actions reference (acquirer_symbol, acquiree_symbol, effective_date).
TYPES: dict[str, tuple[str, str, tuple[str, ...], tuple[str, ...]]] = {
    "forward_split": ("forward_splits", "split", ("symbol",), ("ex_date", "process_date")),
    "reverse_split": ("reverse_splits", "reverse split", ("symbol",), ("ex_date", "process_date")),
    "unit_split": ("unit_splits", "unit split", ("old_symbol", "symbol"), ("ex_date", "effective_date",
                                                                          "process_date")),
    "spin_off": ("spin_offs", "spin-off", ("source_symbol", "symbol"), ("ex_date", "process_date")),
    "cash_merger": ("cash_mergers", "cash merger", ("acquiree_symbol",), ("effective_date", "ex_date",
                                                                        "process_date")),
    "stock_merger": ("stock_mergers", "stock merger", ("acquiree_symbol",), ("effective_date", "ex_date",
                                                                           "process_date")),
    "stock_and_cash_merger": ("stock_and_cash_mergers", "merger", ("acquiree_symbol",),
                              ("effective_date", "ex_date", "process_date")),
    "name_change": ("name_changes", "symbol change", ("old_symbol",), ("process_date", "effective_date",
                                                                      "ex_date")),
}
LISTS = {v[0]: k for k, v in TYPES.items()}


@dataclass(frozen=True)
class Announced:
    symbol: str
    action: str  # plain name: "split", "reverse split", "spin-off", "cash merger", "symbol change", ...
    on: str  # the ex or effective date, YYYY-MM-DD

    def note(self) -> str:
        """The heartbeat's line for a held stock."""
        return f"{self.symbol}: {self.action} on {self.on} — the engine does not handle this yet, check before then"

    def skip_text(self) -> str:
        """What Activity shows for a skipped buy."""
        return f"skipped: corporate action on {self.on}"


def parse(payload: dict) -> list[Announced]:
    """Alpaca's GET /v1/corporate-actions answer -> the announced actions this guard cares about (others ignored;
    a row without a symbol or a date is ignored too: it cannot be placed)."""
    out = []
    block = (payload or {}).get("corporate_actions") or {}
    if not isinstance(block, dict):
        raise ValueError(f"unexpected corporate-actions answer ({type(block).__name__})")
    for name, rows in block.items():
        kind = LISTS.get(name)
        if kind is None or not isinstance(rows, list):
            continue
        _, plain, sym_fields, date_fields = TYPES[kind]
        for r in rows:
            if not isinstance(r, dict):
                continue
            on = next((str(r[f])[:10] for f in date_fields if r.get(f)), None)
            sym = next((str(r[f]).upper() for f in sym_fields if r.get(f)), None)
            if on and len(on) == 10 and sym:
                out.append(Announced(sym, plain, on))
    return out


def window(session: date) -> tuple[date, date]:
    """The dates that count for a buy at the close of `session`: from the session itself (an action going ex at
    its open is already in the venue's share count, but not in the evening's prices) to GUARD_SESSIONS sessions
    later."""
    end = np.busday_offset(np.datetime64(session, "D"), WINDOW_WEEKDAYS, roll="forward").astype(object)
    return session, end


class CorporateActionGuard:
    """`lookup(evening, session, symbols)` -> ({symbol: Announced}, None) for the symbols with an announced action
    in `window(session)`, or ({}, why) when the read failed. `get(path, params)`: Alpaca's market-data GET (an
    `AlpacaData`'s `_d`, or any callable with that shape: a fake feed in tests)."""

    def __init__(self, data=None, *, get: Callable[[str, dict], dict] | None = None) -> None:
        self.get = get or getattr(data, "get", None) or data._d
        self._cache: dict[date, dict[str, Announced | None]] = {}  # one decision evening at a time

    def lookup(self, evening: date, session: date, symbols: Iterable[str]) -> tuple[dict[str, Announced],
                                                                                     str | None]:
        syms = sorted({str(s).upper() for s in symbols if s})
        if evening not in self._cache:
            self._cache = {evening: {}}
        seen = self._cache[evening]
        missing = [s for s in syms if s not in seen]
        if missing:
            try:
                got = self._read(missing, *window(session))
            except Exception as e:  # any failure: nothing is cached, the caller buys nothing
                return {}, f"Alpaca's corporate-actions read failed ({type(e).__name__}: {str(e)[:120]})"
            for s in missing:
                seen[s] = None
            for a in sorted(got, key=lambda a: a.on):
                if a.symbol in seen and seen[a.symbol] is None:
                    seen[a.symbol] = a
        return {s: seen[s] for s in syms if seen.get(s) is not None}, None

    def known(self, evening: date, symbols: Iterable[str]) -> dict[str, Announced]:
        """What the cache already holds for this evening (no read): the heartbeat's notes."""
        seen = self._cache.get(evening) or {}
        return {s: seen[s] for s in (str(x).upper() for x in symbols) if seen.get(s) is not None}

    def _read(self, symbols: Sequence[str], start: date, end: date) -> list[Announced]:
        out: list[Announced] = []
        for i in range(0, len(symbols), CHUNK):
            part = symbols[i:i + CHUNK]
            token = None
            for _ in range(MAX_PAGES):
                p = {"symbols": ",".join(part), "types": ",".join(TYPES), "start": str(start),
                     "end": str(end), "limit": 1000}
                if token:
                    p["page_token"] = token
                d = self.get(CA_PATH, p)
                out += [a for a in parse(d) if str(start) <= a.on <= str(end)]
                token = (d or {}).get("next_page_token")
                if not token:
                    break
            else:
                raise ValueError(f"the corporate actions did not end after {MAX_PAGES} pages")
        return out

