"""M10 ticket 1.26 (issue #451): the helpers' client (common/helper_session.py), the relay and the notice publisher
on the helper path, and scripts/helper_check.py, against a fake Supabase. No network."""

from __future__ import annotations

import importlib.util
import io
import json
import urllib.error
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from kaibot.common.helper_session import HelperError, HelperSession, helper_env

ROOT = Path(__file__).resolve().parents[2]
URL, PUB = "https://example.supabase.co", "sb_publishable_abc"
SECRETS = ("pw-123-secret", "tok-1", "tok-2")


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Resp:
    def __init__(self, body, status=200):
        self.body, self.status = json.dumps(body).encode() if body is not None else b"", status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        return self.body


class FakeSupabase:
    """Auth's password grant, PostgREST's rpc and tables, Realtime's broadcast: what a helper may and may not do."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict, dict]] = []
        self.tokens = 0
        self.expire_next = False
        self.sign_in_ok = True

    def __call__(self, req, timeout):
        path = req.full_url[len(URL):]
        h = {k.lower(): v for k, v in req.header_items()}
        body = json.loads(req.data) if req.data else None
        self.calls.append((req.get_method(), path, h, body))
        assert h["apikey"] == PUB
        if path.startswith("/auth/v1/token"):
            if not self.sign_in_ok or body != {"email": "helper@example.com", "password": SECRETS[0]}:
                raise self.err(req, 400, {"error_description": "Invalid login credentials"})
            self.tokens += 1
            return Resp({"access_token": f"tok-{self.tokens}", "expires_in": 3600})
        if h.get("authorization") != f"Bearer tok-{self.tokens}" or self.expire_next:
            self.expire_next = False
            raise self.err(req, 401, {"message": "JWT expired"})
        if path == "/rest/v1/rpc/kb_helper_whoami":
            return Resp({"helper": True})
        if path == "/rest/v1/rpc/kb_helper_heartbeat":
            if body["p_process"] != "price_stream":
                raise self.err(req, 400, {"message": "not a helper process: engine"})
            return Resp(None, 204)
        if path == "/rest/v1/rpc/kb_helper_publish_notices":
            return Resp({"written": len(body["p_rows"]), "withdrawn": ["old"]})
        if path == "/realtime/v1/api/broadcast":
            return Resp(None, 202)
        raise self.err(req, 403, {"message": "permission denied for table"})

    @staticmethod
    def err(req, code, body):
        return urllib.error.HTTPError(req.full_url, code, "x", {}, io.BytesIO(json.dumps(body).encode()))


def session(fake, clock=None):
    return HelperSession(URL, PUB, "helper@example.com", SECRETS[0], opener=fake, **({"clock": clock} if clock else {}))


def test_the_helper_signs_in_with_the_publishable_key_and_calls_only_helper_functions():
    fake = FakeSupabase()
    s = session(fake)
    assert s.rpc("kb_helper_whoami", {}) == {"helper": True}
    s.broadcast("kb:prices", "prices", {"prices": {"A": 1.0}})
    assert [c[1].split("?")[0] for c in fake.calls] == ["/auth/v1/token", "/rest/v1/rpc/kb_helper_whoami",
                                                         "/realtime/v1/api/broadcast"]
    assert fake.calls[2][3] == {"messages": [{"topic": "kb:prices", "event": "prices", "private": True,
                                              "payload": {"prices": {"A": 1.0}}}]}
    with pytest.raises(ValueError, match="not a helper function"):
        s.rpc("kb_command", {})
    with pytest.raises(ValueError, match="secret key"):
        HelperSession(URL, "sb_secret_xyz", "a", "b")


def test_the_sign_in_is_repeated_before_the_token_ends_and_once_after_a_401():
    fake, t = FakeSupabase(), [0.0]
    s = session(fake, clock=lambda: t[0])
    s.rpc("kb_helper_whoami", {})
    t[0] = 3600 - 119  # inside the last two minutes
    s.rpc("kb_helper_whoami", {})
    assert fake.tokens == 2
    fake.expire_next = True
    assert s.rpc("kb_helper_whoami", {}) == {"helper": True} and fake.tokens == 3


def test_an_error_names_the_status_and_never_a_secret():
    fake = FakeSupabase()
    s = session(fake)
    with pytest.raises(HelperError) as e:
        s.rpc("kb_helper_heartbeat", {"p_process": "engine", "p_detail": {}})
    assert e.value.status == 400 and "not a helper process" in str(e.value)
    fake.sign_in_ok = False
    with pytest.raises(HelperError) as e:
        session(fake).sign_in()
    assert e.value.status == 400 and not any(x in str(e.value) for x in (*SECRETS, PUB))


def test_the_helpers_env_file_is_its_own_file_and_may_not_hold_a_service_key(tmp_path, monkeypatch):
    monkeypatch.delenv("KAIBOT_HELPER_ENV_FILE", raising=False)
    assert helper_env(str(tmp_path / "none.env")) is None
    f = tmp_path / "helper.env"
    f.write_text(f"SUPABASE_PUBLISHABLE_KEY={PUB}\nSUPABASE_HELPER_EMAIL=helper@example.com\n"
                 f"SUPABASE_HELPER_PASSWORD='{SECRETS[0]}'\n# a comment\n")
    env = helper_env(str(f))
    assert env["SUPABASE_HELPER_PASSWORD"] == SECRETS[0]
    import os
    assert "SUPABASE_HELPER_PASSWORD" not in os.environ  # read into a dict, never into the environment
    monkeypatch.setenv("KAIBOT_HELPER_ENV_FILE", str(f))
    assert helper_env() == env
    f.write_text(f.read_text() + "SUPABASE_SERVICE_KEY=sb_secret_x\n")
    with pytest.raises(SystemExit, match="service key"):
        helper_env(str(f))
    f.write_text(f"SUPABASE_PUBLISHABLE_KEY={PUB}\n")
    with pytest.raises(SystemExit, match="missing SUPABASE_HELPER_EMAIL"):
        helper_env(str(f))


def _helper_file(tmp_path, alpaca=True):
    f = tmp_path / "helper.env"
    f.write_text(f"SUPABASE_URL={URL}\nSUPABASE_PUBLISHABLE_KEY={PUB}\nSUPABASE_HELPER_EMAIL=helper@example.com\n"
                 f"SUPABASE_HELPER_PASSWORD={SECRETS[0]}\n"
                 + ("ALPACA_API_KEY=PKFAKE\nALPACA_SECRET=fakesecret\n" if alpaca else ""))
    return f


def test_the_relay_on_the_helper_path_holds_no_service_key(tmp_path, monkeypatch):
    ps = load("price_stream")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "sb_secret_must_not_be_used")
    engine_env = tmp_path / ".env"
    engine_env.write_text("SUPABASE_SERVICE_KEY=sb_secret_in_the_engine_file\nALPACA_API_KEY=X\nALPACA_SECRET=Y\n")
    http, key, secret = ps.build_http(str(engine_env), str(_helper_file(tmp_path)))
    assert (key, secret) == ("PKFAKE", "fakesecret") and http.helper is not None
    assert "sb_secret" not in json.dumps(http.sb_h) and "Authorization" not in http.sb_h
    fake = FakeSupabase()
    http.helper._open = fake
    assert http.broadcast({"as_of": "t", "feed": "iex", "prices": {"A": 1.0}}) == 202
    assert http.heartbeat({"state": "streaming"}) == 204
    paths = [c[1].split("?")[0] for c in fake.calls]
    assert paths == ["/auth/v1/token", "/realtime/v1/api/broadcast", "/rest/v1/rpc/kb_helper_heartbeat"]
    assert fake.calls[2][3] == {"p_process": "price_stream", "p_detail": {"state": "streaming"}}
    assert not any("sb_secret" in json.dumps([c[2], c[3]]) for c in fake.calls)
    with pytest.raises(SystemExit, match="ALPACA_API_KEY"):
        ps.build_http(str(engine_env), str(_helper_file(tmp_path, alpaca=False)))
    with pytest.raises(ValueError, match="not both"):
        ps.Http("k", "s", URL, "sb_secret_x", helper=http.helper)


def test_the_relay_without_the_helpers_file_runs_as_before(tmp_path, monkeypatch):
    ps = load("price_stream")
    for k in ("SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_ROLE_KEY", "ALPACA_API_KEY", "ALPACA_SECRET",
              "ALPACA_API_SECRET", "ALPACA_BASE_URL", "KAIBOT_HELPER_ENV_FILE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SUPABASE_URL", URL)
    engine_env = tmp_path / ".env"
    engine_env.write_text("SUPABASE_SERVICE_KEY=sb_secret_engine\nALPACA_API_KEY=X\nALPACA_SECRET=Y\n")
    http, key, _ = ps.build_http(str(engine_env), str(tmp_path / "no-helper.env"))
    assert http.helper is None and http.sb_h["apikey"] == "sb_secret_engine" and key == "X"
    for k in ("SUPABASE_SERVICE_KEY", "ALPACA_API_KEY", "ALPACA_SECRET"):
        monkeypatch.delenv(k, raising=False)  # load_env_file wrote them into the environment


def test_the_publisher_on_the_helper_path_makes_one_call():
    pn = load("publish_notices")
    fake = FakeSupabase()
    rows = [{"id": "a"}, {"id": "b"}]
    res = pn.publish_as_helper(session(fake), rows, [date(2026, 11, 18)], [date(2026, 11, 19), date(2026, 11, 25)],
                               datetime(2026, 11, 19, 7, tzinfo=UTC))
    assert res == {"written": 2, "withdrawn": ["old"]}
    assert fake.calls[-1][3] == {"p_rows": rows, "p_lo": "2026-11-18", "p_hi": "2026-11-25"}


def test_the_setup_check_reports_each_line_and_prints_no_secret(tmp_path, capsys, monkeypatch):
    hc = load("helper_check")
    fake = FakeSupabase()
    rows = hc.check(session(fake))
    assert [ok for _, ok, _ in rows] == [True] * len(rows) and len(rows) == 8
    assert any("insert a command: refused" in name and note == "HTTP 403" for name, _, note in rows)

    class Open(FakeSupabase):  # a database where the helper CAN write a table: the check must fail
        def __call__(self, req, timeout):
            if req.full_url.endswith("/rest/v1/command"):
                self.calls.append((req.get_method(), "/rest/v1/command", {}, None))
                return Resp(None, 201)
            return super().__call__(req, timeout)

    bad = hc.check(session(Open()))
    assert [name for name, ok, _ in bad if not ok] == ["insert a command: refused"]
    monkeypatch.setattr(hc.HelperSession, "from_env", classmethod(lambda cls, env, url, **kw: session(fake)))
    assert hc.main(["--helper-env", str(_helper_file(tmp_path))]) == 0
    out = capsys.readouterr().out
    assert out.count("OK  ") == 8 and not any(x in out for x in (*SECRETS, PUB))
    assert hc.main(["--helper-env", str(tmp_path / "none.env")]) == 2
