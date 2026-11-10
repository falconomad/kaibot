# Round 2 rebuttal: Investor / User Advocate

## (a) Concessions
- **PM, approval queue first, and 3 specialists in v1:** agreed, as long as news inside research still produces catalysts.
- **Risk, no approving from notifications or Android in v1:** agreed. A push can open the approval screen but can't approve anything.
- **Risk, as-of timestamps, PAPER badge, header showing kill switch and spend:** agreed.
- **Risk, qty/stop changes only through controls that re-run the gate:** a better version of my "Adjust-and-recheck". I'm dropping free editing.

## (b) Pushback
- **PM cuts the market summary.** I need a briefing scoped to my holdings: overnight moves and today's catalysts for what I own or watch. Without it, my 07:40 check happens somewhere else.
- **PM cuts top opportunities.** If ideas only live in Discover, I'll never see them on a 3-minute check.
- **PM cuts correlation.** Losing the matrix is fine; losing the signal isn't. Hidden concentration is what broker apps get wrong.
- **Risk wants an acknowledgement for each soft warning.** If every warning needs its own click, I'll click through all of them, as the PM warned.
- **Risk blocks proposals on stale research.** A single N-day limit is wrong. A swing trade depends on today's technicals, not last week's DCF.

## (c) Compromises
- **Market summary:** a briefing for my holdings and watchlist only; no market-wide feed.
- **Top opportunities:** 3 read-only, gate-pre-checked candidates on the Command Centre, labelled "candidate, not recommendation" and linked to Discover.
- **Correlation:** Python computes 1-year return correlation with my top 5 holdings: one number per idea, with a warning above 0.7. No matrix.
- **Acknowledgements:** one combined acknowledgement listing every warning, shown only when a rule is within 20% of its limit.
- **Research staleness:** limits set per sleeve. Core research is valid for 30 days or until the next earnings report; tactical needs news and technicals under 24h old. A stale item gets an inline "Refresh and continue" button, not a dead end.
- **Approve at lunch, queue for the open:** a conditional approval. At 12:30 I approve with a price band (default ±0.5% of the proposed entry) that expires at today's close. Nothing goes to Alpaca before the open. At the open, the gate re-runs in full with a quote under 60 seconds old. If any gate fails or the price is outside the band, the order is cancelled and I'm told why; it's never re-priced silently. Both the out-of-hours and freshness rules hold.
