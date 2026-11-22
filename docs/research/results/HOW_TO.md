# Research results in the repository (M11 tickets 2.1 and 2.2)

The ten-year results are written by the research scripts into `data/reports` on the research Mac. `data/` is not
in git, so until now the numbers lived on one machine and in issue comments. This folder is where they are
committed.

**Nothing here is filled in yet.** The pages are written by two scripts that must run where `data/reports` is.
Neither re-runs a rule, reads a price bar or logs a trial.

```
cd ~/Projects/kaibot && git pull
.venv/bin/python scripts/fetch_tbill.py --env ~/Projects/kaibot-secrets/.env    # once: the Treasury-bill rate
.venv/bin/python scripts/export_results.py                                       # one page per pre-registration
.venv/bin/python scripts/gate_v3_rescore.py                                      # the gate v3 table
git checkout -b results/export-2026-11
git add docs/research/results && git commit -m "docs: research results exported from the Mac"
git push -u origin results/export-2026-11        # then a pull request from that branch
```

What appears:

| File | What it holds |
|---|---|
| `README.md` | The index: every pre-registration, its runs on record, the headline numbers |
| `<rule>.md`, `<rule>.json` | Per kind of run, from the newest report: what the run was, CAGR, drawdown, Sharpe, mean exposure, SPY on the same sessions, SPY at the rule's exposure, alpha with its t, beta, idle cash both ways, the control's percentile, the gate v2 marks as stored, the trial count, and the neighbours |
| `as_reported/` | The one-table pages the run scripts printed (`experiments_*.md`, the two studies' reports), copied unchanged |
| `gate_v3_rescore.md`, `.json` | Every run scored under gate v3 (decision 16) |
| `index_cost_calibration.md` | The index-fund spread table of ticket 2.3, once measured |

How to read a page:

- **Two windows.** "As run" is the run's own window, as it was made and already reported. "Research window" is
  2017-05-01 to 2024-12-31. Runs made before 21 Nov 2026 go on into what is now the hold-out; their "as run" row
  is history, not a new read of the hold-out.
- **"SPY at the rule's exposure"** is what the same amount of market, with no stock picking, made. "Return above
  it" is the rule's return minus that.
- **A page that says "no account of this rule is in data/reports"** means exactly that. No number is filled in
  from memory or from an issue comment.
- **Gate v2 marks** are printed as the report stored them. They are never recomputed here.

For `trend_momentum` (ticket 2.2) the page answers, from runs already made: how much of the account was
invested, its beta and alpha, and what each of the five neighbours did (`reg60` and `reg125` for the lookback,
`exit30` for the exit band, `nofilter` for the index filter, `risk15` for the size).
