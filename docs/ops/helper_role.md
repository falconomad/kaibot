# A narrow key for the helpers (M10 ticket 1.26, issue #451)

**Why.** The engine's Supabase service key can do anything in the database: insert a command, take the engine
lease, write a fresh engine heartbeat (so the dead-man never fires), replace the owner. Until now the price relay,
the market-notice job and some research scripts used that same key. After this setup they sign in as a separate
"helper" user that can do a few listed things and nothing else, and the service key stays with the engine and its watchdog.

**What the helper can do** (migration `0038_helper_role.sql`; the last row is `0047_helper_shadow_results.sql`):

| It can | Through | It cannot |
|---|---|---|
| send prices on the private channel `kb:prices` | one Realtime policy | send on any other channel, or receive |
| write the heartbeat row `price_stream` | `kb_helper_heartbeat` | write the `engine` or `watchdog` heartbeat |
| publish and withdraw market notices | `kb_helper_publish_notices` | read or write any other table |
| read nine columns of closed trades | `kb_helper_trades` | read open positions, orders, the ledger |
| read the shadow rules' results (#371, migration 0047) | `kb_helper_shadow_results` | read `event` or `signal` rows themselves |
| read which stocks the paper run holds, with their size (#712, migration 0053; the price relay) | `kb_helper_held_symbols` | read cost, stops, strategies, P&L or the ledger rows themselves |

It cannot insert a command, read or take the lease, write a halt, or touch the owner row: the helper is the
ordinary `authenticated` role, which has no right on any `kaibot` table (migration 0004), and the dashboard's
functions answer only the owner. It can never become the owner either: the "first signed-in user becomes the
owner" function of migration 0002 answers only while there is no owner, and 0038 adds a trigger that refuses an
owner row for a user listed as a helper. `tests/test_helper_role_sql.py` checks each of these on a scratch database.

**Nothing changes for the notice job until you do the setup.** Without the helpers' env file it keeps using the
engine's `.env` (it says "still on the service key" in its log). The price relay no longer has that fallback
(#712): without `helper.env` and `data.env` it does not start ([secrets.md](secrets.md)).

## One-time setup (stakeholder, about 10 minutes, any time)

No secret goes into the repo, into GitHub or into a chat. The password you choose in step 1 goes only into the
file of step 3.

1. **Create the helper user.** Supabase dashboard > Authentication > Users > "Add user" > "Create new user".
   - Email: any address you will recognise as the helper, for example `kaibot-helper@<your domain>` (it does not
     need to receive mail). It must NOT be the address you sign in to the dashboard with.
   - Password: a long random one (your password manager's generator; 30 characters or more).
   - Tick "Auto Confirm User". Create.
2. **Apply migration 0038 and list the user as a helper.** Supabase dashboard > SQL editor:
   - run the whole file `migrations/0038_helper_role.sql` (Product may do this on your word, as usual);
   - then run this once, with the email of step 1:
     ```sql
     insert into kaibot.helper (user_id, name)
     select id, 'mac helpers' from auth.users where email = 'PUT-THE-HELPER-EMAIL-HERE'
     on conflict (user_id) do nothing;
     select count(*) from kaibot.helper;   -- 1
     ```
3. **Create the helpers' env file** on the Mac: `~/Projects/kaibot-secrets/helper.env`, with these three lines
   (a new file; do not add them to `.env`, and do not put the service key in this file: the scripts refuse a
   helpers' file that holds one):
   ```
   SUPABASE_PUBLISHABLE_KEY=<Supabase > Project Settings > API keys > Publishable key, sb_publishable_...>
   SUPABASE_HELPER_EMAIL=<the email of step 1>
   SUPABASE_HELPER_PASSWORD=<the password of step 1>
   ```
   Then `chmod 600 ~/Projects/kaibot-secrets/helper.env`. (The publishable key is not a secret: the dashboard's
   web page ships it.) **No Alpaca key here (audit 2 7.2, #712):** the relay's market-data key is the data pair of
   a second paper account in `data.env` ([secrets.md](secrets.md), "The data key"). The setup before #712 put the
   trading pair in this file: delete those two lines; they are skipped since #712 and
   `scripts/data_key_check.py` fails until they are gone.
4. **Check it.**
   ```
   cd ~/Projects/kaibot && git pull
   PYTHONPATH=src python3 scripts/helper_check.py
   ```
   Eight lines, each `OK`: the sign-in, "the database lists this user as a helper", the heartbeat, a test
   broadcast, and four things that must be refused (insert a command, read the lease, write a heartbeat row
   directly, write the engine's heartbeat through the helper function). The two write probes carry values that
   do no harm even if they were accepted (a command kind that does not exist, a heartbeat row no process reads). It prints no key. **If the broadcast line says FAIL**,
   stop here and tell Product: sending on a private channel with a user's sign-in over Realtime's REST endpoint
   is the one step that could not be tried before this setup (see "Not verified" below); delete `helper.env` and
   everything runs as before.
5. **Restart the service** so the relay picks the file up: `./scripts/service.sh restart` outside 09:30-16:00 New
   York time. `logs/price_stream.log` then says "signed in as the helper (no service key in this process)", and
   the Health page still shows the price stream's heartbeat. The next morning's `logs/notices.log` no longer says
   "still on the service key".

To undo: delete `helper.env` and restart the service. To retire the helper for good: Supabase > Authentication >
Users > delete the user (its `kaibot.helper` row goes with it).

## What still holds the service key, and why

- **`scripts/shadow_results.sh`** needs `DATABASE_URL` (the database password); `scripts/shadow_results.py` prints
  the same tables as the helper (migration 0047) and is what the research runner runs.
- **The engine and its watchdog**: by design (the lease, the journal's replica, the commands, the halts).
- **`scripts/trial_registry.py`, `scripts/run_config_replays.py`, `scripts/eval_room.py --live`,
  `scripts/outage_drill.sh`**: run by hand on the Mac; they write research tables (`trial`, `replay_job`), the
  LLM budget ledger, or run a drill against the engine's own rows. Each needs its own narrow function before it
  can move to the helper; not done in this ticket. `scripts/run_ablation.py --supabase` moved (it only reads
  closed trades) and uses the helper when `helper.env` exists.
- Both keys are in files on the same Mac under the same user. The split protects against a bug or a compromise of
  a helper process, and it is what lets a helper run on another machine later without the service key going
  there. It does not protect against someone who can read the Mac's files.

## Not verified

- No production object was read or changed for this ticket; the migration ran only on the scratch Postgres of the
  test suite, where Supabase's `auth`, `realtime.messages` and `realtime.topic()` are stand-ins.
- Supabase's documentation describes the INSERT policy on `realtime.messages` as what authorises a signed-in
  user to send a broadcast on a private channel. Whether Realtime's REST broadcast endpoint applies it to a
  user's token the same way as a websocket send was not confirmed from the documentation; step 4 tests it.
