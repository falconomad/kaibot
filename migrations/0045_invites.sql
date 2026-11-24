-- 0045: sign-in by invite only (stakeholder, 24 Nov 2026). After 0044 (number only: no dependency on it; 0038's
-- kaibot.helper and 0002's kaibot.owner are read). Status (applied or pending): docs/ops/migrations_pending.md.
--
-- Why. Until now any email typed on the sign-in page got a magic link, and Supabase Auth created the account. Such an
-- account saw nothing (every kb_* function checks kaibot.is_owner(), kaibot.owner_run() or kaibot.is_helper()), but
-- anyone could create one. From now on the sign-in page never creates an account (shouldCreateUser: false) and a new
-- account needs an invite from a signed-in user.
--
-- What it adds.
-- 1) kaibot.invite: one row per invite (email, a random single-use code, who invited, created, expires after 7 days,
--    used). kaibot.profile: one row per invited user (user_id, name). RLS on, no policies and no table privilege for
--    anon or authenticated: they are reached only through the SECURITY DEFINER functions below.
-- 2) public.kb_invite_create(email)  a signed-in owner or invited user (never a helper) invites an email; returns the
--                                    code and the link path (/register?code=...). An open invite for the same email is
--                                    returned again, not doubled. Refused for an email that has an account already,
--                                    and when accounts + open invites would pass the cap of 5 (multi-user not launched).
--    public.kb_invite_check(code, email)  anon and authenticated: is this code usable (and for this email)? Answers
--                                    ok + a reason (unknown, used, expired, email_mismatch); never the invited email.
--    public.kb_invite_list()        the caller's invites (the owner: all) and the seats left.
--    public.kb_me()                 who the caller is: owner, invited member (and name), helper; can they invite.
-- 3) Server-side enforcement, without the service key: a BEFORE INSERT trigger on auth.users refuses a new account
--    unless its email has an open (unused, unexpired) invite AND the account carries that invite's code
--    (user_metadata.invite_code, which /register sends and the trigger then removes), and any new account once 5 exist. Supabase Auth inserts as
--    supabase_auth_admin, so a sign-up through the API, the dashboard's "Add user", an OAuth or anonymous sign-in all
--    pass through it. The very first account (no kaibot.owner row and no user at all) is let through, so a fresh
--    project still gets its owner. An admin may add an account in SQL only by setting kaibot.allow_direct_signup = 'on'
--    in that session (the test database sets it for its postgres role).
--    An AFTER INSERT OR UPDATE OF email_confirmed_at trigger: when an account's email is confirmed (the invitee clicked
--    the registration email), the invite is marked used and a profile row is made with the name the invitee typed
--    (user_metadata.name). It never raises: a failure there is a warning and never blocks a sign-in.
--
-- What it does not change. Every existing kb_* function stays as it is: owner-only (kaibot.is_owner() or
-- kaibot.owner_run()) or helper-only (kaibot.is_helper()). An invited user is neither, so they get no run data, no
-- headlines (kb_headlines, kaibot.headline: Alpaca/Benzinga licence owner_only), no config and no command. The realtime
-- price channel is owner-only too (0032). tests/test_invites_sql.py calls every one of them as an invited user.
--
-- Additive, in one transaction: create table / index / function / trigger only when missing, revoke and grant. The only update is inside
-- the redeem trigger (kaibot.invite.used_at of the confirmed email's invite). No delete, drop or truncate. Any time,
-- also while the engine runs (the engine never reads or writes auth.users).
--
-- Check after applying (read-only):
--   select relrowsecurity from pg_class where oid = 'kaibot.invite'::regclass;                            -- true
--   select has_table_privilege('authenticated', 'kaibot.invite', 'select');                              -- false
--   select has_function_privilege('anon', 'public.kb_invite_create(text)', 'execute');                  -- false
--   select has_function_privilege('anon', 'public.kb_invite_check(text, text)', 'execute');             -- true
--   select tgname from pg_trigger where tgrelid = 'auth.users'::regclass and tgname like 'kaibot_%' order by 1;
--     -- kaibot_invite_admit, kaibot_invite_redeem
--   select (public.kb_invite_check('0000-0000-0000-0000'))->>'reason';                                   -- unknown

begin;  -- one transaction: if any step is refused (e.g. the trigger on auth.users), nothing is left half-made

create table if not exists kaibot.invite (
  id          bigint generated always as identity primary key,
  email       text not null check (email = lower(email) and length(email) <= 254
                                   and email ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'),
  code        text not null unique check (code ~ '^[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}$'),
  invited_by  uuid not null references auth.users (id) on delete cascade,
  created_at  timestamptz not null default now(),
  expires_at  timestamptz not null default now() + interval '7 days',
  used_at     timestamptz,
  used_by     uuid references auth.users (id) on delete set null
);
create index if not exists invite_email on kaibot.invite (email) where used_at is null;
alter table kaibot.invite enable row level security;  -- no policies: only the functions below
revoke all on kaibot.invite from public, anon, authenticated;

create table if not exists kaibot.profile (
  user_id     uuid primary key references auth.users (id) on delete cascade,
  name        text not null check (length(btrim(name)) between 1 and 60 and name !~ '[[:cntrl:]]'),
  created_at  timestamptz not null default now()
);
alter table kaibot.profile enable row level security;  -- no policies: only the functions below
revoke all on kaibot.profile from public, anon, authenticated;

-- The cap while multi-user is not launched: accounts (owner and helpers included) plus open invites.
create or replace function kaibot.invite_cap() returns int
language sql immutable set search_path = '' as $$ select 5 $$;

create or replace function kaibot.invite_seats_taken() returns int
language sql stable security definer set search_path = '' as $$
  select (select count(*) from auth.users)::int
       + (select count(*) from kaibot.invite i
           where i.used_at is null and i.expires_at > now()
             and not exists (select 1 from auth.users u where lower(u.email) = i.email))::int;
$$;

-- A typed code: case and separators do not matter. Null unless it is 16 hex digits.
create or replace function kaibot.invite_code_norm(p_code text) returns text
language sql immutable set search_path = '' as $$
  select case when x ~ '^[0-9A-F]{16}$'
              then substr(x, 1, 4) || '-' || substr(x, 5, 4) || '-' || substr(x, 9, 4) || '-' || substr(x, 13, 4) end
  from (select regexp_replace(upper(coalesce(p_code, '')), '[^0-9A-Z]', '', 'g') as x) s;
$$;

-- Who may invite: a signed-in owner or invited member; never a helper (a machine account), never anon.
create or replace function kaibot.can_invite() returns boolean
language sql stable security definer set search_path = '' as $$
  select (select auth.uid()) is not null
     and not kaibot.is_helper()
     and (kaibot.is_owner() or exists (select 1 from kaibot.profile p where p.user_id = (select auth.uid())));
$$;

revoke all on function kaibot.invite_seats_taken(), kaibot.can_invite() from public, anon, authenticated;
revoke all on function kaibot.invite_cap(), kaibot.invite_code_norm(text) from public, anon, authenticated;

create or replace function public.kb_invite_create(p_email text) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
  v_email text := lower(btrim(coalesce(p_email, '')));
  v kaibot.invite;
  v_reused boolean := true;
begin
  if not kaibot.can_invite() then raise exception 'not authorised' using errcode = '42501'; end if;
  if length(v_email) > 254 or v_email !~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$' then
    raise exception 'that is not an email address' using errcode = '22023';
  end if;
  perform pg_advisory_xact_lock(hashtext('kaibot.invite'));  -- one invite or sign-up at a time: the cap holds
  if exists (select 1 from auth.users u where lower(u.email) = v_email) then
    raise exception 'this email has an account already' using errcode = '22023';
  end if;
  select * into v from kaibot.invite i
   where i.email = v_email and i.used_at is null and i.expires_at > now()
   order by i.created_at desc limit 1;
  if v.id is null then
    if kaibot.invite_seats_taken() >= kaibot.invite_cap() then
      raise exception 'invite limit reached: % accounts and open invites at most while multi-user is not launched',
        kaibot.invite_cap() using errcode = '22023';
    end if;
    insert into kaibot.invite (email, code, invited_by)
      values (v_email, kaibot.invite_code_norm(substr(md5(pg_catalog.gen_random_uuid()::text), 1, 16)), (select auth.uid()))
      returning * into v;
    v_reused := false;
  end if;
  return jsonb_build_object('email', v.email, 'code', v.code, 'link', '/register?code=' || v.code,
    'expires_at', v.expires_at, 'reused', v_reused);
end $$;

create or replace function public.kb_invite_check(p_code text, p_email text default null) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare
  v kaibot.invite;
begin
  select * into v from kaibot.invite i where i.code = kaibot.invite_code_norm(p_code);
  if v.id is null then return jsonb_build_object('ok', false, 'reason', 'unknown'); end if;
  if v.used_at is not null then return jsonb_build_object('ok', false, 'reason', 'used'); end if;
  if v.expires_at <= now() then return jsonb_build_object('ok', false, 'reason', 'expired'); end if;
  if p_email is not null and lower(btrim(p_email)) <> v.email then
    return jsonb_build_object('ok', false, 'reason', 'email_mismatch');
  end if;
  return jsonb_build_object('ok', true, 'reason', null, 'expires_at', v.expires_at, 'email_checked', p_email is not null);
end $$;

create or replace function public.kb_invite_list() returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare
  v_owner boolean := kaibot.is_owner();
begin
  if not kaibot.can_invite() then raise exception 'not authorised' using errcode = '42501'; end if;
  return jsonb_build_object(
    'cap', kaibot.invite_cap(),
    'taken', kaibot.invite_seats_taken(),
    'invites', coalesce((select jsonb_agg(jsonb_build_object(
        'id', i.id, 'email', i.email, 'created_at', i.created_at, 'expires_at', i.expires_at, 'used_at', i.used_at,
        'status', case when i.used_at is not null then 'used' when i.expires_at <= now() then 'expired' else 'open' end,
        'code', case when i.used_at is null and i.expires_at > now() then i.code end,
        'mine', i.invited_by = (select auth.uid()))
        order by i.created_at desc)
      from (select * from kaibot.invite x
             where v_owner or x.invited_by = (select auth.uid())
             order by x.created_at desc limit 50) i), '[]'::jsonb));
end $$;

create or replace function public.kb_me() returns jsonb
language sql stable security definer set search_path = '' as $$
  select jsonb_build_object(
    'owner', kaibot.is_owner(),
    'member', exists (select 1 from kaibot.profile p where p.user_id = (select auth.uid())),
    'helper', kaibot.is_helper(),
    'name', (select p.name from kaibot.profile p where p.user_id = (select auth.uid())),
    'can_invite', kaibot.can_invite());
$$;

revoke all on function public.kb_invite_create(text), public.kb_invite_check(text, text), public.kb_invite_list(),
  public.kb_me() from public, anon;
grant execute on function public.kb_invite_create(text), public.kb_invite_list(), public.kb_me() to authenticated;
grant execute on function public.kb_invite_check(text, text) to anon, authenticated;

-- ---------- enforcement on auth.users ----------
create or replace function kaibot.invite_admit() returns trigger
language plpgsql security definer set search_path = '' as $$
declare
  v_email text := lower(btrim(coalesce(new.email, '')));
begin
  if coalesce(current_setting('kaibot.allow_direct_signup', true), '') = 'on' then return new; end if;
  -- a fresh project: the first account becomes the owner (0002's kb_claim_owner)
  if not exists (select 1 from kaibot.owner) and not exists (select 1 from auth.users) then return new; end if;
  if v_email = '' then raise exception 'kaibot: sign-up is by invite only' using errcode = '42501'; end if;
  perform pg_advisory_xact_lock(hashtext('kaibot.invite'));
  if (select count(*) from auth.users) >= kaibot.invite_cap() then
    raise exception 'kaibot: the account limit is reached' using errcode = '42501';
  end if;
  -- the email AND the code (user_metadata.invite_code, sent by /register): knowing an invited address is not enough
  if not exists (select 1 from kaibot.invite i
                  where i.email = v_email and i.used_at is null and i.expires_at > now()
                    and i.code = kaibot.invite_code_norm(new.raw_user_meta_data ->> 'invite_code')) then
    raise exception 'kaibot: sign-up is by invite only' using errcode = '42501';
  end if;
  new.raw_user_meta_data := coalesce(new.raw_user_meta_data, '{}'::jsonb) - 'invite_code';  -- not kept on the account
  return new;
end $$;

create or replace function kaibot.invite_redeem() returns trigger
language plpgsql security definer set search_path = '' as $$
declare
  v_id bigint;
  v_name text;
begin
  if new.email_confirmed_at is null or (tg_op = 'UPDATE' and old.email_confirmed_at is not null) then return null; end if;
  begin
    select i.id into v_id from kaibot.invite i
     where i.email = lower(btrim(coalesce(new.email, ''))) and i.used_at is null
     order by (i.expires_at > now()) desc, i.created_at desc limit 1;
    if v_id is null then return null; end if;  -- the owner, a helper, an account made in SQL: no invite, no profile
    update kaibot.invite set used_at = now(), used_by = new.id where id = v_id;
    v_name := left(btrim(regexp_replace(coalesce(new.raw_user_meta_data ->> 'name', ''), '[[:cntrl:]]', '', 'g')), 60);
    insert into kaibot.profile (user_id, name)
      values (new.id, coalesce(nullif(v_name, ''), left(split_part(new.email, '@', 1), 60)))
      on conflict (user_id) do nothing;
  exception when others then
    raise warning 'kaibot.invite_redeem: % (%)', sqlerrm, sqlstate;  -- never block a sign-in
  end;
  return null;
end $$;

revoke all on function kaibot.invite_admit(), kaibot.invite_redeem() from public, anon, authenticated;

do $$ begin
  if not exists (select 1 from pg_trigger where tgname = 'kaibot_invite_admit' and tgrelid = 'auth.users'::regclass) then
    create trigger kaibot_invite_admit before insert on auth.users
      for each row execute function kaibot.invite_admit();
  end if;
  if not exists (select 1 from pg_trigger where tgname = 'kaibot_invite_redeem' and tgrelid = 'auth.users'::regclass) then
    create trigger kaibot_invite_redeem after insert or update of email_confirmed_at on auth.users
      for each row execute function kaibot.invite_redeem();
  end if;
end $$;

commit;
