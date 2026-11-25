"""#371: scripts/shadow_results.py prints what scripts/shadow_results.sh prints, as the helper, without the service key.

The fixture pair: `rpc_answer.json` is what public.kb_helper_shadow_results() (migration 0047) answered, and
`sh_output.txt` what `scripts/shadow_results.sh` printed (minus psql's two `SET` lines), on the same rows: the
fixture of tests/test_shadow_results_sql.py, on a scratch Postgres with every migration. The SQL test
(tests/test_helper_shadow_results_sql.py) regenerates both from the database and compares them again. No network:
the helper's HTTP calls go to a fake opener."""

from __future__ import annotations

import io
import json
import os
import re
import sys
import urllib.error
from collections.abc import MutableMapping
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import shadow_results as sr  # noqa: E402

from kaibot.common.helper_session import HelperSession  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "shadow_results"
SCRIPT = ROOT / "scripts" / "shadow_results.py"
SENTINEL = "sb_secret_SENTINEL_service_key_must_never_be_read"


def answer() -> dict:
    return json.loads((FIX / "rpc_answer.json").read_text())


# ---------------------------------------------------------------- the format


def test_the_four_tables_match_the_sh_output_byte_for_byte():
    assert sr.render(answer()) == (FIX / "sh_output.txt").read_text()


def test_the_column_names_are_the_sql_files_aliases_in_order():
    sql = (ROOT / "scripts" / "shadow_results.sql").read_text()
    for q, cols in sr.COLUMNS.items():
        names = [c for c, _ in cols]
        assert list(answer()[q][0]) == names, q
        for name in names:
            assert re.search(rf"\b{name}\b", sql), name


def test_no_rows_print_the_header_and_zero_rows_as_psql_does():
    out = sr.table([("finding", False), ("day", False), ("detail", False)], [])
    assert out == (" finding | day | detail \n"
                   "---------+-----+--------\n"
                   "(0 rows)\n")


def test_a_value_with_a_newline_continues_on_the_next_line_as_psql_marks_it():
    """psql 16's output for these rows, captured from a real psql (the error text of a shadow_error may hold one)."""
    out = sr.table([("finding", False), ("day", False), ("detail", False), ("n", True)],
                   [["shadow error", "2026-11-17", "minute: ValueError: bad\nsecond line", "12"],
                    ["x", "2026-11-18", None, None]])
    assert out == ("   finding    |    day     |         detail          | n  \n"
                   "--------------+------------+-------------------------+----\n"
                   " shadow error | 2026-11-17 | minute: ValueError: bad+| 12\n"
                   "              |            | second line             | \n"
                   " x            | 2026-11-18 |                         |   \n"
                   "(2 rows)\n")
    assert sr.table([("a", False), ("b", True), ("c", False)], [["a\nbb", "1", "p\nq"]]) == (
        " a  | b | c \n----+---+---\n a +| 1 | p+\n bb |   | q\n(1 row)\n")


def test_an_answer_whose_columns_drifted_is_refused_not_misprinted():
    a = answer()
    row = a["q4"][0]
    a["q4"][0] = {"day": row["day"], "finding": row["finding"], "detail": row["detail"]}
    with pytest.raises(ValueError, match="are not this script's"):
        sr.render(a)
    with pytest.raises(ValueError, match="unexpected answer"):
        sr.render({"q1": []})


# ---------------------------------------------------------------- the helper path


class Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeSupabase:
    """Answers the sign-in and the RPC; records every request (url, headers, body)."""

    def __init__(self, rpc_status: int = 200, rpc_body: dict | None = None):
        self.calls: list[tuple[str, dict, bytes]] = []
        self.rpc_status, self.rpc_body = rpc_status, rpc_body
        self.answer = json.dumps(answer()).encode()

    def __call__(self, req, timeout):
        self.calls.append((req.full_url, dict(req.header_items()), req.data or b""))
        if "/auth/v1/token" in req.full_url:
            return Resp(json.dumps({"access_token": "tok", "expires_in": 3600}).encode())
        if self.rpc_status != 200:
            raise urllib.error.HTTPError(req.full_url, self.rpc_status, "x", {},
                                         io.BytesIO(json.dumps(self.rpc_body or {}).encode()))
        return Resp(self.answer)


def session(fake) -> HelperSession:
    return HelperSession("https://example.supabase.co", "sb_publishable_x", "helper@example.com", "pw", opener=fake)


def test_it_signs_in_as_the_helper_and_calls_only_the_shadow_function(capsys):
    fake = FakeSupabase()
    assert sr.main([], session=session(fake)) == 0
    assert capsys.readouterr().out == (FIX / "sh_output.txt").read_text()
    urls = [u for u, _, _ in fake.calls]
    assert urls == ["https://example.supabase.co/auth/v1/token?grant_type=password",
                    "https://example.supabase.co/rest/v1/rpc/kb_helper_shadow_results"]
    assert fake.calls[1][2] == b"{}"


def test_a_missing_function_says_the_migration_is_not_applied(capsys):
    """PostgREST answers 404 (PGRST202) for an RPC that is not in its schema cache: migration 0047 not applied."""
    fake = FakeSupabase(404, {"code": "PGRST202", "message": "Could not find the function "
                                                              "public.kb_helper_shadow_results without parameters"})
    assert sr.main([], session=session(fake)) == 3
    out = capsys.readouterr().out
    assert "migration 0047 is not applied yet" in out and "migrations/0047_helper_shadow_results.sql" in out
    assert "|" not in out  # no table, nothing half-printed


def test_another_404_is_not_taken_for_a_missing_migration(capsys):
    assert sr.main([], session=session(FakeSupabase(404, {"message": "no Route matched"}))) == 1
    assert "not applied" not in capsys.readouterr().out


def test_a_refusal_and_other_failures_exit_1_with_the_status(capsys):
    assert sr.main([], session=session(FakeSupabase(403, {"message": "not authorised"}))) == 1
    assert "helper 403" in capsys.readouterr().out
    assert sr.main([], session=session(FakeSupabase(500, {"message": "boom"}))) == 1
    assert "helper 500: boom" in capsys.readouterr().out


def test_without_the_helpers_file_it_stops_before_any_call(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KAIBOT_HELPER_ENV_FILE", str(tmp_path / "absent.env"))
    assert sr.main([]) == 2
    assert "docs/ops/helper_role.md" in capsys.readouterr().out


# ---------------------------------------------------------------- the service key


class RecordingEnviron(MutableMapping):
    """os.environ's stand-in that records every name looked up."""

    def __init__(self, data):
        self.data, self.read = dict(data), []

    def __getitem__(self, k):
        self.read.append(k)
        return self.data[k]

    def __setitem__(self, k, v):
        self.data[k] = v

    def __delitem__(self, k):
        del self.data[k]

    def __iter__(self):
        self.read.append("<iterated>")
        return iter(self.data)

    def __len__(self):
        return len(self.data)

    def get(self, k, default=None):
        self.read.append(k)
        return self.data.get(k, default)

    def __contains__(self, k):
        self.read.append(k)
        return k in self.data


def test_the_script_never_reads_the_service_key(tmp_path, monkeypatch, capsys):
    """A full run (helper file, sign-in, RPC, print) with the service key in the environment and in the engine's
    .env next to the helpers' file: the key's name is never looked up, the environment is never walked, the .env is
    never opened, and the key's value is in no request."""
    secrets = tmp_path / "kaibot-secrets"
    secrets.mkdir()
    (secrets / ".env").write_text(f"SUPABASE_SERVICE_KEY={SENTINEL}\n")
    helper = secrets / "helper.env"
    helper.write_text("SUPABASE_PUBLISHABLE_KEY=sb_publishable_x\nSUPABASE_HELPER_EMAIL=h@example.com\n"
                      "SUPABASE_HELPER_PASSWORD=pw\nSUPABASE_URL=https://example.supabase.co\n")
    env = RecordingEnviron({**os.environ, "SUPABASE_SERVICE_KEY": SENTINEL, "SUPABASE_SERVICE_ROLE_KEY": SENTINEL,
                            "KAIBOT_HELPER_ENV_FILE": str(helper)})
    expected = (FIX / "sh_output.txt").read_text()
    fake = FakeSupabase()
    monkeypatch.setattr(os, "environ", env)
    opened: list[str] = []
    real_read_text = Path.read_text

    def spy_read_text(self, *a, **kw):
        opened.append(str(self))
        return real_read_text(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", spy_read_text)
    monkeypatch.setattr(sr.HelperSession, "from_env", classmethod(
        lambda cls, e, url, **kw: HelperSession(e.get("SUPABASE_URL") or url, e["SUPABASE_PUBLISHABLE_KEY"],
                                                e["SUPABASE_HELPER_EMAIL"], e["SUPABASE_HELPER_PASSWORD"],
                                                opener=fake)))
    assert sr.main([]) == 0
    assert capsys.readouterr().out == expected
    assert not any("SERVICE" in k for k in env.read) and "<iterated>" not in env.read
    assert opened == [str(helper)]
    assert fake.calls and all(SENTINEL not in (u + json.dumps(h) + d.decode()) for u, h, d in fake.calls)


def test_a_helpers_file_that_holds_the_service_key_is_refused_before_any_call(tmp_path, monkeypatch):
    helper = tmp_path / "helper.env"
    helper.write_text("SUPABASE_PUBLISHABLE_KEY=sb_publishable_x\nSUPABASE_HELPER_EMAIL=h@example.com\n"
                      f"SUPABASE_HELPER_PASSWORD=pw\nSUPABASE_SERVICE_KEY={SENTINEL}\n")
    monkeypatch.setenv("KAIBOT_HELPER_ENV_FILE", str(helper))
    with pytest.raises(SystemExit, match="holds a Supabase service key"):
        sr.main([])


def test_the_source_has_no_path_to_the_service_key():
    """Static: no service-key name, no os.environ, no engine import (its config loads the engine's .env), no
    DATABASE_URL, no direct table call, and the only RPC is the shadow function."""
    src = SCRIPT.read_text()
    code = re.sub(r'"""(.*?)"""', "", src, flags=re.S)  # the docstrings may say what it does not do
    code = re.sub(r"#[^\n]*", "", code)
    for bad in ("SERVICE", "service_key", "os.environ", "getenv", "load_env_file", "kaibot.engine", "DATABASE_URL",
                "psycopg", "subprocess", "/rest/v1/", ".env\"", "kaibot-secrets/.env"):
        assert bad not in code, bad
    assert re.findall(r"\.rpc\(\s*([A-Za-z_\"']+)", code) == ["FUNCTION"]
    assert 'FUNCTION = "kb_helper_shadow_results"' in code
    assert "import os" not in code and "add_argument" not in code  # no flag at all: nothing for a job to pass


def test_the_runner_runs_it_with_no_key_no_flag_and_no_writes():
    """ops/runner/allowed.yaml: no --env (the runner would pass the engine's .env), no flag, no writes; the queued
    job posts to #371."""
    import research_runner as rr

    spec = rr.load_allowed().specs["scripts/shadow_results.py"]
    assert (spec.env, spec.flags, spec.writes, spec.sec) == (False, {}, (), False)
    jobs = [j for j in rr.load_queue() if j.get("script") == "scripts/shadow_results.py"]
    assert {"id": "shadow-results-2026-11", "script": "scripts/shadow_results.py", "args": [],
            "posts_to_issue": 371} in jobs
