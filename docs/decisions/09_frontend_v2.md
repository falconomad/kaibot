# 09 Frontend v2: autonomous paper day-trader
*10 Nov 2026 · Frontend · applies 04_prd_v2. Stack unchanged; the SPA moves off the VM (§4).*

## 1. Routes
| Route | Page |
|---|---|
| `/login` | Magic link; one allow-listed email. |
| `/onboarding` | Amount ($1k–100k, € ref) → level as bad/good day → contract → confirm → "Starts 09:30 ET (15:30 CET)" |
| `/` Live | Status, PAPER, data health, spend; pot vs SPY/cash; risk strip; tape; transcripts; evidence |
| `/trades` | Table, CSV |
| `/trades/:id` | Ladder, gate snapshot, transcript, costs, ledger vs broker P&L |
| `/report`, `/report/:date`, `/report/gate` | Daily card, weekly attribution, read-only gate report |
| `/run`, `/run/:id` | Pause, Flatten-and-stop, next-session level, New run; archives read-only |
| `/health` | Source freshness, heartbeat, halts, drift, spend vs $150/$190 |

## 2. Component delta from v1
- **Dropped:** `DecisionPanel`, `Stepper`, `NeedsYouQueue`, `CandidateCard`, `ScenarioStrip`, `ThesisBadge`, `StalenessStrip`, `AllocationBars`, `CoreSatBar`, `AIRail`.
- **Kept:**
  - `Num` (shows the worse of ledger/broker P&L), `Meter` (risk strip), `DataTable`, `SafetyHeader`.
  - `GateTable` and `TradeLadder`, read-only on trade detail.
  - `ReconBanner` → `DriftBanner`; `EvidenceList` folds into `Transcript`.
- **Added:**
  - `EngineStatus`: Running, Paused, Halted with the reason, Flat, Closed.
  - `PotChart`: pot vs SPY vs cash; 3 series × ~390 points, so SVG suffices.
  - `Tape`: every fill after costs, with reason codes.
  - `Transcript`: Proposer, Critic, Judge, sources, veto.
  - `EvidenceCounter`: trades/900, expectancy CI, t against 3.
  - `ReportCard`, `LevelPicker`, `ControlBar`, `HaltLog`.

## 3. Data fetching and auth
- **Polling only.** TanStack Query against FastAPI; no Supabase Realtime.
  - **US market hours:** status and risk strip 5 s; tape 10 s (`?since=<event_id>` cursor); pot 30 s (AC ≤ 60 s); transcripts 30 s; health 10 s; evidence counter 5 min.
  - **Off hours:** 5 minutes.
  - **Background tabs:** polling pauses.
- **Controls are never optimistic:** "Pending…" until the engine confirms; Flatten counts positions down to 0.
- **Auth:**
  - `supabase-js` handles only the magic link; the SPA sends the JWT as a bearer token.
  - FastAPI verifies it via JWKS and takes `account_id` from the claims. The browser never queries tables; RLS is defence in depth.

## 4. Hosting: Cloudflare Pages
- **Why it beats Vercel Hobby:**
  - Hobby is non-commercial only, which a future live account muddies.
  - Pages: unlimited static bandwidth, branch previews, optional free Cloudflare Access.
- **Off the VM:** during a VM outage the dashboard still says "Engine unreachable since 14:02" (risk 7).
- **API:** on the VM behind a Cloudflare Tunnel, strict CORS. Fallback: FastAPI serves the build, losing that visibility.

## 5. Testing
- **Vitest** table tests: `Num`, `EvidenceCounter`, `EngineStatus`.
- **MSW** typed from OpenAPI; CI fails on drift.
- **Playwright:** onboarding, Pause, Flatten-and-stop, each halt reason, next-session level, read-only archive.
- **Replay fixture:** a recorded session at 60× drives Live end to end.
- **Screenshots** of `PotChart` and the risk strip, both themes.
