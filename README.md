# kaibot

**Autonomous paper day-trading engine for liquid US stocks.** Fund a paper pot, pick a level (Steady / Balanced / Hunter),
and Kaibot trades 09:35–15:45 ET on its own, ends flat every day, and reports results **after costs against SPY and cash**.
Free data only. Paper only.

> Honest expectations (research 06): published intraday edges are a few basis points a day after costs, 40–50% of days
> lose, and most retail day traders lose money. Kaibot is built to **prove or kill** strategies with evidence, not to
> promise profit. Going live is only discussed after the pre-registered bar in `docs/decisions/04_prd_v2.md` is met.

## How it decides
1. **Strategy rules (Python):** no-news dip reversal ("loser hunter"): quality ≥ 70, liquid, a beta-driven drop on
   normal turnover, no company news in 24 h, 5-min higher low; regime and FOMC/CPI blackouts. SPY last-30-min
   momentum on Balanced/Hunter. ORB and news continuation run in **shadow** only.
2. **Agent room (Claude):** Proposer, Critic and Judge classify *why* it dropped and can only **veto or downgrade**.
   They never set price, size or exits. The room is A/B-tested against rules-only and removed if it adds nothing.
3. **Risk gate (Python, `src/kaibot/risk/gate.py`):** sizes every trade (min of risk/stop, position cap, open-risk room
   before the daily stop, buying power) and enforces hard limits, halts and the kill switch.
4. **Bracket order** via `BrokerAdapter` (Alpaca paper), re-priced by the conservative shadow cost model; the
   dashboard shows the worse P&L.

## Repo map
| Path | What |
|---|---|
| `src/kaibot/` | engine packages: `ingest`, `features`, `strategies`, `agents`, `risk`, `broker`, `engine`, `evaluation`, `store`, `common` |
| `apps/api` | FastAPI dashboard API |
| `apps/web` | Vite + React + TS dashboard |
| `config/` | `profiles.yaml` (levels), `sources.yaml` (free sources + request budgets) |
| `docs/research/` | the six data-source and strategy-evidence reports (10 Nov 2026) |
| `docs/decisions/` | PRD v2, design v2 (UX, viz, visual), tech design v2, frontend plan (v1 docs kept for history) |
| `docs/debate/` | every team agent's position and rebuttal memo |
| `docs/backlog.json` | 41 stories → GitHub issues via `scripts/create_backlog.py` |

## Quick start
```bash
uv venv && uv pip install -e ".[dev]"
uv run pytest -q
cp .env.example .env   # fill in free keys; paper keys only
```

## Milestones
M0 foundation & replay (wk 1–6) · M1 paper engine live (wk 7–12) · M2 dashboard & reports (wk 13–17) ·
M3 agent room A/B & alt-data (wk 18–23) · M4 evaluation gate (wk 24+, earliest review ≈ wk 26).
