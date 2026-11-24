"""Migration 0045: sign-in by invite only (stakeholder, 24 Nov 2026).

kaibot.invite and kaibot.profile, kb_invite_create / kb_invite_check / kb_invite_list / kb_me, the trigger on
auth.users that refuses an account without an open invite, the redeem trigger, and the data-safety promise: an invited
user reaches no owner data (no headlines, no run, no config, no command) through any kb_* function.

The file checks run everywhere. The SQL checks need a scratch Postgres with every migration applied
(KAIBOT_TEST_PG; CI and KAIBOT_TEST_PG_AUTO=1 build one; skipped otherwise). The scratch database's postgres role has
kaibot.allow_direct_signup on (tests/pg_scratch.py), so the other tests can seed auth.users; here it is turned off,
which is what Supabase Auth's own inserts see in production."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
M45 = ROOT / "migrations" / "0045_invites.sql"
PG = os.environ.get("KAIBOT_TEST_PG")
needs_pg = pytest.mark.skipif(not PG, reason="set KAIBOT_TEST_PG to a scratch Postgres with migration 0045 applied")
# the other SQL tests seed users ...01-...03 (OWNER is ...01 everywhere); this file's own users are ...a1-...a9
OWNER = "00000000-0000-0000-0000-000000000001"
SHARED = ("00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002",
          "00000000-0000-0000-0000-000000000003")
MEMBER, STRANGER, NEWBIE, HELPERX = (f"00000000-0000-0000-0000-0000000000a{i}" for i in (1, 2, 3, 4))
# the functions an invited member may call; every other kb_* must refuse them
MEMBER_OK = {"kb_invite_create", "kb_invite_check", "kb_invite_list", "kb_me", "kb_claim_owner", "kb_helper_whoami"}
# the realtime policies' predicates (0032, 0038): they answer false rather than raise
PREDICATES = {"kb_price_stream_reader", "kb_price_stream_sender"}


def _sql() -> str:
    return "\n".join(ln for ln in M45.read_text().splitlines() if not ln.lstrip().startswith("--")).lower()


# ---------------------------------------------------------------- the file


def test_0045_is_additive():
    s = _sql()
    assert not re.search(r"\b(drop|truncate|alter function|delete from)\b", s)
    assert re.findall(r"\bupdate\s+(kaibot\.\w+)", s) == ["kaibot.invite"]
    assert s.count("enable row level security") == 2
    assert "revoke all on kaibot.invite from public, anon, authenticated;" in s
    assert "revoke all on kaibot.profile from public, anon, authenticated;" in s
    assert "create policy" not in s and not re.search(r"grant [a-z, ]+ on kaibot\.", s)


def test_0045_every_function_is_definer_with_an_empty_search_path_and_anon_gets_only_the_check():
    s = _sql()
    for body in re.split(r"create or replace function ", s)[1:]:
        assert "set search_path = ''" in body.split("as $$")[0], body[:60]
    assert "grant execute on function public.kb_invite_check(text, text) to anon, authenticated;" in s
    assert s.count(" to anon") == 1
    for fn in ("kb_invite_create", "kb_invite_list"):
        body = s.split(f"create or replace function public.{fn}(")[1].split("end $$;")[0]
        assert "if not kaibot.can_invite() then raise exception 'not authorised'" in body, fn


def test_0045_changes_no_existing_function():
    """The data-safety promise rests on the existing owner checks: this file must not re-create any of them."""
    s = _sql()
    made = set(re.findall(r"create or replace function (\w+\.\w+)\(", s))
    assert made == {"kaibot.invite_cap", "kaibot.invite_seats_taken", "kaibot.invite_code_norm", "kaibot.can_invite",
                    "public.kb_invite_create", "public.kb_invite_check", "public.kb_invite_list", "public.kb_me",
                    "kaibot.invite_admit", "kaibot.invite_redeem"}


def test_0045_header_claims_no_status():
    head = [ln for ln in M45.read_text().splitlines() if ln.startswith("--")]
    assert not any(re.match(r"^--\s*(NOT )?APPLIED\b", ln) for ln in head)


# ---------------------------------------------------------------- the database


def psql(sql: str, uid: str = "", role: str | None = "authenticated", direct: bool = True) -> str:
    pre = f"set test.uid = '{uid}';\n" + ("" if direct else "set kaibot.allow_direct_signup = 'off';\n")
    pre += f"set role {role};\n" if role else ""
    r = subprocess.run(["psql", *shlex.split(PG), "-tA", "-q", "-v", "ON_ERROR_STOP=1"], input=pre + sql,
                       capture_output=True, text=True)
    if r.returncode:
        raise AssertionError(r.stderr)
    return r.stdout.strip()


def j(sql: str, uid: str = OWNER, role: str | None = "authenticated") -> dict:
    return json.loads(psql(sql, uid=uid, role=role))


def signup(uid: str, email: str, name: str | None = None, confirm: bool = False, code: str | None = None) -> None:
    """What Supabase Auth does for /register's signInWithOtp: insert the account with the user_metadata the page sent
    (the admit trigger runs), later confirm its email."""
    data = {k: v for k, v in (("name", name), ("invite_code", code)) if v is not None}
    meta = "'" + json.dumps(data).replace("'", "''") + "'" if data else "null"
    psql(f"insert into auth.users (id, email, raw_user_meta_data) values ('{uid}', '{email}', {meta});",
         role=None, direct=False)
    if confirm:
        psql(f"update auth.users set email_confirmed_at = now() where id = '{uid}';", role=None, direct=False)


def _reset() -> None:
    keep = ", ".join(f"'{u}'" for u in SHARED)
    psql(f"""delete from kaibot.invite; delete from kaibot.profile;
      delete from auth.users where id not in ({keep});
      insert into auth.users (id) values ('{OWNER}') on conflict do nothing;
      insert into kaibot.owner (user_id) values ('{OWNER}') on conflict do nothing;""", role=None)


@pytest.fixture
def db():
    _reset()
    yield
    _reset()


def _member(uid: str = MEMBER, email: str = "member@example.com", name: str = "Mia") -> None:
    code = j(f"select public.kb_invite_create('{email}');")["code"]
    signup(uid, email, name, confirm=True, code=code)
    assert j(f"select public.kb_invite_check('{code}');")["reason"] == "used"


@needs_pg
def test_invite_create_check_and_the_link(db):
    inv = j("select public.kb_invite_create('  Friend@Example.COM ');")
    assert inv["email"] == "friend@example.com" and re.fullmatch(r"[0-9A-F]{4}(-[0-9A-F]{4}){3}", inv["code"])
    assert inv["link"] == f"/register?code={inv['code']}" and inv["reused"] is False
    # an open invite for the same email is handed back, not doubled
    again = j("select public.kb_invite_create('friend@example.com');")
    assert again["code"] == inv["code"] and again["reused"] is True
    # anyone holding the code may check it, typed in any case and without dashes; the email is never returned
    typed = inv["code"].replace("-", "").lower()
    ok = j(f"select public.kb_invite_check('{typed}');", role="anon", uid="")
    assert ok["ok"] is True and "email" not in ok
    assert j(f"select public.kb_invite_check('{inv['code']}', 'FRIEND@example.com');", role="anon", uid="")["ok"]
    bad = j(f"select public.kb_invite_check('{inv['code']}', 'someone@example.com');", role="anon", uid="")
    assert bad == {"ok": False, "reason": "email_mismatch"}
    assert j("select public.kb_invite_check('0000-0000-0000-0000');", role="anon", uid="")["reason"] == "unknown"
    assert j("select public.kb_invite_check(null);", role="anon", uid="")["reason"] == "unknown"
    listed = j("select public.kb_invite_list();")
    assert listed["cap"] == 5 and [i["status"] for i in listed["invites"]] == ["open"]
    with pytest.raises(AssertionError, match="not an email"):
        psql("select public.kb_invite_create('not-an-email');", uid=OWNER)


@needs_pg
def test_an_invite_expires_after_seven_days(db):
    inv = j("select public.kb_invite_create('late@example.com');")
    days = psql(f"select extract(day from expires_at - created_at) from kaibot.invite where code = '{inv['code']}';",
                role=None)
    assert days == "7"
    psql(f"update kaibot.invite set expires_at = now() - interval '1 second' where code = '{inv['code']}';", role=None)
    assert j(f"select public.kb_invite_check('{inv['code']}');", role="anon", uid="")["reason"] == "expired"
    # an expired invite admits nobody, and a new one gets a new code
    with pytest.raises(AssertionError, match="invite only"):
        signup(NEWBIE, "late@example.com", code=inv["code"])
    fresh = j("select public.kb_invite_create('late@example.com');")
    assert fresh["code"] != inv["code"] and fresh["reused"] is False


@needs_pg
def test_who_may_invite(db):
    with pytest.raises(AssertionError, match="permission denied"):
        psql("select public.kb_invite_create('x@example.com');", role="anon")
    with pytest.raises(AssertionError, match="permission denied"):
        psql("select public.kb_invite_list();", role="anon")
    # a signed-in account with neither an owner row nor a profile (made in SQL by an admin)
    psql(f"insert into auth.users (id, email) values ('{STRANGER}', 'stranger@example.com');", role=None)
    with pytest.raises(AssertionError, match="not authorised"):
        psql("select public.kb_invite_create('x@example.com');", uid=STRANGER)
    assert j("select public.kb_me();", uid=STRANGER) == {"owner": False, "member": False, "helper": False,
                                                          "name": None, "can_invite": False}
    # a helper (machine account) never invites, even with a profile row
    psql(f"""insert into auth.users (id, email) values ('{HELPERX}', 'helper-x@example.com');
      insert into kaibot.helper (user_id, name) values ('{HELPERX}', 'test helper') on conflict do nothing;
      insert into kaibot.profile (user_id, name) values ('{HELPERX}', 'helper');""", role=None)
    with pytest.raises(AssertionError, match="not authorised"):
        psql("select public.kb_invite_create('x@example.com');", uid=HELPERX)
    psql(f"delete from kaibot.helper where user_id = '{HELPERX}'; "
         f"delete from auth.users where id in ('{HELPERX}', '{STRANGER}');", role=None)  # their seats back
    # an invited member may invite, and sees only their own invites; the owner sees all
    _member()
    assert j("select public.kb_me();", uid=MEMBER)["name"] == "Mia"
    j("select public.kb_invite_create('friend-of-mia@example.com');", uid=MEMBER)
    j("select public.kb_invite_create('friend-of-owner@example.com');")
    mine = j("select public.kb_invite_list();", uid=MEMBER)["invites"]
    assert [i["email"] for i in mine] == ["friend-of-mia@example.com"]
    assert len(j("select public.kb_invite_list();")["invites"]) == 3
    # nobody reads the tables directly
    for uid in (OWNER, MEMBER):
        for t in ("kaibot.invite", "kaibot.profile"):
            with pytest.raises(AssertionError, match="permission denied"):
                psql(f"select count(*) from {t};", uid=uid)


@needs_pg
def test_the_cap_counts_accounts_and_open_invites(db):
    taken = j("select public.kb_invite_list();")["taken"]
    assert taken == int(psql("select count(*) from auth.users;", role=None))
    made = [j(f"select public.kb_invite_create('cap{i}@example.com');") for i in range(5 - taken)]
    assert j("select public.kb_invite_list();")["taken"] == 5
    with pytest.raises(AssertionError, match="invite limit reached"):
        psql("select public.kb_invite_create('one-too-many@example.com');", uid=OWNER)
    # re-asking for an open invite is not a new seat
    assert j(f"select public.kb_invite_create('{made[0]['email']}');")["reused"] is True
    # an expired invite frees its seat
    psql(f"update kaibot.invite set expires_at = now() - interval '1 second' where code = '{made[0]['code']}';",
         role=None)
    j("select public.kb_invite_create('one-more@example.com');")


@needs_pg
def test_the_trigger_refuses_signups_without_an_invite(db):
    with pytest.raises(AssertionError, match="invite only"):
        signup(NEWBIE, "nobody-invited@example.com")
    with pytest.raises(AssertionError, match="invite only"):  # anonymous sign-in: no email
        psql(f"insert into auth.users (id) values ('{NEWBIE}');", role=None, direct=False)
    assert psql(f"select count(*) from auth.users where id = '{NEWBIE}';", role=None) == "0"


@needs_pg
def test_registration_with_another_email_than_the_invited_one_is_refused(db):
    inv = j("select public.kb_invite_create('invited@example.com');")
    # the page asks first, and the database refuses anyway if the page is skipped
    assert j(f"select public.kb_invite_check('{inv['code']}', 'other@example.com');", role="anon", uid="")["reason"] \
        == "email_mismatch"
    with pytest.raises(AssertionError, match="invite only"):
        signup(NEWBIE, "other@example.com", "Mallory", code=inv["code"])
    # the invited email without the code (someone who only knows the address) is refused too
    with pytest.raises(AssertionError, match="invite only"):
        signup(NEWBIE, "invited@example.com", "Mallory")
    with pytest.raises(AssertionError, match="invite only"):
        signup(NEWBIE, "invited@example.com", "Mallory", code="0000-0000-0000-0000")
    assert j(f"select public.kb_invite_check('{inv['code']}');", role="anon", uid="")["ok"] is True


@needs_pg
def test_registration_marks_the_invite_used_once_the_email_is_confirmed(db):
    inv = j("select public.kb_invite_create('newbie@example.com');")
    signup(NEWBIE, "Newbie@Example.com", "Nora  ", code=inv["code"].replace("-", "").lower())  # typed, any case
    # the code is not kept on the account
    assert psql(f"select raw_user_meta_data from auth.users where id = '{NEWBIE}';", role=None) == '{"name": "Nora  "}'
    # admitted, but not yet used: the invitee has not clicked the email
    assert j(f"select public.kb_invite_check('{inv['code']}');", role="anon", uid="")["ok"] is True
    assert j("select public.kb_me();", uid=NEWBIE)["member"] is False
    psql(f"update auth.users set email_confirmed_at = now() where id = '{NEWBIE}';", role=None, direct=False)
    assert j(f"select public.kb_invite_check('{inv['code']}');", role="anon", uid="")["reason"] == "used"
    assert j("select public.kb_me();", uid=NEWBIE) == {"owner": False, "member": True, "helper": False,
                                                        "name": "Nora", "can_invite": True}
    # a later confirmation (or any other update) changes nothing; the used code admits nobody else
    psql(f"update auth.users set email_confirmed_at = now() where id = '{NEWBIE}';", role=None, direct=False)
    assert psql("select count(*) from kaibot.profile;", role=None) == "1"
    with pytest.raises(AssertionError, match="has an account already"):
        psql("select public.kb_invite_create('newbie@example.com');", uid=OWNER)


@needs_pg
def test_the_owner_and_signups_without_an_invite_get_no_profile(db):
    psql(f"update auth.users set email_confirmed_at = now() where id = '{OWNER}';", role=None, direct=False)
    assert psql("select count(*) from kaibot.profile;", role=None) == "0"
    assert j("select public.kb_me();")["owner"] is True


@needs_pg
def test_the_very_first_account_is_let_through(db):
    """A fresh project: no owner and no user at all. Run inside one transaction, rolled back."""
    out = psql(f"""begin;
      set local kaibot.allow_direct_signup = 'off';
      delete from auth.users;
      insert into auth.users (id, email) values ('{NEWBIE}', 'first@example.com');
      select count(*) from auth.users;
      rollback;""", role=None)
    assert out.splitlines()[-1] == "1"
    assert psql(f"select count(*) from kaibot.owner where user_id = '{OWNER}';", role=None) == "1"


def _kb_functions() -> list[tuple[str, int]]:
    rows = psql("""select p.proname, p.pronargs from pg_proc p join pg_namespace n on n.oid = p.pronamespace
       where n.nspname = 'public' and p.proname like 'kb\\_%'
         and has_function_privilege('authenticated', p.oid, 'execute') order by 1;""", role=None)
    return [(r.split("|")[0], int(r.split("|")[1])) for r in rows.splitlines()]


@needs_pg
def test_an_invited_member_reaches_no_owner_data(db):
    """Every kb_* function the authenticated role may call, called as an invited member with null arguments: each
    one refuses (not authorised; the two realtime policy predicates answer false), except the invite functions,
    kb_me, kb_claim_owner (false: the owner exists) and
    kb_helper_whoami (false). This is the database's enforcement; the dashboard's hiding is only the second lock."""
    _member()
    fns = _kb_functions()
    names = {f for f, _ in fns}
    assert {"kb_headlines", "kb_command", "kb_save_config", "kb_apply_config", "kb_new_run", "kb_overview"} <= names
    refused = []
    for fn, nargs in fns:
        if fn in MEMBER_OK:
            continue
        if fn in PREDICATES:
            assert psql(f"select public.{fn}();", uid=MEMBER) == "f", fn
            refused.append(fn)
            continue
        args = ", ".join(["null"] * nargs)
        with pytest.raises(AssertionError, match="not authorised") as e:
            psql(f"select public.{fn}({args});", uid=MEMBER)
        assert "42501" in e.value.args[0] or "not authorised" in e.value.args[0]
        refused.append(fn)
    assert len(refused) >= 35, refused
    assert psql("select public.kb_claim_owner();", uid=MEMBER) == "f"
    assert j("select public.kb_helper_whoami();", uid=MEMBER) == {"helper": False}
    # the headline table: no privilege, and RLS shows nothing to a non-owner even with one
    with pytest.raises(AssertionError, match="permission denied"):
        psql("select count(*) from kaibot.headline;", uid=MEMBER)
    assert psql("""begin; grant usage on schema kaibot to authenticated;
      grant select on kaibot.headline to authenticated;
      grant execute on function kaibot.is_owner() to authenticated;
      insert into kaibot.headline (dedup_key, symbols, headline, source, licence, created_at)
        values ('alp:rls1', array['AAPL'], 'Owner-only', 'alpaca_benzinga', 'owner_only', now());
      set local role authenticated;
      select count(*) from kaibot.headline; rollback;""", uid=MEMBER, role=None).splitlines()[-1] == "0"
    # the licence guard is not callable at all
    with pytest.raises(AssertionError, match="permission denied"):
        psql("select kaibot.headline_list(true, 50);", uid=MEMBER)
    assert j("select public.kb_me();", uid=MEMBER)["helper"] is False


@needs_pg
def test_the_headline_check_with_a_row_present(db):
    """With an owner_only headline in the table, the member gets an error, the owner gets the row."""
    psql("""delete from kaibot.headline;
      insert into kaibot.headline (dedup_key, symbols, headline, source, licence, created_at)
      values ('alp:inv1', array['AAPL'], 'Owner-only headline', 'alpaca_benzinga', 'owner_only', now());""", role=None)
    try:
        _member()
        with pytest.raises(AssertionError, match="not authorised"):
            psql("select public.kb_headlines(20);", uid=MEMBER)
        assert j("select public.kb_headlines(20);")["items"][0]["headline"] == "Owner-only headline"
    finally:
        psql("delete from kaibot.headline;", role=None)
