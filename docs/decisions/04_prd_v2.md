# Kaibot PRD v2: autonomous paper day-trading (FINAL)
*10 Nov 2026 · PM decision · supersedes 01_prd.md (gate and provenance rules carry over).*

## 1. Product and success
Autonomous paper day-trading of liquid US stocks: set amount and level; it trades, ends flat, and reports after-cost results against SPY and cash.

**The single go-live bar** (per strategy, frozen code, forward paper):
- ≥ 900 trades over ≥ 3 months;
- net expectancy after shadow costs with t > 3;
- deflated Sharpe > 0;
- above 95th percentile of 1,000 random entries;
- higher Sharpe than SPY;
- drawdown within cap.

50 trades can only kill. The 40-day bar is withdrawn. Passing opens a discussion, not a switch.

## 2. Decisions
| Decision | Debated | Final call | Won | Reason |
|---|---|---|---|---|
| Strategies | 1 vs 2 + shadows | No-news dip reversal (all levels); SPY last-30-min momentum (B and A); ORB and news continuation shadow-only | Advocate | Levels need to differ; ORB ≈0 net |
| Levels (C/B/A) | 06's looser Aggressive | Risk's: risk/trade 0.25/0.5/1%; daily stop −0.75/−1.5/−3%; open risk ≤ room to stop; max position 10/15/20%; positions 3/5/8; entries/day 10/20/30; run ends at drawdown 4/8/12%; no shorts, leverage or overnight | Risk | Otherwise the stop is void |
| Universe | Per level vs one | One: quality ≥ 70, ADV > $50M, spread < 5 bp | PM | Comparability |
| Controls | Separate Flatten-all? | Pause; Flatten-and-stop (one confirm); New run (archives old). Level changes apply next session, as a new stats segment | Risk + Advocate | Flattening alone re-enters |
| Data IN | — | Alpaca (IEX, SIP volume, News, clock); EDGAR; Finnhub; PRN/GNW; FinBERT; VIX; sector ETFs; FRED/BLS/Fed calendar | Quant | Free; covers the no-news check |
| Data LOGGED | Week 1 vs later | Polymarket, Kalshi, GDELT, ApeWisdom, Tradestie, Wikipedia, collected from M1; traded only after a 3-month ablation, except the `pump_risk` long-block | Advocate | An ablation needs history |
| Data SKIP | — | X, Reddit API, StockTwits, yfinance, delayed news, HF inference | Risk | Terms, cost or delay |
| Agent room | Verdict vs veto | Proposer, Critic, Judge; one round; JSON; **veto or downgrade only**; invalid output means no trade; removed if no lift over the rules-only shadow | Quant + Risk | LLMs explain; Python decides |
| Venue | Sim vs Alpaca | SimBroker for replay; Alpaca paper via `BrokerAdapter`; own ledger; show the worse P&L | Quant | Paper fills are optimistic |
| Cost cap | — | Per-call reservation; over $3/day, rules-only vetoes; alert at $150; at $190, no entries, exits still managed. Expected $45–72/month; VM €4–5/month | Architect + Risk | Fail closed |
| Framing | +0.5–2%/day | "Beat SPY and cash after costs": +2–10 bp/day, 40–50% losing days; progress shown as evidence | Advocate + Quant | Honest and motivating |
| PDT | Day-trade counter | Dropped (retired 4 Jun 2026); buying power capped | 06 | — |

## 3. MVP features
- **Onboarding:** magic link; amount $1k–100k (€ reference); level shown as a bad and good day; contract screen; start at next open.
- **Dashboard:** PAPER badge, status, data health, LLM spend; pot vs SPY and cash; risk strip; tape; transcripts; evidence counter.
- **Engine:** gates; strategies and shadows; broker-held brackets; halts (3 losses: 60-minute pause; stale > 5 s; drift; watchdog; 3 stops/10 days); entries 09:35–15:45 ET; flat 15:55 ET.
- **Reporting:** daily card, weekly attribution, CSV, gate report.
- **Later:** live broker, shorts, ORB/news trading, alt-data trading, Umbrella push.

## 4. Risk register (likelihood/impact)
1. **No edge** (H/H): baselines, kill criteria, bar.
2. **Optimistic fills** (H/H): shadow costs; worse P&L shown.
3. **Overfitting** (M/H): ≤ 3 parameters, trial log, DSR.
4. **LLM lookahead** (H/M): room judged forward only.
5. **Free data or terms change**, including FinBERT's NC licence (M/H): fail closed, fallbacks, licence review.
6. **Runaway orders** (L/H): idempotent IDs, caps, tested gate.
7. **VM outage** (M/M): broker-held stops, watchdog.
8. **Claude overspend** (L/M): reservations, caps.
9. **Premature go-live** (H/M): pre-registered bar.
10. **Solo capacity** (H/M): milestones, shadow-only extras.

## 5. Delegation
U = user, Q = quant, O = operator. Story format: As a… I want… so that… → AC.

| # | Story → AC | Owner | M |
|---|---|---|---|
| **E1 Foundation and replay** |||
| 1 | O wants one repo (worker, API, Postgres) → CI passes | QA/OPS | M0 |
| 2 | O wants bloodbath's client and indicators ported for reuse → fixture parity | BACKEND/QUANT | M0 |
| 3 | Q wants minute bars since 2016 for replay → 300 symbols | DATA | M0 |
| 4 | Q wants a point-in-time quality score against lookahead → pre-date filings only | DATA | M0 |
| 5 | Q wants event-sourced replay for reproducibility → identical hash | BACKEND/QUANT | M0 |
| 6 | Q wants the shadow cost model for honest P&L → unit-tested | BACKEND/QUANT | M0 |
| 7 | Q wants SPY, cash, random and rules-only baselines to measure edge → in every replay | BACKEND/QUANT | M0 |
| 8 | Q wants walk-forward testing with a trial log to expose overfitting → DSR and PBO | BACKEND/QUANT | M0 |
| **E2 Paper engine** |||
| 9 | O wants an idempotent `BrokerAdapter` against duplicates → retry gives one order | BACKEND/QUANT | M1 |
| 10 | U wants level presets in the gate to cap losses → property tests pass | BACKEND/QUANT | M1 |
| 11 | U wants brackets held at the broker to survive crashes → stop fires with the worker dead | BACKEND/QUANT | M1 |
| 12 | U wants dip reversal live to trade → reason codes logged | BACKEND/QUANT | M1 |
| 13 | U wants SPY momentum (Balanced and up) so levels differ → ≤ 1 trade a day, MOC exit | BACKEND/QUANT | M1 |
| 14 | Q wants ORB and news continuation in shadow to see counterfactuals → never sent | BACKEND/QUANT | M1 |
| 15 | U wants regime and event gates to avoid crises → none ±30 min of FOMC/CPI | BACKEND/QUANT | M1 |
| 16 | O wants automatic halts to fail closed → injected faults halt | QA/OPS | M1 |
| 17 | O wants rate-limited ingestion → no 429s | DATA | M1 |
| 18 | U wants a ledger at my amount so buying power is capped → drift halts | BACKEND/QUANT | M1 |
| 19 | Q wants alt-data collectors to build history → engine never reads them | DATA | M1 |
| **E3 Onboarding and controls** |||
| 20 | U wants onboarding to understand the contract → one confirm, start at next open | DESIGN/FRONTEND | M2 |
| 21 | U wants Pause to stop entries → within 1 cycle | FRONTEND | M2 |
| 22 | U wants Flatten-and-stop as a brake → flat < 30 s, tagged | FRONTEND/BACKEND | M2 |
| 23 | U wants level changes to apply next session for clean stats → new segment | FRONTEND | M2 |
| 24 | U wants New run to restart → old run read-only | BACKEND/QUANT | M2 |
| **E4 Dashboard and reporting** |||
| 25 | U wants a clear layout for glanceability → approved mocks | DESIGN | M2 |
| 26 | U wants the pot against SPY and cash so profit isn't misleading → updates ≤ 60 s | FRONTEND | M2 |
| 27 | U wants a tape with reasons to trust it → every fill, after costs | FRONTEND | M2 |
| 28 | U wants a risk strip and status to see halts → red on breach | FRONTEND | M2 |
| 29 | U wants an evidence counter for meaningful progress → trades/900, CI | FRONTEND/QUANT | M2 |
| 30 | U wants a daily report card to learn → by 16:15 ET | BACKEND/QUANT | M2 |
| 31 | Q wants weekly attribution to kill losers → auto flags | BACKEND/QUANT | M2 |
| **E5 Agent room and alt-data** |||
| 32 | O wants a capped LLM gateway against overspend → no call over budget | AGENTS/AI | M3 |
| 33 | U wants triage of deduplicated stories so echoes count once → one score per cluster | AGENTS/AI | M3 |
| 34 | U wants a veto room to skip news drops → ≤ 45 s; no size or price fields | AGENTS/AI | M3 |
| 35 | U wants transcripts to see dissent → linked, sourced | FRONTEND | M3 |
| 36 | Q wants a rules-only A/B so the room earns its place → veto lift | BACKEND/QUANT | M3 |
| 37 | Q wants alt-data features and `pump_risk` for the ablation → `pump_risk` blocks longs | DATA | M3 |
| **E6 Evaluation gate** |||
| 38 | Q wants an automatic gate report so it can't be bent → read-only | BACKEND/QUANT | M4 |
| 39 | Q wants a 3-month alt-data ablation for admission → beats random at 95% | BACKEND/QUANT | M4 |
| 40 | O wants an outage drill to recover → < 5 min | QA/OPS | M4 |
| 41 | U wants a go/no-go review for an evidence-based call → no live button | FRONTEND | M4 |

## 6. Roadmap
- **M0 (weeks 1–6):** foundation and replay; bloodbath replay demo in week 3.
- **M1 (weeks 7–12):** forward paper; evidence clock starts.
- **M2 (weeks 13–17):** UI and reporting.
- **M3 (weeks 18–23):** room A/B; alt-data features.
- **M4 (week 24+):** the gate runs once a strategy has 3 months and 900 trades (about weeks 26–34). SPY momentum (~1 trade/day) can't reach 900 in a year: paper-only.
