"""The live engine: Variant A no-news dip reversal on Alpaca paper, one paper run at a time.

Per completed minute: sync orders/fills -> mark to market -> daily stop / run drawdown -> trailing stops ->
time exit -> (every 5-minute bar) new entries through the risk gate. All prices, sizes, stops and exits come from
these rules and `risk/gate.py`; no LLM is involved (the agent room is off in M1).

Safety:
- Every entry is a bracket order, so the stop and the 2R target live AT THE BROKER and fire even if this
  process (or the laptop) stops.
- On startup, positions the engine doesn't know about block all entries (account not clean); positions it does
  know from an earlier session are closed at the first tick of the next open.
- The run's pot (onboarding amount) caps sizing and buying power, whatever the paper account holds, and so
  does the account's own buying power (the smaller wins).
- Fail closed (S16/S18): unknown open orders or positions, position drift against the broker, three loop errors
  in a row, three entry orders in a row refused by the broker (S74), an event calendar that has run out, or three
  daily stops in ten sessions all stop new entries.
  Nothing here ever adds risk: protection stays at the broker and exits keep being managed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta

import numpy as np

from kaibot.broker.alpaca import CANCEL_WAIT_S
from kaibot.broker.protocol import Broker
from kaibot.broker.shadow_costs import QuoteCtx, sell_fees, shadow_fill_price
from kaibot.engine import shadow  # S14 shadow strategies: never trade, never raise
from kaibot.engine.config import ROOT
from kaibot.engine.events import from_fomc_dates, in_blackout
from kaibot.engine.market import LiveMarket, session_minutes
from kaibot.engine.roomflow import RoomFlow
from kaibot.engine.userconfig import ConfigError, Rules, defaults, tighten
from kaibot.evaluation import gate_report
from kaibot.replay.features import five_minute
from kaibot.replay.sim import half_spread, sell_px
from kaibot.risk.gate import Proposal, RiskPolicy, Side, Snapshot, evaluate
from kaibot.strategies import spy_momentum as spymom

log = logging.getLogger("kaibot.engine")

ATR_MULT = 1.5
TARGET_R = 2.0
TRAIL_ATR = 1.5
FIRST_ENTRY_M = 5  # 09:35
PAUSE_AFTER_LOSSES, PAUSE_MINUTES = 3, 60
MAX_ERROR_STREAK = 3  # consecutive failed loop iterations before the kill switch trips for the session
STOPS_TO_PAUSE, STOPS_WINDOW_DAYS = 3, 14  # 3 daily stops within ~10 sessions pause the run (PRD v2)
LEVEL_NAMES = {"conservative": "Steady", "balanced": "Balanced", "aggressive": "Hunter"}
# S74 (#114). A 403 or 422 on an entry order means the venue refused it: there is no order. One refusal is about one
# candidate (the stop is no longer below the market, the symbol cannot be traded right now). Three in a row with no
# accepted order in between, the same "repeated" as MAX_ERROR_STREAK, is about the account or the order format.
BROKER_REJECT_STATUS = (403, 422)
MAX_REJECT_STREAK = 3
FLATTEN_TRIES = 2  # a close the venue refused is tried once more inside the same flatten; the loop does the rest
# S74 review (#142): one time limit for a whole flatten, retries included, well inside the watchdog's 90 s heartbeat
# staleness window (watchdog.STALE_S). A close is only started while CLOSE_MAX_S is left: the adapter's own
# bounded cancel wait (CANCEL_WAIT_S) plus its reads. What is left when the budget runs out is reported and retried
# by the next loop pass, never dropped. The heartbeat is written between positions (Engine.on_progress).
FLATTEN_BUDGET_S = 30.0
CLOSE_MAX_S = CANCEL_WAIT_S + 3.0
CANCEL_UNCONFIRMED = 409  # the adapter's "cancel not confirmed in time": already waited, so not retried at once


class FlattenIncomplete(RuntimeError):
    """flatten_all tried every position and some are still open. `left` is {symbol: why}; the message names the
    symbols first, so they survive wherever the text is cut (heartbeat, engine_error event, halt reason)."""

    def __init__(self, reason: str, left: dict[str, str]):
        self.reason, self.left = reason, dict(left)
        why = "; ".join(f"{s}: {w}" for s, w in left.items())
        super().__init__(f"flatten ({reason}) incomplete, still open: {', '.join(left)} ({why})")


def _fingerprint() -> str:
    try:
        return gate_report.fingerprint(ROOT, gate_report.Bar.load(ROOT).cfg["fingerprint"])
    except Exception:
        log.exception("strategy fingerprint unavailable")
        return "unknown"


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
    strategy: str = "dip_reversal"

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
    spy_mom_done: bool = False
    last_m: int = -1
    done_m: int = -1  # S74: the last minute whose work completed (last_m: the last one started). See Engine.tick
    scan_m: int = -1  # S74: the last 5-minute bar whose entry scan finished or acted; a retry never scans it again
    rejects: int = 0  # S74: entry orders the broker refused in a row; an accepted order resets it
    events: list = field(default_factory=list)
    rules: Rules | None = None  # M5: the owner's config for this session (None before the open)
    refused: str | None = None  # M5: entries refused for this session only (invalid config); next day re-checks
    scan: dict = field(default_factory=lambda: new_scan())  # dashboard-only counters, never read by decisions

    @property
    def flatten_m(self) -> int:
        return self.n_min - 5  # 15:55 on a normal day, 12:55 on an early close

    @property
    def last_entry_m(self) -> int:
        return self.n_min - 15  # 15:45


class Engine:
    def __init__(
        self,
        broker: Broker,
        market: LiveMarket,
        store,
        policies: dict[str, RiskPolicy],
        fomc: set,
        process: str = "engine",
        now=lambda: datetime.now(UTC),
        calendar_until: date | None = None,
        events: dict | None = None,
        strategies: dict[str, list[str]] | None = None,
        room=None,
        pack_fn=None,
        rules_extra=None,
    ):
        self.broker, self.market, self.store = broker, market, store
        self.policies, self.fomc, self.process, self.now = policies, fomc, process, now
        self.calendar_until = calendar_until  # last date the event calendar covers; None = not checked
        self.events = events if events is not None else from_fomc_dates(fomc)  # {day: [(event, minute)]}
        self.strategies = strategies or {}  # {level: [live strategies]}; missing level -> dip reversal only
        # S34: the agent room (None = rules-only). It can only veto or halve a candidate the rules approved.
        self.roomflow = RoomFlow(self, room, pack_fn) if room is not None else None
        self.rules_extra = rules_extra  # extra rules-only veto (S37 pump risk): (engine, symbol, m) -> str | None
        self.kill: str | None = None  # set by a fail-closed halt; blocks entries until the next session
        # Issue #81: the cross-machine lease (engine/lease.py), set by __main__. None = not enforced (tests,
        # replays). When set, entries need a lease that is provably ours right now.
        self.lease = None
        self.error_streak = 0
        self._drift_seen: dict = {}
        self.run: dict | None = None
        self.day: Day | None = None
        self.pos: dict[str, Pos] = {}
        self.account_clean = True
        self.code = code_sha()
        self.fingerprint = _fingerprint()  # S38: strategy-code hash; trades count toward the gate only on frozen code
        self.peak: float | None = None
        self.on_progress = lambda status: None
        self.monotonic = time.monotonic  # S74: the flatten's time budget (a fake clock in tests)
        self._flatten_slow: set[str] = set()  # S74: symbols the last flatten could not close; tried last next time

    # ================= lifecycle =================
    def startup(self) -> None:
        self.run = self.store.active_run()
        self._restore_positions()
        try:
            self._adopt_orphans()
        except Exception:  # adoption is best effort: if it fails the order stays unknown and blocks entries
            log.exception("order adoption failed")
        self.check_account()
        self.store.event(
            "engine_start",
            {"code_sha": self.code, "fingerprint": self.fingerprint, "run_id": self.run and self.run["id"],
             "positions": list(self.pos)},
            self._rid(),
        )

    def check_account(self) -> None:
        """Positions or open orders the engine doesn't know about block all entries (fail closed).

        An open order counts as known if it is on a symbol the engine holds (its bracket legs and exits). An
        unknown one is e.g. a bracket accepted just before a crash, before its row was written: adopting it
        blindly could double the position, so we stop instead and say so. (One whose client_order_id is exactly
        ours for an approved signal has already been adopted by `_adopt_orphans` at startup.)"""
        broker_pos = self.broker.positions()
        foreign = sorted(set(broker_pos) - set(self.pos))
        orders = sorted({o["symbol"] for o in self.broker.open_orders() if o["symbol"] not in self.pos})
        self.account_clean = not foreign and not orders
        if not self.account_clean:
            msg = {"foreign_positions": foreign, "unknown_open_orders": orders,
                   "action": "entries blocked until the paper account is clean"}
            log.warning("account not clean: %s", msg)
            self.store.event("account_not_clean", msg, self._rid())

    # ================= fail-closed halts =================
    def _halt_entries(self, kind: str, reason: str, detail: dict | None = None) -> None:
        if self.kill:
            return
        self.kill = kind
        log.error("entries halted: %s (%s)", kind, reason)
        self.store.insert(
            "halt",
            {"run_id": self._rid(), "kind": kind, "reason": reason,
             "session": str(self.day.day) if self.day else None},
            returning=False,
        )
        self.store.event("halt", {"kind": kind, "reason": reason, **(detail or {})}, self._rid())

    def on_tick_error(self, err: Exception) -> None:
        """Called by the main loop when an iteration raises. Repeated failures trip the kill switch."""
        self.error_streak += 1
        if self.error_streak >= MAX_ERROR_STREAK:
            self._halt_entries("engine_errors", f"{self.error_streak} loop errors in a row: {str(err)[:120]}")

    def on_tick_ok(self) -> None:
        self.error_streak = 0

    def lease_ok(self) -> bool:
        """Issue #81: no lease object means the lease is not enforced; otherwise it must be verified and fresh."""
        return self.lease is None or bool(self.lease.ok)

    def _check_drift(self) -> None:
        """S18: the engine's book must match the broker's. Any difference stops new entries."""
        broker_pos = self.broker.positions()
        diffs = {}
        for s, p in self.pos.items():
            if p.status != "open" or p.exit_order_id:
                continue
            q = broker_pos.get(s)
            bq = int(float(q["qty"])) if q else 0
            if bq != p.qty:
                diffs[s] = {"engine": p.qty, "broker": bq}
        for s in set(broker_pos) - set(self.pos):
            diffs[s] = {"engine": 0, "broker": int(float(broker_pos[s]["qty"]))}
        # A bracket leg can fill between our order sync and this read; that heals on the next minute's sync.
        # Only a difference seen on two consecutive minutes is drift.
        persistent = {s: v for s, v in diffs.items() if self._drift_seen.get(s) == v}
        self._drift_seen = diffs
        if persistent:
            self._halt_entries("drift", "positions differ from the broker", {"diffs": persistent})

    def _restore_positions(self) -> None:
        self.pos = {}
        if not self.run:
            return
        for t in self.store.open_trades(self.run["id"]):
            f = t.get("features") or {}
            known = {k: f[k] for k in Pos.__dataclass_fields__ if k in f}
            p = Pos(trade_id=t["id"], symbol=t["symbol"], qty=int(float(t["qty"])), **known)
            self.pos[p.symbol] = p

    def _adopt_orphans(self) -> None:
        """S40: a crash between the broker accepting an entry and the engine writing its order/trade rows leaves an
        order the engine doesn't know. If its client_order_id is exactly the one this run gave an approved signal
        (kb-<run>-<signal>-1), it is ours beyond doubt: write the missing rows and manage it. Anything else still
        counts as unknown and blocks entries (check_account)."""
        if not self.run:
            return
        rid = self.run["id"]
        sigs = self.store.select("signal", {"run_id": f"eq.{rid}", "mode": "eq.live", "order": "id.desc",
                                            "limit": "50"})
        if not sigs:
            return
        ids = ",".join(str(r["id"]) for r in sigs)
        traded = {r["signal_id"] for r in self.store.select("trade", {"signal_id": f"in.({ids})"})}
        approved = {g["signal_id"]: g for g in self.store.select("gate_run", {"signal_id": f"in.({ids})"})
                    if g.get("approved")}
        rows = {r["signal_id"]: r for r in self.store.select("order", {"signal_id": f"in.({ids})"})}
        for sig in sigs:
            sid, s = sig["id"], sig["symbol"]
            if sid in traded or sid not in approved or s in self.pos:
                continue
            coid = f"kb-{rid}-{sid}-1"
            o = self.broker.order_by_client_id(coid)
            if not o or o.get("symbol") != s or o.get("side") != "buy" or o.get("status") in (
                    "canceled", "expired", "rejected"):
                continue
            self._adopt(sig, o, coid, rows.get(sid))

    def _adopt(self, sig: dict, o: dict, coid: str, db_order: dict | None) -> None:
        f = sig.get("features") or {}
        ref, stop, target = float(sig["entry"]), float(sig["stop"]), sig.get("target")
        qty = int(float(o["qty"]))
        sigma = f.get("sigma_1m")
        if sigma is None and f.get("sigma") is not None:  # SPY momentum logs the daily sigma
            sigma = float(f["sigma"]) / np.sqrt(390)
        if db_order is None:
            db_order = self.store.insert("order", {
                "signal_id": sig["id"], "client_order_id": coid, "venue": "alpaca_paper",
                "legs": {"stop": round(stop, 2), "target": None if target is None else round(float(target), 2),
                         "qty": qty},
                "status": o.get("status", "new"), "submitted_at": sig.get("ts") or self.now()})
        p = Pos(trade_id=0, symbol=sig["symbol"], qty=qty, coid=coid, order_id=o["id"], ref=ref, risk_ps=ref - stop,
                atr=float(f.get("atr5") or 0), sigma=float(sigma or 0), stop=stop,
                target=float(target) if target is not None else 0.0, db_order_id=db_order["id"],
                entry_ts=_iso(sig.get("ts")) or self.now().isoformat(), strategy=sig.get("strategy") or "dip_reversal")
        trade = self.store.insert("trade", {
            "run_id": self._rid(), "config_version": self._cfg_version(), "signal_id": sig["id"],
            "strategy": p.strategy, "symbol": p.symbol,
            "entry_ts": p.entry_ts, "qty": qty, "entry_px": ref, "stop_px": stop, "target_px": target,
            "risk_usd": p.risk_ps * qty, "features": {**f, **p.features(), "adopted": True}})
        p.trade_id = trade["id"]
        self.pos[p.symbol] = p  # pending: the next order sync books the fill and aligns the legs
        log.warning("adopted %s order %s (%s) left by a crash", p.symbol, coid, o.get("status"))
        self.store.event("order_adopted", {"symbol": p.symbol, "client_order_id": coid,
                                           "broker_status": o.get("status")}, self._rid())

    def _restore_session(self, d: Day) -> None:
        """S40: a restart mid-session rebuilds today's risk state from the store instead of starting from zero:
        entries placed, realized P&L, the loss streak / pause and a daily stop already hit all survive."""
        trades = self.store.select("trade", {"run_id": f"eq.{d.run_id}", "order": "id.desc", "limit": "200"})
        closed = []
        for t in trades:
            if _et_date(t.get("entry_ts")) == d.day:
                d.entries += 1
            if _et_date(t.get("exit_ts")) == d.day and t.get("pnl_usd") is not None:
                closed.append(t)
        for t in sorted(closed, key=lambda t: _iso(t["exit_ts"])):
            f, pnl = t.get("features") or {}, float(t["pnl_usd"])
            d.realized_venue += float(f.get("pnl_venue", pnl))
            d.realized_shadow += float(f.get("pnl_shadow", pnl))
            d.realized_reported += pnl
            d.trades_closed += 1
            d.losses_in_row = d.losses_in_row + 1 if pnl < 0 else 0
            pl, pm = self._pause_rule()
            if d.losses_in_row >= pl:
                exit_m = int((_parse_ts(t["exit_ts"]) - d.open_utc).total_seconds() // 60)
                d.pause_until, d.losses_in_row = exit_m + pm, 0
        halts = self.store.select("halt", {"run_id": f"eq.{d.run_id}", "kind": "eq.daily_stop",
                                           "session": f"eq.{d.day}"})
        d.halted = bool(halts)
        if d.entries or closed or d.halted:
            self.store.event("session_restored", {"day": str(d.day), "entries": d.entries, "closed": len(closed),
                                                  "realized": round(d.realized_reported, 2), "halted": d.halted},
                             self._rid())

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
        d = self.day
        if d is not None and d.rules is not None:
            return d.rules.policy  # M5: the level tightened by the owner's config, fixed at the session open
        return self.policies[self.level()]

    def _user_veto(self, r: Rules, s: str, ref: float, vix: float | None) -> str | None:
        """M5 tighten-only filters from the owner's config (the level's own gates run regardless)."""
        if vix is not None and np.isfinite(vix) and vix > r.vix_max:
            return f"VIX above your limit ({r.vix_max:g})"
        if ref < r.min_price_usd:
            return f"price below your minimum (${r.min_price_usd:g})"
        q = (self._quotes or {}).get(s) or {}
        bid, ask = float(q.get("bp") or 0), float(q.get("ap") or 0)
        if 0 < bid <= ask and (ask - bid) / ((ask + bid) / 2) * 1e4 > r.max_spread_bp:
            return f"spread above your limit ({r.max_spread_bp:g} bp)"
        return None

    def _load_rules(self, d: Day) -> None:
        """M5 S43: the owner's latest applied config created before this session's open, tightened onto the level.
        Invalid -> entries refused for the session (fail closed); management, stops and the flatten still run."""
        lv_pol, lv_strats = self.policies[self.level()], self.strategies.get(self.level(), ["dip_reversal"])
        d.rules = defaults(lv_pol, lv_strats)
        try:
            rows = [c for c in self.store.select("config", {"run_id": f"eq.{d.run_id}", "status": "eq.applied"})
                    if _parse_ts(c["created_at"]) < d.open_utc]
        except Exception as e:
            self._refuse_session(f"config unreadable: {type(e).__name__}")
            return
        if not rows:
            self.market.min_adv = d.rules.min_adv_usd
            return
        c = max(rows, key=lambda c: int(c["version"]))
        uni, themes = self._universe_and_themes()
        try:
            d.rules = tighten(lv_pol, lv_strats, c.get("body") or {}, version=int(c["version"]),
                              universe=uni, themes=themes)
        except (ConfigError, ValueError) as e:
            d.rules = Rules(int(c["version"]), lv_pol, tuple(lv_strats))  # keep the version for the record
            self._refuse_session(f"config v{c['version']} rejected: {e}")
            return
        self.market.min_adv = d.rules.min_adv_usd
        self.store.event("config_applied", {"day": str(d.day), "version": d.rules.version,
                                            "config_sha": d.rules.config_sha}, self._rid())

    def _refuse_session(self, reason: str) -> None:
        """Entries off for this session only; stops, management and the 15:55 flatten keep running."""
        d = self.day
        d.refused = reason[:300]
        log.error("session refused: %s", reason)
        self.store.event("engine_error", {"kind": "config", "reason": d.refused}, self._rid())
        self.store.insert("halt", {"run_id": self._rid(), "kind": "config", "reason": d.refused,
                                   "session": str(d.day)}, returning=False)

    def _universe_and_themes(self) -> tuple[frozenset[str], dict[str, list[str]]]:
        if not hasattr(self, "_uni_cache"):
            import yaml

            u = yaml.safe_load((ROOT / "config" / "universe_seed.yaml").read_text())
            self._uni_cache = (frozenset(x for v in u["sectors"].values() for x in v),
                               {k: list(v["symbols"]) for k, v in (u.get("themes") or {}).items()})
        return self._uni_cache

    def _cfg_version(self) -> int:
        return self.day.rules.version if self.day and self.day.rules else 0

    def _pause_rule(self) -> tuple[int, int]:
        r = self.day.rules if self.day else None
        return (r.pause_losses, r.pause_minutes) if r else (PAUSE_AFTER_LOSSES, PAUSE_MINUTES)

    # ================= per tick =================
    def tick(self) -> dict:
        now = self.now()
        clk = self.broker.clock()
        if not clk["is_open"]:
            if self.day and not self.day.finalized:
                self._sync_orders()
                self._finalize_day()
            self._maybe_prepare(clk, now)
            return {"state": "market_closed", "next_open": clk.get("next_open"), "account_clean": self.account_clean}
        if self.run is None and not self.pos:
            return {"state": "idle", "note": "no paper run: start one from the dashboard",
                    "account_clean": self.account_clean}
        if self.run is not None and (self.day is None or self.day.day != now.astimezone(_et()).date()):
            self._start_day(now)
        d = self.day
        if d is None:
            return {"state": "idle", "account_clean": self.account_clean}
        self.market.update(now)
        m = self.market.last_complete_minute(now)
        if m < 0 or m == d.done_m:
            return self._status(m)  # this minute's work is complete: it is never run twice
        # S74 (#114, audit D6): `last_m` says the minute was started, `done_m` that it was finished. A step below
        # that raises leaves `done_m` behind, so the main loop's retry (about 10 s later) runs the minute again
        # instead of getting "already done" and resetting the error streak. Every failed attempt therefore counts
        # (on_tick_error) and a failure that recurs trips the kill switch. Each step is safe to run again, except
        # an entry scan that has acted: `scan_m` (set below and in _entries) keeps a retry from repeating it.
        d.last_m = m
        self._sync_orders()
        self._check_drift()
        if any(p.carried for p in self.pos.values()):
            for p in [p for p in self.pos.values() if p.carried and p.status in ("open", "pending")]:
                self._exit(p, "carried_over")
        eq, unreal = self.equity_now()
        self._risk_halts(m, eq)
        self._trail_stops(m)
        if m >= d.flatten_m and not d.flattened:
            self.flatten_all("time")
            d.flattened = True
        if self.roomflow is not None and self.roomflow.jobs:
            self._quotes = self.broker_quotes(sorted(self.roomflow.jobs))
            self._bp_read, self._bp_added = self._broker_buying_power(), 0.0
            self.roomflow.collect(m)
        if (m - 4) % 5 == 0 and d.scan_m != m and self._may_enter(m):
            self._entries(m)
            d.scan_m = m
        if m == spymom.ENTRY_M and not d.spy_mom_done and self._runs("spy_late_momentum") and d.n_min == 390:
            d.spy_mom_done = True  # one decision a day, whatever happens below
            if self._may_enter(m):
                self._spy_momentum(m)
        if spymom.EXIT_SUBMIT_M <= m < spymom.MOC_CUTOFF_M:
            self._moc_exits()
        self._ledger(eq)
        shadow.on_minute(self, m)  # S14: shadow ORB / news continuation (signal rows only; exceptions contained)
        d.done_m = m
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
        self._load_rules(self.day)
        self._restore_session(self.day)
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
        if self.kill:
            self.store.event("halt_cleared", {"kind": self.kill, "day": str(today)}, self._rid())
        self.kill, self.error_streak = None, 0
        self._check_recent_stops(today)
        if self.calendar_until is not None and today > self.calendar_until:
            self.store.event("calendar_stale", {"day": str(today), "covers_until": str(self.calendar_until),
                                                "action": "update config/macro_calendar.yaml"}, self._rid())
            self._halt_entries("calendar_stale", f"event calendar ends {self.calendar_until}")
        self.check_account()
        self.store.event(
            "session_start",
            {"day": str(today), "start_equity": start_eq, "level": self.level(), "minutes": n_min},
            self._rid(),
        )

    def _check_recent_stops(self, today: date) -> None:
        rows = self.store.select("halt", {"run_id": f"eq.{self.run['id']}", "kind": "eq.daily_stop"})
        lo = today - timedelta(days=STOPS_WINDOW_DAYS)
        recent = sorted({r["session"] for r in rows if r.get("session") and lo <= date.fromisoformat(r["session"])})
        if len(recent) >= STOPS_TO_PAUSE and self.run["status"] == "running":
            reason = f"{len(recent)} daily stops since {lo}: paused until you resume"
            self.store.insert("halt", {"run_id": self._rid(), "kind": "three_stops", "reason": reason,
                                       "session": str(today)}, returning=False)
            self._set_run_status("paused", reason)
            self.store.event("run_paused", {"reason": reason, "sessions": recent}, self._rid())

    def equity_now(self) -> tuple[float, float]:
        d = self.day
        unreal = 0.0
        for p in self.pos.values():
            if p.status == "open" and p.fill is not None:
                last = self.market.last_close(p.symbol) or p.fill
                unreal += (last - p.fill) * p.qty
        return d.start_equity + d.realized_reported + unreal, unreal

    def _risk_halts(self, m: int, eq: float) -> None:
        if self.run is None:
            # S74b (#137): the run has ended (run drawdown) and its flatten is out; until the closes are booked the
            # engine only manages them. Nothing to halt any more: a second pass wrote the halt again and raised.
            return
        d, pol = self.day, self.policy()
        if not d.halted and eq <= d.start_equity * (1 - pol.max_daily_loss_pct / 100):
            self.flatten_all("daily_stop")
            d.halted = True
            self.store.insert(
                "halt",
                {"run_id": self._rid(), "kind": "daily_stop", "reason": f"day P&L reached -{pol.max_daily_loss_pct}%",
                 "session": str(d.day)},
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
        if self.run is None or self.run["status"] != "running" or not self.account_clean or self.kill:
            return False
        if not self.lease_ok():  # fail closed: the lease could not be renewed, so we may not be the only engine
            return False
        if d.halted or d.flattened or d.refused or m < d.pause_until:
            return False
        r = d.rules
        first, last = (max(FIRST_ENTRY_M, r.first_entry_m), min(d.last_entry_m, r.last_entry_m)) if r else (
            FIRST_ENTRY_M, d.last_entry_m)
        if not (first <= m + 1 <= last):
            return False
        return in_blackout(self.events, d.day, m, r.event_window_min if r else None) is None

    def _runs(self, strategy: str) -> bool:
        r = self.day.rules if self.day else None
        if r is not None:
            return strategy in r.strategies
        return strategy in self.strategies.get(self.level(), ["dip_reversal"])

    # ================= SPY late momentum (S13) =================
    def _spy_momentum(self, m: int) -> None:
        spy = self.market.spy
        if spy in self.pos:
            return
        sd = self.market.symbol_days(spy, m)
        if sd is None or not len(sd.dates) or sd.dates[-1] != np.datetime64(self.day.day, "D"):
            return
        today = len(sd.dates) - 1
        ret, sigma = spymom.inputs_from_days(sd, today)
        z = ret / sigma if sigma and np.isfinite(sigma) and sigma > 0 else float("nan")
        atr = five_minute(sd.o[today:], sd.h[today:], sd.l[today:], sd.c[today:])[0][0, (m - 4) // 5]
        ref = float(sd.c[today, m])
        go = spymom.should_buy(spymom.MomentumInputs(ret, sigma if np.isfinite(sigma) else 0.0, False, False))
        if not (np.isfinite(atr) and atr > 0):
            go = False
        off, vix = self.market.regime_at(m)  # S15: the regime gate (incl. VIX > 35 crisis) applies here too
        r = self.day.rules
        if not off and r is not None and np.isfinite(vix) and vix > r.vix_max:
            off = True  # M5: the owner's VIX limit (tighter than the crisis gate)
        stop = ref - spymom.STOP_ATR * float(atr if np.isfinite(atr) else 0)
        feats = {"ret_close_to_10": _num(ret), "sigma": _num(sigma), "z": _num(z), "atr5": _num(atr), "m": m}
        self._decide(spy, "spy_late_momentum", ref, stop, None, feats,
                     ["SPY +0.5 sigma by 10:00", "last-30-minute momentum"],
                     "regime off" if off else (None if go else "momentum below threshold"),
                     sigma_1m=float(sigma or 0) / np.sqrt(390), atr=float(atr if np.isfinite(atr) else 0), vix=vix)

    def _moc_exits(self) -> None:
        """From 15:45, close momentum positions market-on-close. Cancel the stop first (it reserves the shares);
        if the MOC is refused, the position stays and the 15:55 market flatten closes it."""
        for p in list(self.pos.values()):
            if p.strategy != "spy_late_momentum" or p.status != "open" or p.exit_order_id:
                continue
            if p.stop_order_id:
                self.broker.cancel_order(p.stop_order_id)
                p.stop_order_id = None
            try:
                o = self.broker.submit_moc_sell(f"{p.coid}-moc", p.symbol, p.qty)
                p.exit_order_id, p.exit_reason = o["id"], "moc"
            except Exception as e:
                log.warning("MOC refused for %s (%s); the 15:55 flatten will close it", p.symbol, e)
                self.store.event("moc_refused", {"symbol": p.symbol, "error": str(e)[:200]}, self._rid())
                p.exit_reason = None

    # ================= entries =================
    def _entries(self, m: int) -> None:
        sc = self.day.scan
        off, vix = self.market.regime_at(m)
        if off:
            if (m + 1) % 5 == 0:
                sc["regime_off"] += 1
            return
        cands = self.market.candidates_at(m)
        note_scan(sc, m, getattr(self.market, "last_checked", 0), getattr(self.market, "last_near", []), len(cands))
        if not len(cands) or not self._runs("dip_reversal"):
            return
        r = self.day.rules
        if r is not None and r.mode != "all":  # M5 focus: Only filters; Prefer ranks focus names first
            cands = cands[[r.tradable(x) for x in cands["symbol"]]]
            if not len(cands):
                return
            cands = cands.assign(_rank=[r.rank(x) for x in cands["symbol"]]).sort_values(
                ["_rank", "z"], kind="stable").drop(columns="_rank")
        self._quotes = self.broker_quotes(list(cands["symbol"]))
        self._bp_read, self._bp_added = self._broker_buying_power(), 0.0
        decision = self.now()
        for _, c in cands.iterrows():
            s = c["symbol"]
            if s in self.pos:
                continue
            news = self.market.data.news_count(s, decision - timedelta(hours=24), decision)
            ref = float(c["price"])
            stop = ref - ATR_MULT * float(c["atr5"])
            target = ref + TARGET_R * (ref - stop)
            feats = {k: _num(c[k]) for k in ("z", "resid_z", "turnover", "atr5", "vwap", "sigma_1m")}
            feats.update({"news_24h": news, "vix": vix, "m": m})
            reasons = ["no-news dip", "5-min higher low", "sector-adjusted"]
            veto = "company news in 24 h" if news else None
            if veto is None and r is not None:
                veto = self._user_veto(r, s, ref, vix)
            if veto is None and self.rules_extra is not None:
                try:
                    veto = self.rules_extra(self, s, m)
                except Exception:
                    log.exception("extra rules veto failed for %s: skipping the candidate", s)
                    veto = "extra rules unavailable"
            if veto is not None:
                sc["vetoed"][veto] = sc["vetoed"].get(veto, 0) + 1
            # S74: from here on this bar's scan acts (a room session, a signal row, an order), so a retry of the
            # minute must not scan it again: a second decision would write a new signal row and, with it, a new
            # client order id (kb-<run>-<signal>-1) that the venue's duplicate check cannot recognise. A scan
            # that failed before this line (features, quotes, the news lookup) has done nothing and is repeated.
            self.day.scan_m = m
            rf = self.roomflow
            if veto is None and rf is not None:
                mode = rf.mode(m)
                if mode == "ok":
                    sc["to_room"] += 1
                    rf.submit(s, c, ref, stop, target, feats, reasons, m)
                    continue
                if mode == "no_entries":
                    return
            sig_id, full_qty = self._decide(s, "dip_reversal", ref, stop, target, feats, reasons, veto,
                                            sigma_1m=float(c["sigma_1m"]), atr=float(c["atr5"]), vix=vix,
                                            fresh_reads=False)
            if veto is None and rf is not None:
                rf.record_skipped(sig_id, s, m, ref, ref - stop, float(c["atr5"]), float(c["sigma_1m"]), full_qty,
                                  "daily LLM budget reached: rules-only")
            if self.day.entries >= self.policy().max_entries_per_day:
                break

    def _decide(self, s, strategy, ref, stop, target, feats, reasons, veto, sigma_1m, atr, vix=None,
                fresh_reads=True, size_mult: float = 1.0) -> tuple[int | None, int]:
        """Gate one proposal, log the signal and the gate run (reason codes, S12), and place it if approved.
        size_mult: 0.5 when the room downgrades. Returns (signal id, the gate's full-size qty) for the A/B."""
        pol, d = self.policy(), self.day
        decision = self.now()
        if fresh_reads:  # single decisions read quotes and buying power themselves
            self._quotes = self.broker_quotes([s])
            self._bp_read, self._bp_added = self._broker_buying_power(), 0.0
        if vix is None:
            vix = self.market.regime_at(int(feats.get("m", 0)))[1]
        q = self._quotes.get(s) or {}
        q_age = _age_s(q.get("t"), decision)
        eq, _ = self.equity_now()
        live = [p for p in self.pos.values() if p.status in ("pending", "open")]
        exposure = sum((p.fill or p.ref) * p.qty for p in live)
        bp = self._bp_read
        snap = Snapshot(
            equity=eq,
            day_start_equity=d.start_equity,
            open_positions=len(live),
            symbol_exposure=0.0,
            open_risk=sum(p.risk_ps * p.qty for p in live),
            quote_age_s=q_age,
            kill_switch=bool(self.kill) or not self.lease_ok(),  # second lock on the same door (#81)
            reconciled=self.account_clean,
            market_open=True,
            data_ok=(self.market.last_bar_minute(s) or -99) >= int(feats.get("m", 0)) - 2,
            in_entry_window=True,
            entries_today=d.entries,
            run_peak_equity=self.peak,
            # the account's figure already nets its open positions; subtract what this loop has added since
            buying_power=eq - exposure if bp is None else min(eq - exposure, bp - self._bp_added),
        )
        feats = {**feats, "quote_age_s": q_age, "vix": vix}
        v = evaluate(Proposal(s, Side.BUY, ref, stop, target), snap, pol)
        full_qty = int(np.floor(v.qty * (0.5 if vix > 25 else 1.0))) if v.approved else 0
        qty = int(np.floor(full_qty * size_mult)) if not veto else 0
        sig = self.store.insert(
            "signal",
            {
                "run_id": self._rid(),
                "strategy": strategy,
                "mode": "live",
                "symbol": s,
                "ts": decision,
                "features_hash": hashlib.sha256(json.dumps(feats, sort_keys=True).encode()).hexdigest()[:16],
                "features": feats,
                "entry": ref,
                "stop": stop,
                "target": target,
                "reasons": reasons,
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
            return sig["id"], full_qty
        if not self._open(s, qty, ref, stop, target, ref - stop, atr, sigma_1m, q, sig["id"], feats, strategy):
            return sig["id"], full_qty  # S74: the broker refused it (now on the signal); no entry, the scan goes on
        self._bp_added += qty * ref
        d.entries += 1
        return sig["id"], full_qty

    def _broker_buying_power(self) -> float | None:
        """The paper account's own buying power; None if unknown (then the pot alone caps it)."""
        try:
            bp = self.broker.account().get("buying_power")
            return float(bp) if bp is not None else None
        except Exception:
            log.exception("account read failed")
            return 0.0  # fail closed: no buying power if we can't read it

    def broker_quotes(self, symbols: list[str]) -> dict:
        try:
            return self.market.data.latest_quotes(symbols)
        except Exception:
            log.exception("quotes failed")
            return {}

    def _open(self, s, qty, ref, stop, target, risk_ps, atr, sigma, quote, signal_id, feats,
              strategy: str = "dip_reversal") -> bool:
        """Send the entry and write its order and trade rows. False: the broker refused the order (S74)."""
        coid = f"kb-{self._rid()}-{signal_id}-1"
        bid, ask = float(quote.get("bp") or 0), float(quote.get("ap") or 0)
        if not (0 < bid <= ask):
            hs = half_spread(ref)
            bid, ask = ref - hs, ref + hs
        shadow_entry = shadow_fill_price("buy", qty, QuoteCtx(bid, ask, sigma, max(self.market.bar_volume(s), 1)))
        try:
            if target is None:  # exit timed by the engine (MOC): stop only, still held at the broker
                order = self.broker.submit_oto_stop(coid, s, qty, round(stop, 2))
            else:
                order = self.broker.submit_bracket(coid, s, qty, round(stop, 2), round(target, 2))
        except Exception as e:
            if not _order_refused(e):
                raise  # network error, 5xx, anything unknown: the order may be at the venue. A loop error, as before
            if self._broker_rejected(s, qty, coid, signal_id, feats, e):
                raise  # it keeps happening: a loop error again, and entries are halted
            return False
        self.day.rejects = 0
        db_order = self.store.insert(
            "order",
            {
                "signal_id": signal_id,
                "client_order_id": coid,
                "venue": "alpaca_paper",
                "legs": {"stop": round(stop, 2), "target": None if target is None else round(target, 2), "qty": qty},
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
            target=target if target is not None else 0.0,
            shadow_entry=shadow_entry,
            db_order_id=db_order["id"],
            entry_ts=self.now().isoformat(),
            strategy=strategy,
        )
        trade = self.store.insert(
            "trade",
            {
                "run_id": self._rid(),
                "config_version": self._cfg_version(),
                "signal_id": signal_id,
                "strategy": strategy,
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
        return True

    def _broker_rejected(self, s: str, qty: int, coid: str, signal_id, feats: dict, err: Exception) -> bool:
        """S74 (#114, audit D4): the venue refused this entry (403 / 422), so no order exists. The signal was
        written as approved a moment ago: say on it that the broker rejected it and why, and take the approval
        back, so nothing downstream (the tape, "setups taken", order adoption at the next start) counts it as an
        entry. True when it was the MAX_REJECT_STREAK-th refusal in a row: entries halt for the session and the
        caller raises it as a loop error again. The count is per session; an accepted order resets it."""
        d = self.day
        d.rejects += 1
        status, reason = getattr(err, "status", None), _reject_reason(err)
        log.warning("broker rejected the entry for %s (%s): %s", s, status, reason)
        self.store.update("signal", {"id": signal_id}, {
            "rules_veto": f"broker rejected: {reason}",
            "features": {**feats, "broker_reject": {"status": status, "reason": reason, "qty": qty,
                                                    "client_order_id": coid}}})
        self.store.update("gate_run", {"signal_id": signal_id}, {"approved": False, "qty": 0})
        if d.rejects < MAX_REJECT_STREAK:
            return False
        self._halt_entries("broker_rejects", f"{d.rejects} entry orders in a row rejected by the broker: {reason}")
        return True

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
        p.target = p.fill + TARGET_R * p.risk_ps if p.strategy == "dip_reversal" else 0.0
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
            pl, pm = self._pause_rule()
            if d.losses_in_row >= pl:
                d.pause_until, d.losses_in_row = d.last_m + pm, 0
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
            if p.status != "open" or p.exit_order_id or p.strategy != "dip_reversal":
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
        """Close every position, each one on its own (S74, #114, audit D7), inside one time limit.

        A close that fails no longer stops the loop: the other positions still go out. A close the venue refused
        (4xx, typically while a cancel is still pending) is tried again at once, FLATTEN_TRIES in all; not an
        unreachable venue (the adapter has already retried it for about 30 s) nor a cancel the adapter already
        waited CANCEL_WAIT_S for (409). The whole flatten, retries included, starts no close once less than
        CLOSE_MAX_S of FLATTEN_BUDGET_S is left, and writes the heartbeat before each close, so a slow venue
        cannot make the heartbeat go stale (watchdog, pg_cron dead-man). What is still open after that is logged
        and raised as FlattenIncomplete, once every position was tried or the budget ran out. So no caller
        carries on as if the book were flat (the session is not marked flattened, the halt is not written, the run
        is not ended), and the caller's own retry comes back for the rest: the main loop runs the tick again about
        10 s later, `_exit` skips the positions that already have an exit order, and the positions this flatten
        could not close are tried after the others, so a few stuck ones cannot use up every budget."""
        t0 = self.monotonic()
        failed: dict[str, Exception | str] = {}
        todo = sorted(self.pos.values(), key=lambda p: p.symbol in self._flatten_slow)  # stable: book order kept
        for _ in range(FLATTEN_TRIES):
            again = []
            for k, p in enumerate(todo):
                if self.monotonic() - t0 > FLATTEN_BUDGET_S - CLOSE_MAX_S:
                    for q in todo[k:]:  # not attempted: reported, and the next loop pass starts with the others
                        failed.setdefault(q.symbol, f"not tried: flatten time budget ({FLATTEN_BUDGET_S:g} s) used")
                    again = []
                    break
                self._flatten_beat(reason, p.symbol, len(todo) - k)
                try:
                    self._exit(p, reason)
                    failed.pop(p.symbol, None)
                except Exception as e:  # one failed close must not stop the others
                    log.warning("flatten (%s): closing %s failed: %s: %s", reason, p.symbol, type(e).__name__, e)
                    failed[p.symbol] = e
                    if _refused(e) and getattr(e, "status", None) != CANCEL_UNCONFIRMED:
                        again.append(p)
            todo = again
            if not todo:
                break
        self._flatten_slow = {s for s, e in failed.items() if isinstance(e, Exception)}  # tried and failed
        if failed:
            left = {s: (f"{type(e).__name__}: {e}" if isinstance(e, Exception) else e)[:160] for s, e in failed.items()}
            log.error("flatten (%s) incomplete after %.1f s, still open: %s", reason, self.monotonic() - t0, left)
            cause = next((e for e in failed.values() if isinstance(e, Exception)), None)
            raise FlattenIncomplete(reason, left) from cause

    def _flatten_beat(self, reason: str, symbol: str, left: int) -> None:
        """S74: the heartbeat between the closes of a flatten (the main loop writes it only after the tick)."""
        try:
            self.on_progress({"state": "flattening", "reason": reason, "closing": symbol, "left": left})
        except Exception:  # a heartbeat that fails must not stop the flatten
            log.exception("flatten heartbeat failed")

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
        if self.roomflow is not None:
            try:
                self.roomflow.finalize()  # S36: rules-only counterfactuals for today's room sessions
            except Exception:
                log.exception("room finalize failed")
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
                    "config_version": d.rules.version if d.rules else 0,
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
        shadow.on_day_end(self)  # S14: counterfactual P&L of today's shadow signals (exceptions contained)
        gate_report.on_day_end(self, ROOT)  # S38: random baseline + append-only gate report (exceptions contained)
        d.finalized = True

    def _status(self, m: int) -> dict:
        d = self.day
        st = {
            "state": "trading" if self._may_enter(max(m, 0)) else "managing",
            "minute": m,
            "run_id": self._rid(),
            "level": self.level(),
            "positions": {s: p.status for s, p in self.pos.items()},
            "entries_today": d.entries if d else 0,
            "halted": bool(d and d.halted),
            "account_clean": self.account_clean,
            "code_sha": self.code,
            "scan": dict(d.scan) if d else None,
        }
        if self.run is None and d is not None and d.run_id:
            st["run_ended"] = d.run_id  # S74b (#137): today's run has ended; the engine only winds it down
        return st


def new_scan() -> dict:
    return {"bars": 0, "checks": 0, "regime_off": 0, "setups": 0, "near": 0, "vetoed": {}, "to_room": 0,
            "recent_near": []}


def note_scan(sc: dict, m: int, checked: int, near: list, setups: int) -> None:
    """Dashboard counters for one completed 5-minute bar: symbols checked, setups, near-misses (last 5 kept)."""
    if (m + 1) % 5 != 0:
        return
    sc["bars"] += 1
    sc["checks"] += int(checked)
    sc["setups"] += int(setups)
    sc["near"] += len(near)
    sc["recent_near"] = (sc["recent_near"] + list(near))[-5:]


def _refused(err: Exception) -> bool:
    """S74: the venue answered and said no (HTTP 4xx), as opposed to not answering (network error, 5xx: the
    adapter raises those without a 4xx status after its own retries)."""
    status = getattr(err, "status", None)
    return isinstance(status, int) and 400 <= status < 500


def _order_refused(err: Exception) -> bool:
    """S74: a 403 or 422 on an entry order: the venue refused it and no order exists. Told by the status the
    adapter's error carries, so this does not depend on which adapter raised it. One 422 is not a refusal: "client
    order id must be unique" says an order with our id IS at the venue (an earlier attempt got through and the
    adapter could not read it back). That stays a loop error, and the next start adopts the order."""
    body = str(getattr(err, "body", None) or err)
    return getattr(err, "status", None) in BROKER_REJECT_STATUS and "client_order_id" not in body


def _reject_reason(err: Exception) -> str:
    """S74: the venue's own words for a refused order. Alpaca answers {"code": ..., "message": ...}."""
    body = str(getattr(err, "body", None) or err)
    try:
        msg = json.loads(body).get("message")
    except (ValueError, AttributeError):
        msg = None
    return str(msg or body)[:160]


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


def _parse_ts(v) -> datetime:
    return v if isinstance(v, datetime) else datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def _iso(v) -> str | None:
    return None if v is None else _parse_ts(v).astimezone(UTC).isoformat()


def _et_date(v) -> date | None:
    return None if v is None else _parse_ts(v).astimezone(_et()).date()


def _num(v):
    v = float(v)
    return None if not np.isfinite(v) else round(v, 6)


def new_idempotency_key() -> str:
    return uuid.uuid4().hex
