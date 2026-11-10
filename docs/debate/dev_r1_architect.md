# Dev R1: Software Architect position

## 1. Architecture
**One repo, two processes, one Postgres.** No microservices on about 840 hours.
- `api` (FastAPI): REST plus static React, behind Cloudflare Access.
- `worker` (same codebase): APScheduler, the Alpaca `trade_updates` websocket, research jobs, reconciliation.
- One always-on VM (about $5–10/month). Supabase is Postgres plus pgvector only. Actions does CI only.
- **Modules** (enforced by import-linter):
  - `broker`: the only code that calls Alpaca
  - `marketdata`
  - `quant` (pure functions)
  - `gate`: pure, `snapshot → verdict`
  - `proposals`
  - `research`
  - `llm`
  - `audit`
  - `notify` (FCM to Umbrella)

**Bloodbath migration:**
- **Keep:** the Alpaca wrapper, top movers, the indicators (after golden tests) and the telemetry fields (into `audit_event`).
- **Rewrite:** ranking becomes the candidate generator. Gemini picks become the Claude technical agent (candidates, no numbers).
- **Delete:** LLM-% sizing, auto-submit and the Actions cron. Disable auto-submit on day 1.

## 2. Entities and state machine
**Entities:**
- Account and market: `position`, `quote`, `fx_rate`
- Proposals and orders: `proposal`, `proposal_version`, `gate_run`, `approval`, `order`, `fill`, `reconciliation_run`
- Theses and research: `thesis`, `thesis_status_change`, `evidence(cluster_id)`
- Operations: `llm_call`, `budget_ledger`, `kill_switch`, `audit_event`

**Rules:**
- `proposal_version` is immutable: input hash, computed values, `computed_at`.
- `gate_run` stores `gate_version` (git SHA plus policy hash) and the per-rule results.
- `approval` binds to one version. For staged orders it also stores the limits.
- `client_order_id = proposal:attempt`, which makes submits idempotent.
- `audit_event` is append-only, enforced by a trigger.

```
draft → gated → pending (pass/warn); hard fail stays gated → adjust = new version
pending → approved → submitted → reconciled
pending → staged → [open+5 re-gate] → submitted | needs_reapproval → gated
pending → expired (TTL) → "Recheck prices" → gated ; staged → expired (open+30)
pre-submit states → rejected | cancelled ; submitted → cancelled (broker ack)
```
One `transition()` checks an edge table, takes an optimistic lock and writes the audit row in the same transaction. Submit always re-gates on the server.

## 3. Five riskiest items
1. **Staged fire at open + 5 minutes.** US and EU daylight saving change on different dates, and there are holidays and half-days.
   - Compute the fire time in UTC from Alpaca `/calendar`.
   - Make the job idempotent, using `SKIP LOCKED`.
   - Test with a fake clock that covers the daylight-saving gap weeks.
2. **Reconciliation in 60 seconds with no double-submit.**
   - Websocket first, with 30-second polling as a fallback.
   - Deterministic order IDs.
   - Diff positions against the ledger after every run.
3. **Gate correctness.**
   - Golden fixtures for every rule.
   - Hypothesis property tests: never sizes up, never shorts.
4. **Budget hard stop.**
   - The gateway atomically reserves the worst-case cost (input plus `max_tokens`) before each call, then settles.
   - Small models handle dedup and extraction, with prompt caching.
5. **Data gaps.** Alpaca has no 5-year fundamentals, earnings calendar or € cost basis.
   - Spike in weeks 1–2 on SEC EDGAR plus one calendar source.
   - Store FX on every fill from day 1.

## 4. Change requests to design
1. **Polling, not realtime.** TanStack Query at 2 seconds while an order is in flight, 15 seconds for the header, 60 seconds elsewhere. Saves about 1 week against SSE or Realtime (no channel auth, no reconnects).
2. **Dark theme as tokens only.** No automatic switch at 20:00, no separate dark QA. Saves about 3–4 days.
3. **"n outlets" by heuristic.** Group on a normalised headline within a 6-hour window, not semantic clustering. Saves about 1 week.
4. **Provenance chips on cards only.** Tables use mono plus an as-of time in the header. One `{value, as_of, source}` envelope feeds one `<Value>` component. Saves about 3 days.
5. **Macro dates from a YAML file**, not a feed. Saves about 2 days.

## 5. Hills I'll fight on
1. **An always-on worker, not GitHub Actions.** Actions cron runs 10–30+ minutes late or skips. That breaks the 25-minute staged window and 60-second reconciliation.
2. **Approvals bind to an immutable version, and the server re-gates at submit.** This makes "0 AI-provenance values" provable.
3. **`llm` is the only path to Claude, enforced by a lint rule.** A budget stop that can be bypassed is not a hard stop.
