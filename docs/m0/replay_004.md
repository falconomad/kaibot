# Replay 004: replays 001-003 on SIP and IEX bars, with stricter fills (issue #75)

**Status: not run yet.** The workspace that built this had no minute history, so every column except "Recorded" says "not run". The recorded column repeats `docs/m0/replay_00*.json`. Run the command under "How to run" on the Mac to fill the rest.

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
|  | Baseline (replays 001-003) | Realistic (`--fills realistic`) |
|---|---|---|
| Decision | close of minute m, from bars up to m | same |
| Entry bar | open of minute m+1 | same bar; **no fill if that bar did not trade** (no print), the trade is skipped |
| Entry price | modelled ask: half-spread max(1 bp, $0.005), plus shadow impact | same, plus the slippage setting |
| Entry size | what the risk gate approved | capped at **5% of the fill bar's volume**, whole shares; the rest is dropped |
| Stop, trailing stop, time exit, daily stop | min(stop, bar open) or bar close, at the modelled bid minus impact | same, plus the slippage setting |
| Target | limit order: bar must trade through by $0.01, fills at the limit | same, no slippage |
| Fees | SEC and TAF on sells | same |
| Random-entry baseline | same fills as the strategy | same fills as the strategy |

- The history has no quotes, so the spread is modelled in both columns. The slippage setting (extra basis points
  per side on every market fill) is the way to test a wider real spread.
- A fill reads only the open and volume of the bar after the signal bar. Signal, stop distance and size never see
  that bar.
- Not modelled in either column: queue position, partial fills worked over later bars, auctions, halts.
- With IEX bars the volume is IEX's own, a few percent of the market, so the 5% cap and the impact term bite much
  harder than they would on the real tape. That is part of what the IEX column shows.

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

## Results

### Replay 001: variant v1, full (2025-02-01 to 2026-11-06)

| Metric | Recorded 10 Nov 2026 (SIP, baseline fills) | IEX, baseline fills | IEX, realistic fills | SIP, baseline fills | SIP, realistic fills |
|---|---|---|---|---|---|
| Symbols with bars | 59 | not run | not run | not run | not run |
| Trades | 642 | not run | not run | not run | not run |
| Net return (on $100k) | -4.01% | not run | not run | not run | not run |
| t-stat per trade | -2.38 | not run | not run | not run | not run |
| Profit factor | 0.81 | not run | not run | not run | not run |
| Percentile vs random | 97.0 | not run | not run | not run | not run |

### Replay 002: variant A, design (2025-02-01 to 2026-06-30)

| Metric | Recorded 10 Nov 2026 (SIP, baseline fills) | IEX, baseline fills | IEX, realistic fills | SIP, baseline fills | SIP, realistic fills |
|---|---|---|---|---|---|
| Symbols with bars | 59 | not run | not run | not run | not run |
| Trades | 750 | not run | not run | not run | not run |
| Net return (on $100k) | +0.08% | not run | not run | not run | not run |
| t-stat per trade | 0.03 | not run | not run | not run | not run |
| Profit factor | 1.00 | not run | not run | not run | not run |
| Percentile vs random | 100.0 | not run | not run | not run | not run |

### Replay 002: variant A, holdout (2026-07-01 to 2026-11-06)

| Metric | Recorded 10 Nov 2026 (SIP, baseline fills) | IEX, baseline fills | IEX, realistic fills | SIP, baseline fills | SIP, realistic fills |
|---|---|---|---|---|---|
| Symbols with bars | 59 | not run | not run | not run | not run |
| Trades | 187 | not run | not run | not run | not run |
| Net return (on $100k) | +0.70% | not run | not run | not run | not run |
| t-stat per trade | 0.53 | not run | not run | not run | not run |
| Profit factor | 1.11 | not run | not run | not run | not run |
| Percentile vs random | 95.0 | not run | not run | not run | not run |

### Replay 003: variant B, design (2025-02-01 to 2026-06-30)

| Metric | Recorded 10 Nov 2026 (SIP, baseline fills) | IEX, baseline fills | IEX, realistic fills | SIP, baseline fills | SIP, realistic fills |
|---|---|---|---|---|---|
| Symbols with bars | 59 | not run | not run | not run | not run |
| Trades | 25 | not run | not run | not run | not run |
| Net return (on $100k) | -0.78% | not run | not run | not run | not run |
| t-stat per trade | -2.07 | not run | not run | not run | not run |
| Profit factor | 0.40 | not run | not run | not run | not run |
| Percentile vs random | 5.5 | not run | not run | not run | not run |

### Replay 003: variant B, holdout (2026-07-01 to 2026-11-06)

| Metric | Recorded 10 Nov 2026 (SIP, baseline fills) | IEX, baseline fills | IEX, realistic fills | SIP, baseline fills | SIP, realistic fills |
|---|---|---|---|---|---|
| Symbols with bars | 59 | not run | not run | not run | not run |
| Trades | 5 | not run | not run | not run | not run |
| Net return (on $100k) | -0.11% | not run | not run | not run | not run |
| t-stat per trade | -0.42 | not run | not run | not run | not run |
| Profit factor | 0.61 | not run | not run | not run | not run |
| Percentile vs random | 32.5 | not run | not run | not run | not run |

## Reading the table
- **Recorded vs SIP, baseline fills:** the same strategy and parameters on a fresh download. A difference comes from the bars (Alpaca revisions, the RTH-only download, symbols missing from it) or from replay code changed since 10 Nov; this column does not say which.
- **Baseline vs realistic on the same feed:** the cost of the stricter fill assumptions.
- **IEX vs SIP on the same fills:** what single-venue bars do to signals, volume filters and fills.
- **Variant B** has 25 and 5 recorded trades; its t-stat and percentile are noise at that size.
- v1 was only ever run on the full period, so it has one window.
