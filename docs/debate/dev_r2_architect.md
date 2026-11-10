# Dev R2: Software Architect rebuttal

## Concessions
- **To Frontend:**
  - React SPA plus TypeScript: Approve needs predictable client state, and Umbrella can reuse the OpenAPI client.
  - Desktop only, 1280px and up.
  - Dark theme follows the OS, with a manual toggle.
- **To Frontend, on SSE:** AI streaming needs SSE anyway. An invalidate-only `/events` stream fed by Postgres `LISTEN/NOTIFY` costs about 2 days. Polling stays as the fallback, and the stream ships after Trade approval works on polling.
- **To Backend:** size staged orders at max entry, gate in USD, adopt-on-duplicate order IDs, and make "reconciled within 5 minutes" a hard gate.

## Push back
- **Frontend, the 7-week estimate:** 16 components plus SVG plus screenshots in both themes is optimistic for someone learning TypeScript. I'd budget 10 weeks. Compromises:
  - Non-strict TypeScript.
  - Light-theme screenshots only.
  - Build `DecisionPanel` and `Num` first.
- **Backend, the 5-minute expiry:** decisions take a median of 10 minutes, so Recheck becomes the normal path. Expiry is also a state, not a gate row.
  - Compromise: the 5-minute TTL is for display only, and the server re-gates at submit.
  - If qty is unchanged and the limit is within ±0.5% of what the user saw, submit. Otherwise mark it `expired`.
- **Backend, yfinance:** an unofficial scraper must not feed a hard gate.
  - Sector comes from a user tag set once (yfinance only suggests).
  - Financials come from SEC EDGAR, which gives 5 years. Put it behind a `fundamentals` adapter.

## v1 build order (about 34 weeks)
1. **Week 1:** disable bloodbath auto-submit. Set up the repo, schema, CI and VM.
2. **Weeks 2–6:** `broker`, `marketdata`, gate, sizing, the state machine and audit, driven from the CLI with golden tests. Run the EDGAR spike.
3. **Weeks 7–10:** worker reconciliation, then the staged executor on a fake clock.
4. **Weeks 11–16:** safety header and Trade approval on polling. This is the first usable product.
5. **Weeks 17–20:** LLM gateway and budget ledger, the candidate generator, the risk explainer, and SSE.
6. **Weeks 21–25:** My Portfolio, theses and the FX ledger.
7. **Weeks 26–31:** Discover & Research (screener, DCF, evidence, research agent).
8. **Weeks 32–34:** Command Centre, Umbrella deep links, hardening.
