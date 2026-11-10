# Kaibot v1 tech spec: final rulings from the development team
Team: Software Architect · Frontend Engineer · Backend/Quant Engineer. Two rounds, resolved by the facilitator.

## Architecture
- One repo, two processes, one Postgres:
  - `api` (FastAPI) serves REST, streaming AI answers and the built React SPA, behind Cloudflare Access.
  - `worker` (same codebase) runs APScheduler (persisted jobs), the Alpaca `trade_updates` websocket, research jobs and reconciliation.
- Hosting: one always-on VM (about $5–10/month). Supabase is used as Postgres plus pgvector only. GitHub Actions is CI only; no cron execution, because late Actions runs break the staged window and the 60-second reconciliation.
- Modules (boundaries enforced by import-linter):
  - `broker`: the only code that calls Alpaca
  - `marketdata`
  - `fundamentals` (adapter)
  - `quant`: pure functions
  - `gate`: pure, snapshot → verdict
  - `proposals`
  - `research`
  - `llm`: the only path to Claude, enforced by lint
  - `audit`
  - `notify`: FCM pushes to Umbrella, deep-link only
- Bloodbath migration:
  - Keep: the Alpaca wrapper, top movers, indicators (after golden tests), telemetry fields (moved into `audit_event`).
  - Rewrite: ranking becomes the candidate generator; Gemini picks become the Claude technical agent (qualitative output only, no numbers).
  - Delete: LLM-% sizing, auto-submit, the Actions cron. Auto-submit is disabled on day 1.

## Frontend
- Stack: Vite + React + TypeScript (non-strict to start), served by FastAPI.
  - Tailwind with design tokens as CSS variables; shadcn/Radix primitives; TanStack Query and Table; Lucide icons.
  - Charts (meters, ladder, scenario strip, bars) are hand-written SVG. No chart library in v1.
  - The client is generated from OpenAPI; the same schema can generate Umbrella's Kotlin client.
- Rejected:
  - Streamlit: whole-script reruns, no sticky regions or reflowing rail, unreliable client state for Approve.
  - HTMX: untyped hand-written JS, nothing reusable for Umbrella.
  - Next.js: an extra Node server with no benefit for a single user.
- 16 components: SafetyHeader, Num, Meter, AIRail, DataTable, NeedsYouQueue, CoreSatBar, CandidateCard, StalenessStrip, ScenarioStrip, EvidenceList, ThesisBadge, AllocationBars, ReconBanner, TradeLadder, GateTable, Stepper, DecisionPanel.
  - `Num` renders every number (lint-enforced) from a `{value, as_of, source}` envelope.
- Live data:
  - State is polled: 2 s while an order is in flight, 15 s for the header, 60 s elsewhere.
  - AI answers stream via one `StreamingResponse` per question. Paragraphs commit as they arrive; a final structured event carries the call, counter-argument, source count and links to calculations.
  - The SSE `/events` stream (Postgres LISTEN/NOTIFY) is v1.1.
- Never optimistic: Approve, kill switch, thesis status, Reject.
- Testing: Vitest table tests for `Num` and every DecisionPanel state; MSW fixtures; Playwright on the 4 flows; light-theme screenshots.
- Estimate: about 10 weeks for the 4 screens. The architect's figure is adopted over the frontend's 7, to allow for the TypeScript learning curve.

## Deterministic core (changes signed off by the product team via the facilitator)
- The gate runs in USD on Alpaca equity. € is display only, at the timestamped ECB rate. Gate Actual and Limit are shown as % of equity, which is currency-neutral.
- Sizing: Q = floor(min(0.005E/(P−S), (0.10E − MV_sym)/P, sector_headroom/P)). 0.5% is a ceiling, not a target.
  - The API returns `sizing.candidates[]` and `sizing.binding`; the UI shows a "Size bound by" block.
  - Example: E $100k, P 61.20, S 58.40. Risk allows 178, the position cap allows 163 (binding), so the size is **163 shares, max loss $456.40 (0.46%)**.
- Staged orders are sized at max entry = 1.005P. In the example that is **160 shares, max loss $497.60**.
- The fire time comes from Alpaca `/calendar`: `next_open + 5 min`, stored in UTC and displayed in CET/CEST with the date. Never hard-coded as 15:35. A note appears in the weeks when US and EU daylight saving differ.
- Drawdown of 8% is a soft alert. Daily loss of 1.5% (equity vs `last_equity`, unrealised included) hard-blocks new entries but never forces exits.
- Warn threshold: post-trade value ≥ 0.8 × limit.
- Sector: a tag the user confirms once (yfinance only suggests). Unknown fails closed, with a one-tap assign.
- Correlation: Pearson on daily log returns over 252 aligned days (minimum 120, otherwise "n/a").
- Financials: SEC EDGAR XBRL is primary (5+ years), after a 3-day spike. yfinance is a labelled fallback.
- DCF: 2-stage FCF; shows "not meaningful" when FCF is negative.
- Gate rules: kill switch · reconciled ≤ 5 min (hard) · market hours ≤ close − 10 min · quote ≤ 60 s (warn > 30 s) · duplicate · short · position 10% (warn 8%) · sector 30% (warn 24%) · open risk 2% (warn 1.6%) · daily loss 1.5% · drawdown 8% (soft) · correlation 0.7 (soft) · thesis not Intact (soft).

## Proposals, expiry, idempotency
- `proposal_version` is immutable: input hash, computed values, `computed_at`. A stepper change creates a new version.
- `approval` binds to one version.
- State machine: draft → gated → pending → approved | staged → submitted → reconciled, plus rejected, expired, needs_reapproval and cancelled. One `transition()` function checks the edge table and writes the audit row in the same transaction.
- Expiry: a 5-minute TTL from `computed_at`, used for the display countdown. The server always re-gates at submit and submits if qty is unchanged and the price is within ±0.5%; otherwise the proposal becomes `expired` and the button reads "Recheck prices".
- Two-layer idempotency:
  - HTTP `Idempotency-Key` = `approval_id`.
  - Broker `client_order_id` = `kb-{proposal_version_id}-{attempt}`. Persist `submitting` before the call; on a timeout or duplicate error, GET by `client_order_id` and adopt the order rather than resending.
- Reconciliation: websocket plus a 30-second poll in the worker. Any diff turns the chip red and blocks orders.
- Budget: reserve the worst-case cost (input + `max_tokens`) before each call, then settle. Open reservations count toward $190. The stop disables `llm` only, never `quant`, `gate` or `broker`.

## Design change requests accepted
1. Desktop only, 1280px and up. Mobile stays in Umbrella.
2. Dark theme follows the OS plus a manual toggle; the automatic 20:00 CET switch is dropped.
3. "n outlets" uses a headline-match heuristic (6-hour window), labelled "headline match". Expanding it shows the grouped items.
4. Provenance chips appear only where a cell's source or time differs from its column header.
5. Macro dates come from a YAML file.
6. Numbers the AI quotes become links when the stream completes.
7. The benchmark chart is deferred to v1.1.
8. Ladder labels keep 14px minimum spacing, with leader lines.
9. The setup card gets a "Size bound by" block, and the qty "+" stepper is disabled at the binding cap.

## Build order (about 34 weeks)
1. Week 1: disable bloodbath auto-submit; repo, schema, CI, VM.
2. Weeks 2–6: broker, marketdata, gate, sizing, state machine, audit (CLI plus golden tests); EDGAR spike.
3. Weeks 7–10: worker reconciliation, then the staged executor on a fake clock (covering the daylight-saving weeks).
4. Weeks 11–16: safety header and Trade approval on polling. **This is the first usable product.**
5. Weeks 17–20: LLM gateway and budget ledger, the candidate generator, the risk explainer, AI streaming.
6. Weeks 21–25: My Portfolio, theses, FX ledger.
7. Weeks 26–31: Discover & Research (screener, DCF, evidence, research agent).
8. Weeks 32–34: Command Centre, Umbrella deep links, hardening.

## Unverified (needs checking against current vendor documentation)
- Alpaca's free plan includes SIP historical bars older than 15 minutes.
- Alpaca rejects a reused `client_order_id`.
- yfinance returns about 4 years of annual financials.
