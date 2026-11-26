# Moving the engine to Google Cloud (milestone M20, planned January 2027)

The runbook for tickets 9.1-9.6 (#725-#730) of the second engine audit. Every step that can be done without a cloud
account is already in the repository (below, "Ready now"). Everything here that creates a cloud resource, costs money
or holds a key is **the owner's step**. Claude creates no cloud resource and handles no key. The move is a
"never without the stakeholder" item ([teams.md](../teams.md)).

Owner decisions still open: the Google plan and billing account, the region (`us-east4`, `europe-west3` or
`us-east1`; `us-east4` is next to Alpaca's and Supabase's US regions), and the machine size below.

## Ready now (in the repository, provider-agnostic)

| Ticket | What is ready | Where |
|---|---|---|
| 9.3 deploys follow `main` (#727) | images built from `rel-*` tags only; the host's promotion file; the image refuses an unpromoted build (exit 6); the build in every heartbeat; CI builds the image | `scripts/build_image.sh`, `scripts/release.sh`, `scripts/engine_entry.sh`, `.github/workflows/image.yml`, [always_on.md](always_on.md#pinned-releases) |
| 9.5 monitoring (#729) | the heartbeat watched from outside the host, set by configuration only | `scripts/heartbeat_watch.py`, [monitoring.md](monitoring.md) |
| 9.6 the lease (#730) | Mac and VM side by side: only the lease holder sends, simulated on every pull request; a stale journal is refused at a roll-back (exit 4) | `tests/dst/test_dst_two_hosts.py`, [run_engine.md](run_engine.md#two-hosts-side-by-side-the-cloud-move-m20-96-730) |
| 9.1, 9.2, 9.4 | this runbook (they need the cloud account) | below |

The engine is already portable (decision 11): settings only from the environment, outgoing connections only, the same
Docker image everywhere (`Dockerfile`, `compose.yaml`).

## 1. The VM and its disk (9.1, #725)

- **One VM**, Compute Engine `e2-small` (2 vCPU shared, 2 GB) or `e2-medium` (4 GB) if pandas' peak during the evening
  fetch needs it. The engine needs about 1 GB ([run_engine.md](run_engine.md#moving-to-a-cloud-vm-later-google-oracle-anything-with-docker)).
  Container-Optimized OS or Debian 12 with Docker. **No external IP for inbound traffic**: the engine only connects out
  (Alpaca, Supabase, data sources). SSH through IAP. Firewall: deny all inbound except IAP's range on 22. Time:
  Google's NTP (default). Time zone: UTC (the engine works in UTC and New York time by itself).
- **One durable POSIX disk at `/data`**, a balanced persistent disk (20 GB is ample: journal, daily store, logs). It
  is shared by the engine and the watchdog on the same VM (two containers, one volume), as `./data/live` is on the
  Mac today. The watchdog opens the journal read-only. Never a network file system: the journal is SQLite in WAL mode
  and needs local POSIX locking ([ledger.md](ledger.md)).

  | Host path | Container | What |
  |---|---|---|
  | `/data/live` | engine and watchdog `/data` | journal, `engine_holder_id`, `promoted`, `running_release`, watchdog records |
  | `/data/daily` | engine `/kaibot-daily/daily` (read-only) | the daily store (`KAIBOT_DAILY_DATA`) |
  | `/data/logs` | the containers' log driver, or journald | logs, rotated |
  | `/opt/kaibot` | (build only) | a checkout of the repository, used only to build images from tags |

- **Snapshots**: a daily snapshot schedule on the disk (keep 14). **Journal copy**: hourly, `sqlite3 .backup` of the
  journal (never a plain `cp` of a WAL file), copied to a versioned Cloud Storage bucket with a 30-day lifecycle. It
  is the same backup as `scripts/backup_local.sh` with another target ([backup_restore.md](backup_restore.md)).
- **The ticket's check: restore drill from a snapshot.** Create a disk from last night's snapshot, attach it to a
  throwaway VM with no secrets, and run the restore drill on the backup archive that disk holds (`scripts/restore_drill.sh
  --archive <file>`, [backup_restore.md](backup_restore.md#restore-drill); the script may need a target option for a
  Linux disk, a small follow-up when the VM exists). The journal must open, pass its checks and show the last
  session's entries. Then delete the throwaway VM and disk. Record the result
  in `docs/ops/drill_records/`.

## 2. Secrets (9.2, #726)

Today every key is in one `.env` on the Mac ([secrets.md](secrets.md)). On the VM:

- **Secret Manager, one secret set per process**, each with its own service account:

  | Process | Service account | Secrets (names as in [secrets.md](secrets.md)) |
  |---|---|---|
  | engine | `kaibot-engine@` | trading pair, `SUPABASE_SERVICE_KEY`, FRED, Finnhub, SEC user agent, `HEALTHCHECKS_PING_URL`; `ANTHROPIC_API_KEY` only if the agent room is on |
  | watchdog | `kaibot-watchdog@` | trading pair, `SUPABASE_SERVICE_KEY` |
  | price relay, ticker checks | `kaibot-helpers@` | `data.env` (the second, empty paper account) and the helper sign-in; never the trading pair |
  | heartbeat watcher (Cloud Run job) | `kaibot-monitor@` | a read key for `kaibot.heartbeat`, `HEALTHCHECKS_HEARTBEAT_URL` |
  | research jobs (9.4) | one per job | data keys only; never the trading pair or the service key |

  Each account gets `roles/secretmanager.secretAccessor` **on its own secrets only** (a per-secret IAM binding, not
  project-wide).
- **Rendered to tmpfs at boot.** A systemd unit, before the containers start, reads the process's secrets with the
  VM's metadata token. It writes them to `/run/kaibot/<process>.env` (tmpfs, mode 600, owner uid 1000) and passes
  that file as `KAIBOT_ENV_FILE` (read-only mount). Nothing is written to `/data` or to the image. A reboot renders
  them again.
- **Non-secret settings** (`KAIBOT_*`) live in a plain file under `/opt/kaibot-config/` that is versioned with the
  runbook, not in Secret Manager. The VM's settings differ from the Mac's: `KAIBOT_FOLLOW_MAIN=0` and
  `KAIBOT_REQUIRE_PROMOTED=1`.
- **The ticket's check.** As `kaibot-engine@`, reading the watchdog's or the helpers' secret is refused
  (`gcloud secrets versions access ... --impersonate-service-account=...` answers PERMISSION_DENIED). The same holds
  for every pair of accounts. Record it.
- Supabase Vault keeps the dead-man's own copy of the trading pair, as today. A key rotation changes Vault and
  Secret Manager together ([secrets.md](secrets.md)).

## 3. The research runner off the trading host (9.4, #728)

Today `scripts/research_runner.py` runs on the Mac mini that also trades ([research_runner.md](research_runner.md)).
In January:

- **Cloud Run jobs plus Cloud Scheduler**, one job per nightly task, built from the same `rel-*` tag as the engine
  (`scripts/build_image.sh`, or a research image built the same way). Each job has its own service account with no
  access to the engine's secrets and no route to the engine VM (no shared disk, no network path). It reads only the
  research stores it needs from Cloud Storage.
- **Results through a narrow GitHub App**, not a personal token. The app is installed on this repository only, with
  `contents: write` limited to the results paths by a ruleset and `pull_requests: write`, so a job opens a PR with
  its results (as #711's fine-grained token does now). The private key is a Secret Manager secret of the jobs' account.
- The runner's existing safety rules carry over unchanged: it never runs in the engine's checkout, never on the
  trading account, and writes results only to its own folder ([research_runner.md](research_runner.md#safety-rules)).
- **The ticket's check.** The Mac runner is retired (`./scripts/install_runner.sh --uninstall`) and the nightly jobs
  are green for 10 sessions in a row. Record the 10 runs.

## 4. Releases on the VM (9.3, #727)

On the VM the engine runs an image, never a checkout:

```
cd /opt/kaibot && git fetch --tags origin && git checkout -q --detach origin/main
./scripts/release.sh --latest            # writes data/RELEASE and the promotion /data/live/promoted
./scripts/build_image.sh --latest        # kaibot-engine:<tag> and :latest, from the tag's tree only
sudo systemctl restart kaibot-engine     # or: docker compose up -d engine (with image: kaibot-engine:latest)
```

`release.sh` writes `data/` under the checkout. On the VM, `/opt/kaibot/data/live` is a link to `/data/live`, so the
promotion lands where the container reads it. With `KAIBOT_REQUIRE_PROMOTED=1`, an image that is not the promoted
release exits 6 before it touches anything. Every heartbeat names the build (`code_sha`), so the dashboard shows what
runs. **Roll back:** `./scripts/release.sh <previous tag> && ./scripts/build_image.sh <previous tag>`, then restart.
Later (owner's decision) CI pushes the image to Artifact Registry and the VM pulls it by digest instead of building it.
The promotion check stays the same.

## 5. Monitoring (9.5, #729)

See [monitoring.md](monitoring.md). On the VM, `healthcheck.py watch` runs beside the engine as today (its log
alarms). The heartbeat watcher runs as a Cloud Run job every 5 minutes, and Cloud Monitoring alerts on heartbeat
absence with Firebase push. Healthchecks.io stays as the second channel. The weekly synthetic alert is the ticket's
check.

## 6. Cut-over (9.6, #730), outside 09:30-16:00 ET, best on a Friday evening or a weekend

The lease guarantees that only one host sends. The journal must move with the engine, and the holder file must not:
a copied stale journal is refused at start (exit 4).

1. **Ready on the VM.** Secrets rendered, `/data` mounted, the same release promoted and built as the Mac runs
   (`./scripts/release.sh --show` on both). `docker run --rm ... kaibot-engine:latest sh /app/scripts/engine_entry.sh
   --check` passes. Migrations 0041 and 0042 are applied (they are).
2. **Optional side-by-side check (the drill of #730).** Start the VM's engine with an empty `/data/live`. While the
   Mac runs, every start must exit 3 with `Another Kaibot engine holds the lease: host '<mac>'`, and the VM must place
   no order. Then stop the VM's engine again and empty `/data/live`.
3. **Stop the Mac cleanly** and unload its service so launchd does not start it again: `./scripts/service.sh hold`,
   then `./scripts/uninstall_service.sh`. The clean stop gives the lease back. Check:
   `select * from kaibot.engine_lease;` returns no row.
4. **Move the data folder.** On the Mac: `sqlite3 data/live/journal.sqlite ".backup /tmp/journal.sqlite"`, plus the
   watchdog's records (`watchdog_cancels.jsonl`, `watchdog_closes.jsonl`) and the daily store. Copy them to the VM's
   `/data/live` and `/data/daily` (`gcloud compute scp --tunnel-through-iap`). **Do not copy `engine_holder_id`**: the
   VM makes its own. Owner uid 1000, journal mode 600.
5. **Start the VM** (`sudo systemctl start kaibot-engine kaibot-watchdog`). Its log says `engine lease taken (host
   <vm>, ...)`, and the journal is verified as the newest. The dashboard's heartbeat shows the VM's host and the
   promoted `code_sha`. `./scripts/run_engine.sh --check` (on the VM: the `--check` entry) shows the lease on the VM.
6. **Watch one session.** The next send window's orders come from the VM only (the dashboard's orders, the VM's log).
   No order comes from the Mac. The Mac is stopped, and if it were started its start would exit 3.
7. **Retire the Mac's engine.** Keep the Mac's last journal copy as a backup. Set the Mac's `.env` aside, so that no
   accidental start can reach the paper account (move it out of `~/Projects/kaibot-secrets/`).

## 7. Roll-back to the Mac

1. Stop the VM's engine cleanly (`sudo systemctl stop kaibot-engine`): the lease is given back.
2. Copy the VM's journal back (`sqlite3 /data/live/journal.sqlite ".backup ..."`, then to the Mac's `data/live/`).
   **Not the Mac's old journal**: the VM's copy ran on after it, so the old one is stale and every start on it exits
   4 (simulated in `tests/dst/test_dst_two_hosts.py`, the roll-back case).
3. Put the Mac's `.env` back, then `./scripts/install_service.sh` (or `./scripts/run_engine.sh`). The log says
   `engine lease taken`.
4. Disable the VM's units, so they do not start again at a reboot.

## The owner's checklist (January)

- [ ] Decide the region, the plan and the machine size; create the project with a budget alert.
- [ ] Create the VM, the disk, the snapshot schedule and the versioned bucket (section 1); run the restore drill.
- [ ] Create the service accounts and secrets (section 2); run the access check.
- [ ] Set up the Healthchecks check and the watcher job, and the Cloud Monitoring alert with Firebase push
  (section 5); receive the first synthetic alert.
- [ ] Create the release, promote it and build it on the VM (section 4); run the deploy and roll-back drill once on
  the VM.
- [ ] Do the cut-over (section 6), including the side-by-side check; watch one session.
- [ ] Move the research jobs (section 3); 10 green nightly runs; retire the Mac runner.
