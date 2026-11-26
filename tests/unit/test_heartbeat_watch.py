"""M20 9.5 (#729): the engine's heartbeat watched from outside its host (scripts/heartbeat_watch.py).

One local HTTP server stands in for both Supabase (GET /rest/v1/heartbeat) and Healthchecks.io (POST): fresh beat ->
success ping, exit 0; stale -> /fail with the age, exit 1; Supabase unreadable or no row -> /fail, exit 2; nothing
configured -> no ping, exit 2. Configuration only from the environment or the env file: nothing names a host.
"""

from __future__ import annotations

import http.server
import importlib.util
import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("kaibot_heartbeat_watch", ROOT / "scripts" / "heartbeat_watch.py")
hw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hw)
NOW = datetime(2026, 12, 1, 15, 0, tzinfo=UTC)


@pytest.fixture
def world(monkeypatch):
    state = {"rows": [{"ts": (NOW - timedelta(seconds=20)).isoformat()}], "status": 200, "pings": [], "gets": []}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            state["gets"].append((self.path, self.headers.get("Accept-Profile"), self.headers.get("apikey")))
            self.send_response(state["status"])
            self.end_headers()
            self.wfile.write(json.dumps(state["rows"]).encode())

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            state["pings"].append((self.path, self.rfile.read(n).decode()))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    for k in hw.KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SUPABASE_URL", base)
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "sb_secret_test")
    monkeypatch.setenv("HEALTHCHECKS_HEARTBEAT_URL", f"{base}/hb-uuid")
    yield state
    srv.shutdown()


def test_a_fresh_beat_pings_success(world, capsys):
    assert hw.main([], now=NOW) == 0
    assert world["pings"] == [("/hb-uuid", "kaibot heartbeat fresh: engine 20 s ago")]
    path, profile, key = world["gets"][0]
    assert path.startswith("/rest/v1/heartbeat?") and "process=eq.engine" in path and profile == "kaibot"
    assert "sb_secret_test" not in capsys.readouterr().err  # the key is never printed


def test_a_stale_beat_pings_fail_with_its_age(world):
    world["rows"] = [{"ts": (NOW - timedelta(minutes=10)).isoformat()}]
    assert hw.main([], now=NOW) == 1
    (path, msg), = world["pings"]
    assert path == "/hb-uuid/fail" and "stale" in msg and "600 s ago" in msg


@pytest.mark.parametrize("how", ["no_row", "http_500", "no_key"])
def test_an_unreadable_heartbeat_is_an_alarm(world, monkeypatch, how):
    if how == "no_row":
        world["rows"] = []
    elif how == "http_500":
        world["status"] = 500
    else:
        monkeypatch.delenv("SUPABASE_SERVICE_KEY")
    assert hw.main([], now=NOW) == 2
    (path, msg), = world["pings"]
    assert path == "/hb-uuid/fail" and "unreadable" in msg


def test_nothing_configured_pings_nothing(world, monkeypatch):
    monkeypatch.delenv("HEALTHCHECKS_HEARTBEAT_URL")
    assert hw.main([], now=NOW) == 2 and world["pings"] == [] and world["gets"] == []


def test_the_limit_and_the_settings_come_from_the_env_file(world, monkeypatch, tmp_path):
    import os

    url = os.environ["HEALTHCHECKS_HEARTBEAT_URL"]
    monkeypatch.delenv("HEALTHCHECKS_HEARTBEAT_URL")
    env = tmp_path / "monitor.env"
    env.write_text(f"ALPACA_API_KEY=never-read\nexport HEALTHCHECKS_HEARTBEAT_URL=\"{url}\"\n"
                   "HEARTBEAT_STALE_S=10  # x\n")
    assert hw.main(["--env-file", str(env)], now=NOW) == 1  # 20 s is stale with a 10 s limit
    assert world["pings"][0][0] == "/hb-uuid/fail"
    assert "ALPACA_API_KEY" not in hw.settings(str(env))


def test_dry_run_sends_nothing(world):
    assert hw.main(["--dry-run"], now=NOW) == 0 and world["pings"] == []


def test_the_script_names_no_host():
    text = (ROOT / "scripts" / "heartbeat_watch.py").read_text()
    for mac in ("Library", "launchd", "/Users/", "Projects/kaibot", "pmset", "darwin"):
        assert mac not in text
