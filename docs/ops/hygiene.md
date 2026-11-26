# Code hygiene: dependencies, types, import rules (#122)

## One locked dependency set

- `pyproject.toml` says what Kaibot needs: the engine set (`dependencies`), `research` (scipy) and the `dev` group.
- `uv.lock` pins every package for every platform and Python 3.12+. It is the only lock.
- `requirements-engine.txt` and `requirements-research.txt` are hash-pinned exports of `uv.lock` for plain pip,
  because the Mac scripts and the Dockerfile use pip. Never edit them by hand.

| Who | Installs |
|---|---|
| CI | `uv sync --locked` (engine + research + dev), after `scripts/lock_deps.sh --check` |
| Docker image | `pip install --require-hashes -r requirements-engine.txt` |
| `run_engine.sh`, `fetch_daily.sh`, `fetch_history.sh`, `fetch_earnings_dates.sh` | same, into `.venv` |
| `run_experiments.sh`, replay and report scripts | `pip install --require-hashes -r requirements-research.txt` (engine + scipy) |

Change a dependency: edit `pyproject.toml`, run `./scripts/lock_deps.sh` (or `uv lock --upgrade-package X` first),
commit `pyproject.toml`, `uv.lock` and both requirements files. CI fails if they disagree. The Docker base image is
pinned by tag and digest in `Dockerfile`; bump it on purpose, like a package.

Removed in #122 because nothing imports them: uvicorn, pydantic-settings, httpx, websockets, sqlalchemy, psycopg,
alembic, apscheduler, duckdb, datasketch, structlog, tenacity, feedparser, plus fastapi (only the deleted `apps/api`
used it), the `nlp` extra (transformers, torch) and the dev packages pytest-asyncio, respx and freezegun.
(httpx still arrives as a dependency of anthropic.)

## Dependency updates, audits and the browser policy (audit 2 7.3, #713)

- **Dependabot** (`.github/dependabot.yml`): every Monday, one grouped pull request per set: Python (`uv`, the
  root `uv.lock`), the audit tools (`.github/audit`), the web app (`npm`, runtime and dev tools apart) and the
  GitHub Actions (still pinned by commit; Dependabot moves the sha and the tag comment). The Python ecosystem is
  `uv`, not `pip`: `pip` would rewrite the requirements exports without `uv.lock`. A Python update's PR is red on
  `lock_deps.sh --check` until `./scripts/lock_deps.sh` is run on its branch and the three exports are pushed. Its
  alerts and security updates (Settings > Code security) are repository settings: the owner's.
- **npm audit** (`ci.yml`, job `web`): `npm audit --omit=dev --audit-level=high` blocks, so nothing with a high or
  critical advisory ships to the browser. The same audit of the dev tools runs but does not block yet: on 25 Nov 2026
  vitest 2 pulled in tinypool (critical), vite-node, vite and esbuild with open advisories, fixed only by
  vitest 4.1.11 or later (a major upgrade, a frontend story). Make that step blocking once it is done.
- **Content-Security-Policy** (`apps/web/vercel.json`, every path): sent as `Content-Security-Policy-Report-Only`,
  so a violation is a console line and nothing is blocked. It names what the browser uses: our files; the Inter
  sheet's inline `onload` by hash; Google Fonts; the logo hosts (tickerlogos, Finnhub, FMP); the Supabase project
  over https and wss. Alpaca is not listed: only the functions in `api/` call it. `apps/web/src/csp.test.ts` keeps
  the policy and the code in step (a new logo host, inline handler or project fails it). To enforce: watch the
  production console for a week, then rename the header to `Content-Security-Policy`.

### Security settings only the owner can change

The live Supabase advisors (second engine audit, 25 Nov 2026) raise two notes that no pull request can clear:

| Advisor note | Why it stays for now | What the owner does |
|---|---|---|
| Leaked-password protection off (Auth) | Supabase offers it on the Pro plan and above; the project is on the free plan. Sign-up is by invite only (0045) and the owner's account is the only one that trades | Nothing on the free plan. On a paid plan: Authentication > Providers > Email > "Prevent use of leaked passwords" on |
| `pg_net` installed in schema `public` (lint 0014) | pg_net cannot be moved with `alter extension ... set schema`: it must be dropped and created again, which drops `net._http_response` and any request in flight. The dead-man (0019) reads that table every minute 13:00-21:59 UTC on weekdays | In a planned window outside 13:00-22:00 UTC on a weekday (or at a weekend): `drop extension pg_net; create extension pg_net with schema extensions;`, then the dead-man drill of `failsafe.md` (`select kaibot.deadman_check(force => true);` twice a minute apart, engine stopped) and the advisor again |

## Types and import rules (non-blocking)

CI job `hygiene` runs `mypy` (config in `pyproject.toml`, `[tool.mypy]`) and `lint-imports` (`[tool.importlinter]`).
It is `continue-on-error`; the counts appear in the job summary. Make it blocking once the counts reach zero.

Import rules: live code (`engine`, `risk`, `broker`, `agents`, `strategies`, `features`, `news`) must not import
`kaibot.replay`, and `kaibot.replay` must not import `kaibot.engine`. `evaluation`, `ingest` and `common` are shared.

Baseline on 12 Nov 2026 (branch of #122, main @ the PR's base):

| Check | Count |
|---|---|
| mypy | 254 errors in 44 of 104 files |
| live must not import research | broken: 19 import chains (for example `strategies.orb`, `strategies.news_continuation`, `strategies.spy_momentum` -> `replay.data` / `replay.features`; `engine.shadow` -> `replay.sim`) |
| research must not import live engine | broken: 1 (`replay.config_replay` -> `engine.userconfig`) |

## Migration record (#361)

`tests/test_migrations_pending_hygiene.py` (repo only, no database or network) fails when `docs/ops/migrations_pending.md` and `migrations/` disagree: file numbers must be unique and run 0001, 0002, ... without gaps; every file has exactly one `- NNNN: applied ...` or `- NNNN: pending ...` entry; the "Last checked against main on <date> (<set> applied; <set> pending)" summary matches the list (`none pending` when nothing is); every pending migration has a step, steps run 1, 2, ... and name real files, and an entry's "step N" is its own row; no migration header claims a status (no `-- APPLIED` or `-- NOT APPLIED` line; a header cannot be updated when a file is applied, so the status lives only in `docs/ops/migrations_pending.md`, #368). No other runbook in `docs/ops/` may call a migration file (backticked, `NNNN_name.sql`) `not applied` or `pending` while the list says applied (#686: `failsafe.md` said so of 0017 and 0019 for two weeks). CI runs the file in the suite and again as its own job `migrations-record`, so it can be required on `main` by name. So a new migration's PR adds its file, its list entry, its step and the summary together.

## Dead code

Removed in #122 (no caller): `src/kaibot/engine/run.py`, `docker/` (superseded by the root `Dockerfile` and
`compose.yaml`), `apps/api` and its test, `src/kaibot/store/`, `src/kaibot/features/bloodbath.py` and its 307
indicator parity cases, and 13 `.env.example` keys nothing reads.

Kept (each has a caller or belongs to another team's open work): `strategies/dip_reversal.py` (still every level's
live rule in `profiles.yaml`), `broker/base.py` (`tests/unit/test_broker_protocol.py`), `engine/alpaca.parse_movers`
(movers parity test; engine code), `shadow_costs.max_fill_qty`, `core.new_idempotency_key`,
`scripts/make_bloodbath_fixtures.py` (still records the movers fixture), the unused tables `kaibot.universe_daily`,
`kaibot.macro_event` and column `daily_result.cash_ret` (dropping them is a production migration).

## CI speed (#221)

The suite runs in two disjoint parts on two runners, each with 4 pytest-xdist workers (dev group):
`KAIBOT_TEST_SHARD=1/2` and `2/2` (hook in `tests/conftest.py`, helpers in `tests/ci_shard.py`). Every test lands in
exactly one part (CRC32 of its node id), so the two jobs together run exactly what one plain `pytest -q` runs;
`tests/unit/test_ci_shard.py` checks that. No test was skipped, removed or weakened; the collected count did not fall
and every Hypothesis `max_examples` is unchanged.

Jobs: `python-tests (1/2)` and `python-tests (2/2)` run lock check, ruff and their part; `python` waits for both and
fails unless both passed (it runs even when they fail, so it is never "skipped"). `python` stays the check to require.

Measured on PR #227 (`ubuntu-latest`, public repo: 4 vCPU, 16 GB):

| Setup | pytest | Notes |
|---|---|---|
| before: one job, serial `pytest -q` | 837 s (job 14 min 10 s) | |
| one job, `-n 4 --dist loadscope` | 262 s | big modules pin one worker |
| one job, `-n 4 --dist worksteal` | 194 to 384 s | runner CPU decides: about 250 s on Xeon 6975P / EPYC 9V74, about 380 s on EPYC 7763; peak memory 7 to 8.9 GB of 16 GB |
| two parts, `-n 4 --dist worksteal` each | 127 to 222 s per part | chosen: push to green `python` 3 min 56 s, 3 min 06 s, 2 min 51 s on three runs in a row (slowest part ran on EPYC 7763); peak memory 4.3 to 7.0 GB per runner |

Tried and dropped: `OMP_NUM_THREADS=1` (no change), 6 workers (no change: a 4 vCPU runner is 2 cores with SMT),
turning off the warnings plugin (no change; the ~124,000 pandas `Series.values` deprecation warnings come from
`src/` and are left to their owners). Caching does not help: install and lint take under 10 s, pytest is the rest.

Run the same thing locally: `uv sync`, then `uv run pytest -q -n auto --dist worksteal`, or one part with
`KAIBOT_TEST_SHARD=1/2 uv run pytest -q -n auto`. Plain `pytest -q` still works and runs everything serially.
Every CI log ends with the 30 slowest tests (`--durations=30`) and the job summary shows peak memory and CPU model.
If the repo turns private (2 vCPU, 7 GB runners), use `-n 2`.
