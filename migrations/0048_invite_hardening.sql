-- 0048: invite hardening (#721, M19 ticket 8.3 of the second engine audit). After 0046 (re-creates five of its
-- functions and adds one column to its table). Status (applied or pending): docs/ops/migrations_pending.md.
--
-- Why. The second audit found four ways the invite functions of 0045/0046 said more, or let in more, than they should:
--   a) kb_invite_check (anon too) answered a reason, and with p_email it answered 'email_mismatch': whoever held a code
--      could test which address it was for, and the reasons told an unknown code from a used, revoked or expired one;
--   b) kb_invite_create answered "this email has an account already": an inviter could test which emails have accounts;
--   c) an invited member could invite others (chained invites), each new member inviting the next;
--   d) the redeem trigger picked the invite by EMAIL at confirmation, expired ones too (it only preferred an unexpired
--      one): an account that confirmed an invited address got the invite without its code, even after it expired.
--
-- What it changes.
-- 1) kaibot.can_invite(): the owner only (Product, 25 Nov 2026, under the stakeholder's full permission). A member
--    can no longer create, list or revoke invites (kb_invite_create, kb_invite_list, kb_invite_revoke refuse them
--    with 'not authorised', as they refuse anyone else), and kb_me answers can_invite false for them, so the
--    dashboard hides the invite card. Open invites a member made before this stay open; the owner sees them in
--    kb_invite_list (mine false) and may revoke them. A helper still never invites.
-- 2) kb_invite_create: an email that has an account gets the one generic refusal 'an invite can''t be made for this
--    email', which names no reason.
-- 3) kb_invite_check(code, email): answers {"ok": true|false} and nothing else: no reason, no expiry, no email. The
--    email argument is accepted (same signature, same grants; the dashboard sent it before) and ignored, so the
--    answer depends on the code alone. ok is true only for an open invite: unused, unrevoked and unexpired.
-- 4) Redeem by code. kaibot.invite gets admitted_id (uuid, no foreign key: the account does not exist yet when it is
--    written). The admit trigger, which already checks the email AND the code, now writes the admitted account's id
--    on that very invite. The redeem trigger marks used only the invite whose admitted_id is the confirming account,
--    for its email, unused, unrevoked AND unexpired: no invite is found by email alone, and an expired one is never
--    redeemed (the account then has no profile and sees "Not your dashboard"; kb_profile_repair makes none, since no
--    invite is used by it). A sign-up made before this migration and not yet confirmed has no admitted_id; the one
--    update below writes it for such accounts (an open invite whose email is an unconfirmed account's), so their
--    link still works. Since 0045 such an account could only have been admitted with the code (or made in SQL by an
--    admin).
--
-- What it does not change. The cap of 5, the code format, the seats count, revoking (who may revoke an invite is
-- still "the owner, or its inviter", and only the owner can now call it), kb_claim_owner, kb_profile_repair, the
-- profile's name rule, every other kb_* function, the tables' RLS and privileges (none for anon or authenticated).
-- The sign-in page's "No account for this email" is the owner's decision and is not touched here.
-- tests/test_invite_hardening_sql.py.
--
-- Additive, in one transaction: add column if not exists, create or replace function, revoke and grant. The updates:
-- one at apply time (admitted_id of open invites of unconfirmed accounts, above), and kaibot.invite inside the admit
-- trigger, kb_invite_revoke and the redeem trigger. No delete, drop or truncate. Any time, also while the engine runs
-- (the engine never reads or writes auth.users, kaibot.invite or kaibot.profile).
--
-- Check after applying (read-only):
--   select count(*) from information_schema.columns
--    where table_schema = 'kaibot' and table_name = 'invite' and column_name = 'admitted_id';          -- 1
--   select public.kb_invite_check('0000-0000-0000-0000');                                             -- {"ok": false}
--   select prosrc like '%admitted_id = new.id%' from pg_proc where proname = 'invite_redeem';          -- true
--   select prosrc not like '%profile p%' from pg_proc where proname = 'can_invite';                   -- true
--   select has_function_privilege('anon', 'public.kb_invite_check(text, text)', 'execute');          -- true
--   select has_function_privilege('anon', 'public.kb_invite_create(text)', 'execute');               -- false
--   -- open invites made by someone other than the owner (members, before this migration): revoke them if unwanted
--   select i.id, i.email, i.created_at from kaibot.invite i
--    where i.used_at is null and i.revoked_at is null and i.expires_at > now()
--      and i.invited_by not in (select user_id from kaibot.owner);

begin;

alter table kaibot.invite add column if not exists admitted_id uuid;  -- the account the admit trigger let in with it

-- ---------- 1) who may invite: the owner only ----------
create or replace function kaibot.can_invite() returns boolean
language sql stable security definer set search_path = '' as $$
  select (select auth.uid()) is not null and not kaibot.is_helper() and kaibot.is_owner();
$$;

-- ---------- 2) one generic refusal for an email that has an account ----------
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
    raise exception 'an invite can''t be made for this email' using errcode = '22023';
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

-- ---------- 3) the check answers ok and nothing else; the email is ignored ----------
create or replace function public.kb_invite_check(p_code text, p_email text default null) returns jsonb
language sql stable security definer set search_path = '' as $$
  select jsonb_build_object('ok', exists (
    select 1 from kaibot.invite i
     where i.code = kaibot.invite_code_norm(p_code)
       and i.used_at is null and i.revoked_at is null and i.expires_at > now()));
$$;

-- ---------- 4) redeem by code: the admit trigger holds the invite for the account it admits ----------
create or replace function kaibot.invite_admit() returns trigger
language plpgsql security definer set search_path = '' as $$
declare
  v_email text := lower(btrim(coalesce(new.email, '')));
  v_id bigint;
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
  -- a revoked or expired code admits nobody
  select i.id into v_id from kaibot.invite i
   where i.email = v_email and i.used_at is null and i.revoked_at is null and i.expires_at > now()
     and i.code = kaibot.invite_code_norm(new.raw_user_meta_data ->> 'invite_code');
  if v_id is null then
    raise exception 'kaibot: sign-up is by invite only' using errcode = '42501';
  end if;
  -- 0048: the invite is held for this account; only its confirmation redeems it (kaibot.invite_redeem)
  update kaibot.invite set admitted_id = new.id where id = v_id;
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
    -- the invite the admit trigger held for THIS account (it checked the code), never one found by email alone;
    -- unused, unrevoked and unexpired: an expired invite is refused
    select i.id into v_id from kaibot.invite i
     where i.admitted_id = new.id and i.email = lower(btrim(coalesce(new.email, '')))
       and i.used_at is null and i.revoked_at is null and i.expires_at > now()
     order by i.created_at desc limit 1;
    if v_id is null then return null; end if;  -- the owner, a helper, an account made in SQL, a revoked or expired invite
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

-- sign-ups made before this migration and not confirmed yet: their open invite is held for them, so their link works
update kaibot.invite i set admitted_id = u.id
  from auth.users u
 where lower(u.email) = i.email and u.email_confirmed_at is null
   and i.admitted_id is null and i.used_at is null and i.revoked_at is null and i.expires_at > now();

revoke all on function kaibot.can_invite(), kaibot.invite_admit(), kaibot.invite_redeem() from public, anon, authenticated;
revoke all on function public.kb_invite_create(text), public.kb_invite_check(text, text) from public, anon;
grant execute on function public.kb_invite_create(text) to authenticated;
grant execute on function public.kb_invite_check(text, text) to anon, authenticated;

commit;
