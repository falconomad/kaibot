# Fail-safe drills (M12 ticket 3.7, #469)

Weekly, scripted, against the simulated venue: each drill plays one bad day end to end with the real engine, the
real watchdog and the real SQL dead-man, and checks the outcome these documents promise
([failsafe.md](failsafe.md), [run_engine.md](run_engine.md#engine-lease-one-engine-whatever-the-machine),
[ledger.md](ledger.md)). The result is a pass or fail record per drill.

- Drills: `tests/drills/drills.py` (built on the deterministic simulation of `tests/dst/`: `SimBroker` behind
  `FaultyBroker`, a simulated Supabase, `SimClock`; the dead-man is migration 0019 in a throwaway local Postgres,
  `tests/pg_harness.py`, never Supabase). No broker account, no Supabase, no key.
- Record: `scripts/run_drills.py --out drills.md`. The `drills` workflow runs it on Sundays at 05:41 UTC (and on
  demand), puts the record in the run's summary and artifact, and opens an issue when a drill fails.
- Each drill quotes the sentences it holds the engine to. `tests/drills/test_drill_docs.py` runs on every pull
  request and fails when a sentence is no longer in its document: a pull request that changes a promise changes the
  drill with it.
- Run locally: `PYTHONHASHSEED=0 PYTHONPATH=src python scripts/run_drills.py` (all, about 4 minutes) or
  `pytest tests/drills/check_drills.py -k lease`.

| Drill | What happens | What must follow (checked) |
|---|---|---|
| watchdog takeover | the engine is killed at 15:51 with its near-close buy working at the venue | the watchdog cancels the `kb` buy once the heartbeat is over 90 s old, writes it to `watchdog_cancels.jsonl` first, raises its halt row and event; the next session sends the buy again; the simulation's invariants hold |
| dead-man stop placement | a multi-day sleeve (no stop, by the rule's design) and an engine silent from 09:40 to after the close; played twice, once with the watchdog, once with the SQL dead-man | each places exactly one GTC stop sell for the sleeve, whole shares, never more than held, 10% below the last price it read; the two place the same shares; neither sells the sleeve at 15:55 / 15:56; one `deadman` halt |
| lease lost | lease renewals fail from 15:49 to 15:53; the next day the engine sleeps past its lease and another engine takes it | no order while the lease is unverified, the buy goes out once a renewal works; after the takeover the engine stops at its next renewal, sends nothing and writes no heartbeat |
| supabase down at start | Supabase is unreachable from before the first start until 15:20 | every start is refused with exit code 3 and "The engine lease could not be checked"; nothing reaches the broker and no heartbeat is written; once Supabase is back the engine starts and the session's buy still goes out (a late decision) |
| supabase down at a restart | the engine has run since D0; Supabase goes at 08:55 on D1 and is back at 15:20 on D2, the engine is killed at 09:31 on D1; D1's decided buy of B reaches its 15:50 send while the engine is reducing only | M10 1.25 (#536): the restart (09:41) is not refused; it comes up "reducing only" (the `Starting REDUCING ONLY` warning); until the lease is confirmed the venue receives no buy and the engine attempts no heartbeat write (every attempt is recorded, failed or not); once Supabase is back the lease is confirmed and the owner config read again, the log says `fully active (entries allowed)` and `engine_lease_confirmed` has `entries: true`; the buy of B goes out once, in D2's 15:50-15:57 window; the simulation's invariants hold |
| journal damaged at start | one entry of a finished session's journal is changed on disk | the start exits with code 4 naming the entry, every later start the same; the watchdog reads the book from Supabase's ledger rows |
| journal damaged while running | the session's decision entry is changed on disk at 11:00 while the engine runs | the engine stops and an `engine_error` event reaches Supabase (fixed by #551: the daily path caught the error and the engine kept running) |

Not drilled here, and why: a full disk, a restore from backup, a release roll-back and a key rotation need the
real machine and the real accounts (the stakeholder's, see below); a clock jump is covered by the deterministic
simulation's `clock_skew` fault on every pull request (`tests/dst/`).

## Design note: the same drills on a second Alpaca paper account (later; nothing of this is built)

The simulated venue answers as Alpaca is documented to answer. What it cannot show is how Alpaca really behaves:
how fast a cancel lands near the close, whether a GTC stop survives the night, what a lookup by client order id
returns right after a timeout, what the activities feed says about a partial fill. The drills above can run against
a real paper venue to find that out, under these rules:

1. **A separate paper account, never the trading one.** Alpaca's dashboard creates additional paper accounts, and
   each has its own API keys ([Alpaca, Paper Trading](https://docs.alpaca.markets/us/v1.1/docs/paper-trading)).
   The number per login is limited (users ask Alpaca for more on its forum), so check that one is free before
   planning on it. Creating one is free; anything paid stays the stakeholder's decision. Shorting off, as on the
   trading account (failsafe.md).
2. **No key in CI.** The drills never run on GitHub's runners against Alpaca: `tests/unit/test_ci_supply_chain.py`
   refuses any workflow that uses a secret or names an Alpaca key, and that stays. The real-venue drills run on the
   stakeholder's machine (or a later server), from a terminal, with the drill account's keys in their own file
   (`~/Projects/kaibot-secrets/drills.env`, next to the trading `.env`, never in the repository).
3. **Never a second engine on the trading account.** The drill engine gets its own data folder
   (`KAIBOT_DATA_DIR=data/drills`), its own journal, its own lease holder id and its own Supabase project or schema
   (a lease row shared with the trading engine would stop one of them; a shared `heartbeat` would confuse the
   watchdog and the dead-man of the trading account). The start script refuses to run with the trading account's
   key id (compare it with the one in `.env`).
4. **Outside market hours where possible.** Most drills need an open market (orders that work, a send window).
   Paper orders fill against the real quotes, so they run in a session; they touch only the drill account.
5. **Same drill code, a different venue.** `tests/drills/drills.py` owns the world (clock, outages, faults); for the
   paper account the clock is real and the faults are real actions: kill the process (`kill -9`), block the network
   to Supabase or Alpaca (a firewall rule on the machine), stop Supabase access by revoking a test key, sleep the
   machine past the lease. The checks are the same functions on what the drill account's API and the drill
   Supabase show afterwards. The record has the same form and goes to `docs/ops/drill_records/` by hand.
6. **What it would add**: the real `kb` cancel timing against the watchdog's 90 s, the real GTC stop at the next
   open, the real lookup lag after a submit timeout (compare with `scripts/probe_alpaca_idempotency.py`), and the
   dead-man's pg_net calls against the real API (today only stand-ins).

Order of work when the stakeholder wants it: confirm a free paper account; a `scripts/drill_venue.sh` that loads
`drills.env`, refuses the trading key, and runs one drill; then one drill a week (watchdog takeover first: it has
the most real timing in it).
