# Dev R2: Frontend Engineer rebuttal

**1. Architect: polling instead of SSE.**
- **Concede** for state: 2 s while an order is in flight, 15 s for the header, 60 s elsewhere. Components use cache invalidation, so push can come later.
- **Push back** on AI answers. One `StreamingResponse` per question needs no channels, auth or reconnects (about a day). Without it, an answer sits blank for up to 90 s.
- **Compromise:** poll for state; stream only AI answers.

**2. Architect: provenance chips on cards only.**
- **Concede:** tables put as-of in the column header; one `{value, as_of, source}` envelope and component.
- **Push back:** G3 needs per-cell staleness; one stale quote still shows its clock glyph.
- **Compromise:** a chip appears only where a cell's source or time differs from its header.

**3. Architect: "n outlets" by heuristic.**
- **Concede.** Label it "4 outlets · headline match"; expanding it lists the grouped items, so bad grouping is visible.

**4. Backend: fire time is never hard-coded 15:35.**
- **Concede.** The API returns `fire_at` and `lapses_at` in UTC; the UI shows them in CET/CEST with the date: "Fires Mon 15 Mar 14:35 CET · lapses 15:00".
- In the daylight-saving gap weeks, add a note: "US open 14:30 CET". On a holiday, show the next session.
- The countdown targets `fire_at`; "15:35" becomes a template.

**5. Backend: gate in USD, € on screen.**
- **Agree** that the gate runs in USD. Gate Actual and Limit use % of equity, which is currency-neutral.
- Max loss reads "$456.40 · €393 (ECB, 10 Nov 16:00) · 0.46%", with € labelled as derived.

**6. Backend: the 163-share cap-bound example.**
The setup card gets a "Size bound by" block:

| Constraint | Size |
|---|---|
| Risk 0.5% | 178 |
| Position cap 10% | **163 · binding** |
| Sector headroom | 240 |

- Caption: "Risk 0.46% of 0.50% used; capped by position limit."
- The qty stepper's "+" is disabled at 163, with a tooltip naming the cap.
- A staged order shows 160 shares, "sized at max entry $61.51".
- **Backend request:** return `sizing.candidates[]` and `sizing.binding`. The UI never derives a size.
