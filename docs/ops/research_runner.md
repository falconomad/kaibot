# Research runner (owner's Mac mini)

Product works in the cloud, with no access to the Mac, its data or its secrets. The research runner lets Product
queue **approved** research commands in the repo. The Mac runs them and the results come back to GitHub as issue
comments, so the owner doesn't have to paste anything.

| Piece | Where |
|---|---|
| Queue (Product edits this) | `ops/runner/queue.yaml` |
| Allow-list (QA-Ops key + owner) | `ops/runner/allowed.yaml` |
| Runner, one tick | `scripts/research_runner.py` |
| launchd template, every 15 min | `ops/runner/com.kaibot.research-runner.plist` |
| Install / uninstall / status | `scripts/install_runner.sh` |
| State, outside git (`data/` is ignored) | `data/runner/done.json`, `runner.log`, `logs/<id>.log`, `out/<id>.md` |
| Tests (fakes only, no network) | `tests/unit/test_research_runner.py` |

## How Product queues a job

1. Add an entry to `ops/runner/queue.yaml`, either in a PR (it runs once merged) or as a direct commit to `main`.
   Only `main` is ever run.

   ```yaml
   jobs:
     - id: gbm-prereg-2026-11-24            # unique, [A-Za-z0-9._-], max 64; never reuse an id
       script: scripts/trend_momentum_gbm.py
       args: ["--cost-model", "v2"]
       posts_to_issue: 474
   ```

2. Check it locally or in the cloud: `python scripts/research_runner.py --check` (exit 1 if any job would be
   rejected). CI runs `tests/unit/test_research_runner.py::test_the_committed_queue_is_valid`, so a bad entry fails
   the PR.
3. Within about 15 minutes the Mac pulls `main`, runs the job, and posts on the issue a comment headed
   `Research runner job <id>`: the status (`ok`, `failed` or `timeout`), the exit code, the minutes taken, the commit,
   and the last 150 lines of output (redacted). A rejected job gets a comment that gives the reason and is never run.
4. To run the same thing again, add a **new** id. Ids in `done.json` are skipped forever, including jobs that
   failed, timed out or were rejected.

Allowed scripts and flags (the full patterns are in `allowed.yaml`):

| Script | Flags | Notes |
|---|---|---|
| `scripts/index_core.py` | `--start --end --universe --fixture` | heavy, 180 min |
| `scripts/index_core_book.py` | the same + `--control-runs` | heavy, 240 min |
| `scripts/trend_momentum_gbm.py` | the same + `--cost-model --control-runs` | heavy, 240 min |
| `scripts/fetch_sec_fundamentals.py` | `--status --build --refresh --max-age-days --limit` | SEC, gets `--env`, heavy, 120 min |
| `scripts/export_results.py` | `--rule` | 20 min; writes `docs/research/results/` in the Mac's checkout (not committed by the runner) |
| `scripts/market_alarm_report.py` | `--start --end --flags-only --rankings-only` | heavy, 60 min |
| `kaibot.agents.evalset.golden` | none | the stub run only (`--live` costs money, `--record` writes a tracked file) |

`scripts/shadow_results.py` was asked for but does not exist. The shadow results come from `scripts/shadow_results.sh`,
which needs `DATABASE_URL` from the secrets file, and the runner never reads that file, so the script is not listed.

## Safety rules

- **The allow-list only.** A job names a key of `allowed.yaml` and nothing else. The runner refuses an allow-list
  that names the engine, its service, its lease, `run_engine`, `go_live` or anything outside `scripts/` or the
  `kaibot` package. No shell is used anywhere: each job is an argv list run with `.venv/bin/python`, with
  `PYTHONPATH=src`.
- **Arguments are validated.** Each flag must be listed for that script and its value must fully match the
  pattern (ASCII only). A flag may not appear twice, and there is a cap on the number and length of arguments.
  `--env`, `--data`, `--out`, `--root`, `--trials`, `--universe-file`, `--open-holdout`, `--live` and `--record`
  can never be allowed.
- **Secrets.** The runner never opens `~/Projects/kaibot-secrets/.env`. For a script marked `env` it adds
  `--env <that path>`, and the script reads only the line it needs. A job starts with a short, fixed environment
  (`HOME PATH LANG LC_ALL TMPDIR TZ SSL_CERT_FILE`, plus `PYTHONPATH`). The runner's own environment, including any
  `GH_TOKEN`, is never passed on.
- **Redaction.** Before anything is posted or saved to `out/`, the runner blanks API keys and tokens
  (Anthropic, GitHub, AWS, Alpaca, Supabase, JWTs), `NAME_KEY=value`-style assignments, bearer tokens, emails,
  URLs that carry credentials, private keys and long high-entropy strings. The full raw log stays on the Mac in
  `data/runner/logs/<id>.log`. Redaction works by pattern, so treat a posted comment as public.
- **One job at a time.** A lock (`data/runner/runner.lock`) is held for the whole tick, and launchd never starts
  a second copy while one is running. Each tick runs at most 5 new jobs.
- **SEC.** A job marked `sec` also takes `data/runner/sec.lock`. It is **deferred** to the next tick, not marked
  done, while that lock is held or while another bulk SEC fetcher (`fetch_earnings_dates`, `fetch_fundamentals`,
  `fetch_spinoffs`, `fetch_listings`, `fetch_sp500_history`, `fetch_sec_fundamentals`) is running on the Mac.
- **Runaway jobs.** Every script has a timeout. When it passes, the job's whole process group gets SIGTERM, then
  SIGKILL. A job is recorded as `started` before it runs, so a job that crashes the Mac is not started again on
  its own.
- **Market hours.** Allowed (stakeholder, #600, 24 Nov 2026). Heavy jobs run under `nice -n 10`, so the engine
  keeps priority.
- **The engine.** The runner never starts, stops or restarts the engine, never touches its lease or
  `scripts/service.sh`, and never runs launchctl. `git pull --ff-only` runs only when the checkout is on `main`. If
  the pull fails (for example because of a local change), the failure is logged and the local queue is used.
  Pulling only updates files on disk. The engine picks up `main` only when it restarts (#600).
- **Posting.** Results go out with `gh api --method POST repos/falconomad/kaibot/issues/<n>/comments` (REST, with
  the body taken from a file). If gh is missing or not logged in, the result stays in `data/runner/out/<id>.md`
  and the log says so. A copy is kept there either way.
- **Trust boundary.** Anyone who can push to `main` can queue jobs, and could also change the runner or the
  allow-list. Changes to `allowed.yaml` and `scripts/research_runner.py` need the QA-Ops key, and the owner should
  see them before they merge.

## One-time setup (owner, on the Mac)

```bash
cd ~/Projects/kaibot && git checkout main && git pull --ff-only
# gh for posting results (skip if `gh auth status` already says you are logged in)
brew install gh
gh auth login --hostname github.com --git-protocol https --web   # needs repo access to falconomad/kaibot
./scripts/install_runner.sh            # installs com.kaibot.research-runner (every 15 min) and checks the queue
./scripts/install_runner.sh --status   # loaded? gh logged in? last log lines
```

`.venv` is the same one the research scripts already use (`.venv/bin/python`, with the research and ml extras for
`trend_momentum_gbm.py`). To remove the runner, run `./scripts/install_runner.sh --uninstall`; `data/runner` is
kept. The log is `data/runner/runner.log`. To run one tick by hand, use `.venv/bin/python scripts/research_runner.py`.
