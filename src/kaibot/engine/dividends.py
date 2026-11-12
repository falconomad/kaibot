"""Dividends accrued at the ex-date on the live path (S78, issue #118; replaces the `MultiDayDividendsError` guard of
#117 / PR #160).

A share held over the close before the ex-date earns the dividend; Alpaca pays it later (a DIV activity on the pay
date). The ledger books it at the ex-date, to the sleeves holding the stock at that open (`ExDateDividends`, read
from Alpaca's corporate-actions endpoint, the same one the daily store uses), and writes a `div_accrual` entry:
a receivable. When the payment arrives, `match_payment` ties it to its accrual (`div_paid`) so it is not booked a
second time; cash reconciliation counts the unpaid accruals (`receivable`). A payment with no accrual (the ex-date
data missed it) is booked when it is paid, as before, so nothing is lost.

Not imported by research: the daily path books ex-dates from the daily store (engine/daily.py).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

CA_PATH = "/v1/corporate-actions"


@dataclass(frozen=True)
class ExDividend:
    symbol: str
    ex_date: str
    per_share: float
    pay_date: str | None = None
    special: bool = False

    @property
    def ref(self) -> str:
        return f"exdiv:{self.symbol}:{self.ex_date}:{self.per_share:g}:{int(self.special)}"


def parse_cash_dividends(payload: dict) -> list[ExDividend]:
    """Alpaca's GET /v1/corporate-actions answer -> its cash dividends (unusable rows skipped). The same reading
    of the fields as `ingest.daily_bars.normalise_action` (rate per share, ex_date, payable_date, special)."""
    out = []
    for r in ((payload or {}).get("corporate_actions") or {}).get("cash_dividends") or []:
        if not isinstance(r, dict) or not r.get("symbol"):
            continue
        try:
            rate = float(r.get("rate"))
        except (TypeError, ValueError):
            continue
        ex = str(r.get("ex_date") or "")[:10]
        if not (math.isfinite(rate) and rate > 0 and len(ex) == 10):
            continue
        special = r.get("special") is True or str(r.get("special")).lower() == "true"
        out.append(ExDividend(str(r["symbol"]).upper(), ex, rate, (str(r.get("payable_date") or "")[:10] or None),
                              special))
    return list(dict.fromkeys(out))  # a payment listed under two CUSIPs counts once


class ExDateDividends:
    """`(day, symbols) -> [ExDividend]` with that ex-date, from Alpaca's market-data API (`data.get(path, params)`
    or an `AlpacaData`, whose `_d` is used). One request per call, only for the symbols held over the close."""

    def __init__(self, data) -> None:
        self.get = getattr(data, "get", None) or data._d

    def __call__(self, day: date, symbols: Sequence[str]) -> list[ExDividend]:
        syms = sorted(set(symbols))
        if not syms:
            return []
        d = str(day)
        payload = self.get(CA_PATH, {"symbols": ",".join(syms), "types": "cash_dividend", "start": d, "end": d,
                                     "limit": 1000})
        return [x for x in parse_cash_dividends(payload) if x.ex_date == d and x.symbol in syms]


def open_accruals(entries: Iterable) -> dict[str, dict]:
    """Accruals not yet paid, by ref, from the journal's `div_accrual` and `div_paid` entries."""
    acc: dict[str, dict] = {}
    for e in entries:
        kind, body = (e[2], e[3]) if len(e) == 4 else (e[0], e[1])
        if kind == "div_accrual":
            acc[body["ref"]] = body
        elif kind == "div_paid":
            acc.pop(body.get("ref"), None)
    return acc


def receivable(entries: Iterable) -> float:
    return float(sum(float(b.get("amount") or 0.0) for b in open_accruals(entries).values()))


def match_payment(row: dict, accruals: dict[str, dict]) -> str | None:
    """The ref of the accrual an Alpaca DIV activity pays: same symbol, the pay date if both have one, else the
    oldest unpaid accrual of the symbol not after the payment."""
    sym, day = row.get("symbol"), str(row.get("date") or "")[:10]
    cands = sorted((b for b in accruals.values() if b.get("symbol") == sym and (not day or b["ex_date"] <= day)),
                   key=lambda b: b["ex_date"])
    exact = [b for b in cands if b.get("pay_date") and b["pay_date"] == day]
    pick = exact or [b for b in cands if not b.get("pay_date") or not day] or cands
    return pick[0]["ref"] if pick else None
