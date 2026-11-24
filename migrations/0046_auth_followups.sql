-- 0046: the auth follow-ups of #635 (the #634 security review). After 0045 (re-creates five of its functions and
-- reads its tables); 0002's kb_claim_owner is re-created too. Status (applied or pending): docs/ops/migrations_pending.md.
--
-- What it changes.
-- 1) Ownership claim. 0002's public.kb_claim_owner() made ANY signed-in account the owner while kaibot.owner was
--    empty, so if the owner row were ever deleted the next account to sign in (an invited member, say) would take the
--    dashboard. Now it claims only when no owner row exists AND exactly one account exists in auth.users AND that
--    account is the caller: the fresh project of 0045's admit trigger ("the very first account is let through").
--    It takes 0045's advisory lock, so no sign-up can land between the count and the claim. With an owner row it
--    answers kaibot.is_owner() as before. 0038's trigger still refuses a helper.
-- 2) Revoking invites. kaibot.invite gets revoked_at and revoked_by. public.kb_invite_revoke(id): the inviter, or the
--    owner for any invite, revokes an OPEN invite (unused, unrevoked, unexpired); anyone else, a used, expired or
--    already revoked invite, or an unknown id gets the same error, so a member learns nothing about others' invites.
--    A revoked code is refused everywhere: kb_invite_check answers reason 'revoked'; the admit trigger
--    (kaibot.invite_admit) refuses a sign-up with it; the redeem trigger (kaibot.invite_redeem) never marks it used
--    (an account admitted before the revoke and confirmed after it gets no profile); kb_invite_create does not hand it
--    back (a new invite for that email gets a new code); and it holds no seat (kaibot.invite_seats_taken).
--    kb_invite_list shows status 'revoked' and, per row, can_revoke (the dashboard shows Revoke only where it is true,
--    so before this migration it shows no button).
-- 3) Missing profile. 0045's redeem trigger marked the invite used and made the profile in one block that a failure
--    rolled back whole (a warning, never a blocked sign-in), leaving a signed-in account without a profile: "Not your
--    dashboard". Now the invite is marked used in one block and the profile made in a second, so a failed profile
--    leaves a used invite; and public.kb_profile_repair() (authenticated, called by the dashboard at sign-in when
--    kb_me says neither owner, member nor helper) makes the missing profile for the caller only if the caller's email
--    is confirmed, they are neither the owner nor a helper, and an invite is marked used BY THEM (used_by, set only by
--    the redeem trigger). It returns whether the caller has a profile. The name comes from kaibot.profile_name, the
--    redeem trigger's rule, which can no longer fail the profile's check (a last fallback 'Member').
--    So deleting a member's profile row does not remove them: they get it back at their next sign-in. To remove a
--    member, delete their account (Supabase dashboard, Authentication); the profile and their invites go with it.
--
-- What it does not change. Every other kb_* function, the cap of 5, the code format, who may invite
-- (kaibot.can_invite), the tables' RLS and privileges (none for anon or authenticated). tests/test_auth_followups_sql.py.
--
-- Additive, in one transaction: add column if not exists, create or replace function, revoke and grant. The only
-- updates are of kaibot.invite, inside kb_invite_revoke and the redeem trigger; the only insert of kaibot.owner is
-- 0002's, inside kb_claim_owner. No delete, drop or truncate. Any time, also while the engine runs (the engine never
-- reads or writes auth.users, kaibot.invite or kaibot.profile).
--
-- Check after applying (read-only):
--   select count(*) from information_schema.columns
--    where table_schema = 'kaibot' and table_name = 'invite' and column_name in ('revoked_at', 'revoked_by'); -- 2
--   select has_function_privilege('anon', 'public.kb_invite_revoke(bigint)', 'execute');                -- false
--   select has_function_privilege('authenticated', 'public.kb_invite_revoke(bigint)', 'execute');       -- true
--   select has_function_privilege('anon', 'public.kb_profile_repair()', 'execute');                     -- false
--   select prosrc like '%count(*) from auth.users) = 1%' from pg_proc where proname = 'kb_claim_owner';    -- true
--   select prosrc like '%revoked_at is null%' from pg_proc where proname = 'invite_admit';                -- true

begin;

alter table kaibot.invite add column if not exists revoked_at timestamptz;
alter table kaibot.invite add column if not exists revoked_by uuid references auth.users (id) on delete set null;

-- ---------- 1) ownership claim: only the one account of a fresh project ----------
create or replace function public.kb_claim_owner() returns boolean
language plpgsql security definer set search_path = '' as $$
declare
  v_uid uuid := (select auth.uid());
begin
  if v_uid is null then return false; end if;
  if exists (select 1 from kaibot.owner) then return kaibot.is_owner(); end if;
  perform pg_advisory_xact_lock(hashtext('kaibot.invite'));  -- 0045's lock: no sign-up between the count and the claim
  insert into kaibot.owner (user_id) select v_uid
   where not exists (select 1 from kaibot.owner)
     and (select count(*) from auth.users) = 1
     and exists (select 1 from auth.users u where u.id = v_uid);
  return kaibot.is_owner();
end $$;

-- ---------- 2) revoking invites ----------
-- seats: a revoked invite holds none
create or replace function kaibot.invite_seats_taken() returns int
language sql stable security definer set search_path = '' as $$
  select (select count(*) from auth.users)::int
       + (select count(*) from kaibot.invite i
           where i.used_at is null and i.revoked_at is null and i.expires_at > now()
             and not exists (select 1 from auth.users u where lower(u.email) = i.email))::int;
$$;

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
   where i.email = v_email and i.used_at is null and i.revoked_at is null and i.expires_at > now()
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
  if v.revoked_at is not null then return jsonb_build_object('ok', false, 'reason', 'revoked'); end if;
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
        'revoked_at', i.revoked_at,
        'status', case when i.used_at is not null then 'used' when i.revoked_at is not null then 'revoked'
                       when i.expires_at <= now() then 'expired' else 'open' end,
        'code', case when i.used_at is null and i.revoked_at is null and i.expires_at > now() then i.code end,
        'mine', i.invited_by = (select auth.uid()),
        'can_revoke', i.used_at is null and i.revoked_at is null and i.expires_at > now()
                      and (v_owner or i.invited_by = (select auth.uid())))
        order by i.created_at desc)
      from (select * from kaibot.invite x
             where v_owner or x.invited_by = (select auth.uid())
             order by x.created_at desc limit 50) i), '[]'::jsonb));
end $$;

create or replace function public.kb_invite_revoke(p_id bigint) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
  v kaibot.invite;
begin
  if not kaibot.can_invite() then raise exception 'not authorised' using errcode = '42501'; end if;
  perform pg_advisory_xact_lock(hashtext('kaibot.invite'));  -- after any sign-up that is checking this code
  update kaibot.invite i set revoked_at = now(), revoked_by = (select auth.uid())
   where i.id = p_id and i.used_at is null and i.revoked_at is null and i.expires_at > now()
     and (kaibot.is_owner() or i.invited_by = (select auth.uid()))
   returning * into v;
  if v.id is null then
    raise exception 'no open invite of yours with that id' using errcode = '22023';
  end if;
  return jsonb_build_object('id', v.id, 'email', v.email, 'status', 'revoked', 'revoked_at', v.revoked_at);
end $$;

-- ---------- 3) the profile: made in its own block, and repaired at sign-in ----------
-- The redeem trigger's name rule, which always satisfies kaibot.profile's check: the typed name, else the email's
-- local part, else 'Member' (control characters removed, trimmed, at most 60 characters).
create or replace function kaibot.profile_name(p_meta jsonb, p_email text) returns text
language sql immutable set search_path = '' as $$
  select coalesce(
    nullif(btrim(left(btrim(regexp_replace(coalesce(p_meta ->> 'name', ''), '[[:cntrl:]]', '', 'g')), 60)), ''),
    nullif(btrim(left(btrim(regexp_replace(split_part(coalesce(p_email, ''), '@', 1), '[[:cntrl:]]', '', 'g')), 60)), ''),
    'Member');
$$;

create or replace function public.kb_profile_repair() returns boolean
language plpgsql security definer set search_path = '' as $$
declare
  v_uid uuid := (select auth.uid());
  v_email text;
  v_meta jsonb;
  v_confirmed timestamptz;
begin
  if v_uid is null then return false; end if;
  if exists (select 1 from kaibot.profile p where p.user_id = v_uid) then return true; end if;
  if kaibot.is_owner() or kaibot.is_helper() then return false; end if;
  select u.email, u.raw_user_meta_data, u.email_confirmed_at into v_email, v_meta, v_confirmed
    from auth.users u where u.id = v_uid;
  if v_confirmed is null then return false; end if;  -- no such account, or its email is not confirmed
  -- an invite this account redeemed (used_by is set only by the redeem trigger), never a revoked one
  if not exists (select 1 from kaibot.invite i
                  where i.used_by = v_uid and i.used_at is not null and i.revoked_at is null) then
    return false;
  end if;
  insert into kaibot.profile (user_id, name) values (v_uid, kaibot.profile_name(v_meta, v_email))
    on conflict (user_id) do nothing;
  return true;
end $$;

-- ---------- enforcement on auth.users (0045's triggers keep their functions' oids) ----------
create or replace function kaibot.invite_admit() returns trigger
language plpgsql security definer set search_path = '' as $$
declare
  v_email text := lower(btrim(coalesce(new.email, '')));
begin
  if coalesce(current_setting('kaibot.allow_direct_signup', true), '') = 'on' then return new; end if;
  -- a fresh project: the first account becomes the owner (kb_claim_owner)
  if not exists (select 1 from kaibot.owner) and not exists (select 1 from auth.users) then return new; end if;
  if v_email = '' then raise exception 'kaibot: sign-up is by invite only' using errcode = '42501'; end if;
  perform pg_advisory_xact_lock(hashtext('kaibot.invite'));
  if (select count(*) from auth.users) >= kaibot.invite_cap() then
    raise exception 'kaibot: the account limit is reached' using errcode = '42501';
  end if;
  -- the email AND the code (user_metadata.invite_code, sent by /register): knowing an invited address is not enough;
  -- a revoked code admits nobody (0046)
  if not exists (select 1 from kaibot.invite i
                  where i.email = v_email and i.used_at is null and i.revoked_at is null and i.expires_at > now()
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
begin
  if new.email_confirmed_at is null or (tg_op = 'UPDATE' and old.email_confirmed_at is not null) then return null; end if;
  begin
    select i.id into v_id from kaibot.invite i
     where i.email = lower(btrim(coalesce(new.email, ''))) and i.used_at is null and i.revoked_at is null
     order by (i.expires_at > now()) desc, i.created_at desc limit 1;
    if v_id is null then return null; end if;  -- the owner, a helper, an account made in SQL, a revoked invite
    update kaibot.invite set used_at = now(), used_by = new.id where id = v_id;
  exception when others then
    raise warning 'kaibot.invite_redeem: % (%)', sqlerrm, sqlstate;  -- never block a sign-in
    return null;
  end;
  begin  -- its own block: a failure here keeps the invite used, and kb_profile_repair makes the profile at sign-in
    insert into kaibot.profile (user_id, name) values (new.id, kaibot.profile_name(new.raw_user_meta_data, new.email))
      on conflict (user_id) do nothing;
  exception when others then
    raise warning 'kaibot.invite_redeem profile: % (%)', sqlerrm, sqlstate;
  end;
  return null;
end $$;

revoke all on function kaibot.invite_seats_taken(), kaibot.profile_name(jsonb, text), kaibot.invite_admit(),
  kaibot.invite_redeem() from public, anon, authenticated;
revoke all on function public.kb_claim_owner(), public.kb_invite_create(text), public.kb_invite_check(text, text),
  public.kb_invite_list(), public.kb_invite_revoke(bigint), public.kb_profile_repair() from public, anon;
grant execute on function public.kb_claim_owner(), public.kb_invite_create(text), public.kb_invite_list(),
  public.kb_invite_revoke(bigint), public.kb_profile_repair() to authenticated;
grant execute on function public.kb_invite_check(text, text) to anon, authenticated;

commit;
