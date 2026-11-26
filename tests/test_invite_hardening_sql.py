"""Migration 0048: invite hardening (#721, M19 ticket 8.3 of the second engine audit).

a) kb_invite_check answers {"ok": ...} and nothing else, and ignores the email (no email oracle);
b) kb_invite_create gives one generic refusal for an email that has an account;
c) only the owner invites (Product, 25 Nov 2026): a member cannot create, list or revoke invites;
d) an invite is redeemed by its code (the account the admit trigger let in with it), never by email alone, and never
   once it has expired.

The file checks run everywhere; the SQL checks need the scratch Postgres of tests/pg_scratch.py (KAIBOT_TEST_PG)."""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

import pytest
from test_invites_sql import (  # the 0045 tests' helpers: same users, same reset
    HELPERX,
    MEMBER,
    NEWBIE,
    OWNER,
    PG,
    STRANGER,
    _member,
    db,  # noqa: F401  (the fixture)
    j,
    needs_pg,
    psql,
    signup,
)

ROOT = Path(__file__).resolve().parents[1]
M48 = ROOT / "migrations" / "0048_invite_hardening.sql"


def _sql() -> str:
    return "\n".join(ln for ln in M48.read_text().splitlines() if not ln.lstrip().startswith("--")).lower()


# ---------------------------------------------------------------- the file


def test_0048_is_additive_and_definer_with_an_empty_search_path():
    s = _sql()
    assert not re.search(r"\b(drop|truncate|alter function|delete from)\b", s)
    # the admit trigger holds the invite, the redeem trigger marks it used, the apply-time update holds pending ones
    assert re.findall(r"\bupdate\s+(kaibot\.\w+)", s) == ["kaibot.invite", "kaibot.invite", "kaibot.invite"]
    assert "add column if not exists admitted_id uuid" in s
    assert "create policy" not in s and not re.search(r"grant [a-z, ]+ on kaibot\.", s)
    for body in re.split(r"create or replace function ", s)[1:]:
        assert "set search_path = ''" in body.split("as $$")[0], body[:60]
        assert "security definer" in body.split("as $$")[0], body[:60]
    assert s.count(" to anon") == 1  # only kb_invite_check, as in 0045 and 0046
    assert s.strip().startswith("begin;") and s.strip().endswith("commit;")


def test_0048_names_every_function_it_makes():
    made = set(re.findall(r"create or replace function (\w+\.\w+)\(", _sql()))
    assert made == {"kaibot.can_invite", "public.kb_invite_create", "public.kb_invite_check", "kaibot.invite_admit",
                    "kaibot.invite_redeem"}


def test_0048_says_no_reason_and_no_account():
    s = _sql()
    check = s.split("create or replace function public.kb_invite_check(")[1].split("$$;")[0]
    assert "'reason'" not in check and "email" not in check.split("as $$")[1]
    assert "has an account" not in s
    assert "profile" not in s.split("create or replace function kaibot.can_invite(")[1].split("$$;")[0]


def test_0048_header_claims_no_status():
    head = [ln for ln in M48.read_text().splitlines() if ln.startswith("--")]
    assert not any(re.match(r"^--\s*(NOT )?APPLIED\b", ln) for ln in head)


# ---------------------------------------------------------------- a) the check answers ok only


@needs_pg
def test_the_check_answers_ok_and_nothing_else(db):  # noqa: F811
    # one open invite at a time (an expired or revoked one frees its seat): the other SQL tests' up to three accounts
    # and the member below must fit in the cap of 5
    expired = j("select public.kb_invite_create('expired@example.com');")["code"]
    psql(f"update kaibot.invite set expires_at = now() - interval '1 second' where code = '{expired}';", role=None)
    revoked = j("select public.kb_invite_create('revoked@example.com');")
    rid = next(i["id"] for i in j("select public.kb_invite_list();")["invites"] if i["email"] == "revoked@example.com")
    j(f"select public.kb_invite_revoke({rid});")
    open_ = j("select public.kb_invite_create('open@example.com');")["code"]
    cases = {
        f"'{open_}'": True,
        f"'{open_.replace('-', '').lower()}'": True,  # typed in any case, without dashes
        f"'{open_}', 'open@example.com'": True,
        f"'{open_}', 'someone-else@example.com'": True,  # the email is ignored: no oracle for the address
        f"'{expired}'": False, f"'{expired}', 'expired@example.com'": False,
        f"'{revoked['code']}'": False,
        "'0000-0000-0000-0000'": False, "'0000-0000-0000-0000', 'open@example.com'": False,
        "null": False, "''": False, "'not a code'": False,
    }
    for args, ok in cases.items():
        for role, uid in (("anon", ""), ("authenticated", OWNER)):
            assert j(f"select public.kb_invite_check({args});", role=role, uid=uid) == {"ok": ok}, (args, role)
    _member()  # a used invite
    used = psql("select code from kaibot.invite where email = 'member@example.com';", role=None)
    assert j(f"select public.kb_invite_check('{used}');", role="anon", uid="") == {"ok": False}


# ---------------------------------------------------------------- b) one generic refusal


@needs_pg
def test_an_email_with_an_account_gets_one_generic_refusal(db):  # noqa: F811
    _member()
    psql(f"insert into auth.users (id, email) values ('{STRANGER}', 'Stranger@Example.com');", role=None)
    for email in ("member@example.com", "MEMBER@example.com", "stranger@example.com"):
        with pytest.raises(AssertionError) as e:
            psql(f"select public.kb_invite_create('{email}');", uid=OWNER)
        msg = e.value.args[0]
        assert "an invite can't be made for this email" in msg and "account" not in msg, msg
    # an email without an account still gets its invite (the stranger's seat back first: the cap of 5)
    psql(f"delete from auth.users where id = '{STRANGER}';", role=None)
    assert j("select public.kb_invite_create('new-person@example.com');")["reused"] is False


# ---------------------------------------------------------------- c) only the owner invites


@needs_pg
def test_only_the_owner_invites(db):  # noqa: F811
    _member()
    assert j("select public.kb_me();", uid=MEMBER) == {"owner": False, "member": True, "helper": False,
                                                        "name": "Mia", "can_invite": False}
    assert j("select public.kb_me();")["can_invite"] is True
    for call in ("kb_invite_create('friend@example.com')", "kb_invite_list()", "kb_invite_revoke(1)"):
        with pytest.raises(AssertionError, match="not authorised"):
            psql(f"select public.{call};", uid=MEMBER)
    assert psql("select count(*) from kaibot.invite where email = 'friend@example.com';", role=None) == "0"
    # a helper with a profile row never invites, the owner does
    psql(f"""insert into auth.users (id, email) values ('{HELPERX}', 'helper-x@example.com');
      insert into kaibot.helper (user_id, name) values ('{HELPERX}', 'test helper');
      insert into kaibot.profile (user_id, name) values ('{HELPERX}', 'helper');""", role=None)
    try:
        with pytest.raises(AssertionError, match="not authorised"):
            psql("select public.kb_invite_create('friend@example.com');", uid=HELPERX)
    finally:
        psql(f"delete from kaibot.helper where user_id = '{HELPERX}'; delete from auth.users where id = '{HELPERX}';",
             role=None)
    assert j("select public.kb_invite_create('friend@example.com');")["email"] == "friend@example.com"


# ---------------------------------------------------------------- d) redeem by code, never when expired


def _used(code: str) -> str:
    return psql(f"select coalesce(used_by::text, '-') from kaibot.invite where code = '{code}';", role=None)


@needs_pg
def test_the_admit_trigger_holds_the_invite_and_its_confirmation_redeems_it(db):  # noqa: F811
    code = j("select public.kb_invite_create('nora@example.com');")["code"]
    signup(NEWBIE, "nora@example.com", "Nora", code=code)
    assert psql(f"select admitted_id from kaibot.invite where code = '{code}';", role=None) == NEWBIE
    assert _used(code) == "-"
    psql(f"update auth.users set email_confirmed_at = now() where id = '{NEWBIE}';", role=None, direct=False)
    assert _used(code) == NEWBIE
    assert j("select public.kb_me();", uid=NEWBIE)["member"] is True


@needs_pg
def test_an_account_with_the_invited_email_but_not_its_code_redeems_nothing(db):  # noqa: F811
    """The audit's finding: 0045/0046 picked the invite by email at confirmation. An account that reaches the invited
    address another way (made in SQL by an admin; an email changed to it) is not the invitee."""
    code = j("select public.kb_invite_create('target@example.com');")["code"]
    # made without the admit trigger (kaibot.allow_direct_signup on), confirmed at once
    psql(f"insert into auth.users (id, email, email_confirmed_at) values ('{STRANGER}', 'target@example.com', now());",
         role=None)
    assert _used(code) == "-"
    assert psql("select count(*) from kaibot.profile;", role=None) == "0"
    assert psql("select public.kb_profile_repair();", uid=STRANGER) == "f"
    psql(f"delete from auth.users where id = '{STRANGER}';", role=None)
    # an account whose email is changed to the invited one, then confirmed
    psql(f"insert into auth.users (id, email) values ('{STRANGER}', 'someone@example.com');", role=None)
    psql(f"update auth.users set email = 'target@example.com', email_confirmed_at = now() where id = '{STRANGER}';",
         role=None, direct=False)
    assert _used(code) == "-"
    assert j("select public.kb_me();", uid=STRANGER)["member"] is False
    assert j(f"select public.kb_invite_check('{code}');", role="anon", uid="") == {"ok": True}


@needs_pg
def test_an_invite_that_expires_before_the_click_is_refused(db):  # noqa: F811
    code = j("select public.kb_invite_create('slow@example.com');")["code"]
    signup(NEWBIE, "slow@example.com", "Sol", code=code)
    psql(f"update kaibot.invite set expires_at = now() - interval '1 second' where code = '{code}';", role=None)
    psql(f"update auth.users set email_confirmed_at = now() where id = '{NEWBIE}';", role=None, direct=False)
    assert _used(code) == "-"
    assert psql("select count(*) from kaibot.profile;", role=None) == "0"
    assert psql("select public.kb_profile_repair();", uid=NEWBIE) == "f"
    assert j("select public.kb_me();", uid=NEWBIE)["member"] is False


@needs_pg
def test_reapplying_holds_a_pending_signup_made_before_0048(db):  # noqa: F811
    """A sign-up admitted before 0048 has no admitted_id. The file's one update holds its open invite for it, so its
    link still works; a confirmed account, a revoked or an expired invite is left alone. Re-running the whole file is
    safe (it is one idempotent transaction)."""
    pending = j("select public.kb_invite_create('pending@example.com');")["code"]
    signup(NEWBIE, "pending@example.com", "Pia", code=pending)
    stale = j("select public.kb_invite_create('stale@example.com');")["code"]
    psql(f"""update kaibot.invite set admitted_id = null where code = '{pending}';
      insert into auth.users (id, email) values ('{STRANGER}', 'stale@example.com');
      update kaibot.invite set expires_at = now() - interval '1 second' where code = '{stale}';""", role=None)
    r = subprocess.run(["psql", *shlex.split(PG), "-q", "-v", "ON_ERROR_STOP=1", "-f", str(M48)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert psql(f"select admitted_id from kaibot.invite where code = '{pending}';", role=None) == NEWBIE
    assert psql(f"select coalesce(admitted_id::text, '-') from kaibot.invite where code = '{stale}';",
                role=None) == "-"
    psql(f"update auth.users set email_confirmed_at = now() where id = '{NEWBIE}';", role=None, direct=False)
    assert _used(pending) == NEWBIE
    assert j("select public.kb_me();", uid=NEWBIE)["name"] == "Pia"
    assert j("select public.kb_invite_check('0000-0000-0000-0000');", role="anon", uid="") == {"ok": False}
