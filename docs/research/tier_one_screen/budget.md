# The tier-one screen's budget (append-only)

Alpha-investing over batches (`docs/research/tier_one_screen/design.md` section 4; `config/screen.yaml`). One line
per batch, written from the batch report and committed by the pull request that records the batch's result, and one
line per payout when an engine verdict confirms a survivor. Never edited or removed; `kaibot.evaluation.screen
.read_ledger` replays it, and a run refuses a ledger whose lines do not add up.

| date | batch | kind | wealth_before | level | cost_or_payout | survivors | wealth_after |
|---|---|---|---|---|---|---|---|
| 2026-11-22 | - | start | 0 | - | 0.09 | - | 0.09 |
