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

## Dead code

Removed in #122 (no caller): `src/kaibot/engine/run.py`, `docker/` (superseded by the root `Dockerfile` and
`compose.yaml`), `apps/api` and its test, `src/kaibot/store/`, `src/kaibot/features/bloodbath.py` and its 307
indicator parity cases, and 13 `.env.example` keys nothing reads.

Kept (each has a caller or belongs to another team's open work): `strategies/dip_reversal.py` (still every level's
live rule in `profiles.yaml`), `broker/base.py` (`tests/unit/test_broker_protocol.py`), `engine/alpaca.parse_movers`
(movers parity test; engine code), `shadow_costs.max_fill_qty`, `core.new_idempotency_key`,
`scripts/make_bloodbath_fixtures.py` (still records the movers fixture), the unused tables `kaibot.universe_daily`,
`kaibot.macro_event` and column `daily_result.cash_ret` (dropping them is a production migration).
