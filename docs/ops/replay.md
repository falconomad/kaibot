# Session replay (M12 ticket 3.4, #466)

`scripts/replay_session.py` runs a recorded session again through the code of a commit and compares what the
engine did: **decisions** (which evening decided for which session, late or not, which names), **sends** (each
window's targets and shares, and a late send's `late_of`), **orders** (every order intent, its size and last state)
and the **ledger** (shares per symbol and the cash the fills moved, folded by today's `Ledger`). A difference fails
unless `tests/replay/allowlist.toml` acknowledges it with a reason, in the same pull request that causes it.

The `replay` workflow runs it on every pull request, on main and every night (03:23 UTC); the ordinary `ci` run
replays the fixtures too (`tests/replay/test_replay_session.py`). A nightly difference opens an issue.

## What is replayed

| Session file | Comes from | What is compared |
|---|---|---|
| `tests/replay/fixtures/sample_main.jsonl` | the simulator (tests/dst) on main: one buy, one sale, no fault | everything, re-run through the commit |
| `tests/replay/fixtures/pre_m9_*.jsonl` | the engine of `84250c7`, before milestone M9, on three hand-written scenarios | everything; every difference is one of M9's changes (1.2, 1.3, 1.4, 1.8) and is acknowledged |
| `tests/replay/sessions/*.jsonl` | sessions of the engine on the Mac, only if the stakeholder adds them | the ledger, folded by today's code, against what the engine published (see below) |

A simulator session carries its inputs (seed, plan, outages, faults), so the whole session runs again. A Mac session
does not carry its market data, so its decisions and orders cannot be derived again: the replay lists them, folds
the recorded book with today's code and compares the shares with the positions the engine published that day (and,
given the journal file itself, opens it with today's `Journal` and verifies every checksum). That is the upgrade
path: today's code reads yesterday's book the same way. Re-deriving a live session's decisions needs that day's
daily store and minute bars beside the journal; that is not built.

> **Limitation: a live (Mac) session is checked by the ledger fold and the checksums only.** Its decisions, sends
> and orders are listed but not run again, so a change in what the engine *decides* is caught only by the
> simulator fixtures, never by a live session file. A live session file passes as long as today's `Ledger` folds
> its book to the shares the engine published and today's `Journal` accepts its checksums.

Run it locally:

```bash
PYTHONHASHSEED=0 PYTHONPATH=src python scripts/replay_session.py replay tests/replay/fixtures/*.jsonl
PYTHONHASHSEED=0 PYTHONPATH=src python scripts/replay_session.py record "missed window" --out /tmp/x.jsonl
```

A pre-M9 fixture was recorded with main's simulator and the old engine on the path:
`git worktree add /tmp/pre9 84250c7 && cp src/kaibot/broker/faulty.py /tmp/pre9/src/kaibot/broker/`, then
`PYTHONHASHSEED=0 PYTHONPATH=/tmp/pre9/src python scripts/replay_session.py record "late start" --out tests/replay/fixtures/pre_m9_late_start.jsonl`
(and `"missed window"`, `"flatten partial fill on D3"` → `pre_m9_flatten_partial.jsonl`).

## Exporting the last session from the Mac (read-only)

On the Mac, in a Terminal, with the engine running or not:

```bash
cd ~/Projects/kaibot && python3 scripts/replay_session.py export data/live/journal.sqlite --out ~/Desktop/kaibot-session-$(date +%F).jsonl
```

- It needs only the Python that comes with the Mac (no virtual environment, no `.env`, no key) and makes no network
  connection.
- It opens the journal **read-only** (SQLite `mode=ro`; `immutable` when no engine has it open, so not even the
  two empty side files are created). The engine's file is never written, locked for writing or moved.
- It keeps the last session (`--sessions N` for more): the run, the decisions, the sends, order intents and states,
  fills, the other book entries and the published ledger positions. It drops every other Supabase row (events,
  heartbeats, run rows), the broker's order ids, the engine's fingerprint and the retirement ids, and replaces the
  broker's execution and activity ids with a hash. No account number or account id is in what it keeps. If anything in what it
  would write looks like a key (an Alpaca key id, a Supabase secret, an Anthropic key, a token), it writes nothing.
- The file contains symbols, share counts, prices and the paper run's cash: open it in any text editor before
  deciding. **Whether to upload it is the stakeholder's decision.** The repository is public: a file added to it can be read by anyone. To have it replayed every night, it goes into
  `tests/replay/sessions/` in a pull request; nothing in CI fetches anything from the Mac.

## When the replay fails

- An intended change (a fix that changes what the engine does): add an `[[ack]]` with `journal` (the file name
  without `.jsonl`), `key` (as the report prints it), `was` and `now` (the recorded and the replayed value as JSON,
  `null` for absent; the report prints both) and a `reason` naming the issue. No globs: an entry acknowledges one
  difference with one value, so a later change to the same key still fails.
- "acknowledged difference no longer happens": the change an entry acknowledges was undone (a revert of an M9 fix
  shows up this way: the replay then matches the pre-M9 recording again). If that is intended, or the fixture was
  recorded again, remove the entry.
- Not intended: an engine defect. Open an issue with the session file and the report; do not acknowledge it.
