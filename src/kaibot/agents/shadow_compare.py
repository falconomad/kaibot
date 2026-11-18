"""Shadow verdicts against what actually happened (issue #310). Read-only; no model call, no network.

The shadow pre-open review (agents/preopen_shadow.py) records, per session, what it WOULD have done to each buy of
the rules' plan; nothing acted on it, so every buy it saw was really made. This script joins each verdict with the
outcome of that buy from the engine's own journal: the buy's fill price, then the close `horizon` sessions later
(the journal's `mark` entries), or the sale's fill price when the rule sold it before then.

    python -m kaibot.agents.shadow_compare --journal data/journal.sqlite [--horizon 5] [--json]

What it shows, per verdict (allow / downgrade / veto): how many buys, how many have an outcome yet, their mean and
median return, and the share that lost money. A useful reviewer's vetoes lose more often than its allows. The
numbers are small samples from paper trading: they describe, they prove nothing, and they never feed a decision.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections.abc import Iterable, Mapping
from pathlib import Path

from kaibot.agents.preopen_shadow import KIND, reasons

SEP = "|"  # engine/ledger.price_key: "<strategy>|<symbol>"


def outcomes(shadow: Iterable[Mapping], fills: Iterable[Mapping], marks: Iterable[Mapping],
             horizon: int = 5) -> list[dict]:
    """One row per shadow verdict on a buy (only records with status "done").
    `fills`: the journal's `fill` bodies ({"strategy", "symbol", "side", "px", "session"}); `marks`: the journal's
    `mark` bodies ({"session", "prices": {"<strategy>|<symbol>": close}}). `ret` is None until the horizon (or a
    sale) is reached."""
    closes: dict[str, dict[str, float]] = {}
    for m in marks:
        closes.setdefault(str(m.get("session")), {}).update(
            {str(k): float(v) for k, v in (m.get("prices") or {}).items() if v is not None})
    days = sorted(closes)
    buys: dict[tuple[str, str, str], float] = {}
    sells: dict[tuple[str, str], list[tuple[str, float]]] = {}
    for f in fills:
        key = (str(f.get("strategy")), str(f.get("symbol")))
        if f.get("px") is None:
            continue
        if f.get("side") == "buy":
            buys.setdefault((*key, str(f.get("session"))), float(f["px"]))
        elif f.get("side") == "sell":
            sells.setdefault(key, []).append((str(f.get("session")), float(f["px"])))
    rows = []
    for rec in shadow:
        if rec.get("status") != "done":
            continue
        rule, sess, why = str(rec.get("strategy")), str(rec.get("session")), reasons(rec)
        for cid, verdict in sorted((rec.get("verdicts") or {}).items()):
            sym = cid.split(":", 1)[-1]
            px = buys.get((rule, sym, sess))
            later = [d for d in days if d >= sess]
            end = later[horizon] if len(later) > horizon else None  # `horizon` sessions after the buy's close
            sold = sorted((d, p) for d, p in sells.get((rule, sym), []) if d > sess and (end is None or d <= end))
            exit_px, how = (sold[0][1], f"sold {sold[0][0]}") if sold else (
                (closes.get(end) or {}).get(f"{rule}{SEP}{sym}"), f"close {end}" if end else None)
            ret = round(exit_px / px - 1.0, 6) if px and exit_px else None
            rows.append({"strategy": rule, "session": sess, "symbol": sym, "verdict": verdict,
                         "reason": why.get(sym), "fill_px": px, "exit_px": exit_px, "exit": how, "ret": ret})
    return rows


def summary(rows: list[dict]) -> dict[str, dict]:
    out = {}
    for v in ("allow", "downgrade", "veto"):
        rs = [r for r in rows if r["verdict"] == v]
        done = [r["ret"] for r in rs if r["ret"] is not None]
        out[v] = {"buys": len(rs), "with_outcome": len(done),
                  "mean_ret": round(statistics.fmean(done), 6) if done else None,
                  "median_ret": round(statistics.median(done), 6) if done else None,
                  "share_lost": round(sum(x < 0 for x in done) / len(done), 4) if done else None}
    return out


def _read(path: Path, kind: str) -> list[dict]:
    """The journal's bodies of one kind, opened READ-ONLY (safe beside a running engine; nothing is written)."""
    import sqlite3

    db = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)
    try:
        return [json.loads(b) for (b,) in db.execute("select body from entry where kind = ? order by seq", (kind,))]
    finally:
        db.close()


def from_journal(path: Path, horizon: int = 5) -> list[dict]:
    return outcomes(_read(path, KIND), _read(path, "fill"), _read(path, "mark"), horizon)


def _pct(y: float | None) -> str:
    return "" if y is None else f"{y:+.2%}"


def report(rows: list[dict], horizon: int) -> str:
    s = summary(rows)
    lines = [f"Shadow pre-open verdicts against the actual outcome of each buy ({horizon} sessions, or the sale).",
             "Nothing acted on these verdicts: every buy below was made. Small paper samples: descriptive only.", "",
             "| verdict | buys | with outcome | mean return | median return | share that lost |",
             "|---|---|---|---|---|---|"]
    for v, x in s.items():
        lost = "" if x["share_lost"] is None else f"{x['share_lost']:.0%}"
        lines.append(f"| {v} | {x['buys']} | {x['with_outcome']} | {_pct(x['mean_ret'])} | "
                     f"{_pct(x['median_ret'])} | {lost} |")
    lines += ["", "| session | symbol | verdict | reason | return | exit |", "|---|---|---|---|---|---|"]
    for r in rows:
        if r["verdict"] != "allow" or r["ret"] is not None:
            ret = "" if r["ret"] is None else f"{r['ret']:+.2%}"
            lines.append(f"| {r['session']} | {r['symbol']} | {r['verdict']} | {r['reason'] or ''} | {ret} | "
                         f"{r['exit'] or ''} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--journal", type=Path, required=True, help="the engine's journal.sqlite (read only)")
    ap.add_argument("--horizon", type=int, default=5, help="sessions after the buy (default 5)")
    ap.add_argument("--json", action="store_true", help="rows and summary as JSON")
    a = ap.parse_args(argv)
    rows = from_journal(a.journal, a.horizon)
    print(json.dumps({"summary": summary(rows), "rows": rows}, indent=1) if a.json else report(rows, a.horizon))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
