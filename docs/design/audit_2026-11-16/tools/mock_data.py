"""Dev-only mock of the dashboard's Supabase RPCs for the design audit (issue #226).

Not part of the app and never shipped: it lives under docs/design and is only read by audit.py, which answers the
browser's network calls from here (Playwright request interception). Nothing in apps/web imports it, no production
data or account is touched, and every number below is invented.
"""
from __future__ import annotations

import datetime as dt
import math

UTC = dt.timezone.utc


def iso(t: dt.datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def sessions(n: int, end: dt.date) -> list[dt.date]:
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= dt.timedelta(days=1)
    return out[::-1]


class Mock:
    """scenario: "running" (a paper run with trend_momentum positions, the experiment badge and a halt) or "empty"."""

    def __init__(self, scenario: str = "running", now: dt.datetime | None = None):
        self.scenario = scenario
        self.now = now or dt.datetime.now(UTC)
        self.today = (self.now - dt.timedelta(hours=5)).date()
        self.days = sessions(11, self.today - dt.timedelta(days=1))
        self.run = {"id": 1, "created_at": iso(self.now - dt.timedelta(days=16)), "amount_usd": "10000", "status": "running",
                    "end_reason": None, "peak_equity": "10212.40", "kind": "paper", "label": "Paper 1", "level": "balanced",
                    "started_on": str(self.days[0]), "ended_on": None}
        self.replay = {**self.run, "id": 2, "kind": "replay", "label": "Replay A", "status": "ended", "peak_equity": None,
                       "started_on": "2026-01-02", "ended_on": "2026-06-30"}
        pnl = [18.4, -42.1, 61.0, 35.2, -12.7, 74.9, -55.3, 28.8, 49.6, -31.5, 44.2]
        spy = [0.002, -0.006, 0.008, 0.003, -0.001, 0.005, -0.009, 0.004, 0.006, -0.002, 0.003]
        self.daily = [{"session": str(d), "pnl": str(p), "pnl_venue": str(p + 1.2), "pnl_shadow": str(p), "spy_ret": s,
                       "cash_ret": 0.00017, "trades": 2 + i % 3} for i, (d, p, s) in enumerate(zip(self.days, pnl, spy))]
        self.closed = 10000 + sum(pnl)
        syms = ["NVDA", "AVGO", "LLY", "JPM", "COST", "MSFT", "AMZN", "META", "CAT", "UNH", "GOOGL", "V"]
        self.trades = []
        tid = 140
        for i in range(37):
            d = self.days[-1 - (i // 4) % len(self.days)]
            strat = "trend_momentum" if i % 3 != 2 else "dip_reversal"
            entry = dt.datetime(d.year, d.month, d.day, 14, 40 + (i * 7) % 19, tzinfo=UTC) + dt.timedelta(minutes=i * 11 % 180)
            px = 90 + (i * 37) % 420 + 0.37
            win = i % 5 not in (1, 3)
            r = (1.4 - (i % 4) * 0.2) if win else -1.0 + (i % 3) * 0.1
            risk = 48.0 + i % 7
            opened = i < 3 and strat == "trend_momentum"
            self.trades.append({
                "id": tid - i, "run_id": 1, "signal_id": 900 - i, "strategy": strat, "symbol": syms[i % len(syms)],
                "entry_ts": iso(entry), "exit_ts": None if opened else iso(entry + dt.timedelta(hours=2 + i % 3)),
                "qty": str(3 + i % 9), "entry_px": f"{px:.2f}", "exit_px": None if opened else f"{px * (1 + r * 0.012):.2f}",
                "stop_px": f"{px * 0.94:.2f}", "target_px": f"{px * 1.12:.2f}", "risk_usd": f"{risk:.2f}",
                "exit_reason": None if opened else ("target" if win and i % 2 else "time" if win else "stop"),
                "pnl_usd": None if opened else f"{r * risk:.2f}", "verdict": None if strat == "trend_momentum" else ("allow", "downgrade", "veto")[i % 3 % 2],
                "features": {"z": -1.82, "resid_z": -1.61, "turnover": 0.84, "news_24h": 0, "vix": 17.4, "atr5": 0.62, "vwap": px + 0.4, "quote_age_s": 0.4},
            })

    # ------------------------------------------------------------------ pieces
    def heartbeat(self):
        if self.scenario == "empty":
            return []
        detail = {
            "state": "trading", "minute": 212, "host": "kai-macbook.local", "account_clean": True, "entries_today": 2, "halted": True,
            "code_sha": "e6deca3", "next_open": iso(self.now + dt.timedelta(hours=18)),
            "scan": {"bars": 43, "checks": 1806, "regime_off": 0, "setups": 3, "near": 2, "vetoed": {"company news in the last 24 h": 1},
                     "to_room": 0, "recent_near": [{"symbol": "AMD", "m": 95, "z": -1.31, "why": "dip 1.31 sigma, needs 1.5 sigma"},
                                                   {"symbol": "CRM", "m": 160, "z": -1.72, "why": "sector fell with it (residual 0.9 sigma)"}]},
            "experiments": [{"strategy": "trend_momentum", "label": "Experiment: failed the ten-year test", "evidence": "failed_gate_b",
                             "experiment": True, "real_money": False, "level": "balanced", "max_exposure_pct": 60, "retire_drawdown_pct": 20}],
            "multi_day": {"book": "open", "note": "", "rules": {"trend_momentum": {
                "retired": False, "note": "no decision today: daily store not refreshed after the last close",
                "last_decision": str(self.days[-1]), "waiting": None}}},
        }
        return [{"process": "engine", "ts": iso(self.now - dt.timedelta(seconds=12)), "detail": detail},
                {"process": "watchdog", "ts": iso(self.now - dt.timedelta(seconds=20)), "detail": {"state": "watching", "engine_heartbeat_age_s": 12}}]

    def halts(self):
        if self.scenario == "empty":
            return []
        t = self.now - dt.timedelta(minutes=25)
        return [{"kind": "losses", "reason": "3 consecutive losing trades; entries paused until 13:05 ET", "ts": iso(t), "run_id": 1, "session": str(self.today)}]

    def positions(self):
        rows = {"NVDA": (14, 142.18, 133.65, 159.24, 147.92, 140), "AVGO": (6, 331.40, 311.52, 371.17, 327.05, 139),
                "LLY": (2, 812.66, 763.90, 910.18, 829.31, 138), "COST": (2, 934.20, 878.15, 1046.30, 941.77, 137)}
        return {s: {"qty": q, "fill": f, "stop": st, "target": tg, "status": "open", "last": last, "trade_id": tid}
                for s, (q, f, st, tg, last, tid) in rows.items()}

    def overview(self, run_id):
        if run_id == 2:
            return {"run": self.replay, "daily": self.daily, "heartbeat": [], "pending_commands": [], "ledger": None, "today": None}
        if self.scenario == "empty":
            return {"run": {**self.run, "peak_equity": None}, "daily": [], "heartbeat": [], "pending_commands": [], "ledger": None,
                    "today": None, "segments": [], "halts_today": []}
        eq = self.closed - 63.4
        return {"run": self.run, "daily": self.daily, "heartbeat": self.heartbeat(), "pending_commands": [],
                "ledger": {"ts": iso(self.now - dt.timedelta(seconds=8)), "cash": "4310.22", "positions": self.positions(),
                           "equity_venue": f"{eq + 2.1:.2f}", "equity_shadow": f"{eq:.2f}"},
                "today": {"trades": 2, "pnl": -63.4}, "segments": [{"level": "balanced", "from_session": str(self.days[0])}],
                "halts_today": self.halts()}

    def tape(self):
        out = []
        for i in range(40):
            ts = self.now - dt.timedelta(minutes=9 + i * 23)
            t = self.trades[i % len(self.trades)]
            if i % 3 == 0:
                out.append({"kind": "fill", "ts": iso(ts), "side": "buy" if i % 2 else "sell", "qty": 5, "venue_px": 142.18, "shadow_px": 142.23,
                            "fees": 0.01, "tag": None, "symbol": t["symbol"], "strategy": t["strategy"], "trade_id": t["id"],
                            "exit_reason": None if i % 2 else "target", "pnl_usd": None if i % 2 else 38.2})
            else:
                veto = None if i % 4 else "company news in the last 24 h"
                out.append({"kind": "signal", "ts": iso(ts), "signal_id": 900 - i, "mode": "shadow" if i % 5 == 4 else "live", "strategy": t["strategy"],
                            "symbol": t["symbol"], "entry": 142.18, "stop": 133.6, "target": 159.2,
                            "reasons": ["price above its 100-day trend", "ranked 3 of 40 by steady 90-day climb"] if t["strategy"] == "trend_momentum"
                            else ["fell 1.8 sigma since the open", "sector explains little of it", "5-minute higher low"],
                            "veto": veto, "approved": veto is None, "qty": 5 if veto is None else None, "binding": "position_pct" if i % 7 == 0 else None,
                            "trade_id": t["id"] if veto is None else None,
                            "counterfactual": {"pnl_usd": -21.4, "r_multiple": -0.44, "exit_reason": "stop"} if i % 5 == 4 else None, "room": None})
        return out

    def health(self):
        hb = self.heartbeat()
        fp = "a91c44e2;core=7be02f1c;dip_reversal=11d0a7c3;spy_late_momentum=5f3e9b02;trend_momentum=c40d18aa"
        ev = [("engine_start", 400, {"code_sha": "e6deca3", "fingerprint": fp, "host": "kai-macbook.local"}),
              ("session_start", 215, {"session": str(self.today), "universe": 42}),
              ("multi_day_no_decision", 214, {"rule": "trend_momentum", "note": "daily store not refreshed after the last close"}),
              ("halt", 25, {"kind": "losses", "reason": "3 consecutive losing trades; entries paused until 13:05 ET"}),
              ("command_pause", 1500, {"idem": "0d1f6a52-55a3-4c0e-9a62-2f7f3c3d9d11"}),
              ("session_end", 1300, {"session": str(self.days[-1]), "trades": 4, "pnl": 44.2}),
              ("shadow_session_end", 1299, {"strategies": 2, "signals": 7}),
              ("watchdog_recovered", 2900, {"engine_heartbeat_age_s": 14})]
        return {"now": iso(self.now), "heartbeat": hb,
                "halts": self.halts() + [{"kind": "watchdog", "reason": "no engine heartbeat for 184 s", "ts": iso(self.now - dt.timedelta(minutes=2960)), "run_id": 1, "session": str(self.days[-2])},
                                         {"kind": "daily_stop", "reason": "day -1.52% <= -1.50%", "ts": iso(self.now - dt.timedelta(days=6)), "run_id": 1, "session": str(self.days[-5])}],
                "events": [] if self.scenario == "empty" else [{"type": t, "ts": iso(self.now - dt.timedelta(minutes=m)), "run_id": 1, "payload": p} for t, m, p in sorted(ev, key=lambda e: e[1])],
                "deadman": {"armed": True, "jobs": [{"job": "deadman_1556", "schedule": "56 20 * * 1-5", "active": True}, {"job": "deadman_1556_dst", "schedule": "56 19 * * 1-5", "active": True}],
                            "runs": [{"status": "succeeded", "message": "engine heartbeat fresh; nothing to do", "start": iso(self.now - dt.timedelta(hours=20))}]},
                "pending_commands": []}

    def gate(self):
        if self.scenario == "empty":
            return {"latest": None, "history": [], "random_days": 0}
        def crit(v, l, m): return {"value": v, "limit": l, "met": m}
        strat = lambda n, pnl, t: {"status": "provisional", "kills": [], "n_trades": n, "excluded_older_code": 6, "pnl_usd": pnl, "mean_r_pct": 0.21,
                                   "sessions": 11, "random_days": 9, "random_missing_days": [str(self.days[-1]), str(self.days[-2])], "n_trials": 14,
                                   "trades_to_go": 900 - n,
                                   "criteria": {"trades": crit(n, 900, False), "months": crit(0.5, 3, False), "t_stat": crit(t, 3, False),
                                                "deflated_sharpe": crit(0.31, 0, True), "random_pct": crit(None, 95, None),
                                                "sharpe_vs_spy": crit(0.74, 1.12, False), "drawdown": crit(0.011, 0.08, True)}}
        rep = {"generated_at": iso(self.now - dt.timedelta(hours=19)), "bar_sha": "4c1e0a77b2d94f10aa31", "fingerprint": "a91c44e27be02f1c",
               "frozen_since": iso(self.now - dt.timedelta(days=9)), "overall": "keep collecting", "bar": {}, "kill": {},
               "strategies": {"dip_reversal": strat(13, 61.2, 0.84), "trend_momentum": strat(24, 109.3, 1.12)}}
        hist = [{"id": 40 - i, "ts": iso(self.now - dt.timedelta(days=i, hours=19)), "session": str(d), "overall": "keep collecting", "fingerprint": "a91c44e2",
                 "status": {"dip_reversal": "provisional", "trend_momentum": "provisional"}, "n_trades": {"dip_reversal": max(13 - i, 0), "trend_momentum": max(24 - 2 * i, 0)}}
                for i, d in enumerate(self.days[::-1][:8])]
        return {"latest": {"id": 40, "ts": rep["generated_at"], "session": str(self.days[-1]), "bar_sha": rep["bar_sha"], "fingerprint": rep["fingerprint"],
                           "overall": "keep collecting", "report": rep}, "history": hist, "random_days": 9}

    def report(self, session):
        if self.scenario == "empty":
            return {"session": None, "sessions": []}
        s = session or str(self.days[-1])
        rows = [t for t in self.trades if t["exit_ts"]][:5]
        return {"session": s, "sessions": [str(d) for d in self.days[::-1]],
                "day": {"pnl": 44.2, "pnl_venue": 45.4, "pnl_shadow": 44.2, "spy_ret": 0.003, "cash_ret": 0.00017, "trades": 4},
                "start_equity": 10126.3,
                "trades": [{k: t[k] for k in ("id", "strategy", "symbol", "entry_ts", "exit_ts", "qty", "entry_px", "exit_px", "pnl_usd", "risk_usd", "exit_reason")} for t in rows],
                "signals": {"live": 6, "approved": 4, "shadow": 7}, "vetoes": [{"reason": "company news in the last 24 h", "n": 1}, {"reason": "position_pct", "n": 1}],
                "shadow": [{"strategy": "orb_stocks_in_play", "symbol": "AMD", "ts": iso(self.now - dt.timedelta(hours=22)), "veto": None, "pnl_usd": -14.2, "r": -0.31},
                           {"strategy": "news_continuation", "symbol": "CRM", "ts": iso(self.now - dt.timedelta(hours=21)), "veto": "spread too wide", "pnl_usd": 22.9, "r": 0.48}],
                "halts": []}

    def attribution(self):
        g = lambda dim, key, n, pnl, r, win, t, flag: {"dim": dim, "key": key, "n": n, "pnl": pnl, "mean_r": r, "win": win, "t": t, "flag": flag}
        groups = [g("strategy", "trend_momentum", 24, 109.3, 0.21, 0.58, 1.12, None), g("strategy", "dip_reversal", 13, 61.2, 0.09, 0.54, 0.84, None),
                  g("exit_reason", "stop", 14, -672.0, -0.97, 0.0, -9.1, None), g("exit_reason", "target", 12, 590.4, 1.2, 1.0, 8.8, None),
                  g("exit_reason", "time", 11, 252.1, 0.5, 0.82, 2.4, None), g("hour_et", "10", 15, -38.2, -0.06, 0.47, -0.4, "watch"),
                  g("hour_et", "14", 9, 121.0, 0.29, 0.67, 1.3, None), g("weekday", "Mon", 8, 33.1, 0.1, 0.5, 0.5, None),
                  g("symbol", "NVDA", 4, 88.0, 0.45, 0.75, 1.4, None), g("symbol", "CAT", 3, -61.5, -0.4, 0.33, -1.1, None)]
        if self.scenario == "empty":
            return {"since": str(self.today), "weeks": 4, "n": 0, "groups": [], "weekly": []}
        return {"since": str(self.days[0]), "weeks": 4, "n": 37, "groups": groups,
                "weekly": [{"week": str(self.days[0]), "strategy": "trend_momentum", "n": 11, "pnl": 52.0}, {"week": str(self.days[5]), "strategy": "trend_momentum", "n": 13, "pnl": 57.3},
                           {"week": str(self.days[0]), "strategy": "dip_reversal", "n": 6, "pnl": 20.1}, {"week": str(self.days[5]), "strategy": "dip_reversal", "n": 7, "pnl": 41.1}]}

    def room_ab(self):
        z = {"n": 0, "n_cf": 0, "mean_r": None, "sd_r": None, "cf_pnl": 0, "n_loss_avoided": 0, "loss_avoided": 0, "n_win_missed": 0, "win_missed": 0}
        return {"since": str(self.days[0]), "weeks": 13, "n": 0, "by_verdict": {}, "by_status": {}, "veto": z,
                "allow": {"n": 0, "n_closed": 0, "mean_r": None, "sd_r": None, "pnl": 0}, "downgrade": {"n": 0, "n_paired": 0, "pnl": 0, "cf_pnl": 0, "mean_r": None},
                "no_answer": {"n": 0, "n_cf": 0, "cf_pnl": 0}, "arms": {"n": 0, "rules_pnl": 0, "room_pnl": 0},
                "lift": {"n_veto": 0, "n_allow": 0, "diff": None, "ci_lo": None, "ci_hi": None},
                "spend": {"llm_usd": 0.42, "room_usd": 0, "by_purpose": {"news_triage": 0.42}, "per_loss_avoided": None}}

    def config(self):
        import json, pathlib
        themes = {}
        try:
            root = pathlib.Path(__file__).resolve().parents[4] / "apps/web/src/data"
            themes = json.loads((root / "themes.json").read_text())
        except Exception:
            pass
        uni = sorted({s for t in themes.values() for s in t["symbols"]})
        ver = {"version": 2, "body": {}, "applies_from": str(self.days[4]), "created_at": iso(self.now - dt.timedelta(days=7))}
        return {"level": "balanced", "bounds": {}, "themes": themes, "universe": uni, "applied": ver, "pending": None, "draft": None,
                "versions": [{"version": 2, "status": "applied", "applies_from": str(self.days[4]), "created_at": ver["created_at"]},
                             {"version": 1, "status": "superseded", "applies_from": str(self.days[0]), "created_at": iso(self.now - dt.timedelta(days=15))}]}

    # ------------------------------------------------------------------ dispatch
    def rpc(self, fn: str, a: dict):
        e = self.scenario == "empty"
        if fn == "kb_claim_owner": return True
        if fn == "kb_runs": return [{**self.run, "peak_equity": None}] if e else [self.run, self.replay]
        if fn == "kb_overview": return self.overview(a.get("p_run", 1))
        if fn == "kb_trades":
            rows = [] if e else self.trades
            o, l = a.get("p_offset", 0), a.get("p_limit", 50)
            return {"total": len(rows), "rows": rows[o:o + l]}
        if fn == "kb_trade":
            t = next((x for x in self.trades if x["id"] == a.get("p_id")), None)
            if not t: return None
            checks = {k: {"state": "pass", "actual": v, "limit": l} for k, (v, l) in {
                "kill_switch": (False, False), "reconciled": (True, True), "market_open": (True, True), "entry_window": ("10:52", "09:35-15:45"),
                "event_blackout": (False, False), "data_ok": (True, True), "duplicate_symbol": (False, False), "entries_today": (2, 20),
                "run_drawdown_pct": (0.6, 8), "quote_age_s": (0.4, 5), "daily_loss_pct": (0.21, 1.5), "max_positions": (3, 5),
                "position_pct": (15.0, 15), "open_risk_pct": (1.4, 2.5)}.items()}
            return {"trade": t, "run": self.run, "gate": {"checks": checks, "approved": True, "qty": int(t["qty"]), "binding": "position_pct",
                                                           "policy_hash": "9d2c11aa0f3e", "code_sha": "e6deca3"}, "room": None,
                    "fills": [{"ts": t["entry_ts"], "side": "buy", "qty": int(t["qty"]), "venue_px": float(t["entry_px"]), "shadow_px": float(t["entry_px"]) + 0.04, "fees": 0.0},
                              {"ts": t["exit_ts"] or t["entry_ts"], "side": "sell", "qty": int(t["qty"]), "venue_px": float(t["exit_px"] or t["entry_px"]), "shadow_px": float(t["exit_px"] or t["entry_px"]) - 0.05, "fees": 0.02}]}
        if fn == "kb_evidence":
            if e: return {"n": 0, "pnl": 0, "mean_r": None, "sd_r": None, "t": None, "ci_lo": None, "ci_hi": None, "sessions": 0, "first_session": None, "by_strategy": []}
            return {"n": 37, "pnl": 170.5, "mean_r": 0.17, "sd_r": 1.08, "t": 0.96, "ci_lo": -0.19, "ci_hi": 0.53, "sessions": 11, "first_session": str(self.days[0]),
                    "by_strategy": [{"strategy": "trend_momentum", "n": 24, "pnl": 109.3, "mean_r": 0.21}, {"strategy": "dip_reversal", "n": 13, "pnl": 61.2, "mean_r": 0.09}]}
        if fn == "kb_report": return self.report(a.get("p_session"))
        if fn == "kb_attribution": return self.attribution()
        if fn == "kb_tape": return [] if e or a.get("p_before") else self.tape()
        if fn == "kb_health": return self.health()
        if fn == "kb_room": return None
        if fn == "kb_room_ab": return self.room_ab()
        if fn == "kb_llm_budget":
            return {"day": str(self.today), "day_usd": 0.118, "month_usd": 2.41, "state": "ok", "alert": False,
                    "caps": {"daily_soft": 1, "monthly_alert": 15, "monthly_hard": 25}, "calls_today": 9, "open_reserved_usd": 0, "by_purpose": {"news_triage": 0.118}}
        if fn == "kb_gate_report": return self.gate()
        if fn == "kb_get_config": return self.config()
        if fn == "kb_chapters":
            return [] if e else [{"version": 1, "from": str(self.days[0]), "to": str(self.days[4]), "n_trades": 15, "pnl_usd": 59.8, "mean_r": 0.12, "t_stat": 0.5, "too_early": True},
                                 {"version": 2, "from": str(self.days[4]), "to": None, "n_trades": 22, "pnl_usd": 110.7, "mean_r": 0.2, "t_stat": 0.9, "too_early": True}]
        if fn == "kb_ticker_lists":
            lists = [] if e else [{"name": "AI infrastructure", "updated_at": iso(self.now - dt.timedelta(days=3)), "symbols": [
                {"symbol": s, "status": st, "in_catalogue": False, "reason": r, "etf": "XLK", "name": n} for s, st, r, n in [
                    ("PLTR", "accepted", None, "Palantir Technologies"), ("CRWV", "accepted", None, "CoreWeave"), ("NBIS", "accepted", None, "Nebius Group"),
                    ("VRT", "accepted", None, "Vertiv Holdings"), ("ANET", "accepted", None, "Arista Networks"), ("SMCI", "accepted", None, "Super Micro Computer"),
                    ("FSLY", "refused", "average daily volume below $50M", "Fastly")]]},
                {"name": "Dividend payers", "updated_at": iso(self.now - dt.timedelta(days=9)), "symbols": [
                    {"symbol": s, "status": "accepted", "in_catalogue": False, "reason": None, "etf": "XLP", "name": n} for s, n in [("KO", "Coca-Cola"), ("PEP", "PepsiCo"), ("PG", "Procter & Gamble"), ("MO", "Altria"), ("O", "Realty Income")]]}]
            return {"max_tickers": 40, "max_lists": 20, "min_only": 5, "lists": lists}
        if fn == "kb_check_ticker": return {"symbol": a.get("p_symbol"), "status": "accepted", "reason": None, "etf": "XLK", "in_catalogue": False, "name": "Example Corp"}
        return {"ok": True, "queued": True}


def session_blob(now: dt.datetime) -> dict:
    """A signed-in browser session for the mock project (never a real account)."""
    exp = int(now.timestamp()) + 86400
    user = {"id": "00000000-0000-4000-8000-000000000001", "aud": "authenticated", "role": "authenticated", "email": "owner@example.com",
            "app_metadata": {}, "user_metadata": {}, "created_at": iso(now)}
    return {"access_token": "mock.mock.mock", "token_type": "bearer", "expires_in": 86400, "expires_at": exp, "refresh_token": "mock", "user": user}
