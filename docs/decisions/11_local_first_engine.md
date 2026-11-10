# Decision 11: Engine runs on the user's Mac first, packaged to move to any cloud (10 Nov 2026)

**Context.** Oracle's free VM failed and the GCP VM isn't set up yet. The user wants to start forward paper trading now,
running the engine on their Mac while the dashboard and database stay on Vercel and Supabase.

**Decision.**
- The engine is a single process with outgoing connections only. The dashboard never talks to it directly:
  commands go through `kaibot.command` (via the owner-only `kb_command` / `kb_new_run` functions) and the engine polls
  every ~10 s. This replaces the planned FastAPI control API on the VM; `apps/api` stays as a stub.
- Settings come only from environment variables / one `.env` file. The same Docker image runs on the Mac, GCP, Oracle
  or any Docker host (`scripts/run_engine.sh`, `compose.yaml`, `docs/ops/run_engine.md`).
- The engine writes with the Supabase `service_role` key. Only `service_role` has the `kaibot` schema; anon and
  authenticated have nothing (migration 0004). The key stays in the user's `.env`.
- Safety does not depend on the laptop: every entry is a bracket order with the stop and target held at Alpaca;
  unknown positions in the paper account block all entries; positions left from an earlier day are closed at the next open.

**Consequences.** Days the Mac is off are no-trade days, which slows the path to the 900-trade go-live bar but does not
bias it. Moving to a VM is copying the `.env` and running the same container; never run two engines on one account.
