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
| State, outside git (`data/` is ignored) | `data/runner/done.json`, `runner.log`, `logs/<id>.log`, `out/<id>.md`, `paused.json` |
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
| `scripts/export_results.py` | `--rule` | 20 min; `writes: docs/research/results/`, opened as a PR by the runner (below) |
| `scripts/market_alarm_report.py` | `--start --end --flags-only --rankings-only` | heavy, 60 min |
| `kaibot.agents.evalset.golden` | none | the stub run only (`--live` costs money, `--record` writes a tracked file) |
| `scripts/shadow_results.py` | none | 10 min; the helper's sign-in only, no `--env` (below) |

`scripts/shadow_results.py` (#371) prints the same four tables as `scripts/shadow_results.sh` without any
write-capable key: it signs in as the helper (`~/Projects/kaibot-secrets/helper.env`, `docs/ops/helper_role.md`) and
calls the read-only `kb_helper_shadow_results()` of migration 0047. It never gets `--env`, never reads `DATABASE_URL`
or the service key. Until 0047 is applied it prints "migration 0047 is not applied yet" and exits 3 (the job is
posted as `failed` with that line). Without `helper.env` it says so and exits 2.

## Files a job writes in the checkout

Some jobs write tracked files: `scripts/export_results.py` rewrites `docs/research/results/`. Left on disk, they
made the next `git pull --ff-only` fail and every later job stall until the owner committed by hand. After every job
the runner now looks at the checkout (`git status --porcelain -z --untracked-files=all`; ignored paths such as
`data/` and `.venv/` are not changes):

- **Clean:** nothing to do.
- **Changes only inside the job's `writes:` folders** (per script in `allowed.yaml`, folders under `docs/` only):
  the runner checks each file (a regular file, at most 2 MB, UTF-8, and nothing in it that redaction would hide:
  no key, token, email or home path), commits them on a new branch `runner/<job id>` as
  "Kaibot research runner" (a noreply address, never the owner's), pushes **that branch only** to
  `https://github.com/falconomad/kaibot.git` with gh's credentials (`gh auth git-credential`), opens a pull request
  with `gh api --method POST repos/falconomad/kaibot/pulls`, and puts the PR link in the job's result comment.
  Then `git checkout main`: local `main` was checked to equal `origin/main` before the commit, so switching back
  changes only the committed paths on disk. There is no `git reset --hard` and no `git clean`, so `data/` and any
  other untracked file are never touched. The commit message and PR body say they came from the research runner
  and are redacted like comments. The PR is reviewed and merged like any other.
- **Anything else** (a change outside those folders, a file that fails the checks, the checkout not on `main`, local
  `main` not equal to `origin/main`, a git error): nothing is committed, moved or removed. The result comment
  carries a **Warning** with the paths, `data/runner/paused.json` records the job, and the runner runs no more
  jobs (and does not pull) until the checkout is clean. Once it is, the next tick logs "jobs resume" and carries on.

Without gh (missing or not logged in) the results are committed on the local branch `runner/<job id>` and not
pushed, and the checkout still goes back to clean `main`, so nothing is lost and later jobs still run. The runner
never pushes to `main` and never pushes any branch but `runner/<job id>`.

## Safety rules

- **The allow-list only.** A job names a key of `allowed.yaml` and nothing else. The runner refuses an allow-list
  that names the engine, its service, its lease, `run_engine`, `go_live` or anything outside `scripts/` or the
  `kaibot` package. No shell is used anywhere: each job is an argv list run with `.venv/bin/python`, with
  `PYTHONPATH=src`.
- **Arguments are validated.** Each flag must be listed for that script and its value must fully match the
  pattern (ASCII only). A flag may not appear twice, and there is a cap on the number and length of arguments.
  `--env`, `--data`, `--out`, `--root`, `--trials`, `--universe-file`, `--open-holdout`, `--live` and `--record`
  can never be allowed. Every allowed flag must be a real `add_argument` option in the script's source. argparse
  expands abbreviations (`--d` becomes `--data`), so an abbreviation can never slip through.
- **Secrets.** The runner never opens `~/Projects/kaibot-secrets/.env`. For a script marked `env` it adds
  `--env <that path>`, and the script reads only the line it needs. A job starts with a short, fixed environment
  (`HOME PATH LANG LC_ALL TMPDIR TZ SSL_CERT_FILE`, plus `PYTHONPATH`). The runner's own environment, including any
  `GH_TOKEN`, is never passed on.
- **Redaction.** Before anything is posted or saved to `out/`, the runner blanks API keys and tokens
  (Anthropic, GitHub, AWS, Alpaca, Supabase, JWTs), `NAME_KEY=value`-style assignments, bearer tokens, emails,
  URLs that carry credentials, private keys and long high-entropy strings. It also replaces the home directory
  with `~`. A rejection reason is posted inside a code block, so text from the queue cannot @-mention anyone. The full raw log stays on the Mac in
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
  `scripts/service.sh`, and never runs launchctl. `git pull --ff-only` runs only when the checkout is on `main` and
  clean (a dirty checkout pauses the runner, see above). If the pull fails, the failure is logged and the local
  queue is used.
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
