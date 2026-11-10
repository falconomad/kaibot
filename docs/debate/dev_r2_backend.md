# Round 2: Backend / Quant Engineer rebuttal

## Concessions
- **Architect, SEC EDGAR spike.** EDGAR XBRL is official and covers 5+ years. I drop "accept 4 years".
- **Architect, worst-case budget reservation.** Agreed: reserve input + `max_tokens` cost, then settle the actual. Open reservations count toward $190.
- **Architect, polling.** 60 s reconciliation is measured in the worker (Alpaca websocket to ledger), not in the browser. Polling at 2 s for an in-flight order meets it.

## Pushback
- **Frontend: proposal id + gate hash is the wrong key.** Submit re-gates with a fresh quote, so the hash changes, and a retried tap can double-submit. Two layers fix it:
  - HTTP `Idempotency-Key` = `approval_id`, so one approval submits once.
  - Broker `client_order_id` = `kb-{proposal_version_id}-{attempt}`. The attempt increments only after Alpaca confirms a reject or cancel.
  - The gate hash goes into the audit record only.
- **Architect: immutable `proposal_version` and my 5-minute expiry.** Agreed, with the clock starting at that version's `computed_at`. A stepper change makes a new version and resets it. At open + 5 the executor creates a child version, which needs no re-approval if the approved limits hold.
- **Architect: the budget stop must never block the gate.** The PRD says an AI outage cannot block a trade that passed the gate. The $190 stop disables `llm` only, never `quant`, `gate` or `broker`. Keep `max_tokens` tight, or worst-case reservations trip the stop early.

## Compromises
1. EDGAR is primary for financials and FCF, after a 3-day spike. yfinance gives sectors and is a labelled fallback for 20-F gaps.
2. Polling in v1; SSE `/events` in v1.1.
3. The fire time comes from Alpaca `/calendar`, in UTC.

## PRD changes needing product sign-off
1. **Gate in USD on Alpaca equity.** € is display only, at the timestamped ECB rate.
2. **0.5% is a ceiling.** Qty = the minimum of risk, position and sector headroom. Staged orders are sized at max entry, 1.005 × proposed (buys).
3. **Drawdown 8% is a soft alert.** Daily loss of 1.5% (vs `last_equity`, unrealised included) hard-blocks new entries but never forces exits.
