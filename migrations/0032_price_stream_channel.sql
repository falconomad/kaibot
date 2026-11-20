-- 0032: only the owner may receive the streamed prices (stakeholder, 20 Nov 2026: dashboard prices about once a
-- second; scripts/price_stream.py, apps/web/src/lib/stream.ts).
--
-- APPLIED by Product on 20 Nov 2026 (docs/ops/migrations_pending.md, step 19).
--
-- What changes. scripts/price_stream.py on the Mac broadcasts the held stocks' IEX prices, at most one message a
-- second, on the PRIVATE Supabase Realtime channel `kb:prices` (event `prices`) with the service key, which needs no
-- policy. A browser may join a private channel only where a SELECT policy on realtime.messages lets it. This adds that
-- one policy: a signed-in user receives broadcasts on `kb:prices` only if they are the owner (kaibot.is_owner(),
-- through a small definer wrapper, because 0002 revokes is_owner from authenticated). Anon gets nothing; no one may
-- send on it from a browser (no INSERT policy); presence is not allowed.
-- No table, no row and no engine object changes; the prices are never written to a table (Realtime's REST broadcast
-- does not store them). Until it is applied the page cannot join the channel and stays on its 10 s poll, unchanged.
--
-- Also, in the Supabase dashboard (not SQL): Realtime > Settings > "Allow public access" OFF, so that only private
-- channels exist (Supabase: private channel policies are enforced only then). The dashboard uses no public channel.
--
-- Additive: `create or replace function`, revoke/grant, and `create policy` only when it does not exist yet (no drop).
-- Any time, also while the engine runs.
--
-- Check after applying (read-only):
--   select policyname, cmd, roles from pg_policies where schemaname = 'realtime' and tablename = 'messages'
--     and policyname = 'kb owner receives prices';                                          -- one row, SELECT, {authenticated}
--   select has_function_privilege('anon', 'public.kb_price_stream_reader()', 'execute');     -- false

create or replace function public.kb_price_stream_reader() returns boolean
language sql stable security definer set search_path = '' as $$
  select kaibot.is_owner();
$$;
revoke all on function public.kb_price_stream_reader() from public, anon;
grant execute on function public.kb_price_stream_reader() to authenticated;

do $$ begin
  if not exists (select 1 from pg_policies where schemaname = 'realtime' and tablename = 'messages'
                 and policyname = 'kb owner receives prices') then
    create policy "kb owner receives prices" on realtime.messages
      for select to authenticated
      using ((select realtime.topic()) = 'kb:prices'
             and realtime.messages.extension = 'broadcast'
             and (select public.kb_price_stream_reader()));
  end if;
end $$;
