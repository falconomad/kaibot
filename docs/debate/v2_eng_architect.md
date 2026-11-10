# v2 Engineering input: Architect (feasibility)

## 1. System shape
One repo: a **worker** process, an **API** process and one Postgres.

- **Ingestion:** one task per source, each with a token bucket (Alpaca 180/min, Finnhub 55/min, Massive 5/min) and 429 backoff.
- **Feature store:** in-memory buffers, a 2-day `features_1m` table and a daily Parquet archive for replay.
- **Strategy engine (deterministic, 1-minute cycle):** a 20-second budget. It scores the universe and manages exits, and opens a position only on a live room verdict.
- **Agent room (event-triggered, not on the 1-minute clock)**
  - Starts when a candidate passes the quant pre-filter.
  - Roles:
    - Proposer: the rebound thesis.
    - Critic: the strongest counter-case.
    - Judge: `{verdict, band, horizon, invalidation, evidence_ids, ttl}`.
  - Tool-use JSON, where invalid means no trade. Limits: 45 seconds and about 25k tokens per session, one round.
  - The room never sets size, entry or stop.
- **Risk gate**
  - Pure Python. Aggressiveness maps to presets: risk per trade, maximum positions, daily loss stop and orders per hour.
  - Flattens at close − 10 minutes. Kill switch and watchdog.
- **Broker interface**
  - `submit`, `cancel`, `positions`, `fills()`.
  - SimBroker first (any starting balance, IEX price plus slippage), then Alpaca paper in shadow, then Alpaca live only behind a separate deploy and a cap.
- **Event log:** append-only. P&L is a fold over it, and replay runs the same code over the archive.
- **API and auth:** FastAPI with polling, Supabase Auth with a magic link, and `account_id` on every row.

## 2. Hosting (rough, to be confirmed by research)
- **Supabase free:** fine if 1-minute bars go to Parquet. At about 117k rows a day they would fill the 500 MB cap within weeks.
- **VM:** Oracle Always Free ARM, which risks being reclaimed. Otherwise a €4–5/month Hetzner VM. As far as I know, Fly no longer offers a free tier.
- **Frontend:** Vercel Hobby (non-commercial use only, which fits) or Cloudflare Pages.

## 3. Claude cost model
**Budget:** $150 over about 21 trading days is about $7 a day.

**Routing:**
- A Haiku-class model triages headlines: about 2,000 a day, roughly $1 a day.
- A Sonnet-class model runs the room, with cached prompts.
- Opus-class models are used only for weekly post-mortems.

**Per session:** 3 calls × about 6k tokens in and 600 out. At assumed prices of $3/$15 per million tokens, that is about $0.09.

**Capacity:** roughly **55–65 sessions a day**.

**Rules:** the gateway reserves the worst-case cost before each call. When the budget is exhausted, the engine stops opening positions but keeps managing exits.

## 4. Bloodbath reuse
Bloodbath is already an autonomous mover-scorer, so it is closer to v2 than to v1.

**Port, with tests:**
- The Alpaca client.
- The movers fetch.
- The indicators.
- The JSONL telemetry, which is a free dataset for a baseline backtest.

**Drop:**
- Gemini picks.
- LLM-% sizing. Sizing comes from stop distance.
- The Actions cron, too imprecise for a 1-minute loop.

**Umbrella:** later, for push.

## 5. Top 5 technical risks
1. **No edge.** Intraday rebounds net of slippage may be noise, and the LLM adds narrative, not alpha. Build replay before the room, and pre-register baselines: SPY, random entry and quant-only.
2. **Simulator optimism.** Halts, LULD pauses, partial fills and the IEX-to-NBBO gap all flatter paper results.
   Use conservative fills, shadow Alpaca paper, and require 60 days of parity before going live.
3. **Free-data limits.** 30 websocket symbols, personal-use licences and no usable free X API.
4. **Runaway autonomy.** Idempotent order IDs, an orders-per-minute cap, the daily loss stop, flatten-at-close and property tests on the gate.
5. **Free-tier fragility.** A single-VM outage, a VM reclaim or the Supabase cap can hit during US hours. Fail closed. Before going live, check the current PDT rule against a $5k account.
