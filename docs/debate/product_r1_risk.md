# Round 1 — Risk & Compliance Officer position memo

Principle: friction goes where capital moves. Reading is free; ordering is gated.

## 1. Top 5 failure modes the UX must prevent

1. **Approving on stale or mismatched data.** Price or account state changed after the proposal was computed.
2. **AI numbers dressed as calculations.** An LLM-invented entry, stop, qty or % allocation reaches an order (the bloodbath failure).
3. **Paper/live confusion.** Any path, flag or key that could make a "paper" action touch a live account.
4. **Fake consensus.** Ten articles from one wire story, or an unsourced claim, inflating conviction in a thesis or trade.
5. **Silent safeguard loss.** Kill switch, AI budget hard stop, Alpaca reconciliation failure or a breached daily-loss limit that the user can't see, while the UI keeps offering actions.

## 2. Screen safeguards

**Command Centre**
- Persistent header: PAPER badge, kill-switch state + one-click engage, AI spend vs $150/$190, last Alpaca reconcile time/status.
- Risk-limit strip: daily loss vs 1.5%, open tactical risk vs 2%, drawdown vs 8%; red on breach.
- Every value tile shows as-of time; stale greys out.
- AI chat: answers labelled "AI analysis"; any quoted number links to its source/calc; chat cannot create, size or submit orders, only open a Trade approval draft.
- € figures show FX rate and timestamp.

**Discover & Research**
- Every claim shows source and timestamp; duplicate-wire items collapsed into one evidence item with a "n outlets" count.
- Bull/base/bear and valuation show inputs, model version and data date.
- Screener results marked "candidate, not recommendation."
- Research age badge; >N days triggers "Needs refresh" before it can feed a proposal.
- Agent runs show cost; disabled with explanation at the budget hard stop.

**My Portfolio**
- Alpaca reconcile mismatches shown as a banner, never hidden.
- Concentration vs limits (10% position, 30% sector) with headroom shown.
- Thesis status (Intact/Under pressure/Invalidated/Needs review) shows who changed it (user vs agent), when and why; agents can only suggest a change, the user confirms.
- Price-drop alerts never auto-flip a thesis to Invalidated.
- Core vs satellite sleeve clearly separated; "add to position" routes through Trade approval.

**Trade approval**
- Gate result is the Python verdict, displayed verbatim; Claude commentary sits in a separate, labelled panel and cannot change it.
- Hard fails: Approve disabled, no override. Soft warnings: explicit acknowledgement per warning.
- Active paper orders list with cancel, and post-execution reconcile result.
- Full audit record per decision: inputs snapshot, gate version, user action, timestamp.

## 3. Before Approve is enabled, the screen must show

- PAPER account ID badge (live impossible by design).
- Symbol, side, order type, qty, entry, stop, target: each tagged "calculated by" with data timestamp.
- Quote age within threshold (e.g. <60s in market hours) and market open; otherwise disabled with reason.
- Max loss in $ and €, % equity, and position size derived from stop distance (0.5% rule).
- R:R ratio.
- Post-trade exposure: position %, sector %, aggregate open tactical risk, daily-loss headroom.
- Every gate check listed pass/warn/fail (stale price, duplicate, short, hours, limits, kill switch, reconcile OK).
- Linked rationale with sources.
- Proposal expiry countdown; any input change or expiry forces recompute.

## 4. Hills I'll fight on

1. **No one-tap approvals from notifications or the Android app in v1.** Approval happens on the full screen above; a push may link there, never approve. *Reason: a lock-screen tap is exactly how you approve on stale context.*
2. **The gate, audit log, kill switch and budget hard stop ship in the first release that can place any order, not "later".** *Reason: they're cheap now and the old bot proves what happens without them.*
3. **No free-text or AI-supplied number ever reaches the order payload; the user may adjust qty/stop only via controls that re-run the Python gate.** *Reason: provenance must be machine-enforced, not a design convention.*
