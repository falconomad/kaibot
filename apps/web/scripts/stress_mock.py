"""A crowded multi-day book for the first real holdings (dev-only; used by mobile_check.py --stress).

On top of the design audit's mock (docs/design/audit_2026-11-16/tools/mock_data.py), the book holds 28 multi-day
positions: long and dotted tickers, a sector the universe does not know ("unknown", or none at all), a stock with 0
shares left after a fill, a stop being placed again, and the next close's plan with a partly filled closing-auction
order. Shapes are migration 0020's (kb_book, kb_multiday_plan, kb_book_closed; #229, PR #247). Invented numbers.
"""
# ruff: noqa: E501  (mock rows are long on purpose: one holding per line)
from __future__ import annotations

import datetime as dt

import mock_data

SYMS = ["NVDA", "AVGO", "LLY", "COST", "GOOGL", "BRK.B", "META", "MSFT", "AAPL", "AMZN", "JPM", "V", "MA", "UNH", "HD",
        "PG", "XOM", "ORCL", "ABBV", "CRM", "NFLX", "AMD", "ADBE", "TMO", "LIN", "WMT", "PLTRWW", "SUPERLONG"]


class StressMock(mock_data.Mock):
    def __init__(self, scenario: str = "running", now: dt.datetime | None = None):
        super().__init__(scenario, now)
        self.session = str(self.days[-1])
        self.book_rows = []
        for i, s in enumerate(SYMS):
            cost = 40 + i * 37.31
            qty = 0 if s == "ORCL" else 1 + (i * 7) % 23              # ORCL: sold to 0 shares by the last fill
            mark = round(cost * (1 + ((i % 7) - 3) / 41), 2)
            self.book_rows.append({
                "rule": "trend_momentum", "symbol": s, "qty": qty, "avg_cost": round(cost, 2), "last": mark, "last_ts": mock_data.iso(self.now),
                "mark": mark, "opened_session": str(self.days[i % len(self.days)]), "dividends": round(i * 0.37, 2),
                "stop": None if s == "SUPERLONG" else {"price": round(mark * 0.88, 2), "state": "re-arming" if s == "AMD" else "resting"},
                "sell_when": [
                    {"code": "rank", "reading": i + 1, "threshold": 60, "of": 600, "met": False, "as_of": self.session},
                    {"code": "trend", "reading_pct": round(((i % 9) - 4) * 1.7, 2), "threshold_pct": 0.0, "window_days": 50, "met": i % 9 < 4, "as_of": self.session},
                    {"code": "gap", "reading_pct": round(2 + i % 11, 2), "threshold_pct": 15.0, "window_days": 90, "met": False, "as_of": self.session}],
                "sector": None if s in ("PLTRWW", "SUPERLONG") else "unknown" if s == "BRK.B" else ["Technology", "Health Care", "Financials", "Consumer Staples"][i % 4],
            })

    def rpc(self, fn: str, a: dict):
        if fn == "kb_book":
            held = list(self.book_rows)
            invested = sum(p["qty"] * p["mark"] for p in held)
            cash = 1840.55
            return {"run_id": 1, "session": self.session,
                    "mtm": {"ts": mock_data.iso(self.now), "at_close": True, "cash": cash, "invested": round(invested, 2), "equity": round(cash + invested, 2),
                            "start_equity": 10000, "prev_close_equity": round(cash + invested - 41.2, 2), "dividends_to_date": 12.4},
                    "positions": held,
                    "exposure": {"gross": round(invested, 2), "overnight": round(invested, 2), "by_rule": [{"rule": "trend_momentum", "value": round(invested, 2), "share": 0.6}]},
                    "reconciled": {"ok": True, "ts": mock_data.iso(self.now), "positions": {}, "cash_diff": 0}}
        if fn == "kb_equity_series":
            return [{"session": str(d), "close_equity": 10000 + i * 11.5, "marked": True} for i, d in enumerate(self.days)]
        if fn == "kb_multiday_plan":
            return {"rules": [{"rule": "trend_momentum", "decided_after_close_of": self.session, "fills_at_close_of": str(self.today), "decides": True,
                               "sell": [{"symbol": "NFLX", "why": "rank"}, {"symbol": "SUPERLONG", "why": "trend"}],
                               "buy_candidates": ["TSLA", "QCOM"],
                               "orders": [
                                   {"symbol": "NFLX", "shares_now": 9, "shares_target": 0, "action": "sell", "why": "rank", "coid": "md-1", "state": "filled"},
                                   {"symbol": "SUPERLONG", "shares_now": 21, "shares_target": 0, "action": "sell", "why": "trend", "coid": "md-2", "state": "working"},
                                   {"symbol": "TSLA", "shares_now": 12, "shares_target": 40, "action": "buy", "why": None, "coid": "md-3", "state": "working"}],
                               "skipped": 1, "note": None}]}
        if fn == "kb_book_closed":
            return [{"rule": "trend_momentum", "symbol": s, "opened_session": str(self.days[0]), "closed_session": self.session, "qty": 5 + i,
                     "avg_cost": 100 + i, "exit_px": 104 + i * 1.5, "pnl_usd": round((4 + i * 0.5) * (5 + i), 2), "dividends": 0.4,
                     "days_held": 9 + i, "reason": ["rank", "trend", "gap", None][i % 4]} for i, s in enumerate(["INTC", "BRK.B", "SUPERLONGER", "CSCO"])]
        return super().rpc(fn, a)

    # engine follow-ups (19 Nov 2026): the daily note, the shadow pre-open review and an order not filled at the close
    NOTE = ("kaibo sold NFLX and SUPERLONG at the close because they left the top of the trend list, and bought 28 shares of TSLA; "
            "12 shares of TSLA were not filled and expired at the close. It holds 28 stocks, about 60 percent of the pot, and "
            "the rest is cash. Nothing else changed.")

    def heartbeat(self):
        beats = super().heartbeat()
        for b in beats:
            md = (b.get("detail") or {}).get("multi_day")
            if b["process"] == "engine" and md is not None:
                md["exits"] = {"trend_momentum": "target_list"}
                md["daily_note"] = {"session": self.session, "text": self.NOTE, "source": "model"}
                md["rules"]["trend_momentum"]["exit"] = "target_list"
                md["rules"]["trend_momentum"]["review_shadow"] = {
                    "mode": "shadow", "session": str(self.today), "status": "done", "would_veto": ["SUPERLONGER"], "would_downgrade": ["BRK.B", "TSLA"],
                    "reasons": {"SUPERLONGER": "veto: news: a very long reason about guidance being cut twice in one quarter, with more words than fit",
                                "BRK.B": "downgrade: event: earnings tomorrow", "TSLA": "downgrade: liquidity: thin pre-market"},
                    "cost_usd": 0.0042, "note": "shadow review (observer, no effect): would veto SUPERLONGER, halve BRK.B, TSLA"}
        return beats

    def health(self):
        h = super().health()
        h["events"] = [{"type": "multi_day_unfilled", "ts": mock_data.iso(self.now), "run_id": 1,
                        "payload": {"strategy": "trend_momentum", "symbol": "TSLA", "side": "buy", "qty": 28, "filled": 16, "left": 12,
                                    "coid": "md-3", "session": str(self.today), "status": "expired", "purpose": "entry", "type": "market", "tif": "day"}},
                       *h["events"]]
        return h
