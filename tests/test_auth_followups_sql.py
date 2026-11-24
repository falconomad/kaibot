"""Migration 0046: the auth follow-ups of #635 (the #634 security review).

1) kb_claim_owner claims only when no owner row exists and exactly one account exists (the caller's);
2) kb_invite_revoke: the inviter, or the owner for any invite, revokes an open invite; a revoked code is refused by
   kb_invite_check, the admit trigger and the redeem trigger, holds no seat and is not handed back by kb_invite_create;
3) the redeem trigger keeps the invite used when the profile fails, and kb_profile_repair makes the missing profile
   for a confirmed account with an invite it redeemed (and for nobody else).

The file checks run everywhere; the SQL checks need the scratch Postgres of tests/pg_scratch.py (KAIBOT_TEST_PG)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from test_invites_sql import (  # the 0045 tests' helpers: same users, same reset
    MEMBER,
    NEWBIE,
    OWNER,
    STRANGER,
    _member,
    db,  # noqa: F401  (the fixture)
    j,
    needs_pg,
    psql,
    signup,
)

ROOT = Path(__file__).resolve().parents[1]
M46 = ROOT / "migrations" / "0046_auth_followups.sql"
HELPERX = "00000000-0000-0000-0000-0000000000a4"


def _sql() -> str:
    return "\n".join(ln for ln in M46.read_text().splitlines() if not ln.lstrip().startswith("--")).lower()


# ---------------------------------------------------------------- the file


def test_0046_is_additive_and_definer_with_an_empty_search_path():
    s = _sql()
    assert not re.search(r"\b(drop|truncate|alter function|delete from)\b", s)
    assert re.findall(r"\bupdate\s+(kaibot\.\w+)", s) == ["kaibot.invite", "kaibot.invite"]
    assert "create policy" not in s and not re.search(r"grant [a-z, ]+ on kaibot\.", s)
    for body in re.split(r"create or replace function ", s)[1:]:
        assert "set search_path = ''" in body.split("as $$")[0], body[:60]
    assert s.count(" to anon") == 1  # only kb_invite_check, as in 0045
    for fn in ("kb_invite_revoke", "kb_invite_list", "kb_invite_create"):
        body = s.split(f"create or replace function public.{fn}(")[1].split("end $$;")[0]
        assert "if not kaibot.can_invite() then raise exception 'not authorised'" in body, fn


def test_0046_names_every_function_it_makes():
    made = set(re.findall(r"create or replace function (\w+\.\w+)\(", _sql()))
    assert made == {"public.kb_claim_owner", "kaibot.invite_seats_taken", "public.kb_invite_create",
                    "public.kb_invite_check", "public.kb_invite_list", "public.kb_invite_revoke",
                    "kaibot.profile_name", "public.kb_profile_repair", "kaibot.invite_admit", "kaibot.invite_redeem"}


def test_0046_header_claims_no_status():
    head = [ln for ln in M46.read_text().splitlines() if ln.startswith("--")]
    assert not any(re.match(r"^--\s*(NOT )?APPLIED\b", ln) for ln in head)


# ---------------------------------------------------------------- 1) the ownership claim


@needs_pg
def test_no_claim_while_more_than_one_account_exists(db):  # noqa: F811
    """The #634 finding: with the owner row deleted, an invited member (or any account) must not take the dashboard."""
    _member()
    psql("delete from kaibot.owner;", role=None)
    try:
        assert int(psql("select count(*) from auth.users;", role=None)) > 1
        for uid in (MEMBER, OWNER):
            assert psql("select public.kb_claim_owner();", uid=uid) == "f"
        assert psql("select count(*) from kaibot.owner;", role=None) == "0"
        assert psql("select public.kb_claim_owner();", uid="", role="authenticated") == "f"  # no session
    finally:
        psql(f"insert into kaibot.owner (user_id) values ('{OWNER}') on conflict do nothing;", role=None)
    # with the owner row there: the owner is the owner, the member is not, as before
    assert psql("select public.kb_claim_owner();", uid=OWNER) == "t"
    assert psql("select public.kb_claim_owner();", uid=MEMBER) == "f"


@needs_pg
def test_the_only_account_of_a_fresh_project_claims(db):  # noqa: F811
    """A fresh project: one account and no owner row. Run in one transaction, rolled back."""
    out = psql(f"""begin;
      delete from kaibot.owner; delete from auth.users;
      insert into auth.users (id, email) values ('{NEWBIE}', 'first@example.com');
      set local test.uid = '{STRANGER}'; set local role authenticated;
      select public.kb_claim_owner();
      reset role; set local test.uid = '{NEWBIE}'; set local role authenticated;
      select public.kb_claim_owner();
      select public.kb_claim_owner();
      reset role;
      select string_agg(user_id::text, ',') from kaibot.owner;
      rollback;""", role=None)
    # someone who is not that one account gets false; the account itself claims, and stays the owner
    assert out.splitlines()[-4:] == ["f", "t", "t", NEWBIE]
    assert psql("select string_agg(user_id::text, ',') from kaibot.owner;", role=None) == OWNER


@needs_pg
def test_a_member_left_alone_cannot_claim(db):  # noqa: F811
    """Second-key review: the owner's account deleted (its owner row goes with it), one invited member left."""
    _member()
    out = psql(f"""begin;
      delete from kaibot.owner; delete from auth.users where id <> '{MEMBER}';
      select count(*) from auth.users;
      set local test.uid = '{MEMBER}'; set local role authenticated;
      select public.kb_claim_owner();
      reset role;
      select count(*) from kaibot.owner;
      rollback;""", role=None)
    # alone, with a profile: false. (The inviter's deletion took the invite rows with it, on delete cascade: the
    # profile is what marks a member then. An account with neither is a fresh project's first account.)
    assert out.splitlines()[-3:] == ["1", "f", "0"]


@needs_pg
def test_a_lone_helper_still_cannot_claim(db):  # noqa: F811
    with pytest.raises(AssertionError, match="a helper cannot be the owner"):
        psql(f"""begin;
          delete from kaibot.owner; delete from auth.users;
          insert into auth.users (id, email) values ('{HELPERX}', 'helper-x@example.com');
          insert into kaibot.helper (user_id, name) values ('{HELPERX}', 'lone helper');
          set local test.uid = '{HELPERX}'; set local role authenticated;
          select public.kb_claim_owner();
          rollback;""", role=None)
    assert psql("select string_agg(user_id::text, ',') from kaibot.owner;", role=None) == OWNER


# ---------------------------------------------------------------- 2) revoking invites


@needs_pg
def test_the_inviter_revokes_and_the_code_is_refused_everywhere(db):  # noqa: F811
    inv = j("select public.kb_invite_create('gone@example.com');")
    row = j("select public.kb_invite_list();")["invites"][0]
    assert row["status"] == "open" and row["can_revoke"] is True
    taken = j("select public.kb_invite_list();")["taken"]
    out = j(f"select public.kb_invite_revoke({row['id']});")
    assert out["status"] == "revoked" and out["email"] == "gone@example.com"
    # the check, the list, the seats
    assert j(f"select public.kb_invite_check('{inv['code']}');", role="anon", uid="") == {"ok": False,
                                                                                       "reason": "revoked"}
    row = j("select public.kb_invite_list();")["invites"][0]
    assert row["status"] == "revoked" and row["code"] is None and row["can_revoke"] is False
    assert j("select public.kb_invite_list();")["taken"] == taken - 1
    # the admit trigger: the revoked code admits nobody
    with pytest.raises(AssertionError, match="invite only"):
        signup(NEWBIE, "gone@example.com", "Gina", code=inv["code"])
    # revoking twice is refused; a new invite for the email gets a new code
    with pytest.raises(AssertionError, match="no open invite of yours"):
        psql(f"select public.kb_invite_revoke({row['id']});", uid=OWNER)
    fresh = j("select public.kb_invite_create('gone@example.com');")
    assert fresh["reused"] is False and fresh["code"] != inv["code"]
    signup(NEWBIE, "gone@example.com", "Gina", confirm=True, code=fresh["code"])
    assert j("select public.kb_me();", uid=NEWBIE)["member"] is True


@needs_pg
def test_a_revoke_between_registration_and_confirmation_makes_no_member(db):  # noqa: F811
    """The redeem trigger: an account admitted before the revoke and confirmed after it gets no profile."""
    inv = j("select public.kb_invite_create('late-revoke@example.com');")
    signup(NEWBIE, "late-revoke@example.com", "Lars", code=inv["code"])
    iid = j("select public.kb_invite_list();")["invites"][0]["id"]
    j(f"select public.kb_invite_revoke({iid});")
    psql(f"update auth.users set email_confirmed_at = now() where id = '{NEWBIE}';", role=None, direct=False)
    assert psql("select count(*) from kaibot.profile;", role=None) == "0"
    assert psql(f"select used_at is null from kaibot.invite where id = {iid};", role=None) == "t"
    assert psql("select public.kb_profile_repair();", uid=NEWBIE) == "f"
    assert j("select public.kb_me();", uid=NEWBIE)["member"] is False


@needs_pg
def test_who_may_revoke(db):  # noqa: F811
    """At most one open invite at a time: the other SQL tests' up to three accounts and the member fit the cap of 5."""
    _member()
    by_owner = j("select public.kb_invite_create('by-owner@example.com');")
    ids = {i["email"]: i["id"] for i in j("select public.kb_invite_list();")["invites"]}
    # a member sees and revokes only their own: the owner's invite is refused with the same words as an unknown id
    assert j("select public.kb_invite_list();", uid=MEMBER)["invites"] == []
    for bad in (ids["by-owner@example.com"], 999999, "null"):
        with pytest.raises(AssertionError, match="no open invite of yours with that id"):
            psql(f"select public.kb_invite_revoke({bad});", uid=MEMBER)
    assert j(f"select public.kb_invite_check('{by_owner['code']}');", role="anon", uid="")["ok"] is True
    # a used invite cannot be revoked, not even by the owner; an expired one neither
    with pytest.raises(AssertionError, match="no open invite"):
        psql(f"select public.kb_invite_revoke({ids['member@example.com']});", uid=OWNER)
    psql(f"update kaibot.invite set expires_at = now() - interval '1 second' "
         f"where id = {ids['by-owner@example.com']};", role=None)
    with pytest.raises(AssertionError, match="no open invite"):
        psql(f"select public.kb_invite_revoke({ids['by-owner@example.com']});", uid=OWNER)
    # the owner revokes a member's invite; the member revokes their own
    j("select public.kb_invite_create('by-mia@example.com');", uid=MEMBER)
    mine = j("select public.kb_invite_list();", uid=MEMBER)["invites"]
    assert [(i["email"], i["can_revoke"]) for i in mine] == [("by-mia@example.com", True)]
    assert j(f"select public.kb_invite_revoke({mine[0]['id']});")["status"] == "revoked"
    j("select public.kb_invite_create('by-mia-2@example.com');", uid=MEMBER)
    again = j("select public.kb_invite_list();", uid=MEMBER)["invites"][0]
    assert j(f"select public.kb_invite_revoke({again['id']});", uid=MEMBER)["status"] == "revoked"
    # anon and a signed-in account with no profile: refused
    with pytest.raises(AssertionError, match="permission denied"):
        psql("select public.kb_invite_revoke(1);", role="anon")
    psql(f"insert into auth.users (id, email) values ('{STRANGER}', 'stranger@example.com');", role=None)
    with pytest.raises(AssertionError, match="not authorised"):
        psql("select public.kb_invite_revoke(1);", uid=STRANGER)
    assert psql("select has_function_privilege('anon', 'public.kb_profile_repair()', 'execute');", role=None) == "f"


# ---------------------------------------------------------------- 3) the missing profile


@needs_pg
def test_a_failed_profile_keeps_the_invite_used_and_is_repaired_at_sign_in(db):  # noqa: F811
    inv = j("select public.kb_invite_create('broken@example.com');")
    signup(NEWBIE, "broken@example.com", "Bea", code=inv["code"])
    # make the profile insert fail inside the trigger (a check the trigger does not expect), then confirm
    psql("alter table kaibot.profile add constraint test_no_profile check (false) not valid;", role=None)
    try:
        psql(f"update auth.users set email_confirmed_at = now() where id = '{NEWBIE}';", role=None, direct=False)
    finally:
        psql("alter table kaibot.profile drop constraint test_no_profile;", role=None)
    assert psql("select count(*) from kaibot.profile;", role=None) == "0"
    assert psql(f"select used_by from kaibot.invite where code = '{inv['code']}';", role=None) == NEWBIE
    assert j("select public.kb_me();", uid=NEWBIE)["member"] is False  # "Not your dashboard" until repaired
    assert psql("select public.kb_profile_repair();", uid=NEWBIE) == "t"
    assert j("select public.kb_me();", uid=NEWBIE)["name"] == "Bea"
    assert psql("select public.kb_profile_repair();", uid=NEWBIE) == "t"  # idempotent
    assert psql("select count(*) from kaibot.profile;", role=None) == "1"


@needs_pg
def test_nobody_else_gets_a_profile_by_repair(db):  # noqa: F811
    # an unconfirmed account with a used invite (an admin's update), an account with no invite, the owner, a helper
    inv = j("select public.kb_invite_create('unconfirmed@example.com');")
    signup(NEWBIE, "unconfirmed@example.com", "Una", code=inv["code"])
    psql(f"update kaibot.invite set used_at = now(), used_by = '{NEWBIE}' where code = '{inv['code']}';", role=None)
    assert psql("select public.kb_profile_repair();", uid=NEWBIE) == "f"
    psql(f"insert into auth.users (id, email, email_confirmed_at) "
         f"values ('{STRANGER}', 'stranger@example.com', now());", role=None)
    assert psql("select public.kb_profile_repair();", uid=STRANGER) == "f"
    assert psql("select public.kb_profile_repair();", uid=OWNER) == "f"
    psql(f"""insert into auth.users (id, email, email_confirmed_at) values ('{HELPERX}', 'helper-x@example.com', now());
      insert into kaibot.helper (user_id, name) values ('{HELPERX}', 'test helper');
      update kaibot.invite set used_by = '{HELPERX}' where code = '{inv['code']}';""", role=None)
    try:
        assert psql("select public.kb_profile_repair();", uid=HELPERX) == "f"
    finally:
        psql(f"delete from kaibot.helper where user_id = '{HELPERX}';", role=None)
    assert psql("select public.kb_profile_repair();", uid="") == "f"
    with pytest.raises(AssertionError, match="permission denied"):
        psql("select public.kb_profile_repair();", role="anon")
    assert psql("select count(*) from kaibot.profile;", role=None) == "0"


@needs_pg
def test_the_profile_name_always_fits(db):  # noqa: F811
    rows = psql(r"""select kaibot.profile_name('{"name": "  Nora  "}', 'n@example.com')
      union all select kaibot.profile_name('{"name": "\u0001\u0002"}', 'nina@example.com')
      union all select kaibot.profile_name(null, '@example.com')
      union all select length(kaibot.profile_name(jsonb_build_object('name', repeat('x', 59) || ' y'), 'a@b.co'))::text
      union all select kaibot.profile_name('[1]', 'arr@example.com');""", role=None).splitlines()
    assert rows == ["Nora", "nina", "Member", "59", "arr"]
