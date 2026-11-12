# Engine server: Oracle Cloud Always Free VM

Decision (10 Nov 2026): the engine runs on Oracle's Always Free tier (€0). Trade-off: Oracle may reclaim
Always Free instances it considers idle, so the watchdog, broker-held stops and the Supabase `pg_cron`
dead-man flatten (tech design v2 §3) must be in place before paper trading starts.

## 1. Create the account (you)
- Sign up at https://signup.cloud.oracle.com. Oracle asks for a card to verify identity; Always Free resources
  are not charged. Pick **Germany Central (Frankfurt)** as the home region (close to Supabase eu-central-1).
- Do not upgrade to Pay As You Go unless you decide to; Always Free limits then still apply but billing is enabled.

## 2. Create the VM (you, ~5 minutes)
- Compute → Instances → Create instance.
- Image: **Canonical Ubuntu 24.04**. Shape: **VM.Standard.A1.Flex (Ampere, arm64)**, 2 OCPU / 12 GB RAM
  (Always Free allows up to 4 OCPU / 24 GB in total).
- Networking: create a VCN with a public subnet; assign a public IPv4.
- SSH: upload your public key (e.g. `~/.ssh/id_ed25519.pub`).
- If you get "Out of capacity", retry later or pick another availability domain; A1 capacity is often scarce.

## 3. Open HTTPS (you)
- VCN → Security list → add ingress rule TCP 443 from 0.0.0.0/0 (SSH 22 is open by default).

## 4. Bootstrap (you or Claude over SSH)
```bash
scp scripts/oracle_bootstrap.sh ubuntu@<ip>:~ && ssh ubuntu@<ip> 'bash oracle_bootstrap.sh'
```

## 5. Deploy Kaibot
- Add a **read-only deploy key** on github.com/falconomad/kaibot → Settings → Deploy keys, then
  `git clone git@github.com:falconomad/kaibot.git /opt/kaibot`.
- Copy the `.env` from `~/Projects/kaibot-secrets/` to `/opt/kaibot/.env` (chmod 600). Paper keys only.
- `cd /opt/kaibot && docker compose up -d --build` (only outside 09:30–16:00 ET).

## Reclaim protection
Oracle's idle rule looks at low CPU, network and memory use over 7 days. The engine's 1-minute cycle and
websockets keep it busy during US sessions; the healthchecks.io ping alerts within minutes if the box disappears.

## Database
Supabase project `kaibot-db` (ref `dosdkdlxejqynbgkjhup`, eu-central-1 Frankfurt, free plan), restored on
10 Nov 2026. Kaibot v2 lives in schema `kaibot` (migration `migrations/0001_init.sql`, applied as `kaibot_v2_init`).
Tables from an earlier prototype remain in `public` and are not used by v2.
