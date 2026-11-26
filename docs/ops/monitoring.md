# Monitoring that does not depend on the engine's host (M20 9.5, #729)

Today most alarms come from the machine the engine runs on. If that machine is off, asleep or offline, those alarms go
quiet, and they are noticed only because they are quiet. The 24-25 Nov 2026 sleep showed the gap
([incidents/2026-11-24-mac-sleep.md](incidents/2026-11-24-mac-sleep.md)). This page lists every channel and the host it
needs. It also gives the design that makes the heartbeat-absence alert independent of the host before the January
cloud move, and the steps for that move.

## Channels today

| Channel | Runs on | Says | Quiet when |
|---|---|---|---|
| `scripts/healthcheck.py watch` ([always_on.md](always_on.md#alarms-free-healthchecksio)) | the engine's host, next to the engine | start, alive every 5 min, `/fail` with the log line (halts, lease, refused start, stall, disk) | the host is off, asleep or offline: the check turns red only by its own silence |
| SQL dead-man alerts, 0051 ([failsafe.md](failsafe.md#dead-man-alerts-audit-2-77-717)) | Supabase (pg_cron + pg_net) | success every minute 13:00-21:59 UTC on weekdays; `/fail` while it acts on a silent engine, on a halt row, on missing Vault keys | Supabase is down. Outside those hours, by design |
| Research runner check ([research_runner.md](research_runner.md)) | the runner's machine | its own ticks | that machine is off |
| **Heartbeat watcher** (this page, `scripts/heartbeat_watch.py`) | **any machine except the engine's host** | success while `kaibot.heartbeat` (process `engine`) is fresher than 180 s; `/fail` with the age when stale, or when the row cannot be read | the watcher's own machine or scheduler stops: its own check turns red |

During market hours the SQL dead-man already notices a silent engine from Supabase, whatever the host. The watcher
adds the hours outside the session and a second path that does not run in the same database. It gives one plain
signal, "the heartbeat stopped", from a place that is neither the engine's host nor its database.

## Design: the external ping

- **Every input is configuration, none is code.** `scripts/heartbeat_watch.py` reads `SUPABASE_URL`,
  `SUPABASE_SERVICE_KEY`, `HEALTHCHECKS_HEARTBEAT_URL` and `HEARTBEAT_STALE_S` from the environment or from one env
  file, and nothing else. It never names a host, a path of the Mac or launchd (a test checks this). It runs on the
  stdlib alone, so any Python 3.9+ can run it.
- **One run, one ping.** The schedule belongs to the scheduler: cron on any second machine now, Cloud Scheduler with a
  Cloud Run job after the move (one service account, with access only to its own secret). Exit 0 fresh, 1 stale,
  2 unreadable or not configured.
- **Fail closed.** If it cannot read the row, that is a `/fail`. The watcher cannot vouch for the engine, so it never
  stays green by default.
- **Its own check.** `HEALTHCHECKS_HEARTBEAT_URL` is a separate Healthchecks.io check, not the engine's
  `HEALTHCHECKS_PING_URL`. Its period follows the schedule (for example 5 minutes, grace 5 minutes). It must not
  share a check with `healthcheck.py`: a success ping from one would hide the other's silence.
- **Why the heartbeat row.** The engine writes it from wherever it runs. The dashboard, the watchdog and the dead-man
  already read it. The cut-over and the roll-back change only which host writes it, not what is watched. Since #727
  every beat names the build (`code_sha`) and the host. The watcher's message names neither: they stay on the
  dashboard.
- **Not in CI.** CI holds no secret (`tests/unit/test_ci_supply_chain.py`), so GitHub Actions cannot read Supabase.
  The watcher needs a scheduler with a secret store.

## January (the cloud move): the owner's steps

1. In Healthchecks.io (free plan), add a check "kaibot heartbeat (external)". Set period 5 min, grace 5 min. Connect the
   phone integration (the Healthchecks app, or Pushover/Telegram on the free plan). Copy its ping URL.
2. Store the URL and a read key as their own secrets (Secret Manager, one per process, #726). Give them to a Cloud Run
   job running `python scripts/heartbeat_watch.py`, scheduled every 5 minutes by Cloud Scheduler. Until then, any
   second machine can run it: `*/5 * * * * cd ~/kaibot && python3 scripts/heartbeat_watch.py --env-file
   ~/monitor.env`.
3. Ticket 9.5's target: a Cloud Monitoring alert on heartbeat absence, with Firebase push. It is fed the same signal:
   the job's exit code, or a log-based metric on its `kaibot heartbeat stale` line. Healthchecks stays as the second
   channel.
4. The weekly synthetic alert (the ticket's check): once a week, outside market hours, run the watcher with
   `HEARTBEAT_STALE_S=1`. The beat is then "stale" and a `/fail` must arrive on the phone. The next scheduled run
   turns the check green again.

What stays on the engine's host: `healthcheck.py`'s log alarms (halts, lease, disk). They need the log, so they move
with the engine (on the VM: `docker logs`, or the journald unit). They are the detail channel. The watcher is the one
that says "the host is gone".
