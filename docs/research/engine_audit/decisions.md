# Engine audit: Product's decisions of 21 Nov 2026

- **Date:** 21 Nov 2026. **Author:** Product. **For:** all teams.
- **Report:** [`report.md`](report.md) ("Harden the Engine, Change the Yardstick"), committed unchanged. It read
  the code at commit `3f9bfee`; nothing in it was reproduced by running the engine or a backtest.
- **Mandate:** the stakeholder delegated these decisions to Product on 21 Nov 2026: "decide yourselves the best
  and proceed". The decisions below answer the report's table "Decisions only the owner can take" as far as that
  delegation reaches. Anything that costs money, and the question of US users, stays with the stakeholder.
- **Status:** a record of decisions. This file changes no code, rule, gate mark, setting or database object. Each
  change arrives by its own ticket, with the tests, keys and decision records the standing rules require
  (`CLAUDE.md`, `docs/teams.md`, `docs/decisions/15_engine_v3.md`).

## Decided

| # | Decision | What it means in practice | Tickets |
|---|---|---|---|
| D1 | **The goal is an SPY-like return with smaller drawdowns, not a higher raw return.** | Every later pass mark is set against this goal. "More than SPY" is not promised anywhere, in the product or in research. This matches the goal already recorded in decision 15, section 1. | all of Stage 2 |
| D2 | **Gate v3 will be introduced, by decision record.** | Gate v3 has three parts: a sleeve test (return above an exposure-matched SPY), a book test (the absolute test kept for the combined book) and a family-wise correction across rule families. Gate marks change only by a decision record in `docs/decisions/`; until that record is merged, Gate B (gate v2) stays the gate in force. | 2.5 |
| D3 | **An SPY index core is accepted in principle.** | It goes live only after all three of: a pre-registration in `docs/prereg/`, a run through the engine with `SimBroker`, and two keys (Backend-quant and QA-Ops). **No live change until then.** | 2.7, 2.8 |
| D4 | **The retired intraday rule is to be removed from the live loop.** | The daily soak test of the retired dip rule ends when the removal ships. The removal is itself an engine change and follows the usual rules (tests first, two keys, outside 09:30-16:00 ET). | 1.5, 1.19 |
| D5 | **A hold-out window is to be frozen.** | The last 18 to 24 months of history are held back from research runs; opening the window is logged. The exact length is fixed in the ticket. | 2.6 |
| D6 | **"Veto or halve" continues to bind language models.** A non-LLM ranking overlay is allowed **only as a pre-registered rule**. | No LLM output may set price, size, stop or exit; a model may only veto or halve. A trained ranking model (for example a gradient-boosted one) may order candidates inside an existing rule only if it has its own pre-registration, fixed settings and counted trials, and it is judged by the gate like any other rule. | 3.9, 3.10, 3.12 |
| D7 | **Leverage and short selling stay excluded.** | Standing project rule, unchanged. Not researched further. | none |

## Left with the stakeholder, undecided

Product did not decide these and no ticket may assume an answer.

| Item | Why it stays with the stakeholder | What waits on it |
|---|---|---|
| A German regulatory legal opinion, and when to commission it | Paid; legal exposure | Any live multi-user step |
| Forming a company | Paid; administration | Store apps, a commercial Alpaca app |
| Spending the one-time $125 Databento credit on spread calibration | Paid data is excluded by decision | One of the two calibration routes in ticket 2.3; the free route (the paper account's fills against quotes) is open |
| Admitting US users | Adds US adviser law | Multi-user launch scope |

## In the report's table, not decided on 21 Nov 2026

These rows of the report's table were not part of the decisions above. They stay open until Product or the
stakeholder records a decision; the tickets that touch them say so.

- Non-stock diversifiers such as gold or managed-futures funds.
- Replacing the permanent book stop (ticket 2.10; a risk rule: decision record, tests first, two keys).
- A separate cap for index funds (bears on tickets 2.3 and 2.7; a level limit for multi-day books, which
  `docs/teams.md` reserves for the stakeholder).
- A second person with emergency halt access.

## What does not change

- Paper trading only. No live broker keys, live base URLs or paths around `BrokerAdapter`.
- One decision core for test and live; go/no-go numbers come only from the engine with `SimBroker`.
- A pre-registration before a rule's first run, and a trial row for every run.
- Changes to `risk/gate.py`, sizing or the broker need tests first.
- Still only the stakeholder: production database migrations, paid services, the agent room on, the cloud move,
  real money, and the level limits for multi-day books.

## Milestones

The report's staged plan is tracked as GitHub milestones, one issue per ticket.

| Milestone | Tickets | Stage in the report |
|---|---|---|
| M9 Engine safety | 1.1 to 1.18 | Stage 1A |
| M10 Engine structure | 1.19 to 1.26 | Stage 1B |
| M11 Yardstick and portfolio | 2.1 to 2.11 | Stage 2 |
| M12 Self-testing and bounded AI | 3.1 to 3.12 | Stage 3 |
| M13 Server and multi-user paper | 4.1 to 4.7 | Stage 4 |

## Stakeholder decisions, 21 Nov 2026 (19:38 CET)

The four rows left undecided above are decided by the stakeholder:

- **Non-stock diversifiers (gold, managed futures):** not for now. The product stays US stocks and index funds.
- **The permanent book stop:** replace it (ticket 2.10, #461). Still a risk-rule change: decision record, tests first, two keys.
- **A separate cap for index funds:** yes (tickets 2.7 and 2.8, #458 and #459).
- **A second person with emergency halt access:** no. Instead, when a single transaction would buy or sell a large part of the portfolio, the user is notified and must approve it. To be built with multi-user (M13).
- **Direction:** an automated bot that can later be connected to Alpaca's or Interactive Brokers' API for live trading. The broker layer must therefore stay swappable (M13).
