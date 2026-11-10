"""The live engine: Variant A no-news dip reversal on Alpaca paper, one paper run at a time.

Per completed minute: sync orders/fills -> mark to market -> daily stop / run drawdown -> trailing stops ->
time exit -> (every 5-minute bar) new entries through the risk gate. All prices, sizes, stops and exits come from
these rules and `risk/gate.py`; no LLM is involved (the agent room is off in M1).

Safety:
- Every entry is a bracket order, so the stop and the 2R target live AT THE BROKER and fire even if this
  process (or the laptop) stops.
- On startup, positions the engine doesn't know about block all entries (account not clean); positions it does
  know from an earlier session are closed at the first tick of the next open.
- The run's pot (onboarding amount) caps sizing and buying power, whatever the paper account holds.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta

import numpy as np

from kaibot.broker.shadow_costs import QuoteCtx, sell_fees, shadow_fill_price
from kaibot.engine.market import LiveMarket, session_minutes
from kaibot.replay.sim import BLACKOUT_FOMC, half_spread, sell_px
from kaibot.risk.gate import Proposal, RiskPolicy, Side, Snapshot, evaluate

log = logging.getLogger("kaibot.engine")

ATR_MULT = 1.5
TARGET_R = 2.0
TRAIL_ATR = 1.5
FIRST_ENTRY_M = 5  # 09:35
PAUSE_AFTER_LOSSES, PAUSE_MINUTES = 3, 60
LEVEL_NAMES = {"conservative": "Steady", "balanced": "Balanced", "aggressive": "Hunter"}


def code_sha() -> str:
    import os

    if os.environ.get("KAIBOT_CODE_SHA"):
        return os.environ["KAIBOT_CODE_SHA"]
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return "unknown"


@dataclass
class Pos:
    trade_id: int
    symbol: str
    qty: int
    coid: str
    order_id: str
    ref: float
    risk_ps: float
    atr: float
    sigma: float
    stop: float
    target: float
    status: str = "pending"  # pending -> open -> exiting -> closed
    fill: float | None = None
    shadow_entry: float | None = None
    stop_order_id: str | None = None
    target_order_id: str | None = None
    exit_order_id: str | None = None
    exit_reason: str | None = None
    armed: bool = False
    hi_close: float = -1e18
    db_order_id: int | None = None
    entry_ts: str | None = None
    carried: bool = False  # left over from an earlier session

    def features(self) -> dict:
        return {k: v for k, v in asdict(self).items() if k not in ("trade_id", "symbol", "qty")}


@dataclass
class Day:
    run_id: int
    day: date
    open_utc: datetime
    n_min: int
    start_equity: float
    realized_venue: float = 0.0
    realized_shadow: float = 0.0
    realized_reported: float = 0.0
    entries: int = 0
    trades_closed: int = 0
    losses_in_row: int = 0
    pause_until: int = -1
    halted: bool = False
    flattened: bool = False
    finalized: bool = False
    last_m: int = -1
    events: list = field(default_factory=list)

    @property
    def flatten_m(self) -> int:
        return self.n_min - 5  # 15:55 on a normal day, 12:55 on an early close

    @property
    def last_entry_m(self) -> int:
        return self.n_min - 15  # 15:45


class Engine:
    def __init__(
        self,
        broker,
        market: LiveMarket,
        store,
        policies: dict[str, RiskPolicy],
        fomc: set,
        process: str = "engine",
        now=lambda: datetime.now(UTC),
    ):
        self.broker, self.market, self.store = broker, market, store
        self.policies, self.fomc, self.process, self.now = policies, fomc, process, now
        self.run: dict | None = None
        self.day: Day | None = None
        self.pos: dict[str, Pos] = {}
        self.account_clean = True
        self.code = code_sha()
        self.peak: float | None = None
        self.on_progress = lambda status: None

    # ================= lifecycle =================
    def startup(self) -> None:
        self.run = self.store.active_run()
        self._restore_positions()
        self.check_account()
        self.store.event(
            "engine_start",
            {"code_sha": self.code, "run_id": self.run and self.run["id"], "positions": list(self.pos)},
            self._rid(),
        )

    def check_account(self) -> None:
        broker_pos = self.broker.positions()
        foreign = sorted(set(broker_pos) - set(self.pos))
        self.account_clean = not foreign
        if foreign:
            msg = {"foreign_positions": foreign, "action": "entries blocked until the paper account is clean"}
            log.warning("account not clean: %s", foreign)
            self.store.event("account_not_clean", msg, self._rid())

    def _restore_positions(self) -> None:
        self.pos = {}
        if not self.run:
            return
        for t in self.store.open_trades(self.run["id"]):
            f = t.get("features") or {}
            known = {k: f[k] for k in Pos.__dataclass_fields__ if k in f}
            p = Pos(trade_id=t["id"], symbol=t["symbol"], qty=int(float(t["qty"])), **known)
            self.pos[p.symbol] = p

    def _rid(self) -> int | None:
        return self.run["id"] if self.run else None

    # ================= commands =================
    def apply_commands(self) -> None:
        for c in self.store.pending_commands():
            kind, pl = c["kind"], c.get("payload") or {}
            try:
                if kind == "new_run":
                    if self.pos:  # close the old run's positions first; the command waits until we are flat
                        self.flatten_all("manual")
                        continue
                    self._new_run(float(pl["amount_usd"]), pl["level"])
                elif kind == "pause" and self.run:
                    self._set_run_status("paused")
                elif kind == "resume" and self.run:
                    self._set_run_status("running")
                elif kind == "flatten_and_stop" and self.run:
                    self.flatten_all("manual")
                    self._end_run("stopped", "stopped by user")
                elif kind == "set_level" and self.run and pl.get("level") in self.policies:
                    nxt = self._next_session_date()
                    self.store.insert(
                        "segment",
                        {
                            "run_id": self.run["id"],
                            "level": pl["level"],
                            "from_session": str(nxt),
                            "policy_hash": self._policy_hash(pl["level"]),
                        },
                        returning=False,
                    )
                self.store.mark_applied(c["id"])
                self.store.event(f"command_{kind}", pl, self._rid())
            except Exception as e:  # a bad command must never stop the engine
                log.exception("command %s failed", kind)
                self.store.mark_applied(c["id"], {**pl, "error": str(e)[:200]})

    def _new_run(self, amount: float, level: str) -> None:
        if level not in self.policies:
            raise ValueError(f"unknown level {level}")
        if self.run:
            self._end_run("ended", "replaced by a new run")
        run = self.store.insert(
            "run",
            {
                "amount_usd": amount,
                "status": "scheduled",
                "kind": "paper",
                "level": level,
                "label": f"Paper · {LEVEL_NAMES[level]} · ${amount:,.0f}",
                "peak_equity": amount,
            },
        )
        self.store.insert(
            "segment",
            {
                "run_id": run["id"],
                "level": level,
                "from_session": str(self.now().date()),
                "policy_hash": self._policy_hash(level),
            },
            returning=False,
        )
        self.run, self.peak = run, amount
        self.day = None  # if a session is in progress, the new run starts trading at the next 5-minute check

    def _set_run_status(self, status: str, reason: str | None = None) -> None:
        vals = {"status": status}
        if reason:
            vals["end_reason"] = reason
        self.store.update("run", {"id": self.run["id"]}, vals)
        self.run = {**self.run, **vals}

    def _end_run(self, status: str, reason: str) -> None:
        self._set_run_status(status, reason)
        self.store.update("run", {"id": self.run["id"]}, {"ended_on": str(self.now().date())})
        self.run = None

    def _policy_hash(self, level: str) -> str:
        return hashlib.sha256(json.dumps(asdict(self.policies[level]), sort_keys=True).encode()).hexdigest()[:12]

    def _next_session_date(self) -> date:
        clk = self.broker.clock()
        return datetime.fromisoformat(clk["next_open"]).date()

    def level(self) -> str:
        return (self.run or {}).get("level") or "balanced"

    def policy(self) -> RiskPolicy:
        return self.policies[self.level()]

    # ================= per tick =================
    def tick(self) -> dict:
        now = self.now()
        clk = self.broker.clock()
        if not clk["is_open"]:
            if self.day and not self.day.finalized:
                self._sync_orders()
                self._finalize_day()
            self._maybe_prepare(clk, now)
            return {"state": "market_closed", "next_open": clk.get("next_open")}
        if self.run is None and not self.pos:
            return {"state": "idle", "note": "no paper run: start one from the dashboard"}
        if self.run is not None and (self.day is None or self.day.day != now.astimezone(_et()).date()):
            self._start_day(now)
        d = self.day
        if d is None:
            return {"state": "idle"}
        self.market.update(now)
        m = self.market.last_complete_minute(now)
        if m < 0 or m == d.last_m:
            return self._status(m)
        d.last_m = m
        self._sync_orders()
        if any(p.carried for p in self.pos.values()):
            for p in [p for p in self.pos.values() if p.carried and p.status in ("open", "pending")]:
                self._exit(p, "carried_over")
        eq, unreal = self.equity_now()
        self._risk_halts(m, eq)
        self._trail_stops(m)
        if m >= d.flatten_m and not d.flattened:
            self.flatten_all("time")
            d.flattened = True
        if (m - 4) % 5 == 0 and self._may_enter(m):
            self._entries(m)
        self._ledger(eq)
        return self._status(m)

    def _maybe_prepare(self, clk: dict, now: datetime) -> None:
        """Load history ahead of the open (the first load takes a few minutes; later ones are incremental)."""
        if self.run is None:
            return
        nxt = datetime.fromisoformat(clk["next_open"])
        if timedelta(0) < nxt - now <= timedelta(minutes=45) and self.market.day != nxt.date():
            cal = self.broker.calendar(str(nxt.date()))
            open_utc, _ = session_minutes(cal, nxt.date())
            self.market.prepare_day(
                nxt.date(),
                open_utc,
                progress=lambda n, tot: self.on_progress(
                    {"state": "preparing", "note": f"loading price history {n}/{tot}"}
                ),
            )

    def _start_day(self, now: datetime) -> None:
        today = now.astimezone(_et()).date()
        cal = self.broker.calendar(str(today))
        open_utc, n_min = session_minutes(cal, today)
        if self.run["status"] == "scheduled":
            self._set_run_status("running")
            self.store.update("run", {"id": self.run["id"]}, {"started_on": str(today)})
        segs = self.store.select(
            "segment",
            {
                "run_id": f"eq.{self.run['id']}",
                "from_session": f"lte.{today}",
                "order": "from_session.desc,id.desc",
                "limit": "1",
            },
        )
        if segs and segs[0]["level"] != self.run.get("level"):
            self.store.update("run", {"id": self.run["id"]}, {"level": segs[0]["level"]})
            self.run["level"] = segs[0]["level"]
        start_eq = float(self.run["amount_usd"]) + self.store.run_realized(self.run["id"])
        self.peak = max(float(self.run.get("peak_equity") or start_eq), start_eq)
        self.day = Day(self.run["id"], today, open_utc, n_min, start_eq)
        if self.market.day != today:
            self.on_progress({"state": "preparing", "note": "loading price history (a few minutes the first time)"})
            self.market.prepare_day(
                today,
                open_utc,
                progress=lambda n, tot: self.on_progress(
                    {"state": "preparing", "note": f"loading price history {n}/{tot}"}
                ),
            )
        for p in self.pos.values():  # positions opened on an earlier session are closed at the first tick
            p.carried = bool(p.entry_ts) and datetime.fromisoformat(p.entry_ts).astimezone(_et()).date() < today
        self.check_account()
        self.store.event(
            "session_start",
            {"day": str(today), "start_equity": start_eq, "level": self.level(), "minutes": n_min},
            self._rid(),
        )

    def equity_now(self) -> tuple[float, float]:
        d = self.day
        unreal = 0.0
        for p in self.pos.values():
            if p.status == "open" and p.fill is not None:
                last = self.market.last_close(p.symbol) or p.fill
                unreal += (last - p.fill) * p.qty
        return d.start_equity + d.realized_reported + unreal, unreal

    def _risk_halts(self, m: int, eq: float) -> None:
        d, pol = self.day, self.policy()
        if not d.halted and eq <= d.start_equity * (1 - pol.max_daily_loss_pct / 100):
            self.flatten_all("daily_stop")
            d.halted = True
            self.store.insert(
                "halt",
                {"run_id": self._rid(), "kind": "daily_stop", "reason": f"day P&L reached -{pol.max_daily_loss_pct}%"},
                returning=False,
            )
        self.peak = max(self.peak or eq, eq)
        if self.peak and eq <= self.peak * (1 - pol.run_drawdown_end_pct / 100):
            self.flatten_all("run_drawdown")
            self.store.insert(
                "halt",
                {
                    "run_id": self._rid(),
                    "kind": "run_drawdown",
                    "reason": f"drawdown reached {pol.run_drawdown_end_pct}% from peak",
                },
                returning=False,
            )
            self._end_run("ended", f"drawdown {pol.run_drawdown_end_pct}% from peak")

    def _may_enter(self, m: int) -> bool:
        d = self.day
        if self.run is None or self.run["status"] != "running" or not self.account_clean:
            return False
        if d.halted or d.flattened or m < d.pause_until:
            return False
        if not (FIRST_ENTRY_M <= m + 1 <= d.last_entry_m):
            return False
        return not (np.datetime64(d.day, "D") in self.fomc and BLACKOUT_FOMC[0] <= m <= BLACKOUT_FOMC[1])

    # ================= entries =================
    def _entries(self, m: int) -> None:
        off, vix = self.market.regime_at(m)
        if off:
            return
        cands = self.market.candidates_at(m)
        if not len(cands):
            return
        quotes = self.broker_quotes(list(cands["symbol"]))
        pol, d = self.policy(), self.day
        decision = self.now()
        for _, c in cands.iterrows():
            s = c["symbol"]
            if s in self.pos:
                continue
            news = self.market.data.news_count(s, decision - timedelta(hours=24), decision)
            ref = float(c["price"])
            stop = ref - ATR_MULT * float(c["atr5"])
            risk_ps = ref - stop
            target = ref + TARGET_R * risk_ps
            q = quotes.get(s) or {}
            q_age = _age_s(q.get("t"), decision)
            eq, _ = self.equity_now()
            exposure = sum((p.fill or p.ref) * p.qty for p in self.pos.values() if p.status in ("pending", "open"))
            snap = Snapshot(
                equity=eq,
                day_start_equity=d.start_equity,
                open_positions=sum(p.status in ("pending", "open") for p in self.pos.values()),
                symbol_exposure=0.0,
                open_risk=sum(p.risk_ps * p.qty for p in self.pos.values() if p.status in ("pending", "open")),
                quote_age_s=q_age,
                kill_switch=False,
                reconciled=self.account_clean,
                market_open=True,
                data_ok=(self.market.last_bar_minute(s) or -99) >= m - 2,
                in_entry_window=True,
                entries_today=d.entries,
                run_peak_equity=self.peak,
                buying_power=eq - exposure,
            )
            feats = {k: _num(c[k]) for k in ("z", "resid_z", "turnover", "atr5", "vwap", "sigma_1m")}
            feats.update({"news_24h": news, "vix": vix, "m": m, "quote_age_s": q_age})
            veto = "company news in 24 h" if news else None
            v = evaluate(Proposal(s, Side.BUY, ref, stop, target), snap, pol)
            qty = int(np.floor(v.qty * (0.5 if vix > 25 else 1.0))) if v.approved and not veto else 0
            sig = self.store.insert(
                "signal",
                {
                    "run_id": self._rid(),
                    "strategy": "dip_reversal",
                    "mode": "live",
                    "symbol": s,
                    "ts": decision,
                    "features_hash": hashlib.sha256(json.dumps(feats, sort_keys=True).encode()).hexdigest()[:16],
                    "features": feats,
                    "entry": ref,
                    "stop": stop,
                    "target": target,
                    "reasons": ["no-news dip", "5-min higher low", "sector-adjusted"],
                    "rules_veto": veto,
                },
            )
            self.store.insert(
                "gate_run",
                {
                    "signal_id": sig["id"],
                    "policy_hash": self._policy_hash(self.level()),
                    "code_sha": self.code,
                    "checks": {
                        ch.rule: {"state": ch.state.value, "actual": ch.actual, "limit": ch.limit} for ch in v.checks
                    },
                    "approved": bool(v.approved and not veto and qty > 0),
                    "qty": qty,
                    "binding": v.binding,
                },
                returning=False,
            )
            if qty <= 0:
                continue
            self._open(s, qty, ref, stop, target, risk_ps, float(c["atr5"]), float(c["sigma_1m"]), q, sig["id"], feats)
            d.entries += 1
            if d.entries >= pol.max_entries_per_day:
                break

    def broker_quotes(self, symbols: list[str]) -> dict:
        try:
            return self.market.data.latest_quotes(symbols)
        except Exception:
            log.exception("quotes failed")
            return {}

    def _open(self, s, qty, ref, stop, target, risk_ps, atr, sigma, quote, signal_id, feats) -> None:
        coid = f"kb-{self._rid()}-{signal_id}-1"
        bid, ask = float(quote.get("bp") or 0), float(quote.get("ap") or 0)
        if not (0 < bid <= ask):
            hs = half_spread(ref)
            bid, ask = ref - hs, ref + hs
        shadow_entry = shadow_fill_price("buy", qty, QuoteCtx(bid, ask, sigma, max(self.market.bar_volume(s), 1)))
        order = self.broker.submit_bracket(coid, s, qty, round(stop, 2), round(target, 2))
        db_order = self.store.insert(
            "order",
            {
                "signal_id": signal_id,
                "client_order_id": coid,
                "venue": "alpaca_paper",
                "legs": {"stop": round(stop, 2), "target": round(target, 2), "qty": qty},
                "status": order.get("status", "new"),
                "submitted_at": self.now(),
            },
        )
        p = Pos(
            trade_id=0,
            symbol=s,
            qty=qty,
            coid=coid,
            order_id=order["id"],
            ref=ref,
            risk_ps=risk_ps,
            atr=atr,
            sigma=sigma,
            stop=stop,
            target=target,
            shadow_entry=shadow_entry,
            db_order_id=db_order["id"],
            entry_ts=self.now().isoformat(),
        )
        trade = self.store.insert(
            "trade",
            {
                "run_id": self._rid(),
                "signal_id": signal_id,
                "strategy": "dip_reversal",
                "symbol": s,
                "entry_ts": self.now(),
                "qty": qty,
                "entry_px": ref,
                "stop_px": stop,
                "target_px": target,
                "risk_usd": risk_ps * qty,
                "features": {**feats, **p.features(), "room": "off (M1)"},
            },
        )
        p.trade_id = trade["id"]
        self.pos[s] = p

    # ================= order sync =================
    def _sync_orders(self) -> None:
        for p in list(self.pos.values()):
            if p.status == "closed":
                continue
            o = self.broker.order(p.exit_order_id or p.order_id)
            if p.status == "pending":
                if o["status"] == "filled":
                    self._on_entry_fill(p, o)
                elif o["status"] in ("canceled", "expired", "rejected"):
                    self._close_trade(p, None, "not_filled")
                continue
            if p.exit_order_id:  # we sent a market sell (time / daily stop / manual)
                if o["status"] == "filled":
                    self._on_exit_fill(p, float(o["filled_avg_price"]), p.exit_reason or "manual", o.get("filled_at"))
                continue
            for leg in o.get("legs") or []:
                if leg["status"] == "filled":
                    is_target = leg.get("type") == "limit"
                    reason = "target" if is_target else ("trail" if p.armed else "stop")
                    self._on_exit_fill(p, float(leg["filled_avg_price"]), reason, leg.get("filled_at"))
                    break

    def _on_entry_fill(self, p: Pos, o: dict) -> None:
        p.fill = float(o["filled_avg_price"])
        p.status = "open"
        p.stop = p.fill - p.risk_ps
        p.target = p.fill + TARGET_R * p.risk_ps
        for leg in o.get("legs") or []:
            if leg.get("type") == "limit":
                p.target_order_id = leg["id"]
                if abs(float(leg["limit_price"]) - p.target) >= 0.01:
                    p.target_order_id = self.broker.replace_limit(leg["id"], round(p.target, 2))["id"]
            elif leg.get("type") in ("stop", "stop_limit"):
                p.stop_order_id = leg["id"]
                if abs(float(leg["stop_price"]) - p.stop) >= 0.01:
                    p.stop_order_id = self.broker.replace_stop(leg["id"], round(p.stop, 2))["id"]
        self.store.insert(
            "fill",
            {
                "order_id": p.db_order_id,
                "ts": o.get("filled_at") or self.now(),
                "side": "buy",
                "qty": p.qty,
                "venue_px": p.fill,
                "shadow_px": p.shadow_entry,
                "fees": 0,
            },
            returning=False,
        )
        self.store.update(
            "trade",
            {"id": p.trade_id},
            {
                "entry_px": p.fill,
                "stop_px": p.stop,
                "target_px": p.target,
                "entry_ts": o.get("filled_at") or self.now(),
                "features": p.features(),
            },
        )

    def _on_exit_fill(self, p: Pos, px: float, reason: str, ts) -> None:
        fees = sell_fees(p.qty, px)
        shadow_exit = px if reason == "target" else sell_px(px, p.qty, p.sigma, self.market.bar_volume(p.symbol))
        pnl_v = (px - p.fill) * p.qty - fees
        pnl_s = (shadow_exit - (p.shadow_entry or p.fill)) * p.qty - fees
        self.store.insert(
            "fill",
            {
                "order_id": p.db_order_id,
                "ts": ts or self.now(),
                "side": "sell",
                "qty": p.qty,
                "venue_px": px,
                "shadow_px": shadow_exit,
                "fees": round(fees, 4),
                "tag": reason,
            },
            returning=False,
        )
        d = self.day
        if d:
            d.realized_venue += pnl_v
            d.realized_shadow += pnl_s
            d.realized_reported += min(pnl_v, pnl_s)
            d.trades_closed += 1
            d.losses_in_row = d.losses_in_row + 1 if min(pnl_v, pnl_s) < 0 else 0
            if d.losses_in_row >= PAUSE_AFTER_LOSSES:
                d.pause_until, d.losses_in_row = d.last_m + PAUSE_MINUTES, 0
        p.exit_reason = reason
        self._close_trade(p, px, reason, min(pnl_v, pnl_s), ts, {"pnl_venue": pnl_v, "pnl_shadow": pnl_s})

    def _close_trade(self, p: Pos, px, reason, pnl=None, ts=None, extra=None) -> None:
        p.status = "closed"
        self.store.update(
            "trade",
            {"id": p.trade_id},
            {
                "exit_ts": ts or self.now(),
                "exit_px": px,
                "exit_reason": reason,
                "pnl_usd": None if pnl is None else round(pnl, 2),
                "features": {**p.features(), **(extra or {})},
            },
        )
        self.pos.pop(p.symbol, None)

    # ================= exits =================
    def _trail_stops(self, m: int) -> None:
        for p in self.pos.values():
            if p.status != "open" or p.exit_order_id:
                continue
            c = self.market.last_close(p.symbol)
            if c is None:
                continue
            p.hi_close = max(p.hi_close, c)
            if not p.armed and c >= p.fill + p.risk_ps:
                p.armed = True
            if p.armed:
                new = max(p.stop, p.fill, p.hi_close - TRAIL_ATR * p.atr)
                if new >= p.stop + 0.01 and p.stop_order_id:
                    try:
                        p.stop_order_id = self.broker.replace_stop(p.stop_order_id, round(new, 2))["id"]
                        p.stop = new
                        self.store.update("trade", {"id": p.trade_id}, {"stop_px": new, "features": p.features()})
                    except Exception:
                        log.exception("trail replace failed for %s", p.symbol)

    def _exit(self, p: Pos, reason: str) -> None:
        if p.status == "pending":
            self.broker.cancel_order(p.order_id)
            self._close_trade(p, None, "not_filled")
            return
        if p.exit_order_id or p.status != "open":
            return
        o = self.broker.close_position(p.symbol)
        p.exit_reason = reason
        if o:
            p.exit_order_id = o["id"]
        else:  # nothing to close at the broker: already flat (a leg filled meanwhile)
            self._sync_orders()

    def flatten_all(self, reason: str) -> None:
        for p in list(self.pos.values()):
            self._exit(p, reason)

    # ================= bookkeeping =================
    def _ledger(self, eq: float) -> None:
        d = self.day
        if self.run is None:
            return
        pos = {
            s: {
                "qty": p.qty,
                "fill": p.fill,
                "stop": round(p.stop, 2),
                "target": round(p.target, 2),
                "status": p.status,
                "last": self.market.last_close(s),
                "trade_id": p.trade_id,
            }
            for s, p in self.pos.items()
        }
        unreal = eq - d.start_equity - d.realized_reported
        self.store.insert(
            "ledger",
            {
                "run_id": d.run_id,
                "ts": self.now().replace(microsecond=0),
                "cash": round(eq - sum((v["last"] or 0) * v["qty"] for v in pos.values()), 2),
                "positions": pos,
                "equity_venue": round(d.start_equity + d.realized_venue + unreal, 2),
                "equity_shadow": round(d.start_equity + d.realized_shadow + unreal, 2),
            },
            returning=False,
        )
        if self.run and self.peak and self.peak > float(self.run.get("peak_equity") or 0):
            self.store.update("run", {"id": self.run["id"]}, {"peak_equity": round(self.peak, 2)})
            self.run["peak_equity"] = round(self.peak, 2)

    def _finalize_day(self) -> None:
        d = self.day
        if self.pos:
            log.warning("session ended with open positions %s; they will be closed at the next open", list(self.pos))
            for p in self.pos.values():
                p.carried = True
        rid = d.run_id
        if rid:
            self.store.upsert(
                "daily_result",
                {
                    "run_id": rid,
                    "session": str(d.day),
                    "pnl_venue": round(d.realized_venue, 2),
                    "pnl_shadow": round(d.realized_shadow, 2),
                    "spy_ret": self.market.spy_return(),
                    "trades": d.trades_closed,
                },
                "run_id,session",
            )
        self.store.event(
            "session_end",
            {
                "day": str(d.day),
                "pnl_reported": round(d.realized_reported, 2),
                "trades": d.trades_closed,
                "halted": d.halted,
            },
            rid,
        )
        d.finalized = True

    def _status(self, m: int) -> dict:
        d = self.day
        return {
            "state": "trading" if self._may_enter(max(m, 0)) else "managing",
            "minute": m,
            "run_id": self._rid(),
            "level": self.level(),
            "positions": {s: p.status for s, p in self.pos.items()},
            "entries_today": d.entries if d else 0,
            "halted": bool(d and d.halted),
            "account_clean": self.account_clean,
            "code_sha": self.code,
        }


def _et():
    from zoneinfo import ZoneInfo

    return ZoneInfo("America/New_York")


def _age_s(t: str | None, now: datetime) -> float:
    if not t:
        return 1e9
    try:
        ts = datetime.fromisoformat(t.replace("Z", "+00:00"))
        return max(0.0, (now - ts).total_seconds())
    except ValueError:
        return 1e9


def _num(v):
    v = float(v)
    return None if not np.isfinite(v) else round(v, 6)


def new_idempotency_key() -> str:
    return uuid.uuid4().hex
