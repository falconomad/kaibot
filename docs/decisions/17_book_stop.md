# Decision 17: the multi-day book's stop is the Gate C bootstrap band, not a permanent line at 1.5 x one drawdown

- **Status:** **proposed** (Quant research, 22 Nov 2026, for Product). It takes effect for the code when the pull
  request that adds it is merged. **It changes no live behaviour when merged**: the switch `mode` in
  `config/book_stop.yaml` stays `fixed` (today's stop) until every step of section 7 is done, and the last of those
  steps is the stakeholder's own go.
- **Stakeholder, 21 Nov 2026** (`docs/research/engine_audit/decisions.md`, "Stakeholder decisions"): "The permanent
  book stop: replace it (ticket 2.10, #461). Still a risk-rule change: decision record, tests first, two keys."
- **Milestone:** M11, ticket 2.10, issue #461. **Evidence:** `docs/research/engine_audit/report.md` (Stage 2, row 2.10:
  "Permanent retirement at 1.5 times one sample's drawdown adds path risk without a tested benefit"); Gate C of
  decision 15 section 7 and `config/gate_v2.yaml` `gate_c`.
- **Files:** this record; `config/book_stop.yaml` (the switch, the band's settings, the tables, `mode: fixed`);
  `src/kaibot/risk/book_stop.py` (pure: the band, the verdict, the fixed line unchanged); `scripts/book_stop_bands.py`
  (builds a band table from an engine + SimBroker run); `tests/unit/test_book_stop.py`.
- **Does not change:** `risk/gate.py` (`book_run_ended`, `BOOK_HARD_CEILINGS.run_drawdown_end_pct = 27`, the levels'
  13 / 21 / 27% in `config/profiles.yaml`), the engine (`engine/core.py` is not touched; the engine PRs #534, #536
  and #538 restructure it and merge after Monday 23 Nov 22:00 CET), the intraday run end (4 / 8 / 12%,
  `HARD_CEILINGS`), the daily stop, "Flatten and stop", the evidence ladder for the end (two checks a minute apart,
  confirmed prices; M9 ticket 1.1), and any retirement already recorded.

## 1. What is replaced, and why

Today (decision 15 section 11, "option A, as tested"), the multi-day book ends when its equity falls 13%, 21% or
27% below its peak (Careful, Balanced, Hunter). That is 1.5 times the maximum drawdown of one ten-year test at the
level's cap. At the end, every multi-day position is sold at market, the rule is **retired across runs**, and only a
single-use `KAIBOT_REENABLE` value set by the stakeholder brings it back.

| Fault | Why it matters |
|---|---|
| One sample | 18.27% is the worst drawdown of **one** path, 2017 to 2026. Another decade of the same rule would have had another worst drawdown. 1.5 times one draw is not a statement about the rule's distribution |
| It ignores time | The same 27% applies on day 5 and in year 8. On day 5 a 20% fall is far outside anything the test saw, and the line does not fire; in year 8 a 27% fall can be inside the rule's normal range, and the line fires |
| Permanent | A retirement is forever unless the stakeholder acts. Selling the whole book at the bottom of a fall, and never buying back, turns a temporary drawdown into a realised one. That is the path risk the audit names |
| It does not fit an index core | A book that is 97% SPY (ticket 2.7) has SPY's drawdown: about 34% in 2020. At 27% it would have been sold near the bottom of March 2020 and retired. The stop would decide the book's result more than its construction would |

Gate C already says how to judge a live record against its test. Its retirement trigger is "live drawdown outside
the 99% block-bootstrap band of the test", with the band drawn for a stretch as long as the live record
(`config/gate_v2.yaml` `gate_c`). This record makes that band the book's stop.

## 2. The band

| Setting | Value | Where from |
|---|---|---|
| Returns resampled | the daily mark-to-market returns of the book's own engine + `SimBroker` run, at the level it trades, on the **research window** (2017-05-01 to 2024-12-31; the hold-out is never read) | decision 16 section 3 |
| Method | stationary bootstrap (Politis and Romano), mean block 20 sessions, **2,000 paths**, seed 17 | `gate_c.bootstrap`, as gate v2 |
| Statistic | the maximum drawdown of each path of h sessions | `mtm.bootstrap_max_drawdowns` |
| Horizons | h = 5, 10, 20, 40, 60, 90, 126, 189, 252, 378, 504, 756, 1008, 1260, 1512, 1764 and the window's length | `book_stop.HORIZONS` |
| Retire line | the **99%** quantile at each horizon | Gate C's retirement trigger |
| Alert line | the **95%** quantile (an event and a dashboard note, nothing is sold) | Gate C's incubation mark |
| Between horizons | the value at the largest listed horizon at or below h (the stricter neighbour). Beyond the last horizon, the last value | |
| Monotone | a table whose 99% line falls as h grows is refused (resampled drawdowns over a longer stretch are never smaller in distribution) | `book_stop.BandTable` |
| Ceiling | the retire line is never above **40%** (proposal; a level limit, the stakeholder's) | section 6 |
| Short records | before 20 live sessions the band at h = 20 is used, not a narrower one | `min_sessions` |

The table is computed **once, on the Mac**, from the run named in the book's pre-registration
(`scripts/book_stop_bands.py`), and committed under `docs/research/results/book_bands/` with the run's report hash,
the window and the settings. The engine reads numbers; it never bootstraps. A new rule, level or configuration needs
a new table, committed before it trades.

## 3. How the live book is judged against it

- **The measured drawdown** is the live record's maximum drawdown since the book's start: the largest fall of the
  book's equity (the run's pot plus the book's P&L, as today) from its running peak, the current tick included. The
  peak and the confirmed prices are those of M9 ticket 1.1 (two readings, confirmed marks), unchanged.
- **The horizon** h is the number of sessions the book has traded since its start (the live record's length), at
  least `min_sessions`.
- **Alert** when the measured drawdown reaches the 95% line: the event `multi_day_book_band_alert` once a day, a
  note on the dashboard. Nothing is sold.
- **End** when it reaches the 99% line (capped at the ceiling) on two checks at least a minute apart, exactly as the
  end is confirmed today (`BREACH_CONFIRM_S`, the journalled first breach that survives a restart).
- `risk/book_stop.verdict` gives one answer for both modes. In mode `fixed` it is `gate.book_run_ended` itself, and a
  property test checks the two agree on every input.

## 4. What happens at the end, and what replaces "retired forever"

| | Today (`fixed`) | With the band (`band`) |
|---|---|---|
| Sold | every multi-day position, at market | the **return sleeves** at market. A book with an index core keeps the core, because a band breach of the core is a market outside the test, and selling SPY then would be market timing that no test supports. A book without a core (today's `trend_momentum`) sells everything, as today (`config/book_stop.yaml` `keep_index_core`) |
| Afterwards | retired across runs; only `KAIBOT_REENABLE=<rule>:<id>` brings it back | **paused for review**: the daily path decides and buys nothing for the book's sleeves. Product reviews within 5 sessions whether the live book is still the book tested (Gate C tracking, the ledger, fills). The book resumes only with a new run of the Gate C report and two keys; the stakeholder may also end it |
| Record | `md_book_end`, `md_retired`, `multi_day_retired` halt row | `md_book_end` with `why: band_99`, the horizon, the line and the measured drawdown; a `multi_day_paused` halt row; the same single-use re-enable id, so a restart never resumes it |

## 5. What replaces the 27% line

- `BOOK_HARD_CEILINGS.run_drawdown_end_pct = 27` **stays** as the ceiling of the fixed line, and the fixed line stays
  the stop in mode `fixed` and for any book without a committed table.
- In mode `band`, the band replaces the line for a book that has a table. The band's own ceiling (40%, proposed) is
  a new level limit that the stakeholder confirms with the switch.
- The intraday run end (4 / 8 / 12%, `HARD_CEILINGS`) is untouched, and so is the daily stop. With the daily path
  on, the intraday end is already measured without the book's P&L (`gate.intraday_equity`).

## 6. Decisions for the stakeholder

1. **Switch.** `mode: band` in `config/book_stop.yaml`, after section 7.
2. **Ceiling.** 40% proposed, so that a 97% SPY core is not sold at a 2020-like fall (about 34%). Lower is safer for
   a book without a core and costlier for a book with one.
3. **Keep the core at the end** (`keep_index_core: true` proposed).
4. **What review means**: who reviews (Product), and in how many sessions (5 proposed).

## 7. Transition (in this order; nothing reaches the paper engine before the last step)

1. This record and its code merge with `mode: fixed`. Nothing changes live; the tests show the fixed mode is today's
   stop, bit for bit.
2. On the Mac: the band table of each book that trades or will trade, from its engine + SimBroker run on the research
   window (for `trend_momentum` at the 30 / 60 / 100% caps, the existing runs' daily CSVs; for `index_core` its
   ticket 2.7 run). Command: `scripts/book_stop_bands.py` (in the issue). The tables are committed by pull request.
3. Engine wiring, a separate story after #534, #536 and #538 have merged: `engine/core.Engine._book_end_check` asks
   `risk/book_stop.verdict` instead of `gate.book_run_ended`; the pause and its events; tests first; a replay diff of
   zero in mode `fixed`. Two keys: Backend-quant writes it, QA-Ops gives the second key.
4. The stakeholder confirms the ceiling, the keep-the-core choice and the review rule, and sets `mode: band`.
   Retirements already recorded stay retired; the band applies to the next start.

## 8. What this record cannot promise

A band from one research window still rests on 7.7 years and a few deep falls. The bootstrap describes the window
it is drawn from, not the future. A live record outside the 99% band means "not the book we tested, or a market
outside the window". It does not mean "losing money", and a record inside it is no proof of skill. The band is
narrower than the fixed line early in a run and can be wider late in a run. Both are intended.
