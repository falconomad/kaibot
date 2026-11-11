"""The agent room inside the live engine (stories S34, S36), asynchronous so the minute loop never waits on it.

1. A dip candidate passes every deterministic rule (incl. the 24 h news veto and pump risk) at minute m.
2. submit(): the room deliberates in a worker thread (evidence pack + Proposer/Critic/Judge, <= 45 s).
3. collect(): on each later tick, finished sessions go through the normal decision path: the risk gate with a
   fresh quote, at full size (allow), half size (downgrade) or not at all (veto, timeout, invalid, error).
   A session with no verdict after two 5-minute cycles is dropped: no trade.
4. Every session is stored in room_session with the rules-only arm ("allow": the rules alone would have traded
   it). finalize() adds the counterfactual: what that full-size rules-only trade would have made, from the same
   fill and exit model as the replay. Veto lift = how much the room's vetoes and downgrades saved or cost.

Budget: if the daily soft cap is reached, the room is skipped and the rules decide alone (status "skipped").
At the monthly hard cap, the engine opens nothing new. Exits are never affected.
"""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import timedelta

import numpy as np

from kaibot.broker.shadow_costs import sell_fees
from kaibot.replay.sim import buy_px, first_exit_trail, sell_px

log = logging.getLogger("kaibot.room")
MAX_WAIT_MIN = 10  # two 5-minute cycles
STATE_EVERY_MIN = 5


@dataclass
class Job:
    symbol: str
    m: int
    cand: dict
    ref: float
    stop: float
    target: float
    atr: float
    sigma_1m: float
    feats: dict
    reasons: list
    future: Future
    pack: dict = field(default_factory=dict)


class RoomFlow:
    def __init__(self, engine, room, pack_fn=None, workers: int = 3):
        self.e, self.room = engine, room
        self.pack_fn = pack_fn or default_pack
        self.ex = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="room")
        self.jobs: dict[str, Job] = {}
        self.sessions: list[dict] = []  # today's room candidates, for the day-end counterfactual
        self._state, self._state_m = "ok", -99

    # ---------- budget ----------
    def mode(self, m: int) -> str:
        """ok | rules_only | no_entries (read from Postgres at most every 5 minutes; fails closed to rules_only)."""
        if m - self._state_m >= STATE_EVERY_MIN:
            self._state = (self.room.gw.state() or {}).get("state", "rules_only")
            self._state_m = m
            if self._state == "no_entries":
                self.e._halt_entries("llm_budget", "monthly LLM budget reached: no new entries")
        return self._state

    # ---------- 1-2: submit ----------
    def submit(self, s: str, c, ref, stop, target, feats: dict, reasons: list, m: int) -> None:
        if s in self.jobs:
            return
        d = self.e.day
        cid = f"{self.e._rid()}-{d.day}-{s}-{m}"
        cand = {"symbol": s, "sector_etf": c.get("etf") if hasattr(c, "get") else None, "minute": m,
                **{k: v for k, v in feats.items() if k not in ("quote_age_s",)}}
        as_of = self.e.now()
        etf = cand.get("sector_etf")

        def run():
            pack = self.pack_fn(self.e, s, etf, as_of, feats)
            return pack, self.room.deliberate(cid, cand, pack)

        self.jobs[s] = Job(s, m, cand, ref, stop, target, float(feats.get("atr5") or 0),
                           float(feats.get("sigma_1m") or 0), feats, reasons, self.ex.submit(run))

    # ---------- 3: collect ----------
    def collect(self, m: int) -> None:
        for s, j in list(self.jobs.items()):
            expired = m - j.m > MAX_WAIT_MIN
            if not j.future.done() and not expired:
                continue
            del self.jobs[s]
            pack, out = {}, None
            if j.future.done():
                try:
                    pack, out = j.future.result()
                except Exception as ex:  # a crash in the room is a no-trade, never a crash of the engine
                    log.exception("room session for %s failed", s)
                    out = _error(str(ex))
            if out is None:
                out = _error("no verdict after two cycles")
                out.status = "timeout"
            mult = out.trade_multiplier
            veto = None
            if mult <= 0:
                veto = {"timeout": "room timeout", "invalid": "room output invalid", "error": "room error"}.get(
                    out.status, f"room veto: {(out.judge or {}).get('cause', 'unknown')}")
            elif not self.e._may_enter(m) or s in self.e.pos:
                veto = "entry window closed" if s not in self.e.pos else "already holding"
            ref = self.e.market.last_close(s) or j.ref  # re-anchor at the current price, same stop distance
            dist, tdist = j.ref - j.stop, j.target - j.ref
            reasons = [*j.reasons, f"room: {out.verdict or out.status}"]
            feats = {**j.feats, "room_submitted_m": j.m, "room_latency_ms": out.latency_ms}
            sig_id, full_qty = self.e._decide(s, "dip_reversal", ref, ref - dist, ref + tdist, feats, reasons, veto,
                                              sigma_1m=j.sigma_1m, atr=j.atr, size_mult=mult)
            self._store(sig_id, out, pack)
            self.sessions.append({"signal_id": sig_id, "symbol": s, "m": m, "ref": ref, "risk_ps": dist,
                                  "atr": j.atr, "sigma_1m": j.sigma_1m, "qty": full_qty})

    def record_skipped(self, sig_id, s: str, m: int, ref: float, risk_ps: float, atr: float, sigma_1m: float,
                       full_qty: int, reason: str) -> None:
        """The rules decided alone (budget reached): still one room_session row, so the A/B sees it."""
        self.e.store.insert("room_session", {"signal_id": sig_id, "status": "skipped", "valid": False,
                                             "verdict": None, "rules_only": "allow", "error": reason},
                            returning=False)
        self.sessions.append({"signal_id": sig_id, "symbol": s, "m": m, "ref": ref, "risk_ps": risk_ps, "atr": atr,
                              "sigma_1m": sigma_1m, "qty": full_qty})

    def _store(self, sig_id, out, pack) -> None:
        if sig_id is None:
            return
        self.e.store.insert("room_session", {
            "signal_id": sig_id, "status": out.status, "valid": out.status == "done", "verdict": out.verdict,
            "rules_only": "allow", "proposer": out.proposer, "critic": out.critic, "judge": out.judge,
            "evidence": pack, "latency_ms": out.latency_ms, "cost_usd": round(out.cost_usd, 5), "error": out.error,
        }, returning=False)

    # ---------- 4: counterfactual ----------
    def finalize(self) -> None:
        """Day end: what the rules-only arm (the same trade at full size) would have made, for every session."""
        flat_m = self.e.day.flatten_m
        for sess in self.sessions:
            try:
                cf = self._counterfactual(sess, flat_m)
                if cf and sess["signal_id"] is not None:
                    self.e.store.update("room_session", {"signal_id": sess["signal_id"]}, {"counterfactual": cf})
            except Exception:
                log.exception("counterfactual failed for %s", sess.get("symbol"))
        self.sessions.clear()
        for j in self.jobs.values():
            j.future.cancel()
        self.jobs.clear()

    def _counterfactual(self, sess: dict, flat_m: int) -> dict | None:
        sd = self.e.market.symbol_days(sess["symbol"], flat_m)
        if sd is None or not len(sd.dates) or sess["qty"] <= 0:
            return None
        i = len(sd.dates) - 1
        o, h, lo, c, v = (np.array(getattr(sd, k)[i], dtype=float) for k in ("o", "h", "l", "c", "v"))
        fm = sess["m"] + 1
        if fm > flat_m or not np.isfinite(o[fm]):
            return None
        for x in (o, h, lo, c):  # time exit at the flatten minute (12:55 on early closes)
            x[flat_m + 1:] = c[flat_m]
        q = sess["qty"]
        fill = buy_px(o[fm], q, sess["sigma_1m"], v[fm])
        xm, gross, why = first_exit_trail(h, lo, o, c, fm, fill, sess["risk_ps"], sess["atr"])
        xp = gross if why == "target" else sell_px(gross, q, sess["sigma_1m"], v[min(xm, len(v) - 1)])
        pnl = (xp - fill) * q - sell_fees(q, xp)
        return {"qty": q, "fill": round(float(fill), 4), "exit": round(float(xp), 4), "exit_reason": why,
                "exit_m": int(xm), "pnl_usd": round(float(pnl), 2),
                "r_multiple": round(float(pnl / (sess["risk_ps"] * q)), 3) if sess["risk_ps"] > 0 else None}

    def status(self) -> dict:
        return {"pending": sorted(self.jobs), "budget": self._state}


def _error(msg: str):
    from kaibot.agents.room import RoomOutcome

    return RoomOutcome("error", None, error=msg[:200])


def default_pack(engine, symbol: str, etf: str | None, as_of, feats: dict) -> dict:
    """Minimal evidence pack until the news module (S33) supplies clusters: the candidate's own features, the
    regime and today's scheduled events. Ids are stable within the session."""
    items = [{"id": "feat", "kind": "features", "summary": "deterministic features at the decision",
              "value": {k: feats.get(k) for k in ("z", "resid_z", "turnover", "vix", "news_24h")}}]
    for name, m in engine.events.get(engine.day.day, []) if engine.day else []:
        items.append({"id": f"cal:{name}", "kind": "calendar", "summary": f"{name} at session minute {m}"})
    try:
        n = engine.market.data.news_count(symbol, as_of - timedelta(hours=24), as_of)
        items.append({"id": "news24", "kind": "news_count", "value": int(n),
                      "summary": f"{n} headlines for {symbol} in 24 h (the rules veto any)"})
    except Exception:
        pass
    return {"items": items, "context": {"symbol": symbol, "sector_etf": etf, "as_of": str(as_of)}}
