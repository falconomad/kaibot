# Skipped tests (#256)

What the CI python job skips, and why. Before #256 every run reported 14 skipped and nobody had the list. Now:
- CI prints every skip with its reason (`pytest -rs`).
- A part of the suite that skips more than `KAIBOT_MAX_SKIPPED` (3, the number below) fails (`tests/conftest.py`).
- A new skip therefore has to be written down here, or made to run.

## Run in CI since #256: 12 tests

These are the SQL tests that only needed a scratch Postgres with the migrations applied (`KAIBOT_TEST_PG`). With `KAIBOT_TEST_PG_AUTO=1` (set in CI), `tests/pg_scratch.py` sets one up:
- it starts a private PostgreSQL 16 cluster in a temp directory, unix socket only. GitHub's ubuntu runner has the binaries.
- it builds a template database with Supabase stand-ins (roles, `auth.uid()` from the session setting `test.uid`, the `net` / `vault` / `cron` schemas, the legacy tables 0003 locks).
- it applies **every migration file in order**. Only 0006's two `create extension if not exists pg_cron / pg_net` lines are left out: those extensions exist only on Supabase, and the stand-in schemas replace them.
- each xdist worker gets its own copy of the template database.
- it never touches Supabase or production, costs nothing, and adds about 5 s per part.

So every CI run also checks that **migrations 0001 to the latest apply in order** on plain Postgres 16.

| Test | Skip condition before | Verdict |
|---|---|---|
| `tests/test_config_sql.py::test_sql_and_python_validators_agree` | `KAIBOT_TEST_PG` unset | runs in CI (migration 0011/0014 `config_check` against the Python validator, up to 2,000 fuzzed configs) |
| `tests/test_custom_tickers_sql.py` (9 tests) | `KAIBOT_TEST_PG` unset | runs in CI (migration 0014: ticker checks, saved lists, the accepted-ticker rule) |
| `tests/test_engine_lease.py::test_real_sql_race_renew_takeover_release` | `KAIBOT_TEST_PG` unset | runs in CI (migration 0013: the engine lease) |
| `tests/test_engine_lease_machine.py::test_real_sql_a_copied_holder_id_on_another_machine_is_refused_and_cannot_renew` | `KAIBOT_TEST_PG` unset | runs in CI (migration 0041, #562: the lease is bound to holder and machine) |

## Skipped on purpose: 3 tests

| Test | Skip condition | Why, and where it runs |
|---|---|---|
| `tests/test_engine.py::test_live_matches_replay_on_a_real_day` | no `data/bars_1m/symbol=SPY` or `KAIBOT_SLOW` unset | It needs the M0 replay data, which is not in the repository, and takes about 7 minutes. Run on the Mac with `KAIBOT_SLOW=1 pytest tests/test_engine.py -k real_day`. |
| `tests/test_journal_pre0018.py::test_pg_before_0018_the_engine_rows_land_in_the_old_tables` | `KAIBOT_TEST_PG_PRE` unset | It needs `psycopg`, which is not in the locked dependency set (`uv.lock`), and a database in production's pre-0018 state. Adding `psycopg` as a test dependency would let it run on the same scratch cluster (a template stopped before 0018). That is a separate small story because it changes the lock. |
| `tests/test_journal_pre0018.py::test_pg_after_0018_the_engine_rows_and_the_ledger_land_with_engine_ids` | `KAIBOT_TEST_PG_POST` unset | Same as above, with 0018 applied. |

## Conditional skips that do not fire in CI
These are skips in the code that CI does not hit today. They are not counted, and if one ever fires, the budget above catches it:
- `pytest.importorskip("hypothesis")` and `pytest.importorskip("scipy.stats")`: both are in the locked set.
- `skipif(git is None)`: git is on the runner.
- "a shallow clone has no history of the pre-registrations": CI checks out with `fetch-depth: 0`.
- `tests/pg_harness.py` (the dead-man drills): skipped only without Postgres binaries.
- `tests/test_universe_files.py`: skipped only when a universe file is missing.

## Removed as dead
None.

## Locally
`KAIBOT_TEST_PG_AUTO=1 pytest -q -rs` runs the SQL tests on a scratch cluster when Postgres 16 binaries are installed. As root, the server runs as the `postgres` user. `KAIBOT_TEST_PG=...` still points them at a database of your own instead.
