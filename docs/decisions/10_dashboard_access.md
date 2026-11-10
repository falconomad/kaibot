# Decision 10: Dashboard access before the API exists (10 Nov 2026)

**Context.** The v2 design has the dashboard talk only to the FastAPI service on the VM. That VM arrives in M1,
but the user wants the dashboard live on Vercel now. The browser ships Supabase's public key, so anything the
public key can read is effectively public.

**Decision.**
- Single owner, email sign-in (Supabase magic link). The first account to sign in claims the dashboard
  (`kb_claim_owner`); after that every other account is refused, server-side.
- The browser never reads tables. It calls six `SECURITY DEFINER` functions in `public` (`kb_runs`, `kb_overview`,
  `kb_trades`, `kb_trade`, `kb_claim_owner`, `kb_new_run`), each checking `kaibot.is_owner()` first. `anon` can call none.
- The only write is `kb_new_run`, which queues a `new_run` command for the engine (idempotency key required).
  Pause and flatten stay on the VM API (M1), never optimistic.
- Old bloodbath tables in `public`: RLS on, no policies, grants to anon/authenticated revoked. Nothing deleted.
- Until the engine runs, the dashboard shows the Variant A hold-out replay as run 1 (`kind = 'replay'`), clearly
  labelled. Replays don't fake broker fills, gate records or agent-room output; those tabs say so.

**Consequences.** When the VM API lands, the dashboard can keep these read functions (cheap, already
owner-gated) or switch to the API; either way the browser still never holds the service key.
Supabase advisor warnings for these six functions are intentional.
