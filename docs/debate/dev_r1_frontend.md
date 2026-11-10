# Dev R1: Frontend Engineer position

## 1. Stack: Vite + React + TypeScript SPA, served by FastAPI
- **Libraries:** Tailwind (design tokens as CSS variables, so the theme is one swap), copied-in shadcn/Radix parts (Dialog, Tooltip, Tabs, Checkbox), TanStack Query and Table, Lucide.
- **Charts:** hand-written SVG for meters, ladder, scenario strip and bars. They're simple shapes; a chart library would fight the spec.
- **Types:** generated from FastAPI's OpenAPI schema. The same schema can generate Umbrella's Kotlin client: one API, two apps.
- **Not Streamlit:** whole-script reruns, no real sticky regions, no reflowing rail, and no reliable client state for Approve, so double-submits are a risk. Every custom widget we need would be React anyway.
- **Not HTMX + Jinja:** the best Python-first option, but stepper rechecks that lock Approve, a rail that persists across pages, and streaming into structured cards all mean untyped hand-written JS. It also gives Umbrella nothing.
- **Not Next.js:** one user, no SEO; a second (Node) server is overhead.
- **Cost:** the developer learns TypeScript, softened by generated types and Claude Code. About 7 weeks of 3.5-hour days for the 4 screens.

## 2. Component inventory (16)
- **All screens:** `SafetyHeader`; `Num` (€/$/%/pp, mono, source chip, stale state); `Meter` (bullet, header and ghost variants); `AIRail`; `DataTable`.
- **Command Centre:** `NeedsYouQueue`, `CoreSatBar`, `CandidateCard`.
- **Discover & Research:** `StalenessStrip`, `ScenarioStrip`, `EvidenceList`, `ThesisBadge`.
- **My Portfolio:** `AllocationBars`, `ReconBanner`.
- **Trade approval:** `TradeLadder`, `GateTable` + `Stepper` + `DecisionPanel` (the Approve state machine).

## 3. Data and state
- **Fetching:** TanStack Query is the only server cache, with `staleTime` set by data class. UI preferences live in localStorage.
- **Live updates:** one SSE stream, `/events` (meters, orders, reconciliation, the Needs-you queue). Events invalidate queries; they never patch numbers. No WebSockets.
- **Optimistic UI only for preferences.** Approve, kill switch, thesis status and Reject wait for the server.
- **Approve:** an idempotency key (proposal ID + gate-snapshot hash) and server-driven states. During a stepper recheck it shows "Rechecking…" and is disabled.
- **AI streaming:** SSE tokens render as serif prose, committed paragraph by paragraph so the layout doesn't jitter. A final structured event delivers the call, counter-argument, source count and calculation references, and only then appear the rail summary and the linked numbers. `aria-live` fires once, on completion.
- **Accessibility:** Radix focus management, `role="meter"`, gate changes announced, "/" ignored inside inputs, reduced motion respected, both themes contrast-checked in CI.
- **Testing:** Vitest table tests for `Num` and every `DecisionPanel` state; MSW fixtures; Playwright on the 4 flows with screenshots of the ladder and meters in both themes.

## 4. Design change requests
1. **Desktop-only at 1280px and wider;** mobile is Umbrella's job. *Saves about 1 week.*
2. **Dark theme follows the OS plus a manual toggle,** not an automatic switch at 20:00 CET. *Saves the timezone/DST logic and flash-of-wrong-theme bugs (about 1 day).*
3. **AI-quoted numbers link only once the stream completes.** *Saves fragile mid-stream parsing (about 3 days).*
4. **Defer the benchmark chart (a SHOULD) to v1.1,** so no chart library in v1. *Saves about 3 days and about 60 KB.*
5. **Specify ladder label collisions** (14px minimum spacing, leader lines). *Avoids about 2 days of rework.*
6. **Subset the fonts to 7 weights** (Plex Sans 400/500/600, Plex Mono 300/400/500, Serif 400). *Faster load, no layout shift.*

## 5. Hills I'll fight on
1. **A React SPA on a typed OpenAPI contract:** Approve needs deterministic client state, and the same API feeds Umbrella.
2. **Approve is never optimistic:** a UI that lies about an order is worse than a slow one.
3. **One `Num` component renders every number,** enforced by lint: provenance and staleness only hold if they live in one place.
