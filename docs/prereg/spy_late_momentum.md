# Pre-registration: spy_late_momentum

- **Rule:** `spy_late_momentum`
- **Status:** retroactive (written 22 Nov 2026, after this rule's runs; it can never pass a gate)
- **Date:** 2026-11-22
- **Author:** quant research
- **Issue:** #472 (M12 ticket 3.10: the registry refuses unregistered runs)

**Why a retroactive file.** The rule was retired from every level on 12 Nov 2026 (#128). It stays registered so that
the research command can still run it. Since #472 every research run needs a pre-registration, so this file
registers the rule as it is in the code. `Status: retroactive`: it never passes a gate.

## Hypothesis

When SPY's return from the prior close to 10:00 ET is above +0.5 sigma of that window's trailing returns, SPY keeps
rising into the close (Gao, Han, Li and Zhou, 2018, *Market intraday momentum*, Journal of Financial Economics).
The project's own ten-year replay rejected it: -4.94 bp per trip, t -5.42, 0 of 10 years positive (#128).

## Who is on the other side of the trade

Late-day liquidity demand from investors who trade towards the close. Nothing was left after costs in the replay.

## Published evidence

Gao, Han, Li and Zhou (2018), as summarised in `src/kaibot/strategies/spy_momentum.py` and issue #128.

## Frozen settings

As in `src/kaibot/strategies/spy_momentum.py` at the date of this file: sigma over 60 sessions, entry at 15:30 ET when
the 10:00 return is above +0.5 sigma, a market-on-close exit sent at 15:45, a protective stop 3 x ATR(5-minute) below
the entry, at most one trade a day, long only, sized by the risk gate.

## Neighbours (at most five)

- None.

## Fill model

Cost model v1 of the replay, minute bars, the close at the market-on-close print.

## Pass marks

None that can be met: a retroactive pre-registration never passes a gate.
