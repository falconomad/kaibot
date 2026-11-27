# Research runner (owner's Mac mini)

Product works in the cloud, with no access to the Mac, its data or its secrets. The research runner lets Product
queue **approved** research commands in the repo. The Mac runs them and the results come back to GitHub as issue
comments, so the owner doesn't have to paste anything.

The runner works in a checkout of its **own**, `~/Projects/kaibot-runner` (a second clone, made by
`install_runner.sh`), never in the engine's `~/Projects/kaibot` (QA-Ops audit, 25 Nov 2026: below).

| Piece | Where |
|---|---|
| The runner's checkout | `~/Projects/kaibot-runner` (`KAIBOT_RUNNER_CHECKOUT`); the engine's stays `~/Projects/kaibot` |
| Queue (Product edits this) | `ops/runner/queue.yaml` |
| Allow-list (QA-Ops key + owner) | `ops/runner/allowed.yaml` |
| Runner, one tick | `scripts/research_runner.py` |
| launchd template, every 15 min | `ops/runner/com.kaibot.research-runner.plist` |
| Install / uninstall / status | `scripts/install_runner.sh` |
| State, outside git (`data/` is ignored), in the runner's checkout | `data/runner/done.json`, `runner.log`, `logs/<id>.log`, `out/<id>.md`, `paused.json` |
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
3. Within about 15 minutes the Mac pulls `main` into the runner's checkout, runs the job, and posts on the issue a comment headed
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
| `scripts/fetch_listings.py` | `--since --first-bars --limit --status --build --report` | #376: SEC history (`--since`) and Alpaca first bars (`--first-bars`, paper keys); gets `--env`, sec, heavy, 120 min; writes `data/listings/` only. Not `--prices` |
| `scripts/fetch_minute_pit.py` | `--end --per-min --max-minutes --limit --status --report` | #794: the 15:45-15:59 ET minutes of every point-in-time member (paper keys, data host only); gets `--env`, heavy, 240 min; at most 40 requests a minute by default (99 by the pattern); `--end` never in the hold-out; writes `data/bars_1550/` only |
| `scripts/fetch_daily.py` | `--key --retry-holes --per-min --status` | #812: exactly one point-in-time key of the daily store (`SYMBOL@ASOF`, only a key `sp500_pit.Membership` names), as of its date; paper keys, data host only, at most 40 requests a minute; gets `--env`, heavy, 30 min; under the daily fetch lock (the engine's `data/fetch_daily.lock`); a complete key is skipped; writes `data/daily/` only |
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
  `https://github.com/falconomad/kaibot.git` with the runner's own token (below; `gh auth git-credential` with
  `GH_TOKEN`), opens a pull request
  with `gh api --method POST repos/falconomad/kaibot/pulls`, and puts the PR link in the job's result comment.
  Then `git checkout main`: local `main` was checked to equal `origin/main` before the commit, so switching back
  changes only the committed paths on disk. There is no `git reset --hard` and no `git clean`, so `data/` and any
  other untracked file are never touched. The commit message and PR body say they came from the research runner
  and are redacted like comments. The PR is reviewed and merged like any other.
- **Anything else** (a change outside those folders, a file that fails the checks, the checkout not on `main`, local
  `main` not equal to `origin/main`, a git error): nothing is committed, moved or removed. The result comment
  carries a **Warning** with the paths, `data/runner/paused.json` records the job, and the runner runs no more
  jobs (and does not pull) until the checkout is clean. Once it is, the next tick logs "jobs resume" and carries on.

Without gh, or without a usable runner token (below), the results are committed on the local branch `runner/<job id>` and not
pushed, and the checkout still goes back to clean `main`, so nothing is lost and later jobs still run. The runner
never pushes to `main` and never pushes any branch but `runner/<job id>`.

## Its own checkout (QA-Ops audit, 25 Nov 2026)

Until 25 Nov 2026 the runner and the live engine shared `~/Projects/kaibot`: the runner's `git pull` every 15
minutes, market hours included, changed the files the running engine imports lazily (about 33 imports inside
functions), and any engine restart then ran unreviewed `main`. Now:

- **Two checkouts.** The runner runs only in `~/Projects/kaibot-runner`, a second clone of the same repository
  (not a `git worktree`: a worktree shares the engine's `.git` and cannot check out `main` beside it). It pulls,
  commits its `runner/<id>` branches and checks out `main` there only. The engine's checkout is never pulled,
  checked out or written by the runner.
- **The runner refuses the engine's checkout.** Every tick first compares its checkout with the engine's
  (`KAIBOT_ENGINE_CHECKOUT`, which `install_runner.sh` writes into the runner's plist; `~/Projects/kaibot`; and the
  `WorkingDirectory` of `com.kaibot.engine`'s plist, which it only reads). The same folder, or one inside the other,
  and the tick logs `refused: ...` and stops: no pull, no job (`main()` exits 3). So an old install still in the
  engine's checkout stops by itself at its first tick after this change is on `main`, until it is moved (below).
- **`.venv`.** `~/Projects/kaibot-runner/.venv` is a link to the engine's `.venv` (the same packages; the runner
  never installs into it). To give the runner its own, remove the link and make one there
  (`python3 -m venv .venv && .venv/bin/pip install --require-hashes -r requirements-research.txt`, with the ml extras
  for `trend_momentum_gbm.py`).
- **`data/`.** The runner has a `data/` of its own. `install_runner.sh` links into it each folder of the engine's
  `data/` that the jobs read (`daily/`, `bars_1m/`, `earnings/`, ...), **except** `data/live` (the engine's journal and
  state), `data/runner`, `RELEASE`, `HOLD` and `release_history.log`, which are never linked. What the jobs write
  (`reports/`, `trials.jsonl`, `alarm/`, `fundamentals/`) is the runner's own: copied once from the engine's (an
  APFS clone, no extra space), then written only in the runner's checkout. A tick refuses a `data/` that is itself
  a link, or any link in it into the engine's `data/live`, `data/runner`, the whole `data/` or the engine's code. A
  job that writes `data/live/...` writes the runner's own folder, never the engine's.
- **The engine says when its checkout drifts.** At every start `run_engine.sh` runs `scripts/checkout_drift.sh`:
  one `WARNING:` line in `engine.log` when the engine's checkout has uncommitted changes or is ahead of or behind
  `origin/main` (as of the last fetch). Informational only: the engine starts either way (a pinned release is
  behind `main` by design).

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
- **Secrets.** The runner never opens `~/Projects/kaibot-secrets/.env`. The one file it reads from that folder is
  its own GitHub token, `runner_gh_token` (below), which no job ever sees. For a script marked `env` it adds
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
- **One SEC window for the whole Mac (audit 2 7.6, #716).** That guard sees only bulk fetchers. Every SEC
  request now also takes a slot from one window shared by every process: the engine's 8-K feed, the nightly
  `collect_alt`, `fetch_earnings_dates`, `fetch_listings`, `fetch_sec_fundamentals` and anything else on the
  `sec_edgar` budget. The window is a file under a flock, `data/ratelimit/sec_edgar.slots`, with at most 8 requests
  a second in total, spaced 125 ms apart (SEC allows 10 per IP). Each process keeps its own 5 a second as well
  (`config/sources.yaml`, `shared_budget_per_s`; `src/kaibot/common/ratelimit.py`, `SharedWindow`). Jobs get
  `KAIBOT_RATELIMIT_DIR=<engine checkout>/data/ratelimit`, so they share the engine checkout's file, not this
  checkout's. If the file cannot be used, a process logs one warning and keeps its own budget only. **Not covered:**
  an engine in Docker (its container has its own `data/ratelimit`, and a flock does not cross the Docker Desktop VM).
  With `KAIBOT_HEADLINE_EDGAR` off, the default, the engine sends no SEC request at all.
- **Runaway jobs.** Every script has a timeout. When it passes, the job's whole process group gets SIGTERM, then
  SIGKILL. A job is recorded as `started` before it runs, so a job that crashes the Mac is not started again on
  its own.
- **Market hours.** Allowed (stakeholder, #600, 24 Nov 2026). Heavy jobs run under `nice -n 10`, so the engine
  keeps priority.
- **The quiet window around the close (audit 2 7.8, #718).** The engine sends its daily orders in the 10 minutes
  before the close, so no heavy job runs from 30 minutes before a session's close to 10 minutes after it: 15:30 to
  16:10 ET (21:30 to 22:10 Berlin most of the year), or 12:30 to 13:10 on a 13:00 close (NYSE sessions from
  `kaibot.ingest.nyse_sessions`; a holiday has no window).
  - A heavy job that comes due in the window is `deferred` to a later tick.
  - A heavy job already running when the window starts is paused with SIGSTOP to its process group and resumed with
    SIGCONT when the window ends. Its log says so, and the pause does not count against its timeout. A network
    connection the job held open may time out during the pause; `common.http` retries it.
  - The launchd job runs as `ProcessType Background` with `LowPriorityIO`, so macOS throttles the runner and its
    jobs, CPU and disk, whenever the engine wants them. After this change is pulled, run `install_runner.sh` again
    so the new plist is loaded.
- **The engine.** The runner never starts, stops or restarts the engine, never touches its lease or
  `scripts/service.sh`, and never runs launchctl. It works only in its own checkout (above) and refuses to run in
  the engine's. `git pull --ff-only` runs only when the runner's checkout is on `main` and clean (a dirty checkout
  pauses the runner, see above). If the pull fails, the failure is logged and the local queue is used.
  The engine's checkout changes only when the owner (or `run_engine.sh` at a start, per the release pin) changes it.
- **Posting.** Results go out with `gh api --method POST repos/falconomad/kaibot/issues/<n>/comments` (REST, with
  the body taken from a file), signed in with the runner's own token only. If gh is missing or there is no usable
  token, the result stays in `data/runner/out/<id>.md` and the log says why ("not posted to GitHub: ..."). A copy
  is kept there either way.
- **Trust boundary.** Anyone who can push to `main` can queue jobs, and could also change the runner or the
  allow-list. Changes to `allowed.yaml` and `scripts/research_runner.py` need the QA-Ops key, and the owner should
  see them before they merge: `.github/CODEOWNERS` (#711) makes `ops/runner/**`, `scripts/research_runner.py`,
  `scripts/runner_token.sh`, `scripts/install_runner.sh` and `.github/**` owner-reviewed, which GitHub enforces once
  main's ruleset requires a code owner's review (step 4 below).

## Its own GitHub token (audit 2 7.1, #711)

Until #711 the runner pushed its `runner/<id>` branches and posted comments with the owner's own `gh auth login`,
which can do anything the owner can, `main` included. Now it uses a fine-grained personal access token
for this one repository, read from `~/Projects/kaibot-secrets/runner_gh_token` (outside the repository; mode 600;
`KAIBOT_RUNNER_GH_TOKEN_FILE` names another file). Every gh call and the push get it as `GH_TOKEN`, with an empty gh
config folder of the runner's own (`data/runner/gh`), so gh cannot fall back to the owner's login; every other git
credential helper is switched off for the push. The runner refuses a file that is a link, belongs to another user,
can be read by others, or holds anything but a fine-grained token (`github_pat_...`): a classic token (`ghp_`,
every repository of the account) and gh's own login token (`gho_`) are refused. Then nothing is pushed or posted,
the results stay on the local `runner/<id>` branch and in `data/runner/out/`, and `runner.log` says why.

**Until the token is installed (Product, 26 Nov 2026: nothing may stop working while the owner's setup waits).**

| The token file | What the runner uses | Said where |
|---|---|---|
| not there at all, and no token was ever used | the owner's `gh auth login`, exactly as before #711 | `runner.log`: one `WARNING: owner login in use ...` per tick; `install_runner.sh --status`: "owner login in use; install runner token" |
| there and valid | the runner's token only; the first use writes `data/runner/runner_token_in_use` | |
| not there, but a token was used before (`runner_token_in_use` exists) | nothing: no push, no post; **never** the owner's login again | `runner.log` and `--status` ("MISSING after it was in use") |
| there but bad (a link, a dangling link, another owner, readable by others, a classic or gh-login token) | nothing: refused, **never** a fall-back to the owner's login | `runner.log` ("the runner token is refused") and `--status` ("REFUSED") |

So the owner's login stops being used the moment a valid token is installed, and a mistake in the file can only
stop the pushes, never widen them. To go back to the owner's login deliberately (not recommended), delete both the
token file and `data/runner/runner_token_in_use`.

**What the token can do, and what it cannot be limited to.** Fine-grained tokens are limited by repository and by
permission, **not by branch**: "Contents: write" lets the token push any branch or tag of the repository. So the
branch limit comes from two other places: the runner's own code pushes only `refs/heads/runner/<id>` (and refuses
anything else), and **main's ruleset** (step 4) refuses a direct push to `main` from anyone, this token included.
Without that ruleset the token can push to `main`: `runner_token.sh --check` says so in a WARNING line. A GitHub App
or a second "machine" GitHub account would limit it further; not done here.

**Setup (owner, about 10 minutes, any time; no secret goes into the repository or a chat):**

1. **Make the token.** github.com > Settings > Developer settings > Personal access tokens > **Fine-grained tokens**
   > Generate new token:
   - Token name `kaibot-research-runner`; Expiration 90 days (put a reminder in your calendar to renew it);
   - Resource owner `falconomad`; Repository access **Only select repositories** > `falconomad/kaibot`;
   - Repository permissions: **Contents: Read and write** (push `runner/<id>`), **Pull requests: Read and write**
     (open the PR), **Issues: Read and write** (post the result comment). Metadata: Read is added by GitHub.
     Nothing else: no Workflows (so it can never change `.github/workflows/`), no Administration, no Actions, no
     Secrets. No account permissions.
   - Generate, and copy the token (shown once).
2. **Install it on the Mac** (the paste is not shown and not kept in the shell history):
   ```bash
   ~/Projects/kaibot-runner/scripts/runner_token.sh --install
   ```
   It writes `~/Projects/kaibot-secrets/runner_gh_token` (mode 600) and runs `--check`: mode, kind, the login it
   signs in as, that it reaches `falconomad/kaibot`, its expiry, and main's rules.
3. **Nothing else changes for the runner.** From its first tick with the token it ignores your own `gh auth login`
   for good (you may keep it for yourself). `install_runner.sh --status` shows the token check.
4. **Main's ruleset (audit 2 5.1, the owner's action).** github.com/falconomad/kaibot > Settings > Rules >
   Rulesets > New branch ruleset: name `main`, Enforcement **Active**, target **Default branch**; tick **Restrict
   deletions**, **Require a pull request before merging** with **Require review from Code Owners**, **Require status
   checks to pass** (`python`, `migrations-record` and the `supply-chain` checks), **Block force pushes**. Bypass
   list: empty, or "Repository admin" set to **For pull requests only**, never "Always": the token acts as you (an
   admin), and an "Always" bypass would let it push to `main`. Note that GitHub never lets an author approve their
   own pull request, so with Code Owner review required, a PR under your own login that touches the paths above
   needs that "for pull requests only" bypass to merge (the #600 trust question in the audit).
5. **Check it:** `~/Projects/kaibot-runner/scripts/runner_token.sh --check` ends with "main's rules: ...
   pull_request ...: a direct push to main is refused". The ticket's test, once that line shows: from a scratch
   clone, `GH_TOKEN=$(cat ~/Projects/kaibot-secrets/runner_gh_token) git -c credential.helper= -c
   'credential.helper=!gh auth git-credential' push origin HEAD:main` after an empty commit
   (`git commit --allow-empty -m probe`) must be refused with "protected branch" / "repository rule violations".

To retire it: `./scripts/runner_token.sh --remove` and delete the token on GitHub (Settings > Developer settings).
Not done in #711: running the runner as a separate macOS user that cannot read the secrets folder (the issue's
second half); until then a job could still read files of the owner's user (see "Safety rules").

## One-time setup (owner, on the Mac)

The same commands install the runner the first time and move an install that still runs in the engine's checkout
(they never touch `~/Projects/kaibot` beyond reading it, and never the engine service):

```bash
# gh for posting results; the runner signs in with its own token, not with a gh login (#711)
brew install gh
git clone https://github.com/falconomad/kaibot.git ~/Projects/kaibot-runner
~/Projects/kaibot-runner/scripts/install_runner.sh            # the runner's checkout, then com.kaibot.research-runner
~/Projects/kaibot-runner/scripts/runner_token.sh --install   # its own token ("Its own GitHub token" above)
~/Projects/kaibot-runner/scripts/install_runner.sh --status   # loaded? which checkout? the token? last log lines
```

`install_runner.sh` stops the old job first (it refuses while a tick is running: try again once it ends), copies
the old `~/Projects/kaibot/data/runner` (done jobs, logs, pause) to the runner's checkout once, so no done job runs
again, links the engine's `.venv` and data folders (above), writes the plist with the new checkout and loads it.
`--status` prints `runner checkout:` and `engine checkout:`, and a WARNING while the two are the same. The old
`~/Projects/kaibot/data/runner` is left as it was (delete it whenever you like); a local `runner/<id>` branch in
the engine's checkout from an old job that was not pushed stays there too.

**The runner's alarm (audit 2 7.4, #714).** A second, free Healthchecks.io check, separate from the engine's: period
15 minutes, grace 30 minutes. Paste its ping URL into the runner's state folder (never the secrets file, which the
runner does not read):

```bash
printf '%s\n' 'https://hc-ping.com/<the runner check uuid>' > ~/Projects/kaibot-runner/data/runner/healthchecks_url
chmod 600 ~/Projects/kaibot-runner/data/runner/healthchecks_url
```

Each tick then pings it. A good tick sends a success ping, and so does every 10 minutes of a long job. A tick sends
a fail ping, with the reason, when it was refused (the engine's checkout), was paused (the checkout holds changes
the runner will not commit), could not read the queue or the allow-list, or found less than 10 GB free on the
disk. No pings at all (the runner unloaded, the Mac asleep, launchd stuck) turn the check red after period +
grace. Without the file, the runner's alarms are off and nothing else changes. `HEALTHCHECKS_RUNNER_PING_URL`
in the environment takes the file's place.

**Logs are rotated.** At the start of each tick, every `data/runner/*.log` over 10 MB is copied to `<name>.1` and
emptied in place; `.1` to `.4` move up one, and the old `.5` is dropped, so 5 are kept. The per-job logs in
`data/runner/logs/` are overwritten by each run of the job.

To remove the runner, run `~/Projects/kaibot-runner/scripts/install_runner.sh --uninstall`; `data/runner` is
kept. The log is `~/Projects/kaibot-runner/data/runner/runner.log`. To run one tick by hand:
`cd ~/Projects/kaibot-runner && .venv/bin/python scripts/research_runner.py`. `./scripts/install_runner.sh --prepare`
sets up the checkout without launchd (it works on Linux too).
