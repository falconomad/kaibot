# Pre-registration: `preopen_negative_news_veto`

- **Rule:** `preopen_negative_news_veto`
- **Kind:** a veto test in logging mode, not a trading rule: it changes no order. The pre-open review stays a
  shadow (`agents/preopen_shadow.py`); turning any veto on is a separate decision of the stakeholder.
- **Status:** pre-registered
- **Date:** 2026-11-22
- **Author:** agents-ai
- **Issue:** #471 (M12 ticket 3.9); report `docs/research/engine_audit/report.md`, "Honest uses of AI and trained
  models"; Product's decision D6 (`docs/research/engine_audit/decisions.md`): a language model may only veto or halve.

Written and committed before any shadow record under the frozen fingerprint below exists, and before the golden
set has been run against the real model. Append-only: a change is an `## Amendment <n>` at the end, committed
before the data it affects; any change of prompt, model, tool or masking is a new fingerprint, and records under
it do not count for this file.

## Hypothesis

A buy that the daily rule chose and that the pinned language model, reading the news shown to it before the
session, would veto for **company news** does worse over the next 5 sessions than a buy the same model would allow.
The published effect of language models reading news is small, strongest in small stocks and carried by the short
side, and fading (Lopez-Lira and Tang); Kaibot cannot short, so the only usable form is "do not buy on bad news".
Expected size, if real: a fraction of a percent over a week. The honest prior is that it is too small to find in
Kaibot's large-stock universe; a null result is the most likely outcome and is a result.

## Who is on the other side of the trade

Holders selling on fresh bad news that the closing price has not fully absorbed (the delayed-reaction story of the
news-drift literature). A veto does not trade against them; it declines to buy from them. If the price has already
absorbed the news, nobody is on the other side and the vetoed buys do no worse.

## Published evidence

Report, section "Honest uses of AI and trained models": Lopez-Lira and Tang (post-cutoff Sharpe ratios near 3 on
overnight headlines, small-cap, short-side, unprofitable at 20 basis points round trip, falling from 6.54 in late
2021 to 1.22 in early 2024); Lopez-Lira, Tang and Zhu (models recall pre-cutoff facts and name anonymised companies
more than 85% of the time for large technology firms); Gao, Jiang and Yan (that contamination inflates backtests);
He et al. (finding it modest for next-day news trades). Hence forward-only, after the cutoff, with a masked arm.
None of this was replicated here.

## Frozen settings

- **Reviewer:** `agents/preopen.GatewayReviewer`, one call per (rule, session), the shadow's records only
  (`md_review_shadow` in the engine's journal). Model `claude-sonnet-5-5` (`models.preopen` in config/llm.yaml), a
  pinned snapshot: from the 4.6 generation on, a dateless model id is one fixed snapshot and never an alias
  (Anthropic, "Model IDs and versioning", read 22 Nov 2026). Reliable knowledge cutoff June 2026 (models overview),
  recorded as 2026-06-30 in config/llm.yaml.
- **Frozen fingerprint:** `1c5c8833b9de955c109f707918f33e698b0a62c2c995987fc62be32c66e5eaa2`
  (`preopen.fingerprint("claude-sonnet-5-5")`: model, system prompt, tool schema, prompt version `preopen-v1`).
- **What each record keeps:** the model asked for, the model the API says answered, the response id, the sha256 of
  the whole prompt, the fingerprint and the input text; both arms (raw, and masked with `mask-v1`:
  `agents/preopen_mask.py`, tickers and issuer names replaced by aliases).
- **A record counts only when:** status done; asked and answered model are both `claude-sonnet-5-5`; fingerprint is
  the frozen one; the golden set (`agents/evalset/preopen_golden_v1.jsonl`, 100 items) has a passing `live` run of
  that fingerprint in `agents/evalset/golden_runs.jsonl` dated on or before the session; the session is after
  2026-11-22 (the freeze) and after the knowledge cutoff. The golden run passes with, on the raw arm, every item
  answered, agreement >= 0.85, negative-news veto rate >= 0.80 and false hold-back rate <= 0.15.
- **Unit and outcome:** one buy of the rules' plan (every buy was really made: the review is a shadow). Return from
  the buy's fill to the close 5 sessions later, or to the rule's own sale if earlier
  (`agents/shadow_compare.outcomes`). No cost is charged: a veto would save the round trip, which the effect mark
  below exceeds several times.
- **Groups:** vetoed = verdict `veto` with flag `company_news`; accepted = verdict `allow`. Downgrades and other
  flags are reported, not tested.
- **Statistic:** mean(vetoed) - mean(accepted). One-sided permutation test, 10 000 shuffles, seed 471.
- **Stopping rule:** looks at 25 and 50 vetoed buys with an outcome: stop for futility if the statistic is >= 0.
  Final look at 100 vetoed buys with an outcome, or 24 months after the first counting session, whichever first.
  Fewer than 25 vetoed buys at 24 months: inconclusive. With a handful of planned buys a day and a few percent of
  them vetoed, the final look may well take a year or more.
- **Code:** `agents/veto_prereg.py` repeats these constants (a test checks they agree) and computes the status:
  `python -m kaibot.agents.veto_prereg --journal data/journal.sqlite`.

## Neighbours (at most five)

Reported beside the result at the final look, never used to pass it:

- `horizon_3`: the return over 3 sessions instead of 5.
- `horizon_10`: the return over 10 sessions instead of 5.
- `any_flag`: every veto, whatever its flag.
- `with_downgrades`: downgrades counted with the vetoes.
- `masked_primary`: the masked arm's verdicts instead of the raw arm's.

## Fill model

None of its own: the buys are the daily rule's real paper fills (Alpaca paper, market orders 15:50-15:57 ET, as
`docs/prereg/trend_momentum.md` Amendments 10 and 11 describe), and the exit is the journal's close 5 sessions
later or the rule's own sale fill. No simulated fill is involved.

## Pass marks

At the final look, all three: the statistic is <= -0.5%; the permutation p-value is < 0.05; the masked arm's
statistic over the same records is also negative (otherwise the result is labelled `identity_driven`: the model's
memory of the company, not the headline, carried it). Any other outcome (stopped for futility, failed,
identity-driven, inconclusive) means no veto is proposed from this test. Even a pass only allows Product to propose
a veto, by its own decision record, to the stakeholder; it switches nothing on.
