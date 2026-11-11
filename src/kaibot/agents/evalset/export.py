"""Build real evaluation cases from Supabase (schema `kaibot`). Documented here, run by hand: nothing in CI or the
engine calls it, and it only reads.

The query, as SQL (the function below sends the same thing through PostgREST with the engine's read path):

    select rs.id, rs.ts, rs.status, rs.verdict, rs.evidence, rs.counterfactual,
           s.id as signal_id, s.symbol, s.ts as decision_ts, s.strategy, s.features,
           t.pnl_usd, t.risk_usd, t.exit_reason, t.verdict as traded_verdict
    from kaibot.room_session rs
    join kaibot.signal s on s.id = rs.signal_id
    left join kaibot.trade t on t.signal_id = s.id and t.exit_ts is not null
    where s.strategy = 'dip_reversal'
      and rs.ts >= :cutoff + 1 day         -- after the model's training cutoff (s.ts is re-checked in Python)
      and rs.evidence is not null          -- the pack the room saw
      and rs.counterfactual is not null    -- the rules-only outcome, written at day end
    order by s.ts;

Outcome label: the counterfactual (the same trade at full size with the replay's fill and exit model) is used for
every case, vetoed or not, so vetoes and allows are scored on the same basis. The realised trade, when there is
one, is kept only as a cross-check in `outcome.source`.

Cutoff: pass the training cutoff of the model being evaluated (config/llm.yaml `models.room`), taken from
Anthropic's model documentation. Rows on or before it are excluded in the query and again in Python.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from kaibot.agents.evalset.cases import FORMAT_VERSION, EvalCase, after_cutoff

SESSION_SELECT = ("id,ts,status,verdict,evidence,counterfactual,"
                  "signal:signal_id(id,symbol,ts,strategy,features)")
TRADE_SELECT = "signal_id,pnl_usd,risk_usd,exit_reason,verdict"
ROOM_ONLY_FEATURES = ("room_submitted_m", "room_latency_ms", "quote_age_s")
PAGE = 500


def session_query(cutoff: date, margin_days: int = 0, offset: int = 0) -> dict:
    """PostgREST parameters for kaibot.room_session (see the SQL in the module docstring)."""
    start = datetime.combine(cutoff + timedelta(days=margin_days + 1), time.min, UTC)
    return {"select": SESSION_SELECT, "ts": f"gte.{start.isoformat()}", "evidence": "not.is.null",
            "counterfactual": "not.is.null", "order": "id.asc", "limit": str(PAGE), "offset": str(offset)}


def case_from_row(row: dict, trade: dict | None = None) -> EvalCase | None:
    sig, cf, pack = row.get("signal") or {}, row.get("counterfactual") or {}, row.get("evidence") or {}
    if sig.get("strategy") != "dip_reversal" or cf.get("pnl_usd") is None or not pack.get("items"):
        return None
    feats = {k: v for k, v in (sig.get("features") or {}).items() if k not in ROOM_ONLY_FEATURES}
    src = "room_session.counterfactual"
    if trade and trade.get("pnl_usd") is not None:
        src += f"; realised trade pnl_usd={trade['pnl_usd']} ({trade.get('exit_reason')})"
    return EvalCase.model_validate({
        "format_version": FORMAT_VERSION, "case_id": f"real-{row['id']}", "synthetic": False,
        "category": f"real_{row.get('verdict') or row.get('status')}", "decision_ts": sig["ts"],
        "signal": {"symbol": sig["symbol"], "strategy": sig["strategy"],
                   "sector_etf": (pack.get("context") or {}).get("sector_etf"), "features": feats},
        "evidence": {"items": pack.get("items"), "context": pack.get("context") or {}},
        "outcome": {"label": "win" if float(cf["pnl_usd"]) > 0 else "loss", "r_multiple": cf.get("r_multiple"),
                    "source": src},
    })


def export_real_cases(store, cutoff: date, margin_days: int = 0, max_cases: int = 2000) -> list[EvalCase]:
    """Real cases decided strictly after `cutoff` (+ margin). `store` is a kaibot.engine.store.Store (read-only
    use: two selects). Not run in CI.

        from kaibot.agents.evalset import dump_cases
        from kaibot.agents.evalset.export import export_real_cases
        dump_cases(export_real_cases(store, date(2026, 8, 31), margin_days=30), "data/room_eval/real_v1.jsonl")
    """
    rows, offset = [], 0
    while len(rows) < max_cases:
        page = store.select("room_session", session_query(cutoff, margin_days, offset))
        rows += page
        if len(page) < PAGE:
            break
        offset += PAGE
    trades: dict = {}
    ids = [str(r["signal"]["id"]) for r in rows if (r.get("signal") or {}).get("id") is not None]
    for i in range(0, len(ids), 100):
        for t in store.select("trade", {"select": TRADE_SELECT, "exit_ts": "not.is.null",
                                        "signal_id": f"in.({','.join(ids[i:i + 100])})"}):
            trades[t["signal_id"]] = t
    cases = [c for r in rows[:max_cases]
             if (c := case_from_row(r, trades.get((r.get("signal") or {}).get("id")))) is not None]
    kept, _ = after_cutoff(cases, cutoff, margin_days)  # second line of defence: signal time, not session time
    return kept
