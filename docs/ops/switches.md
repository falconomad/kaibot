# Protection switches: defaults and why

Audit 2, ticket 5.15 (#695): protections that were built and tested but left off. This page records each one's
default, who decided it and why. The switches themselves are declared once, with their owner and review date, in
`src/kaibot/common/switches.py` (the settings registry, M10 1.23); `config/book_stop.yaml` holds the book stop's
mode. Every switch here takes effect at the engine's next start.

| Protection | Where | Default | Decided | Why |
|---|---|---|---|---|
| Daily-data checks that block buys | `KAIBOT_DAILY_CHECKS_BLOCK` (`kaibot.ingest.entry_checks`) | **on**: `benchmark_bar_missing`, `daily_bar_stale`, `price_not_positive`, `split_not_applied`, `universe_stale` | Product, 26 Nov 2026, under the stakeholder's full permission (#600); #695 | A buy sized on a daily store that is late, stale, has a bad price or a split it does not know is a buy on wrong numbers. The checks block **buys only**: sales, exits, stops, cancels and the flatten never ask, so the book can always be reduced. A market check that blocks holds every buy (the engine's state is `reducing_only`); a stock check holds that stock's buy. A check that raises blocks (fail closed). Each block clears by itself when the data is right. Switch off with `KAIBOT_DAILY_CHECKS_BLOCK=off`. |
| `price_jump` (one of the daily checks) | `KAIBOT_DAILY_CHECKS_BLOCK` | **off** (not in the default set) | Product, #695 | A real news move looks the same as a bad bar, so it would hold genuine buys, and the pre-send quote collar below already checks every buy against its live quote. Name it in a comma list to switch it on. |
| Pre-send quote collar | `engine/execution.PreSendGate` (M9 1.16, #441), wired by `live_daily.presend_quotes` | **on** in the live process (no switch) | M9 1.16; confirmed by #695 | A near-close buy is withheld when the stock is halted, its quote is older than 300 s, both sides of the quote are more than 10% from the sizing close, or its value is above the book's per-name cap. A sale is never withheld. It is off only where no live quote source exists (replays, SimBroker research, tests), so research results cannot change. |
| Book stop in band mode | `config/book_stop.yaml` `mode` | **fixed** (`mode: fixed`) | Stakeholder, #600 (item 1); #695 leaves it | Switching to `mode: band` needs the owner: the band tables from the owner's Mac, the engine wiring with two keys and zero replay difference in fixed mode, then the switch as its own reviewed change (decision 17, section 7). |
| Minute-bar data checks that block entries | `KAIBOT_DATA_CHECKS_BLOCK` (`engine/market.py`) | **off** (unchanged) | not part of #695's ruling | Left as it was; a change needs its own ruling. |

## How the default change was checked (#695)

- The unit tests of the switch: unset or empty means the five checks above, `off` means none
  (`tests/unit/test_entry_checks.py`).
- The DST seeds 1-200 in book-loop mode with the checks wired to a daily store that follows the world's nightly
  refresh, against the same seeds with the checks off: the result is in the pull request of #695.
