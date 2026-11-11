"""Render docs/m0/replay_004.md (issue #75): replays 001-003 side by side by bar feed and fill model.

Pure: reports in, Markdown out. A cell is only ever a number taken from a replay report; a run that has not
happened renders as "not run", so the document can be generated before any history exists.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

NOT_RUN = "not run"

ROWS = (
    ("Symbols with bars", "symbols", lambda v: f"{int(v)}"),
    ("Trades", "trades", lambda v: f"{int(v)}"),
    ("Net return (on $100k)", "net_return_pct", lambda v: f"{v:+.2f}%"),
    ("t-stat per trade", "t_stat_per_trade", lambda v: f"{v:.2f}"),
    ("Profit factor", "profit_factor", lambda v: "no losing trades" if math.isinf(v) else f"{v:.2f}"),
    ("Percentile vs random", "strategy_percentile_vs_random", lambda v: f"{v:.1f}"),
)

FILL_ROWS = (
    ("", "Baseline (replays 001-003)", "Realistic (`--fills realistic`)"),
    ("Decision", "close of minute m, from bars up to m", "same"),
    ("Entry bar", "open of minute m+1",
     "same bar; **no fill if that bar did not trade** (no print), the trade is skipped"),
    ("Entry price", "modelled ask: half-spread max(1 bp, $0.005), plus shadow impact",
     "same, plus the slippage setting"),
    ("Entry size", "what the risk gate approved",
     "capped at **5% of the fill bar's volume**, whole shares; the rest is dropped"),
    ("Stop, trailing stop, time exit, daily stop",
     "min(stop, bar open) or bar close, at the modelled bid minus impact", "same, plus the slippage setting"),
    ("Target", "limit order: bar must trade through by $0.01, fills at the limit", "same, no slippage"),
    ("Fees", "SEC and TAF on sells", "same"),
    ("Random-entry baseline", "same fills as the strategy", "same fills as the strategy"),
)
FILL_TABLE = "\n".join(["| " + " | ".join(FILL_ROWS[0]) + " |", "|---|---|---|",
                        *("| " + " | ".join(r) + " |" for r in FILL_ROWS[1:])])

INTRO = """\
## What this checks
Research action 1 (`claude/kaibot-engine-and-market-research.md`) asks how much IEX-only bars and optimistic fills
flatter replays 001-003. Reading the code for this story showed two things that change the question:

- **Replays 001-003 already ran on SIP bars.** The history download has asked Alpaca for the SIP feed since the
  first version (`src/kaibot/ingest/alpaca_history.py`), and `replay_001.md` says so. IEX bars are only used by the
  live engine for today's session. An IEX column therefore needs a separate IEX-feed download, which does not
  exist yet (Data team).
- **The fills were already next-bar and spread-crossing.** The stricter model below adds the parts that were
  missing.

So each window is run four ways (IEX or SIP bars, baseline or realistic fills), next to the numbers recorded on
10 Nov 2026. Strategy code, parameters, universe, windows, risk level and random-run count are the originals.

## Fill assumptions
{fill_table}

- The history has no quotes, so the spread is modelled in both columns. The slippage setting (extra basis points
  per side on every market fill) is the way to test a wider real spread.
- A fill reads only the open and volume of the bar after the signal bar. Signal, stop distance and size never see
  that bar.
- Not modelled in either column: queue position, partial fills worked over later bars, auctions, halts.
- With IEX bars the volume is IEX's own, a few percent of the market, so the 5% cap and the impact term bite much
  harder than they would on the real tape. That is part of what the IEX column shows.
"""

HOW = """\
## How to run
On the Mac, from the repository root, once the history download (`./scripts/fetch_history.sh`, issue #3) has
finished. It reads files only, so it does not matter whether the engine is running. It needs `scipy` in the venv
(`.venv/bin/pip install scipy`) and, for the VIX series, either `data/inputs/fred_vixcls.json` or `FRED_API_KEY`
in the `.env`:

```
PYTHONPATH=src .venv/bin/python -m kaibot.replay.sip_rerun --env ~/Projects/kaibot-secrets/.env --sip-data data
```

- It runs the five windows on SIP bars with both fill models (10 replays), then rewrites this file and
  `replay_004.json`.
- Add `--iex-data data_iex` once an IEX-feed download exists there (10 more replays).
- Add `--sweep-bps 1,2,5` for extra SIP runs with realistic fills plus that many basis points per side.
- Every replay appends one row to `data/trials.jsonl` with its feed and fill model, so the deflated Sharpe in
  `scripts/trial_report.py` counts all of them.
- SIP bars are only read for sessions before today (the 15-minute rule); the run stops otherwise.
- A symbol of the seed universe with no bars in the download is skipped without an error. The "Symbols with bars"
  row shows it: the recorded runs had 59.
- Variant B also reads `data/fundamentals/companyfacts` (downloaded by the same script).
"""


def _cell(rep: Mapping | None, key: str, fmt) -> str:
    if rep is None:
        return NOT_RUN
    v = rep.get(key)
    return "—" if v is None else fmt(float(v))


def window_table(recorded: Mapping | None, by_cfg: Mapping[str, Mapping], cfgs: Sequence) -> str:
    head = ["Metric", "Recorded 10 Nov 2026 (SIP, baseline fills)", *[c.title for c in cfgs]]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for name, key, fmt in ROWS:
        cells = [_cell(recorded, key, fmt), *[_cell(by_cfg.get(c.key), key, fmt) for c in cfgs]]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def warnings(windows: Sequence, results: Mapping[str, Mapping[str, Mapping]]) -> list[str]:
    """Things that would make the table misleading."""
    out = []
    for w in windows:
        r = results.get(w.key, {})
        a, b = r.get("iex_baseline"), r.get("sip_baseline")
        if a and b and a.get("bars_sha256") and a.get("bars_sha256") == b.get("bars_sha256"):
            out.append(f"{w.key}: the IEX and SIP bars are identical, so `--iex-data` is not an IEX-feed download.")
    return out


def render(windows: Sequence, results: Mapping[str, Mapping[str, Mapping]], recorded: Mapping[str, Mapping],
           cfgs: Sequence, meta: Mapping | None = None) -> str:
    meta = meta or {}
    ran = [rep for r in results.values() for rep in r.values()]
    parts = ["# Replay 004: replays 001-003 on SIP and IEX bars, with stricter fills (issue #75)", ""]
    if ran:
        shas = sorted({str(rep.get("code_sha")) for rep in ran})
        parts.append(f"**Status: run {meta.get('run_at', '')} at code {', '.join(shas)}, {len(ran)} replays, "
                     f"{meta.get('runs', '')} random runs each.** Columns marked \"{NOT_RUN}\" have no data yet. "
                     "The verdict is written by backend-quant after reading the tables; none is implied here.")
    else:
        parts.append(f"**Status: not run yet.** The workspace that built this had no minute history, so every "
                     f"column except \"Recorded\" says \"{NOT_RUN}\". The recorded column repeats "
                     "`docs/m0/replay_00*.json`. Run the command under \"How to run\" on the Mac to fill the rest.")
    parts += ["", INTRO.replace("{fill_table}", FILL_TABLE), HOW, "## Results"]
    for msg in warnings(windows, results):
        parts.append(f"- **Warning:** {msg}")
    for w in windows:
        parts += ["", f"### Replay {w.replay}: variant {w.variant}, {w.label} ({w.start} to {w.end})", "",
                  window_table(recorded.get(w.key), results.get(w.key, {}), cfgs)]
    parts += ["", "## Reading the table",
              "- **Recorded vs SIP, baseline fills:** the same strategy and parameters on a fresh download. A "
              "difference comes from the bars (Alpaca revisions, the RTH-only download, symbols missing from it) or "
              "from replay code changed since 10 Nov; this column does not say which.",
              "- **Baseline vs realistic on the same feed:** the cost of the stricter fill assumptions.",
              "- **IEX vs SIP on the same fills:** what single-venue bars do to signals, volume filters and fills.",
              "- **Variant B** has 25 and 5 recorded trades; its t-stat and percentile are noise at that size.",
              "- v1 was only ever run on the full period, so it has one window.", ""]
    return "\n".join(parts)
