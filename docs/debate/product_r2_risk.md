# Round 2: Risk & Compliance Officer rebuttal

## (a) Concessions
- **PM and advocate are right: a clean pass needs no ceremony.** I drop per-warning acknowledgements and typed confirmations. The advocate's "extra steps only within 20% of a limit" is the right trigger, and I adopt it.
- **PM's "approval queue first"** is my hill 2. I back it, including the metric of zero order values with AI provenance.
- **Advocate's Adjust-and-recheck** fits my hill 3 if it re-runs the Python gate. That settles the PM's "no in-app editing": controls, not free text.

## (b) Pushback
- **Advocate, approve at lunch and queue for the open.** A 12:30 approval prices a 15:30 market. A gap at the open breaks the stop distance, and so the size and max loss the user approved. This is failure mode #1 with a timer on it.
- **Advocate, "AI commits to a call".** A call is fine; the brief asks for a decision. An LLM's self-rated confidence is not: an uncalibrated number posing as a measurement. And the gate checks limits, not judgement, so it gives no safety on core calls like Nebius.
- **PM, "no friction beyond the gate".** Paper trading is where the habit is built. The gate checks a snapshot; it can't know the screen sat open for 40 minutes or the thesis flipped meanwhile.
- **PM, expiry as only a SHOULD.** Expiry is the stale-data protection; without it the price check is theatre. It's cheap: store the computed-at time and re-check on the server at submit.

## (c) Compromises I can live with
1. **Staged orders become conditional approvals.** The user approves limits: max entry, max quantity, max loss. At the open, Python re-prices and re-runs the gate. If every limit holds, it submits a limit order (never market). Otherwise it cancels and re-queues with a push. It never resizes upward; the approval lapses after 30 minutes.
2. **AI call:** add / hold / trim / pass, with a kill condition, the strongest counter-argument and an independent-evidence count. Confidence shown as verbal bands, no percentages, until the decision log can calibrate it. The call never pre-fills a quantity.
3. **Friction:** none on a clean pass. One acknowledgement if a limit is within 20%, a soft warning fires, or the thesis isn't Intact. Hard fails disable Approve.
4. **Expiry:** server-side re-check at submit is a MUST; the visible countdown is a SHOULD.
