# Backups, the off-box copy and the restore drill (audit 2 ticket 7.5, #715)

The second engine audit found three gaps. The backups sat on the same disk as what they protect. They left out the
research stores and the runner's `done.json`. And nobody had ever restored one. This page covers what is backed up, the
encrypted copy that leaves the Mac, the drill that proves a backup restores, and the secrets, which are never in a
backup and need a second copy of their own.

## What is backed up

`scripts/backup_local.sh` runs every night from the daily job (`service_run.sh daily`) and is safe to run by hand. It
writes one archive, `~/Projects/kaibot-backups/kaibot-<date>-<time>.tar.gz`, and keeps 30 days of them
(`KAIBOT_BACKUP_DIR`, `KAIBOT_BACKUP_KEEP_DAYS`).

| In the archive | From | Why it matters |
|---|---|---|
| `data/live/` (the journal `journal.sqlite` copied with `sqlite3 .backup`, `engine_holder_id`, the trial log, the running release) | the engine's checkout | the engine's own record; without the holder id a restored engine starts as a stranger to its lease |
| `data/RELEASE`, `data/release_history.log` | the engine's checkout | which release ran |
| `data/fundamentals/` without `raw/` | `scripts/fetch_sec_fundamentals.py` | the point-in-time SEC store (about 996,000 rows). `raw/` is the download cache. `--build` re-reads it, and a fetch downloads it again in 3-5 minutes |
| `data/listings/`, `data/reports/`, `data/alarm/` | the research scripts | listings and first bars, every results report, the market alarm's `top10.csv` (the live alarm reads it) |
| `data/trials.jsonl` | every replay, walk-forward and ablation | the gate's trial counts read it; it exists only on the Mac |
| `runner/data/runner/done.json` | `~/Projects/kaibot-runner` (`KAIBOT_RUNNER_CHECKOUT`) | if it is lost, every queued job runs again |
| `logs/`, `service-logs/` | the checkout, `~/Library/Logs/kaibot` | what happened |
| `MANIFEST.sha256` | written last | the restore drill checks every file against it |

Some things are left out on purpose. The price-history caches (`data/live/history`, `data/daily`) download again. The
runner's `healthchecks_url` is left out, and so is every file in `~/Projects/kaibot-secrets`: **a backup never holds a
secret** (see [Secrets](#secrets-a-second-copy-off-the-mac)). Results, trades and the ledger are in Supabase too.

## The encrypted off-box copy

When it is set up, the same archive is also encrypted and written to a folder you choose. That can be iCloud Drive
or an external disk. A plain copy never leaves the Mac. If the copy is set up and cannot be written, the script exits
1. The causes are: no key, a key inside a checkout, the disk not mounted, or no access. The daily job then sends
a Healthchecks `fail` ("kaibot daily job failed: backup"). The local archive is kept either way. Off-box copies are
kept 14 days (`KAIBOT_BACKUP_OFFBOX_KEEP_DAYS`).

`./scripts/backup_local.sh --setup` prints these commands and the current state. It changes nothing.

1. **A key pair with age (recommended).** Only the **public** key stays on the Mac, so someone who reads the Mac's
   disk still cannot read the off-box copies.
   ```bash
   brew install age
   mkdir -p ~/Projects/kaibot-secrets && chmod 700 ~/Projects/kaibot-secrets
   age-keygen -o /tmp/kaibot-backup.agekey        # prints "Public key: age1..."
   age-keygen -y /tmp/kaibot-backup.agekey > ~/Projects/kaibot-secrets/backup_age_recipients.txt
   ```
   Put the whole of `/tmp/kaibot-backup.agekey` (the line `AGE-SECRET-KEY-1...`) in your password manager **and** on
   a USB stick kept away from the Mac. Then delete it from the Mac: `rm -P /tmp/kaibot-backup.agekey`. If you lose
   this key, the off-box copies cannot be read.
   - Without age, use an openssl passphrase. The key then stays on the Mac, because it encrypts with it. Keep a
     second copy of it off the Mac too:
     `openssl rand -base64 48 > ~/Projects/kaibot-secrets/backup.pass && chmod 600 ~/Projects/kaibot-secrets/backup.pass`.
     The copy is AES-256-CBC with PBKDF2 (200,000 rounds). It has no built-in integrity check, so the drill's manifest
     check is the one that catches damage. age is used whenever its recipients file exists.
2. **The folder**: one line in `~/Projects/kaibot-secrets/.env`. The quotes are needed because of the spaces:
   - iCloud Drive: `KAIBOT_BACKUP_OFFBOX_DIR="~/Library/Mobile Documents/com~apple~CloudDocs/kaibot-backups"`
   - an external disk: `KAIBOT_BACKUP_OFFBOX_DIR="/Volumes/<disk name>/kaibot-backups"` (a disk that is unplugged
     at night fails the job each night; leave it plugged in, or use iCloud Drive)
   - iCloud's free 5 GB holds about 30 copies of a large `data/reports`. Lower `KAIBOT_BACKUP_OFFBOX_KEEP_DAYS` if it fills.
3. **Run it once by hand** in Terminal: `./scripts/backup_local.sh`. macOS may ask whether Terminal may use
   iCloud Drive or the disk; allow it. If the nightly run then logs "Operation not permitted", give `/bin/bash` the same access
   (System Settings > Privacy & Security > Files and Folders, or Full Disk Access).
4. **Run the restore drill** on the encrypted copy (next section).

The settings come from the environment, else from the `.env`. `KAIBOT_BACKUP_AGE_RECIPIENTS` and
`KAIBOT_BACKUP_PASSFILE` move the key files elsewhere. The script refuses a key file, or an off-box folder, inside
the engine's checkout or the runner's.

## Restore drill

`scripts/restore_drill.sh` rebuilds the engine and the runner state from a backup in a temporary folder, then checks
them. It starts no engine, watchdog or runner and writes nothing in the live `data/`. It talks to neither the broker
nor Supabase.

```bash
./scripts/restore_drill.sh                                                   # the newest local archive
./scripts/restore_drill.sh --offbox --identity /Volumes/<usb>/kaibot-backup.agekey   # the newest off-box copy
./scripts/restore_drill.sh --archive <file> --keep --into ~/kaibot-restore   # one file, and keep the result
```

What it does and checks (exit 0 when every check passes, 1 when one fails):
1. It decrypts the copy (`.age` with the identity, `.enc` with the passphrase file) and unpacks it. It refuses any
   member that would land outside the folder.
2. Every file against `MANIFEST.sha256`: none missing, none changed, none extra.
3. **The engine.** It makes a fresh clone of the checkout at the backup's pinned release (`data/RELEASE`, else
   HEAD) in `<tmp>/kaibot`, with the restored `data/` laid into it. The journal is opened with the engine's own
   `Journal`, which checks the structure and every entry's checksum, as a start does (a failure here would be exit
   code 4). It is then folded with `JournalStore`, which gives the tables and their row counts, the journal id and
   the last entry. The holder id must be there.
4. **The research stores.** `trials.jsonl` is read as the gate reads it: rows, configurations, unreadable lines.
   The fundamentals parquet row count is taken, and the files in listings, reports and alarm are counted.
5. **The runner.** `<tmp>/kaibot-runner/data/runner/done.json` must parse, and its job ids are counted.
6. The newest backup must be under 36 hours old.

It prints one block (`restore drill <date>: PASSED`, one line per check) to paste into the drill record. Run it once
after setting up the off-box copy, then once a month with the USB key.
`tests/unit/test_backup_restore.py` runs the drill on every pull request with a synthetic Mac. That covers age (a
stand-in when the binary is missing), openssl, a damaged journal, a changed file and an archive that tries to write
outside its folder.

**A real restore** (the Mac's disk is lost) follows the same steps on the new machine:
1. Clone the repository and install (`docs/ops/always_on.md`).
2. Put the secrets back from their second copy.
3. Run the drill with `--keep --into ~/kaibot-restore`.
4. With no engine running anywhere, copy `~/kaibot-restore/kaibot/data/` into `~/Projects/kaibot/data/`, and
   `~/kaibot-restore/kaibot-runner/data/runner/done.json` into the runner's checkout.
5. Start the engine. The lease and the journal identity check against Supabase decide whether it may run
   ([run_engine.md](run_engine.md#engine-lease-one-engine-whatever-the-machine)). A journal older than the one
   Supabase has seen is refused by design (`IdentityError`).

## Secrets: a second copy off the Mac

Secrets are never in a backup, local or off-box. A disk lost with them on it is a compromise, and backups are copied
around. So they need a second copy of their own, kept **off the Mac**: in your password manager (a secure note per
file), or on an encrypted USB stick stored somewhere other than the Mac. Keep it current. Each time a key is rotated,
update the copy the same day.

| Secret | Where on the Mac | Also held by |
|---|---|---|
| `~/Projects/kaibot-secrets/.env` (Supabase service key, Alpaca paper keys, Anthropic key, Healthchecks ping URL, SEC user agent) | the engine | the Alpaca, Supabase, Anthropic and Healthchecks dashboards can issue new ones |
| `~/Projects/kaibot-secrets/helper.env` (the helpers' narrow key, the relay's Alpaca keys) | the ticker worker and price relay | [helper_role.md](helper_role.md) |
| `~/Projects/kaibot-secrets/staging.env`, if any | the staging account | its dashboards |
| `~/Projects/kaibot-runner/data/runner/healthchecks_url` | the runner | Healthchecks.io |
| the age identity (`AGE-SECRET-KEY-1...`) | **not on the Mac** (USB stick and password manager) | nobody else: if it is lost, the off-box copies are lost |
| `~/Projects/kaibot-secrets/backup.pass` (openssl only) | the backup | nobody else |
| the dead-man's paper keys | Supabase Vault ([failsafe.md](failsafe.md)) | the `.env` (the same keys) |

Without its second copy, a lost Mac means issuing every key again. That is possible, but the dead-man's Vault
copy, the helpers and the Healthchecks checks then all have to be set up again in one sitting.
