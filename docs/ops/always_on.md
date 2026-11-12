# Always-on engine on the Mac mini (issue #121)

Until now someone had to type `./scripts/run_engine.sh` every trading day and leave the terminal open. Once
positions are held overnight, "engine off" is a risk, not just a missed day. This page sets the Mac up to run the
engine by itself, restart it after a crash, raise an alarm when something is wrong, and keep a nightly backup.

**Starting by hand still works exactly as before.** Nothing on this page is needed for `./scripts/run_engine.sh`.

## One-time setup (stakeholder, about 15 minutes)

1. **Create the free alarm.** Sign up at <https://healthchecks.io> (free plan, no card). "Add Check":
   - Name: `kaibot engine`. Schedule: **Simple**, Period **10 minutes**, Grace **10 minutes**.
   - Under Integrations, keep e-mail on (add the phone app or Telegram if you like push).
   - Copy the **ping URL** (looks like `https://hc-ping.com/1b2c…`).
2. **Add it to the secrets file.** Open `~/Projects/kaibot-secrets/.env` in a text editor and add one line:
   `HEALTHCHECKS_PING_URL=https://hc-ping.com/…` (paste yours). Nothing else in that file changes.
3. **Mac settings for an unattended Mac mini.**
   - System Settings > Users & Groups > Automatically log in as: `appucyriac` (launch agents only run while you are
     logged in).
   - System Settings > Energy: "Start up automatically after a power failure" on; "Prevent automatic sleeping when
     the display is off" on.
   - Docker mode only: Docker Desktop > Settings > General > "Start Docker Desktop when you sign in" on.
4. **Install once**, outside 09:30-16:00 New York time (15:30-22:00 Berlin), with no engine running in a terminal:
   ```
   cd ~/Projects/kaibot
   git pull
   ./scripts/install_service.sh          # or --docker / --python to choose, as with run_engine.sh
   ./scripts/service.sh status
   ```
   It starts the engine at once. Within 15 minutes the Healthchecks.io check turns green.
5. Optional, recommended: pin a release (next section) so the engine stops following `main`.

## What runs, and when

| Agent (`~/Library/LaunchAgents`) | When | What |
|---|---|---|
| `com.kaibot.engine` | at login, every weekday at 15:00 Mac time, and about a minute after a crash | `scripts/service_run.sh engine` → `scripts/run_engine.sh` (Docker or Python, chosen at install). The engine itself waits for the broker's session (holidays and early closes come from Alpaca's calendar), so starting early is harmless. It keeps running overnight and over the weekend; `caffeinate` keeps the Mac awake while it runs. |
| `com.kaibot.daily` | 03:15 Tuesday-Saturday (after each US session is complete) | rotate logs, back up local files, refresh the daily bar store (`scripts/fetch_daily.sh`; skip with `KAIBOT_DAILY_FETCH=0` in the plist) |

Never two engines: the service checks for an engine started by hand (`engine_guard.sh`), keeps a pid lock
(`data/live/service.pid`), and the engine's database lease refuses a second engine on any machine. If you start one
by hand while the service runs, yours is refused. If the service finds one started by hand, it stands by and takes
over about a minute after that one stops.

Logs, in `~/Library/Logs/kaibot/`: `engine.log`, `watchdog.log` (Python mode; Docker: `docker logs kaibot-watchdog`),
`daily.log`, `healthcheck.log`. Each is rotated at 10 MB and every night, 14 gzipped copies kept.

## Everyday commands

| I want to… | Command |
|---|---|
| see what is going on | `./scripts/service.sh status` / `./scripts/service.sh logs` |
| stop for today | `./scripts/service.sh stop` (clean stop, as Ctrl-C: lease released, broker-held stops stay). It starts again next weekday at 15:00 or at the next login. |
| stop until I say so | `./scripts/service.sh hold` (survives reboots), later `./scripts/service.sh unhold` |
| start now | `./scripts/service.sh start` |
| go back to starting by hand | `./scripts/uninstall_service.sh`, then `./scripts/run_engine.sh` as before |

A clean stop also stops the pings, so Healthchecks.io e-mails "down" after about 20 minutes. That is expected; pause
the check on the website if you stop on purpose for longer.

## Pinned releases

The engine trades a **tagged release**, not whatever is on `main`. Product tags a merged commit
(`git tag v2026.11.13 && git push --tags`); then, **outside market hours**:

```
./scripts/release.sh v2026.11.13      # refused 09:30-16:00 New York time unless you add --force
./scripts/service.sh restart          # switch now; otherwise it switches at the next start
```

- The pin is stored in `data/RELEASE` (not in git); `data/release_history.log` lists every promotion.
- At every start `run_engine.sh` checks out the pinned tag and records it: `Release: v2026.11.13@abc1234` in the log,
  in `data/live/running_release`, and as `code_sha` on the `engine_start` event, every heartbeat and every
  risk-gate decision (`gate_run`).
- A tag that does not exist, or local edits in `~/Projects/kaibot`, stop the start with a message (nothing trades).
- `./scripts/release.sh --show` shows the pin and history.
- **Escape hatch:** `./scripts/release.sh main` (or `KAIBOT_RELEASE=main ./scripts/run_engine.sh` for one start)
  follows `main` again, pulling at every start. `./scripts/release.sh --clear` removes the pin; with no pin the
  engine behaves exactly as before #121 (pulls `main`).
- With a pin, `~/Projects/kaibot` is on a detached HEAD. That is normal; `git checkout main` gets you back for
  reading code, and the next start checks the pin out again.

## Alarms (free, Healthchecks.io)

A small separate process (`scripts/healthcheck.py`) runs next to the engine. It only reads the engine's log file and
sends short pings (10 s timeout, in the background); it is not on the order path and never touches the broker.

| Ping | When |
|---|---|
| start | every engine start |
| success | every 5 minutes while the engine process is alive and not stalled |
| fail, with the log line | a halt (daily stop, drift, kill switch, …), lease lost or refused, a refused session, an incomplete flatten, an unclean paper account, the watchdog cancelling entries or flattening, the engine exiting with an error, the engine not ticking for 10 minutes during the session, the nightly job failing (at most once per kind per 30 minutes) |
| log | the nightly job finished (shown on the check's page, does not change its state) |

No pings at all (Mac off, asleep, offline, service stopped) turns the check red after period + grace (about 20
minutes). Without `HEALTHCHECKS_PING_URL` the alarms are off: one line in `healthcheck.log`, nothing else.

## Kill switches, strongest last

1. Dashboard: **Pause** (no new entries) or **Flatten and stop**.
2. `./scripts/service.sh hold`: the engine stops cleanly and is not started again.
3. Alpaca paper dashboard: cancel all orders and close all positions by hand.
Broker-held stops protect open positions whenever the engine is off; the watchdog and the 15:56 ET database
dead-man cover a dead engine during the session (`docs/ops/failsafe.md`).

## Recovery

| Symptom | Do this |
|---|---|
| Healthchecks "down", no fail message | The Mac is off, asleep or offline, or the service is stopped. `./scripts/service.sh status`. After a power cut the Mac restarts, logs in and starts the engine by itself. |
| `kaibot engine exited with code N` repeating | `./scripts/service.sh logs`. Code 3 = the lease (another engine runs somewhere: `docs/ops/run_engine.md`, "Engine lease"). Code 1 at start = a key missing in `.env`, a missing release tag or local edits. It retries every minute; fix and wait, or `hold` while you look. |
| `kaibot halt: …` | Read the reason on the dashboard. Entries stay off for the session; positions are still managed. |
| `kaibot stalled` | Process alive but no tick for 10 minutes. `./scripts/service.sh restart`. |
| "waiting for Docker Desktop" in the log | Open Docker Desktop; the service starts within a minute after it is up. |
| A bad release | `./scripts/release.sh <previous tag> --force` then `./scripts/service.sh restart`. The previous tags are in `./scripts/release.sh --show`. |
| Restore local files | Backups are in `~/Projects/kaibot-backups/kaibot-<date>.tar.gz` (30 days). `tar -xzf <file>` into a temporary folder and copy back what you need (with the engine stopped). The price-history cache is not backed up: it downloads again. |

## Backups

`scripts/backup_local.sh` (nightly from the daily agent, safe to run by hand) packs `data/live` (holder id, trial
log, running release, and any `*.sqlite` journal copied consistently with `sqlite3`), the release pin and history,
and the logs into `~/Projects/kaibot-backups/kaibot-<date>-<time>.tar.gz`, then deletes archives older than 30 days
(`KAIBOT_BACKUP_DIR`, `KAIBOT_BACKUP_KEEP_DAYS`). The secrets file is never included. Results and trades live in
Supabase as well; this backup covers what exists only on the Mac.

## A second paper account as staging

To try a release candidate before promoting it, without touching the real paper run:
- Open a second free Alpaca paper account and a second free Supabase project (the lease and results are per
  database, so staging must not share the main one; apply the migrations there).
- Put its keys in a second file, `~/Projects/kaibot-secrets/staging.env` (same keys as `.env`; never in the repo).
- Run it on **another computer** (or the Mac while the main engine is on hold), in its own clone:
  `KAIBOT_ENV_FILE=~/Projects/kaibot-secrets/staging.env KAIBOT_RELEASE=<candidate tag> ./scripts/run_engine.sh`.
  On the same Mac the local guard refuses a second engine while the main one runs, by design.
- Promote with `release.sh` only after a clean staging session.

## A cloud host (option only, not built)

Decision 11 stands: the Mac first; moving is the stakeholder's call. The same image runs on any Docker host with
`docker compose up -d` (restart policy built in; `docs/ops/run_engine.md`, "Moving to a cloud VM later"). On a VM,
cron replaces the launchd agents (`scripts/service_run.sh daily` at 03:15 Berlin) and the same
`HEALTHCHECKS_PING_URL` works. Stop and uninstall the Mac service first: one engine at a time.
