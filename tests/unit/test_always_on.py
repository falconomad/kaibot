"""Issue #121: always-on operation. launchd plists (validated as XML/plist on Linux), pinned releases, the
Healthchecks.io pinger, the nightly backup and the service wrapper. No network: pings go to a local HTTP server."""

from __future__ import annotations

import http.server
import importlib.util
import os
import plistlib
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path
from xml.dom import minidom

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"


def load_hc():
    spec = importlib.util.spec_from_file_location("kaibot_healthcheck", SCRIPTS / "healthcheck.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["kaibot_healthcheck"] = mod
    spec.loader.exec_module(mod)
    return mod


hc = load_hc()


def sh(args, cwd, env=None, check=False):
    e = {k: v for k, v in os.environ.items() if not k.startswith(("KAIBOT_", "HEALTHCHECKS_"))}
    e.update(env or {})
    r = subprocess.run(args, cwd=cwd, env=e, capture_output=True, text=True, timeout=60)
    if check:
        assert r.returncode == 0, r.stdout + r.stderr
    return r


# ---------------------------------------------------------------- launchd plists
@pytest.fixture
def plists(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    out = tmp_path / "agents"
    env_file = tmp_path / "secret.env"
    env_file.write_text("ALPACA_SECRET=do-not-copy-me\n")
    r = sh(["bash", str(SCRIPTS / "install_service.sh"), "--dry-run", "--python", "--out", str(out)], ROOT,
           {"HOME": str(home), "KAIBOT_ENV_FILE": str(env_file), "PATH": os.environ["PATH"]}, check=True)
    assert "mode python" in r.stdout
    texts = {n: (out / f"com.kaibot.{n}.plist").read_text() for n in ("engine", "daily")}
    return home, env_file, texts


def test_plists_are_valid_xml_and_plists(plists):
    _, _, texts = plists
    for t in texts.values():
        minidom.parseString(t.encode())  # well-formed XML
        assert t.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist')
        plistlib.loads(t.encode())


def test_engine_plist_keys(plists):
    home, env_file, texts = plists
    p = plistlib.loads(texts["engine"].encode())
    assert p["Label"] == "com.kaibot.engine"
    assert p["ProgramArguments"] == ["/bin/bash", str(SCRIPTS / "service_run.sh"), "engine"]
    assert p["WorkingDirectory"] == str(ROOT)
    assert p["RunAtLoad"] is True
    assert p["KeepAlive"] == {"SuccessfulExit": False}  # restart after a crash, not after a clean stop
    assert p["ThrottleInterval"] >= 30
    assert p["StartCalendarInterval"] == [{"Weekday": d, "Hour": 15, "Minute": 0} for d in range(1, 6)]
    assert p["AbandonProcessGroup"] is True  # the watchdog survives an engine crash
    log_dir = f"{home}/Library/Logs/kaibot"
    assert p["StandardOutPath"] == p["StandardErrorPath"] == f"{log_dir}/engine.log"
    env = p["EnvironmentVariables"]
    assert env["KAIBOT_ENV_FILE"] == str(env_file) and env["KAIBOT_LOG_DIR"] == log_dir
    assert env["KAIBOT_SERVICE_MODE"] == "python" and env["HOME"] == str(home)
    assert "/usr/bin" in env["PATH"].split(":")


def test_daily_plist_keys(plists):
    home, _, texts = plists
    p = plistlib.loads(texts["daily"].encode())
    assert p["Label"] == "com.kaibot.daily"
    # go-live: also at login and boot (a Mac switched off at the weekend catches up Friday's bars on Monday)
    assert p["ProgramArguments"][-1] == "daily" and p["RunAtLoad"] is True and "KeepAlive" not in p
    # #206: 03:15 and a 07:45 catch-up (only the daily bar refresh), Tuesday-Saturday, Mac time
    assert p["StartCalendarInterval"] == [{"Weekday": d, "Hour": h, "Minute": m} for d in range(2, 7)
                                          for h, m in ((3, 15), (7, 45))]
    assert p["StandardOutPath"] == f"{home}/Library/Logs/kaibot/daily.log"


def test_the_daily_bar_refresh_runs_after_alpacas_daily_bar_and_before_the_open_whatever_the_dst():
    """#206: the Mac runs on Europe/Berlin. For every weekday session of 2026-2028 the refreshes of the next morning
    (03:15 and 07:45 Berlin) fall after 20:15 New York of the session (Alpaca's complete daily bar,
    kaibot.ingest.daily_bars.COMPLETE_AT) and before the next session's 09:30 New York open."""
    from datetime import date, datetime, time, timedelta
    from zoneinfo import ZoneInfo

    from kaibot.ingest.daily_bars import COMPLETE_AT

    berlin, ny = ZoneInfo("Europe/Berlin"), ZoneInfo("America/New_York")
    d = date(2026, 1, 1)
    while d < date(2029, 1, 1):
        if d.weekday() < 5:
            nxt = d + timedelta(days=3 if d.weekday() == 4 else 1)
            ready = datetime.combine(d, COMPLETE_AT, ny)
            for h, m in ((3, 15), (7, 45)):  # Tuesday-Saturday: the morning after each weekday
                run = datetime.combine(d + timedelta(days=1), time(h, m), berlin)
                assert ready < run < datetime.combine(nxt, time(9, 30), ny), (d, h, m)
        d += timedelta(days=1)


def test_the_daily_job_refreshes_the_store_twice_and_backs_up_once(tmp_path):
    r = tmp_path / "kaibot"
    (r / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPTS / "service_run.sh", r / "scripts")
    for n, body in (("backup_local.sh", f"echo backup >> {tmp_path}/calls"),
                    ("fetch_daily.sh", f"echo fetch >> {tmp_path}/calls; echo stored SPY")):
        (r / "scripts" / n).write_text(f"#!/bin/bash\n{body}\n")
        (r / "scripts" / n).chmod(0o755)
    env = {"HOME": str(tmp_path), "KAIBOT_LOG_DIR": str(tmp_path / "logs"), "KAIBOT_ENV_FILE": str(tmp_path / "x"),
           "PATH": os.environ["PATH"]}
    for _ in range(2):
        sh(["bash", "scripts/service_run.sh", "daily"], r, env, check=True)
    assert (tmp_path / "calls").read_text().split() == ["backup", "fetch", "fetch"]
    assert (r / "logs" / "fetch_daily.log").read_text().count("stored SPY") == 2


def daily_repo(tmp_path):
    r = tmp_path / "kaibot"
    (r / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPTS / "service_run.sh", r / "scripts")
    for n, body in (("backup_local.sh", f"echo backup >> {tmp_path}/calls"),
                    ("fetch_daily.sh", f"echo fetch >> {tmp_path}/calls; echo stored SPY")):
        (r / "scripts" / n).write_text(f"#!/bin/bash\n{body}\n")
        (r / "scripts" / n).chmod(0o755)
    env = {"HOME": str(tmp_path), "KAIBOT_LOG_DIR": str(tmp_path / "logs"), "KAIBOT_ENV_FILE": str(tmp_path / "x"),
           "PATH": os.environ["PATH"]}
    return r, env


def fetches(tmp_path):
    f = tmp_path / "calls"
    return f.read_text().split().count("fetch") if f.exists() else 0


def test_the_daily_fetch_switch_is_read_from_the_env_file_too(tmp_path):
    """KAIBOT_DAILY_FETCH reads like KAIBOT_TICKER_CHECKS: the .env too (the plist does not carry it), and the
    service's own environment wins over the file."""
    r, env = daily_repo(tmp_path)
    (tmp_path / "x").write_text("KAIBOT_DAILY_FETCH=0  # no refresh tonight\n")
    sh(["bash", "scripts/service_run.sh", "daily"], r, env, check=True)
    assert fetches(tmp_path) == 0
    sh(["bash", "scripts/service_run.sh", "daily"], r, {**env, "KAIBOT_DAILY_FETCH": "1"}, check=True)
    assert fetches(tmp_path) == 1


@pytest.mark.parametrize("value", ["yes", "off", "2"])
def test_a_daily_fetch_value_that_is_not_0_or_1_keeps_the_refresh_and_says_so(tmp_path, value):
    r, env = daily_repo(tmp_path)
    x = sh(["bash", "scripts/service_run.sh", "daily"], r, {**env, "KAIBOT_DAILY_FETCH": value}, check=True)
    assert fetches(tmp_path) == 1 and "KAIBOT_DAILY_FETCH" in x.stdout and "not 0 or 1" in x.stdout


def test_plists_never_contain_secrets(plists):
    _, _, texts = plists
    assert all("do-not-copy-me" not in t for t in texts.values())


def test_plist_escapes_xml_in_paths(tmp_path):
    home = tmp_path / "a&b<c"
    home.mkdir()
    out = tmp_path / "agents"
    sh(["bash", str(SCRIPTS / "install_service.sh"), "--dry-run", "--docker", "--out", str(out)], ROOT,
       {"HOME": str(home), "PATH": os.environ["PATH"]}, check=True)
    p = plistlib.loads((out / "com.kaibot.engine.plist").read_bytes())
    assert p["StandardOutPath"].startswith(str(home)) and p["EnvironmentVariables"]["KAIBOT_SERVICE_MODE"] == "docker"


# ---------------------------------------------------------------- Healthchecks pinger
class Pings:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def __call__(self, kind, body=""):
        self.calls.append((kind, body))

    def kinds(self):
        return [k for k, _ in self.calls]


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def tick(minute):
    return f"2026-11-12 10:00:07,123 INFO tick {{'state': 'trading', 'minute': {minute}, 'halted': False}}\n"


def test_success_pings_every_five_minutes_not_at_once():
    p, c = Pings(), Clock()
    m = hc.Monitor(p, now=c)
    m.periodic(True)
    assert p.calls == []  # the start ping counts; a crash loop never flips the check up
    for _ in range(11):
        c.t += 60
        m.line(tick(30))
        m.periodic(True)
    assert p.kinds() == ["success", "success"]


@pytest.mark.parametrize("line,kind", [
    ("ERROR entries halted: drift (positions differ from the broker)", "halt"),
    ("ERROR entries halted: daily_stop (day loss)", "halt"),
    ("ERROR The engine lease is no longer ours: another engine took it over", "lease_lost"),
    ("ERROR engine stopped: lease lost (positions it was managing: [])", "lease_lost"),
    ("ERROR Another Kaibot engine holds the lease: host 'vm', started x", "lease_refused"),
    ("ERROR session refused: config v3 rejected: bad", "session_refused"),
    ("ERROR flatten (time) incomplete after 30.0 s, still open: ['AAPL']", "flatten_incomplete"),
    ("WARNING account not clean: {'foreign_positions': ['X']}", "account_not_clean"),
    ("ERROR watchdog engine heartbeat 120 s old: entries canceled ['AAPL']", "watchdog_tripped"),
    ("ERROR watchdog 15:55 with the engine down: closed ['AAPL']", "watchdog_flatten"),
    # QA-Ops (#523): M9's alerts, as the engine logs them
    ("ERROR replica poison: Supabase refuses halt entry 7 for good (POST halt -> 400: ...); skipped, the journal "
     "keeps it", "replica_poison"),
    ("ERROR replica backlog: 40 rows waiting for 10 minutes (no error): the dashboard and the dead-man's book are "
     "stale", "replica_backlog"),
    ("ERROR clock skew: the host clock and the broker's disagree ({'state': 'skewed', 'seconds': -240.0}): no new "
     "orders until they agree", "clock_skew"),
    ("ERROR calendar expiring: macro_calendar ends 2027-12-31 (in 29 days): extend config/macro_calendar.yaml",
     "calendar_expiring"),
    # QA-Ops (24-25 Nov 2026 incident): engine/gap.py's line, as the engine logs it
    ("WARNING engine gap: no engine heartbeat for 17 h 05 min (2026-11-24T22:45 to 2026-11-25T15:50; the last "
     "heartbeat before this start was not a clean stop's).", "engine_gap"),
    # audit 2, 5.13 (#693): engine/event_names.note's line
    ("WARNING event 'brand_new' is not registered in engine/event_names.py (written anyway; one unregistered_event)",
     "unregistered_event"),
])
def test_alarm_lines_send_fail(line, kind):
    p = Pings()
    hc.Monitor(p, now=Clock()).line(f"2026-11-12 15:00:00,000 {line}\n")
    assert p.calls and p.calls[0][0] == "fail" and p.calls[0][1].startswith(f"kaibot {kind}: ")


def test_ordinary_lines_send_nothing():
    p = Pings()
    m = hc.Monitor(p, now=Clock())
    for ln in ["INFO engine up (code v1@abc). Ctrl-C to stop", "ERROR loop error (will retry)", tick(5),
               "ERROR engine lease renewal failed (timeout): no new entries until it works again"]:
        m.line(ln)
    assert p.calls == []


def test_same_alarm_at_most_every_30_minutes():
    p, c = Pings(), Clock()
    m = hc.Monitor(p, now=c)
    for _ in range(3):
        m.line("ERROR entries halted: drift (x)")
        c.t += 600
    m.line("ERROR session refused: y")  # another kind is not suppressed
    c.t += 1300
    m.line("ERROR entries halted: drift (x)")
    assert p.kinds() == ["fail", "fail", "fail"]


def test_stall_mid_session_fails_once_and_recovers():
    p, c = Pings(), Clock()
    m = hc.Monitor(p, now=c)
    m.line(tick(100))
    c.t += 660
    m.periodic(True)
    m.periodic(True)
    assert p.kinds() == ["fail"] and "stalled" in p.calls[0][1]
    m.line(tick(111))
    m.periodic(True)
    assert p.kinds() == ["fail", "success"]


@pytest.mark.parametrize("minute", [388, 208, -1])
def test_silence_after_the_close_is_not_a_stall(minute):
    p, c = Pings(), Clock()
    m = hc.Monitor(p, now=c)
    m.line(tick(minute) if minute >= 0 else "INFO tick {'state': 'idle'}")
    c.t += 3600
    m.periodic(True)
    assert p.kinds() == ["success"]


def test_dead_process_sends_nothing():
    p, c = Pings(), Clock()
    m = hc.Monitor(p, now=c)
    c.t += 3600
    m.periodic(False)
    assert p.calls == []


def test_url_from_env_file_reads_only_that_line(tmp_path):
    f = tmp_path / ".env"
    f.write_text("ALPACA_SECRET=x\nHEALTHCHECKS_PING_URL=https://hc-ping.com/abc/  # free check\n")
    assert hc.url_from({}, str(f)) == "https://hc-ping.com/abc"
    assert hc.url_from({"HEALTHCHECKS_PING_URL": "https://e/x"}, str(f)) == "https://e/x"
    f.write_text("HEALTHCHECKS_PING_URL=\n")
    assert hc.url_from({}, str(f)) == ""
    assert hc.url_from({}, str(tmp_path / "missing")) == ""


@pytest.fixture
def server():
    got: list[tuple[str, str]] = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            got.append((self.path, self.rfile.read(n).decode()))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/uuid", got
    srv.shutdown()


def test_cli_pings_the_right_endpoints(server):
    url, got = server
    for action, path in [("start", "/uuid/start"), ("success", "/uuid"), ("fail", "/uuid/fail"), ("log", "/uuid/log")]:
        r = sh([sys.executable, str(SCRIPTS / "healthcheck.py"), action, "halt: drift"], ROOT,
               {"HEALTHCHECKS_PING_URL": url}, check=True)
        assert url not in r.stdout + r.stderr  # never printed
        assert got[-1][0] == path
    assert got[2][1] == "halt: drift"


def test_cli_without_url_logs_once_and_does_nothing(tmp_path):
    r = sh([sys.executable, str(SCRIPTS / "healthcheck.py"), "fail", "x", "--env-file", str(tmp_path / "none")],
           ROOT, check=True)
    assert r.stderr.count("alarms are off") == 1
    r = sh([sys.executable, str(SCRIPTS / "healthcheck.py"), "watch", "--pid", "1", "--log", "/nonexistent"], ROOT,
           check=True)  # returns at once: no URL, no watching
    assert "alarms are off" in r.stderr


def test_unreachable_endpoint_never_raises_or_hangs():
    p = hc.Pinger("http://127.0.0.1:9/x")  # discard port: refused at once
    t0 = time.monotonic()
    p.send("fail", "x")
    p.wait()
    assert time.monotonic() - t0 < hc.TIMEOUT_S + 2


def test_watch_follows_the_log_and_stops_with_the_process(tmp_path, server):
    url, got = server
    logf = tmp_path / "engine.log"
    logf.write_text("old line: entries halted: drift (before the start, ignored)\n")
    proc = subprocess.Popen(["sleep", "30"])
    w = subprocess.Popen([sys.executable, str(SCRIPTS / "healthcheck.py"), "watch", "--pid", str(proc.pid),
                          "--log", str(logf)], env={**os.environ, "HEALTHCHECKS_PING_URL": url})
    try:
        time.sleep(1.5)
        with logf.open("a") as f:
            f.write("2026-11-12 15:00:00 ERROR entries halted: drift (positions differ from the broker)\n")
        logf.write_text("")  # copy-truncate rotation
        with logf.open("a") as f:
            f.write("2026-11-12 15:01:00 ERROR session refused: config\n")
        time.sleep(2.5)
        proc.kill()
        proc.wait()
        assert w.wait(timeout=15) == 0
    finally:
        proc.kill()
        w.kill()
    paths = [p for p, _ in got]
    assert paths[0] == "/uuid/start"
    bodies = [b for p, b in got if p == "/uuid/fail"]
    assert any("session refused" in b for b in bodies)
    assert not any("before the start" in b for b in bodies)


# ---------------------------------------------------------------- pinned releases
@pytest.fixture
def repo(tmp_path):
    """A throwaway clone-like repo with the real scripts, two tags and a fake docker on PATH."""
    r = tmp_path / "kaibot"
    (r / "scripts").mkdir(parents=True)
    for n in ("release.sh", "run_engine.sh", "engine_guard.sh"):
        shutil.copy(SCRIPTS / n, r / "scripts" / n)
    (r / ".gitignore").write_text("data/\nlogs/\n")
    g = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}
    for cmd in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "one"], ["tag", "-a", "v1", "-m", "v1"]):
        sh(["git", *cmd], r, g, check=True)
    (r / "file.txt").write_text("two\n")
    for cmd in (["add", "-A"], ["commit", "-qm", "two"]):
        sh(["git", *cmd], r, g, check=True)
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "docker").write_text(f'#!/bin/sh\necho "$@" >> {tmp_path}/docker.calls\nexit 0\n')
    (bin_ / "docker").chmod(0o755)
    env_file = tmp_path / "secret.env"
    env_file.write_text("ALPACA_API_KEY=a\nALPACA_SECRET=b\nFRED_API_KEY=c\nSUPABASE_SERVICE_KEY=d\n")
    env = {"PATH": f"{bin_}:{os.environ['PATH']}", "KAIBOT_ENV_FILE": str(env_file), **g}
    return r, env, tmp_path


def rev(r, ref="HEAD"):
    return sh(["git", "rev-parse", "--short", f"{ref}^{{commit}}"], r, check=True).stdout.strip()


def test_release_refused_in_market_hours_unless_forced(repo):
    r, env, _ = repo
    x = sh(["bash", "scripts/release.sh", "v1"], r, {**env, "KAIBOT_FAKE_ET": "3 1015"})
    assert x.returncode == 1 and "Refused" in x.stdout and not (r / "data/RELEASE").exists()
    sh(["bash", "scripts/release.sh", "v1", "--force"], r, {**env, "KAIBOT_FAKE_ET": "3 1015"}, check=True)
    assert (r / "data/RELEASE").read_text().strip() == "v1"
    assert "(forced)" in (r / "data/release_history.log").read_text()


@pytest.mark.parametrize("when", ["3 0929", "3 1600", "6 1200", "7 1000"])
def test_release_allowed_outside_market_hours(repo, when):
    r, env, _ = repo
    sh(["bash", "scripts/release.sh", "v1"], r, {**env, "KAIBOT_FAKE_ET": when}, check=True)


def test_release_unknown_tag_is_refused(repo):
    r, env, _ = repo
    x = sh(["bash", "scripts/release.sh", "v9"], r, {**env, "KAIBOT_FAKE_ET": "6 1200"})
    assert x.returncode == 1 and "No tag 'v9'" in x.stdout


def test_apply_without_pin_does_nothing(repo):
    r, env, _ = repo
    x = sh(["bash", "scripts/release.sh", "--apply"], r, env, check=True)
    assert x.stdout == "" and rev(r) == rev(r, "main")


def test_apply_checks_out_the_pinned_tag(repo):
    r, env, _ = repo
    sh(["bash", "scripts/release.sh", "v1"], r, {**env, "KAIBOT_FAKE_ET": "6 1200"}, check=True)
    x = sh(["bash", "scripts/release.sh", "--apply"], r, env, check=True)
    assert x.stdout.strip() == f"v1@{rev(r, 'v1')}" and rev(r) == rev(r, "v1")
    x = sh(["bash", "scripts/release.sh", "--apply"], r, {**env, "KAIBOT_RELEASE": "main"}, check=True)
    assert x.stdout.strip() == f"main@{rev(r, 'main')}" and rev(r) == rev(r, "main")


def test_apply_refuses_to_switch_over_local_changes(repo):
    r, env, _ = repo
    (r / "file.txt").write_text("edited\n")
    x = sh(["bash", "scripts/release.sh", "--apply"], r, {**env, "KAIBOT_RELEASE": "v1"})
    assert x.returncode == 1 and "Local changes" in x.stderr and rev(r) == rev(r, "main")
    x = sh(["bash", "scripts/release.sh", "--apply"], r, {**env, "KAIBOT_RELEASE": "nope"})
    assert x.returncode == 1 and "does not exist" in x.stderr


def built_sha(tmp):
    calls = (tmp / "docker.calls").read_text().splitlines()
    build = [c for c in calls if c.startswith("build")][-1]
    return build.split("CODE_SHA=")[1].split()[0]


def test_run_engine_default_is_unchanged(repo):
    """No pin: run_engine.sh pulls main as before (no remote here: it says so and runs what is there) and records
    the plain short sha, exactly as before #121."""
    r, env, tmp = repo
    x = sh(["bash", "scripts/run_engine.sh", "--docker"], r, env, check=True)
    assert "could not pull the latest code" in x.stdout and "Release:" not in x.stdout
    assert built_sha(tmp) == rev(r) == rev(r, "main")
    assert (r / "data/live/running_release").read_text().split()[0] == rev(r)
    run = [c for c in (tmp / "docker.calls").read_text().splitlines() if c.startswith("run --rm --name kaibot-engine")]
    assert run and run[-1].endswith("sh /app/scripts/engine_entry.sh")  # #727: the image's entry, then the engine


def test_run_engine_trades_the_pinned_tag(repo):
    r, env, tmp = repo
    sh(["bash", "scripts/release.sh", "v1"], r, {**env, "KAIBOT_FAKE_ET": "6 1200"}, check=True)
    x = sh(["bash", "scripts/run_engine.sh", "--docker"], r, env, check=True)
    assert f"Release: v1@{rev(r, 'v1')}" in x.stdout
    assert rev(r) == rev(r, "v1") and built_sha(tmp) == f"v1@{rev(r, 'v1')}"
    x = sh(["bash", "scripts/run_engine.sh", "--docker"], r, {**env, "KAIBOT_RELEASE": "main"}, check=True)
    assert built_sha(tmp).startswith("main@") and rev(r) == rev(r, "main")


def test_run_engine_stops_on_a_missing_release(repo):
    r, env, tmp = repo
    x = sh(["bash", "scripts/run_engine.sh", "--docker"], r, {**env, "KAIBOT_RELEASE": "v404"})
    assert x.returncode == 1 and not (tmp / "docker.calls").exists()


# ---------------------------------------------------------------- audit 2 5.2 (#682): explicit releases
def _git(r, env, *args):
    return sh(["git", *args], r, env, check=True)


def test_a_lightweight_tag_is_not_a_release(repo):
    r, env, _ = repo
    _git(r, env, "tag", "light", "main")
    x = sh(["bash", "scripts/release.sh", "light"], r, {**env, "KAIBOT_FAKE_ET": "6 1200"})
    assert x.returncode == 1 and "lightweight tag" in x.stdout and not (r / "data/RELEASE").exists()
    x = sh(["bash", "scripts/release.sh", "--apply"], r, {**env, "KAIBOT_RELEASE": "light"})
    assert x.returncode == 1 and "lightweight tag" in x.stderr and rev(r) == rev(r, "main")


def test_a_tag_off_main_is_not_a_release(repo):
    """A tag on a commit that never reached main (a branch, a fork's push of a tag) is refused at the pin and at the
    start, so a pin can only name merged code."""
    r, env, _ = repo
    _git(r, env, "checkout", "-q", "-b", "side")
    (r / "side.txt").write_text("unreviewed\n")
    _git(r, env, "add", "-A")
    _git(r, env, "commit", "-qm", "side")
    _git(r, env, "tag", "-a", "rel-20261126-9", "-m", "x")
    _git(r, env, "checkout", "-q", "main")
    x = sh(["bash", "scripts/release.sh", "rel-20261126-9"], r, {**env, "KAIBOT_FAKE_ET": "6 1200"})
    assert x.returncode == 1 and "is not on main" in x.stdout and not (r / "data/RELEASE").exists()
    x = sh(["bash", "scripts/run_engine.sh", "--docker"], r, {**env, "KAIBOT_RELEASE": "rel-20261126-9"})
    assert x.returncode == 1 and "is not on main" in x.stderr and rev(r) == rev(r, "main")


def test_latest_pins_the_newest_ci_release(repo):
    r, env, _ = repo
    _git(r, env, "tag", "-a", "rel-20261126-2", "-m", "x", "v1")
    _git(r, env, "tag", "-a", "rel-20261126-10", "-m", "x", "main")
    _git(r, env, "tag", "-a", "rel-20261125-30", "-m", "x", "v1")
    x = sh(["bash", "scripts/release.sh", "--latest"], r, {**env, "KAIBOT_FAKE_ET": "6 1200"}, check=True)
    assert "Newest release: rel-20261126-10" in x.stdout
    assert (r / "data/RELEASE").read_text().strip() == "rel-20261126-10"


def test_latest_without_a_release_changes_nothing(repo):
    r, env, _ = repo
    x = sh(["bash", "scripts/release.sh", "--latest"], r, {**env, "KAIBOT_FAKE_ET": "6 1200"})
    assert x.returncode == 1 and "No rel-* tag yet" in x.stdout and not (r / "data/RELEASE").exists()


def test_unpinned_follows_main_by_default_and_says_so_loudly(repo):
    """Product decision (26 Nov 2026, #600): unpinned starts still follow main for now, with WARNING lines that name
    the one-command pin; the start records the full commit it runs (data/live/running_commit)."""
    r, env, tmp = repo
    x = sh(["bash", "scripts/run_engine.sh", "--docker"], r, env, check=True)
    assert x.stderr.count("WARNING: UNPINNED ENGINE") == 3 and "./scripts/release.sh --latest" in x.stderr
    full = sh(["git", "rev-parse", "HEAD"], r, check=True).stdout.strip()
    assert (r / "data/live/running_commit").read_text().strip() == full
    x = sh(["bash", "scripts/run_engine.sh", "--docker"], r, {**env, "KAIBOT_RELEASE": "main"}, check=True)
    assert "WARNING: UNPINNED ENGINE" in x.stderr and "says 'main'" in x.stderr


@pytest.mark.parametrize("where", ["environment", "env file"])
def test_follow_main_0_refuses_an_unpinned_start(repo, where):
    r, env, tmp = repo
    if where == "environment":
        env = {**env, "KAIBOT_FOLLOW_MAIN": "0"}
    else:
        f = Path(env["KAIBOT_ENV_FILE"])
        f.write_text(f.read_text() + "KAIBOT_FOLLOW_MAIN=0  # x\n")
    for extra in ({}, {"KAIBOT_RELEASE": "main"}):
        x = sh(["bash", "scripts/run_engine.sh", "--docker"], r, {**env, **extra})
        assert x.returncode == 1 and "REFUSED" in x.stderr and "./scripts/release.sh --latest" in x.stderr
    assert not (tmp / "docker.calls").exists() and not (r / "data/live/running_release").exists()
    x = sh(["bash", "scripts/run_engine.sh", "--docker"], r, {**env, "KAIBOT_RELEASE": "v1"}, check=True)
    assert "UNPINNED" not in x.stderr and built_sha(tmp) == f"v1@{rev(r, 'v1')}"  # a pinned start is unaffected


def test_a_follow_main_value_that_is_not_0_or_1_keeps_the_default(repo):
    r, env, _ = repo
    x = sh(["bash", "scripts/run_engine.sh", "--docker"], r, {**env, "KAIBOT_FOLLOW_MAIN": "off"}, check=True)
    assert "KAIBOT_FOLLOW_MAIN='off' is not 0 or 1" in x.stderr and "WARNING: UNPINNED ENGINE" in x.stderr


def test_the_release_workflow_tags_only_a_green_main_and_runs_no_code():
    """The CI side of #682: workflow_dispatch only, from main's copy, checks ci and supply-chain on main's head, makes
    an annotated tag (a tag object, then its ref) named rel-YYYYMMDD-N; contents write and nothing else that writes."""
    doc = __import__("yaml").safe_load((ROOT / ".github/workflows/release.yml").read_text())
    on = doc.pop(True, doc.get("on"))
    assert set(on) == {"workflow_dispatch"} and doc["permissions"] == {"contents": "read"}
    job = doc["jobs"]["tag"]
    assert job["if"] == "github.ref == 'refs/heads/main'"
    assert job["permissions"] == {"contents": "write", "actions": "read"}
    (step,) = job["steps"]
    run = step["run"]
    assert "for wf in ci.yml supply-chain.yml" in run and '"completed success "*) ;;' in run
    assert "head_sha=$sha&branch=main&event=push" in run
    assert run.index("completed success") < run.index("repos/$REPO/git/tags") < run.index("repos/$REPO/git/refs")
    assert 'name="rel-$day-$(( ${n:-0} + 1 ))"' in run and "${{" not in run  # inputs only through env


# ---------------------------------------------------------------- #613: no hand start beside the service
@pytest.fixture
def fake_service(repo):
    """A live process that looks like the service's run (`service_run.sh`), named in data/live/service.pid."""
    r, env, tmp = repo
    (r / "data/live").mkdir(parents=True)
    holder = subprocess.Popen(["bash", "-c", "exec -a service_run_fake sleep 30"])
    (r / "data/live/service.pid").write_text(str(holder.pid))
    yield r, env, tmp, holder.pid
    holder.kill()
    holder.wait()


def test_run_engine_refuses_while_the_service_runs(fake_service):
    """22 Nov 2026 (#613): `run_engine.sh` by hand beside the service started a second engine and watchdog. Now it
    refuses before it touches the checkout, starts nothing and names the command to use."""
    r, env, tmp, pid = fake_service
    head = rev(r)
    for args in (["--docker"], ["--python"], []):
        x = sh(["bash", "scripts/run_engine.sh", *args], r, env)
        assert x.returncode == 1, x.stdout + x.stderr
        assert f"service is running (pid {pid})" in x.stderr and "./scripts/service.sh restart" in x.stderr
    assert not (tmp / "docker.calls").exists() and not (r / "data/live/running_release").exists()
    assert not (r / "data/live/watchdog.pid").exists() and rev(r) == head


def test_run_engine_check_is_allowed_beside_the_service(fake_service):
    r, env, tmp, _ = fake_service
    sh(["bash", "scripts/run_engine.sh", "--docker", "--check"], r, env, check=True)
    calls = (tmp / "docker.calls").read_text().splitlines()
    assert [c for c in calls if c.endswith("sh /app/scripts/engine_entry.sh --check")]
    assert not [c for c in calls if "kaibot-watchdog" in c]


def test_the_services_own_run_is_not_refused(fake_service):
    r, env, tmp, pid = fake_service
    sh(["bash", "scripts/run_engine.sh", "--docker"], r, {**env, "KAIBOT_SERVICE_RUN": str(pid)}, check=True)
    assert [c for c in (tmp / "docker.calls").read_text().splitlines() if c.startswith("run --rm --name kaibot-engine")]


def test_a_stale_or_borrowed_service_marker_is_refused(fake_service):
    """KAIBOT_SERVICE_RUN only counts when it names the live service run of the pid lock."""
    r, env, tmp, pid = fake_service
    x = sh(["bash", "scripts/run_engine.sh", "--docker"], r, {**env, "KAIBOT_SERVICE_RUN": str(pid + 1)})
    assert x.returncode == 1 and "./scripts/service.sh restart" in x.stderr and not (tmp / "docker.calls").exists()


def test_a_dead_service_pid_does_not_block_a_hand_start(repo):
    r, env, tmp = repo
    (r / "data/live").mkdir(parents=True)
    gone = subprocess.Popen(["bash", "-c", "exec -a service_run_fake true"])
    gone.wait()
    (r / "data/live/service.pid").write_text(str(gone.pid))
    sh(["bash", "scripts/run_engine.sh", "--docker"], r, env, check=True)


def test_a_pid_lock_naming_another_program_does_not_block_a_hand_start(repo):
    """A pid reused after a reboot by something that is not the service run."""
    r, env, tmp = repo
    (r / "data/live").mkdir(parents=True)
    other = subprocess.Popen(["sleep", "30"])
    try:
        (r / "data/live/service.pid").write_text(str(other.pid))
        sh(["bash", "scripts/run_engine.sh", "--docker"], r, env, check=True)
    finally:
        other.kill()
        other.wait()


@pytest.mark.parametrize("state, refused", [("running", True), ("not running", False)])
def test_run_engine_asks_launchd_about_the_installed_service(repo, state, refused):
    """No pid lock (the service run is between two starts, or was killed): launchd's word decides."""
    r, env, tmp = repo
    bin_ = Path(env["PATH"].split(":")[0])
    (bin_ / "launchctl").write_text(
        f'#!/bin/sh\n[ "$1 $2" = "print gui/$(id -u)/com.kaibot.engine" ] || exit 113\necho "\tstate = {state}"\n')
    (bin_ / "launchctl").chmod(0o755)
    x = sh(["bash", "scripts/run_engine.sh", "--docker"], r, env)
    assert (x.returncode == 1) is refused, x.stdout + x.stderr
    assert ("./scripts/service.sh restart" in x.stderr) is refused
    assert (tmp / "docker.calls").exists() is not refused


# ---------------------------------------------------------------- backup and wrapper
def test_backup_archives_local_files_and_prunes_old_ones(tmp_path):
    r = tmp_path / "kaibot"
    (r / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPTS / "backup_local.sh", r / "scripts")
    live = r / "data/live"
    (live / "history").mkdir(parents=True)
    (live / "history/SPY.parquet").write_text("cache")
    (live / "engine_holder_id").write_text("id")
    (live / "trials.jsonl").write_text("{}\n")
    with sqlite3.connect(live / "journal.sqlite") as db:
        db.execute("create table j (x)")
        db.execute("insert into j values (1)")
    (r / "data/RELEASE").write_text("v1\n")
    (r / "logs").mkdir()
    (r / "logs/watchdog.log").write_text("w\n")
    dest = tmp_path / "backups"
    dest.mkdir()
    old = dest / "kaibot-20200101-000000.tar.gz"
    old.write_text("x")
    os.utime(old, (time.time() - 40 * 86400,) * 2)
    env = {"HOME": str(tmp_path), "KAIBOT_BACKUP_DIR": str(dest), "KAIBOT_LOG_DIR": str(tmp_path / "svc")}
    x = sh(["bash", "scripts/backup_local.sh"], r, env, check=True)
    assert "deleted old backup" in x.stdout and not old.exists()
    (arch,) = dest.glob("kaibot-*.tar.gz")
    with tarfile.open(arch) as t:
        names = t.getnames()
    for want in ("kaibot/data/live/engine_holder_id", "kaibot/data/live/trials.jsonl", "kaibot/data/RELEASE",
                 "kaibot/data/live/journal.sqlite", "kaibot/logs/watchdog.log"):
        assert want in names
    assert not any("history" in n for n in names)


@pytest.fixture
def wrapper(tmp_path):
    r = tmp_path / "kaibot"
    (r / "scripts").mkdir(parents=True)
    for n in ("service_run.sh", "engine_guard.sh"):
        shutil.copy(SCRIPTS / n, r / "scripts")
    (r / "scripts/run_engine.sh").write_text(f"#!/bin/bash\necho ran >> {tmp_path}/ran\nexit ${{FAKE_CODE:-0}}\n")
    (r / "scripts/run_engine.sh").chmod(0o755)
    # a git checkout (#682: the worker and the relay start only on the commit the engine's start recorded)
    (r / ".gitignore").write_text("data/\nlogs/\n.venv/\n")
    g = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}
    for cmd in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "one"]):
        sh(["git", *cmd], r, g, check=True)
    # #712: the worker and the relay start only with their key files (the default paths under HOME)
    secrets = tmp_path / "Projects/kaibot-secrets"
    secrets.mkdir(parents=True)
    (secrets / "data.env").write_text("ALPACA_DATA_KEY=PKDATA\nALPACA_DATA_SECRET=d\n")
    (secrets / "helper.env").write_text("SUPABASE_PUBLISHABLE_KEY=p\n")
    return r, {"HOME": str(tmp_path), "KAIBOT_LOG_DIR": str(tmp_path / "logs"), "KAIBOT_SERVICE_MODE": "python",
               "KAIBOT_ENV_FILE": str(tmp_path / "none.env"), "PATH": os.environ["PATH"]}, tmp_path


def test_wrapper_on_hold_exits_zero_without_starting(wrapper):
    r, env, tmp = wrapper
    (r / "data").mkdir()
    (r / "data/HOLD").write_text("x")
    for _ in range(2):
        x = sh(["bash", "scripts/service_run.sh", "engine"], r, env)
        assert x.returncode == 0
    assert x.stdout == "" and not (tmp / "ran").exists()  # the reason is said once, not every minute


def test_wrapper_exit_codes_drive_keepalive(wrapper):
    r, env, tmp = wrapper
    assert sh(["bash", "scripts/service_run.sh", "engine"], r, env).returncode == 0
    x = sh(["bash", "scripts/service_run.sh", "engine"], r, {**env, "FAKE_CODE": "3"})
    assert x.returncode == 1 and "exited with code 3" in x.stdout
    assert (tmp / "ran").read_text().count("ran") == 2 and not (r / "data/live/service.pid").exists()


def test_wrapper_backs_off_after_a_refused_start_code_5(wrapper):
    """#643 follow-up: exit code 5 (multi-day sleeves and no daily path) is refused again on every try, so the
    service waits KAIBOT_REFUSED_BACKOFF_S before it exits 1, with one clear line per try (not launchd's minute).
    The pid lock is kept during the wait, so a hand start is still refused."""
    r, env, tmp = wrapper
    env = {**env, "FAKE_CODE": "5", "KAIBOT_REFUSED_BACKOFF_S": "3"}
    t0 = time.monotonic()
    p = subprocess.Popen(["bash", "scripts/service_run.sh", "engine"], cwd=r, env=env, stdout=subprocess.PIPE,
                         text=True)
    held = False
    while p.poll() is None:
        held = held or ((tmp / "ran").exists() and (r / "data/live/service.pid").exists())
        time.sleep(0.1)
    out = p.stdout.read()
    assert p.returncode == 1, out
    assert time.monotonic() - t0 >= 3  # it waited before handing back to launchd
    assert held and not (r / "data/live/service.pid").exists()  # the lock was held while waiting, then removed
    lines = [ln for ln in out.splitlines() if "code 5" in ln]
    assert len(lines) == 1, out  # one line per try, nothing else about code 5
    assert "engine refused to start (exit code 5)" in lines[0] and "KAIBOT_MULTIDAY" in lines[0]
    assert "Next try in 3 s" in lines[0] and "docs/ops/run_engine.md" in lines[0]
    assert "launchd starts it again" not in out  # not the generic restart line (or its second alarm)


def _code_5_service(wrapper, backoff: str):
    r, env, tmp = wrapper
    env = {**env, "FAKE_CODE": "5", "KAIBOT_REFUSED_BACKOFF_S": backoff}
    p = subprocess.Popen(["bash", "scripts/service_run.sh", "engine"], cwd=r, env=env, stdout=subprocess.PIPE,
                         text=True)
    return p, r, tmp


def _read_until(p, text: str, limit_s: float = 60) -> list[str]:
    """The service's lines up to and including the first that holds `text` (a reader thread: never blocks the test
    past `limit_s`, however slow the machine)."""
    import queue
    import threading

    q: queue.Queue = queue.Queue()
    threading.Thread(target=lambda: [q.put(ln) for ln in p.stdout] + [q.put(None)], daemon=True).start()
    seen, end = [], time.monotonic() + limit_s
    while time.monotonic() < end:
        try:
            ln = q.get(timeout=max(0.0, end - time.monotonic()))
        except queue.Empty:
            break
        if ln is None:
            break
        seen.append(ln)
        if text in ln:
            p._kb_rest = q  # the rest of the output, read by _rest()
            return seen
    p.kill()
    raise AssertionError(f"no line with {text!r} within {limit_s} s: {''.join(seen)}")


def _rest(p, limit_s: float = 30) -> str:
    p.wait(timeout=limit_s)
    out, q = [], p._kb_rest
    while (ln := q.get(timeout=limit_s)) is not None:
        out.append(ln)
    return "".join(out)


@pytest.mark.parametrize("sig", ["INT", "TERM"])
def test_a_stop_during_the_code_5_back_off_ends_it_at_once(wrapper, sig):
    """service.sh stop (SIGINT) or a logout (SIGTERM) during the 5-minute wait: exit 0 at once, so launchd leaves
    it stopped. A back-off value that is not a number of seconds is named and the default (300 s) is used.
    QA-Ops (CI timeout on main e2693e8): the signal is sent once the back-off has said "Next try in", not after a
    fixed sleep, so it always lands in the wait, however slow the machine. The line is the last thing before the
    wait (the alarm goes first), so nothing but builtins runs between it and `wait`: bash 5.2 can drop a trap whose
    signal lands while it parses a command substitution (`$(date)`, `$(hostname -s)`)."""
    import signal

    p, r, _ = _code_5_service(wrapper, "5m")
    out = "".join(_read_until(p, "Next try in"))
    t0 = time.monotonic()
    p.send_signal(getattr(signal, f"SIG{sig}"))
    out += _rest(p)
    assert p.returncode == 0, out
    assert time.monotonic() - t0 < 10  # not the 300 s
    assert "KAIBOT_REFUSED_BACKOFF_S='5m' is not a number of seconds: 300 is used" in out
    assert "Next try in 300 s" in out and "stopped (on request)" in out
    assert not (r / "data/live/service.pid").exists()


@pytest.mark.parametrize("sig", ["INT", "TERM"])
def test_a_stop_while_the_code_5_alarm_is_sent_is_not_lost(wrapper, sig):
    """QA-Ops (the cause of that timeout): a stop that came while a command ran in the foreground between the
    engine's exit and the wait (the alarm pinger's 2 s, the code-5 alarm itself) was dropped by bash once that
    command ended, and the service sat out the whole wait. It is remembered now: exit 0 as soon as the alarm is
    done. The alarm here is a stand-in that says when it has started and then takes 2 s, so the stop lands inside
    it every time."""
    import signal

    r, _, tmp = wrapper
    (r / "scripts/healthcheck.py").write_text(
        "import pathlib, sys, time\n"
        f"if 'fail' in sys.argv:\n    pathlib.Path({str(tmp / 'alarm')!r}).write_text('x')\n    time.sleep(2)\n"
        "elif 'watch' in sys.argv:\n    time.sleep(600)\n")
    p, r, tmp = _code_5_service(wrapper, "300")
    for _ in range(6000):
        if (tmp / "alarm").exists():
            break
        time.sleep(0.01)
    assert (tmp / "alarm").exists(), "the code-5 alarm was never sent"
    t0 = time.monotonic()
    p.send_signal(getattr(signal, f"SIG{sig}"))
    out, _ = p.communicate(timeout=60)
    assert p.returncode == 0, out
    assert time.monotonic() - t0 < 10, out  # the alarm's 2 s, not the 300 s
    assert "Next try in 300 s" in out and "stopped (on request)" in out
    assert not (r / "data/live/service.pid").exists()


def test_the_ops_docs_list_exit_code_5():
    doc = (ROOT / "docs/ops/run_engine.md").read_text()
    for code in ("1", "3", "4", "5"):
        assert f"\n| {code} | " in doc
    assert "KAIBOT_REFUSED_BACKOFF_S" in doc and "daily_path: FAILED" in doc
    assert "exit code 5" in (ROOT / "docs/ops/always_on.md").read_text()
    head = (SCRIPTS / "service_run.sh").read_text().split("set -uo pipefail")[0]
    assert "code 5" in head and "KAIBOT_REFUSED_BACKOFF_S" in head  # the exit-code list at the top of the script


def test_wrapper_pid_lock_refuses_a_second_run(wrapper):
    r, env, tmp = wrapper
    (r / "data/live").mkdir(parents=True)
    holder = subprocess.Popen(["bash", "-c", "exec -a service_run_fake sleep 20"])
    try:
        (r / "data/live/service.pid").write_text(str(holder.pid))
        x = sh(["bash", "scripts/service_run.sh", "engine"], r, env)
        assert x.returncode == 1 and "another service run" in x.stdout and not (tmp / "ran").exists()
    finally:
        holder.kill()


def test_wrapper_tells_run_engine_it_is_the_service(wrapper):
    """#613: the service's run passes its own pid (the one in its pid lock) so run_engine.sh lets it start."""
    r, env, tmp = wrapper
    (r / "scripts/run_engine.sh").write_text(
        f"#!/bin/bash\necho \"$KAIBOT_SERVICE_RUN $(cat data/live/service.pid)\" >> {tmp}/ran\n")
    sh(["bash", "scripts/service_run.sh", "engine"], r, env, check=True)
    seen, lock = (tmp / "ran").read_text().split()
    assert seen == lock and int(seen) > 0


def test_wrapper_rotates_big_logs(wrapper):
    r, env, tmp = wrapper
    logs = tmp / "logs"
    logs.mkdir()
    (logs / "engine.log").write_bytes(b"x" * 10_000_001)
    sh(["bash", "scripts/service_run.sh", "engine"], r, env, check=True)
    assert (logs / "engine.log").stat().st_size < 1000 and (logs / "engine.log.1.gz").exists()


def test_wrapper_stop_is_a_clean_ctrl_c(wrapper):
    """launchctl kill SIGINT (service.sh stop) or SIGTERM (logout, bootout) reaches the engine as SIGINT, like
    Ctrl-C in a terminal; the wrapper then exits 0, so KeepAlive does not start it again."""
    r, env, tmp = wrapper
    (r / "scripts/run_engine.sh").write_text(
        f"#!/bin/bash\ntrap 'echo got-int >> {tmp}/ran; exit 0' INT\necho up >> {tmp}/ran\n"
        "while :; do sleep 0.2; done\n")
    for sig in ("INT", "TERM"):
        (tmp / "ran").unlink(missing_ok=True)
        p = subprocess.Popen(["bash", "scripts/service_run.sh", "engine"], cwd=r, env=env, stdout=subprocess.PIPE,
                             text=True)
        for _ in range(50):
            if (tmp / "ran").exists():
                break
            time.sleep(0.1)
        time.sleep(0.5)
        p.send_signal(getattr(__import__("signal"), f"SIG{sig}"))
        out, _ = p.communicate(timeout=30)
        assert p.returncode == 0, out
        assert "got-int" in (tmp / "ran").read_text() and "stopped (on request)" in out


# ---------------------------------------------------------------- the ticker-check worker beside the engine
# scripts/run_ticker_checks.py (M5 S68, #96) answers the dashboard's ticker checks. The service runs it beside the
# engine, restarts it when it dies, stops it with the engine, and never lets it change the engine's exit code.
FAKE_WORKER = """#!/bin/bash
# stand-in for .venv/bin/python: "-c" is the import check, "-m certifi" fails, the alarm pinger is off, anything
# else is the worker
case "$1" in -c) exit "${{FAKE_IMPORT:-0}}";; -m) exit 1;; scripts/healthcheck.py) exit 0;; esac
echo "worker $$ $*" >> {tmp}/worker.calls
trap 'echo worker-term >> {tmp}/worker.calls; exit 0' TERM
[ -n "${{FAKE_WORKER_EXIT:-}}" ] && exit "$FAKE_WORKER_EXIT"
while :; do sleep 0.2; done
"""


def engine_for(tmp, seconds, code=0):
    # as run_engine.sh: the commit this start runs goes to data/live/running_commit (#682)
    return (f"#!/bin/bash\ntrap 'echo got-int >> {tmp}/ran; exit 0' INT\necho up >> {tmp}/ran\n"
            "mkdir -p data/live; git rev-parse HEAD > data/live/running_commit\n"
            f"for _ in $(seq {seconds * 5}); do sleep 0.2; done\nexit {code}\n")


@pytest.fixture
def checks(wrapper):
    r, env, tmp = wrapper
    (r / ".venv/bin").mkdir(parents=True)
    (r / ".venv/bin/python").write_text(FAKE_WORKER.format(tmp=tmp))
    (r / ".venv/bin/python").chmod(0o755)
    return r, {**env, "KAIBOT_CHECKS_START_DELAY": "0"}, tmp


def worker_starts(tmp):
    f = tmp / "worker.calls"
    return [ln for ln in f.read_text().splitlines() if ln.startswith("worker ")] if f.exists() else []


def gone(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True).stdout.strip() \
        .startswith("Z")


def test_the_worker_runs_beside_the_engine_and_stops_with_it(checks):
    r, env, tmp = checks
    (r / "scripts/run_engine.sh").write_text(engine_for(tmp, 4))
    x = sh(["bash", "scripts/service_run.sh", "engine"], r, env)
    assert x.returncode == 0 and "ticker-check worker on" in x.stdout
    (start,) = worker_starts(tmp)
    data = f"{tmp}/Projects/kaibot-secrets/data.env"
    assert start.endswith(f"-u scripts/run_ticker_checks.py --env {env['KAIBOT_ENV_FILE']} --data-env {data}")
    assert gone(int(start.split()[1])) and "worker-term" in (tmp / "worker.calls").read_text()
    assert not (r / "data/live/ticker_checks.pid").exists()
    log = (tmp / "logs/ticker_checks.log").read_text()
    assert "ticker-check worker started" in log and "stopped with the engine" in log


def test_the_worker_is_restarted_when_it_dies_and_never_fails_the_engine(checks):
    r, env, tmp = checks
    (r / "scripts/run_engine.sh").write_text(engine_for(tmp, 9))
    x = sh(["bash", "scripts/service_run.sh", "engine"], r, {**env, "FAKE_WORKER_EXIT": "3"})
    assert x.returncode == 0 and "engine exited cleanly" in x.stdout  # the engine's code, not the worker's
    assert len(worker_starts(tmp)) >= 2  # restarted after 5 s, then 10 s
    assert "worker exited with code 3: starting it again in 5 s" in (tmp / "logs/ticker_checks.log").read_text()
    (r / "scripts/run_engine.sh").write_text(engine_for(tmp, 1, code=4))
    x = sh(["bash", "scripts/service_run.sh", "engine"], r, {**env, "FAKE_WORKER_EXIT": "0"})
    assert x.returncode == 1 and "exited with code 4" in x.stdout
    assert "refused its journal" in x.stdout  # QA-Ops (#511): code 4 is named, not just "restarting"


def test_a_worker_that_cannot_start_leaves_the_engine_alone(checks):
    r, env, tmp = checks
    (r / "scripts/run_engine.sh").write_text(engine_for(tmp, 2))
    x = sh(["bash", "scripts/service_run.sh", "engine"], r, {**env, "FAKE_IMPORT": "1"})  # .venv without the deps
    assert x.returncode == 0 and (tmp / "ran").read_text().split() == ["up"] and worker_starts(tmp) == []
    assert (tmp / "logs/ticker_checks.log").read_text().count("cannot start the ticker-check worker yet") == 1


def test_a_stop_reaches_the_worker_too(checks):
    r, env, tmp = checks
    (r / "scripts/run_engine.sh").write_text(engine_for(tmp, 60))
    p = subprocess.Popen(["bash", "scripts/service_run.sh", "engine"], cwd=r, env=env, stdout=subprocess.PIPE,
                         text=True)
    try:
        for _ in range(100):
            if worker_starts(tmp) and (r / "data/live/ticker_checks.pid").exists():
                break
            time.sleep(0.1)
        pid = int((r / "data/live/ticker_checks.pid").read_text())
        p.send_signal(__import__("signal").SIGINT)
        out, _ = p.communicate(timeout=40)
    finally:
        p.kill()
    assert p.returncode == 0 and "stopped (on request)" in out
    assert gone(pid) and "worker-term" in (tmp / "worker.calls").read_text()


def test_a_worker_left_by_a_killed_service_run_is_stopped_first(checks):
    r, env, tmp = checks
    (r / "data/live").mkdir(parents=True)
    orphan = subprocess.Popen(["bash", "-c", "exec -a 'python scripts/run_ticker_checks.py' sleep 30"])
    try:
        (r / "data/live/ticker_checks.pid").write_text(str(orphan.pid))
        (r / "scripts/run_engine.sh").write_text(engine_for(tmp, 2))
        sh(["bash", "scripts/service_run.sh", "engine"], r, env, check=True)
        assert orphan.wait(timeout=5) != 0  # killed, so two workers never answer the same rows
        assert len(worker_starts(tmp)) == 1
    finally:
        orphan.kill()


def test_the_worker_can_be_turned_off(checks):
    r, env, tmp = checks
    (r / "scripts/run_engine.sh").write_text(engine_for(tmp, 1))
    x = sh(["bash", "scripts/service_run.sh", "engine"], r, {**env, "KAIBOT_TICKER_CHECKS": "0"}, check=True)
    assert "ticker-check worker" not in x.stdout and worker_starts(tmp) == []


def test_the_switch_is_read_from_the_env_file_too(checks):
    """KAIBOT_TICKER_CHECKS=0 in the .env (the settings file every other setting is read from) turns the worker off,
    although install_service.sh writes only KAIBOT_ENV_FILE, KAIBOT_LOG_DIR and KAIBOT_SERVICE_MODE into the plist.
    The service's own environment, when set, wins (as the engine's config reads its settings)."""
    r, env, tmp = checks
    (r / "scripts/run_engine.sh").write_text(engine_for(tmp, 1))
    env_file = tmp / "service.env"
    env_file.write_text("ALPACA_KEY=x\nKAIBOT_TICKER_CHECKS=0  # the worker off\n")
    x = sh(["bash", "scripts/service_run.sh", "engine"], r, {**env, "KAIBOT_ENV_FILE": str(env_file)}, check=True)
    assert "ticker-check worker on" not in x.stdout and worker_starts(tmp) == []
    env_file.write_text('KAIBOT_TICKER_CHECKS="0"\n')
    (r / "scripts/run_engine.sh").write_text(engine_for(tmp, 1))
    x = sh(["bash", "scripts/service_run.sh", "engine"], r,
           {**env, "KAIBOT_ENV_FILE": str(env_file), "KAIBOT_TICKER_CHECKS": "1"}, check=True)
    assert "ticker-check worker on" in x.stdout  # the process environment wins over the file


@pytest.mark.parametrize("value", ["yes", "off", "", "2"])
def test_a_switch_value_that_is_not_0_or_1_keeps_the_default_and_says_so(checks, value):
    """Only an explicit 0 or 1 counts. Anything else used to turn the worker off (the test was `= 1`); now it keeps
    the default (on) and the log says the value was not understood."""
    r, env, tmp = checks
    (r / "scripts/run_engine.sh").write_text(engine_for(tmp, 1))
    x = sh(["bash", "scripts/service_run.sh", "engine"], r, {**env, "KAIBOT_TICKER_CHECKS": value}, check=True)
    assert "ticker-check worker on" in x.stdout
    if value:
        assert "KAIBOT_TICKER_CHECKS" in x.stdout and "not 0 or 1" in x.stdout


def test_the_worker_log_is_rotated(checks):
    r, env, tmp = checks
    (r / "scripts/run_engine.sh").write_text(engine_for(tmp, 1))
    (tmp / "logs").mkdir()
    (tmp / "logs/ticker_checks.log").write_bytes(b"x" * 10_000_001)
    sh(["bash", "scripts/service_run.sh", "engine"], r, env, check=True)
    assert (tmp / "logs/ticker_checks.log").stat().st_size < 10_000 and (tmp / "logs/ticker_checks.log.1.gz").exists()


@pytest.fixture
def fake_docker(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "docker").write_text(
        f'#!/bin/bash\necho "$@" >> {tmp_path}/docker.calls\n'
        'case "$1" in\n'
        '  ps) printf "c1\\tkaibot-ticker-checks\\t\\n";;\n'
        f'  run) trap "echo container-term >> {tmp_path}/docker.calls; exit 0" TERM; while :; do sleep 0.2; done;;\n'
        'esac\nexit 0\n')
    (bin_ / "docker").chmod(0o755)
    return bin_


def test_the_worker_and_the_relay_run_only_the_engines_commit(checks):
    """#682: the engine's start recorded another commit than the checkout's HEAD (the tree moved under it): neither the
    worker nor the relay starts on that tree; each says why once, and the engine is not affected."""
    r, env, tmp = checks
    (r / "scripts/run_engine.sh").write_text(
        engine_for(tmp, 3).replace("git rev-parse HEAD >", "echo 0123456789abcdef0123456789abcdef01234567 >"))
    x = sh(["bash", "scripts/service_run.sh", "engine"], r,
           {**env, "KAIBOT_PRICE_STREAM": "on", "KAIBOT_STREAM_START_DELAY": "0"})
    assert x.returncode == 0 and "engine exited cleanly" in x.stdout and worker_starts(tmp) == []
    for log in ("ticker_checks.log", "price_stream.log"):
        text = (tmp / "logs" / log).read_text()
        assert text.count("not at the engine's 0123456: not starting on code the engine does not run") == 1, text


def test_the_worker_names_the_commit_it_runs(checks):
    r, env, tmp = checks
    (r / "scripts/run_engine.sh").write_text(engine_for(tmp, 2))
    sh(["bash", "scripts/service_run.sh", "engine"], r, env, check=True)
    short = sh(["git", "rev-parse", "--short", "HEAD"], r, check=True).stdout.strip()
    assert "ticker-check worker started (pid" in (tmp / "logs/ticker_checks.log").read_text()
    assert f", code {short})" in (tmp / "logs/ticker_checks.log").read_text()
    assert (r / "data/live/running_commit").read_text().strip()


def test_in_docker_mode_without_a_venv_the_worker_runs_in_the_engine_image(wrapper, fake_docker):
    r, env, tmp = wrapper
    (r / "scripts/run_engine.sh").write_text(engine_for(tmp, 4))
    env = {**env, "KAIBOT_SERVICE_MODE": "docker", "KAIBOT_CHECKS_START_DELAY": "0",
           "PATH": f"{fake_docker}:{env['PATH']}"}
    x = sh(["bash", "scripts/service_run.sh", "engine"], r, env)
    assert x.returncode == 0, x.stdout
    calls = (tmp / "docker.calls").read_text().splitlines()
    (run,) = [c for c in calls if c.startswith("run ")]
    assert run.startswith("run --rm --init --name kaibot-ticker-checks ")
    assert f"-v {env['KAIBOT_ENV_FILE']}:/secrets/.env:ro" in run and run.endswith(
        "kaibot-engine python -u scripts/run_ticker_checks.py --env /secrets/.env --data-env /secrets/data.env")
    assert f"-v {tmp}/Projects/kaibot-secrets/data.env:/secrets/data.env:ro" in run
    assert "scripts/run_ticker_checks.py:/app/scripts/run_ticker_checks.py:ro" in run
    assert "container-term" in calls and "rm -f kaibot-ticker-checks" in calls


def test_the_worker_container_is_not_an_engine(tmp_path, fake_docker):
    """engine_guard.sh must not see the worker's container (kaibot-engine image) as a second engine."""
    x = sh(["bash", str(SCRIPTS / "engine_guard.sh"), "--list"], tmp_path,
           {"PATH": f"{fake_docker}:{os.environ['PATH']}"})
    assert x.returncode == 0 and x.stdout == ""


def test_service_status_says_whether_the_worker_runs(tmp_path):
    r = tmp_path / "kaibot"
    (r / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPTS / "service.sh", r / "scripts")
    (r / "scripts/engine_guard.sh").write_text("#!/bin/bash\nexit 0\n")
    (r / "scripts/release.sh").write_text("#!/bin/bash\necho 'release: main'\n")
    (r / "scripts/engine_guard.sh").chmod(0o755)
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "launchctl").write_text('#!/bin/bash\nprintf "\\tstate = running\\n\\tpid = 42\\n"\n')
    (bin_ / "launchctl").chmod(0o755)
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "ticker_checks.log").write_text('{"symbol": "NBIS", "status": "accepted"}\n')
    (logs / "engine.log").write_text("engine up\n")
    env = {"HOME": str(tmp_path), "KAIBOT_LOG_DIR": str(logs), "PATH": f"{bin_}:{os.environ['PATH']}"}
    x = sh(["bash", "scripts/service.sh", "status"], r, env, check=True)
    assert "ticker checks: not running" in x.stdout
    (r / "data/live").mkdir(parents=True)
    w = subprocess.Popen(["bash", "-c", "exec -a 'python -u scripts/run_ticker_checks.py' sleep 30"])
    try:
        (r / "data/live/ticker_checks.pid").write_text(str(w.pid))
        x = sh(["bash", "scripts/service.sh", "status"], r, env, check=True)
    finally:
        w.kill()
    assert f"ticker checks: running (pid {w.pid}" in x.stdout and '"NBIS"' in x.stdout


# ---------------------------------------------------------------- the price relay beside the engine
# scripts/price_stream.py (20 Nov 2026) streams the held stocks' prices to the dashboard. Off unless
# KAIBOT_PRICE_STREAM=on; when on it runs like the ticker-check worker, and nothing it does reaches the engine.
@pytest.fixture
def relay_env(checks):
    r, env, tmp = checks
    return r, {**env, "KAIBOT_TICKER_CHECKS": "0", "KAIBOT_STREAM_START_DELAY": "0"}, tmp


def relay_starts(tmp):
    return [ln for ln in worker_starts(tmp) if "price_stream.py" in ln]


def test_the_relay_is_off_by_default(relay_env):
    r, env, tmp = relay_env
    (r / "scripts/run_engine.sh").write_text(engine_for(tmp, 1))
    x = sh(["bash", "scripts/service_run.sh", "engine"], r, env, check=True)
    assert "price relay" not in x.stdout and relay_starts(tmp) == []


@pytest.mark.parametrize("value", ["off", "0", "maybe"])
def test_only_on_turns_the_relay_on(relay_env, value):
    r, env, tmp = relay_env
    (r / "scripts/run_engine.sh").write_text(engine_for(tmp, 1))
    x = sh(["bash", "scripts/service_run.sh", "engine"], r, {**env, "KAIBOT_PRICE_STREAM": value}, check=True)
    assert "price relay on" not in x.stdout and relay_starts(tmp) == []


def test_the_relay_runs_beside_the_engine_from_the_env_file_and_stops_with_it(relay_env):
    r, env, tmp = relay_env
    env_file = tmp / "service.env"
    env_file.write_text("KAIBOT_PRICE_STREAM=on  # the dashboard's streamed prices\n")
    (r / "scripts/run_engine.sh").write_text(engine_for(tmp, 4))
    x = sh(["bash", "scripts/service_run.sh", "engine"], r, {**env, "KAIBOT_ENV_FILE": str(env_file)})
    assert x.returncode == 0 and "price relay on" in x.stdout
    (start,) = relay_starts(tmp)
    sec = f"{tmp}/Projects/kaibot-secrets"
    # #712: the relay itself decides (with data.env it never opens --env; tests/unit/test_helper_session.py)
    assert start.endswith(f"-u scripts/price_stream.py --env {env_file} --helper-env {sec}/helper.env "
                          f"--data-env {sec}/data.env")
    assert gone(int(start.split()[1])) and "worker-term" in (tmp / "worker.calls").read_text()
    assert not (r / "data/live/price_stream.pid").exists()
    log = (tmp / "logs/price_stream.log").read_text()
    assert "price relay started" in log and "price relay stopped with the engine" in log


def test_a_relay_that_dies_or_cannot_start_never_touches_the_engine(relay_env):
    r, env, tmp = relay_env
    on = {**env, "KAIBOT_PRICE_STREAM": "on"}
    (r / "scripts/run_engine.sh").write_text(engine_for(tmp, 7))
    x = sh(["bash", "scripts/service_run.sh", "engine"], r, {**on, "FAKE_WORKER_EXIT": "3"})
    assert x.returncode == 0 and "engine exited cleanly" in x.stdout  # the engine's code, not the relay's
    assert (tmp / "ran").read_text().split() == ["up"]  # one engine run: never stopped or restarted by the relay
    assert len(relay_starts(tmp)) >= 2
    assert "price relay exited with code 3" in (tmp / "logs/price_stream.log").read_text()
    (tmp / "ran").unlink()
    (tmp / "logs/price_stream.log").unlink()
    nopy = tmp / "nopy"
    nopy.mkdir()
    (nopy / "python3").write_text("#!/bin/bash\nexit 1\n")  # no usable Python 3.11+ on PATH either
    (nopy / "python3").chmod(0o755)
    (r / "scripts/run_engine.sh").write_text(engine_for(tmp, 2))
    x = sh(["bash", "scripts/service_run.sh", "engine"], r,
           {**on, "FAKE_IMPORT": "1", "PATH": f"{nopy}:{env['PATH']}"})
    assert x.returncode == 0 and (tmp / "ran").read_text().split() == ["up"]
    assert (tmp / "logs/price_stream.log").read_text().count("cannot start the price relay yet") == 1


def test_without_the_data_key_the_worker_and_the_relay_still_start(relay_env):
    """Product (26 Nov 2026): no data.env yet: nothing stops. Both start as before #712 (the scripts warn and use
    their old keys; tests/unit/test_helper_session.py, test_secret_files.py)."""
    r, env, tmp = relay_env
    (tmp / "Projects/kaibot-secrets/data.env").unlink()
    (r / "scripts/run_engine.sh").write_text(engine_for(tmp, 3))
    x = sh(["bash", "scripts/service_run.sh", "engine"], r,
           {**env, "KAIBOT_TICKER_CHECKS": "1", "KAIBOT_PRICE_STREAM": "on"})
    assert x.returncode == 0 and "engine exited cleanly" in x.stdout
    starts = worker_starts(tmp)
    assert [ln for ln in starts if "run_ticker_checks.py" in ln] and relay_starts(tmp)


def _docker_relay_run(relay_env, fake_docker, data_env: bool):
    r, env, tmp = relay_env
    if not data_env:
        (tmp / "Projects/kaibot-secrets/data.env").unlink()
    (r / ".venv/bin/python").unlink()  # Docker Mac: no .venv
    nopy = tmp / "nopy"
    nopy.mkdir()
    (nopy / "python3").write_text("#!/bin/bash\nexit 1\n")
    (nopy / "python3").chmod(0o755)
    (r / "scripts/run_engine.sh").write_text(engine_for(tmp, 4))
    env = {**env, "KAIBOT_SERVICE_MODE": "docker", "KAIBOT_PRICE_STREAM": "on",
           "PATH": f"{nopy}:{fake_docker}:{env['PATH']}"}
    x = sh(["bash", "scripts/service_run.sh", "engine"], r, env)
    assert x.returncode == 0, x.stdout
    (run,) = [c for c in (tmp / "docker.calls").read_text().splitlines() if c.startswith("run ")]
    return run, env, tmp


def test_with_data_env_the_relay_container_never_gets_the_engines_env(relay_env, fake_docker):
    run, env, tmp = _docker_relay_run(relay_env, fake_docker, data_env=True)
    assert "kaibot-price-stream" in run and ":/secrets/.env:ro" not in run and env["KAIBOT_ENV_FILE"] not in run
    sec = f"{tmp}/Projects/kaibot-secrets"
    assert f"-v {sec}/data.env:/secrets/data.env:ro" in run and f"-v {sec}/helper.env:/secrets/helper.env:ro" in run


def test_with_data_env_but_no_helper_env_the_box_still_gets_no_engine_env(relay_env, fake_docker):
    (relay_env[2] / "Projects/kaibot-secrets/helper.env").unlink()
    run, env, _ = _docker_relay_run(relay_env, fake_docker, data_env=True)
    assert ":/secrets/.env:ro" not in run and env["KAIBOT_ENV_FILE"] not in run


def test_a_dangling_data_env_link_never_brings_the_engines_env_into_the_box(relay_env, fake_docker):
    sec = relay_env[2] / "Projects/kaibot-secrets"
    (sec / "data.env").unlink()
    (sec / "data.env").symlink_to(sec / "gone.env")
    run, env, _ = _docker_relay_run(relay_env, fake_docker, data_env=True)
    assert ":/secrets/.env:ro" not in run and env["KAIBOT_ENV_FILE"] not in run
    assert "--tmpfs /secrets/data.env" in run  # the relay then refuses: not a regular file


def test_without_data_env_the_relay_container_is_as_before(relay_env, fake_docker):
    run, env, tmp = _docker_relay_run(relay_env, fake_docker, data_env=False)
    assert "/secrets/data.env:ro" not in run  # a missing file is never mounted (it would become a folder)
    assert f"-v {tmp}/Projects/kaibot-secrets/helper.env:/secrets/helper.env:ro" in run


def test_a_stop_reaches_the_relay_too(relay_env):
    r, env, tmp = relay_env
    (r / "scripts/run_engine.sh").write_text(engine_for(tmp, 60))
    p = subprocess.Popen(["bash", "scripts/service_run.sh", "engine"], cwd=r, env={**env, "KAIBOT_PRICE_STREAM": "on"},
                         stdout=subprocess.PIPE, text=True)
    try:
        for _ in range(100):
            if relay_starts(tmp) and (r / "data/live/price_stream.pid").exists():
                break
            time.sleep(0.1)
        pid = int((r / "data/live/price_stream.pid").read_text())
        p.send_signal(__import__("signal").SIGINT)
        out, _ = p.communicate(timeout=40)
    finally:
        p.kill()
    assert p.returncode == 0 and "stopped (on request)" in out
    assert gone(pid) and "worker-term" in (tmp / "worker.calls").read_text()


# ---------------------------------------------------------------- keep the Mac awake (24-25 Nov 2026 incident)
# The Mac mini fell asleep at 23:45 Berlin and the engine's heartbeat stopped for about 17 hours. service_run.sh now
# holds `caffeinate -i -s -w <engine pid>` for the whole engine run, and `run_engine.sh --check` reports the Mac's sleep
# settings (pmset -g), FAILED when system sleep on AC power is not 0. Linux CI: fake caffeinate and pmset on PATH.
FAKE_CAFFEINATE = """#!/bin/bash
echo "$$ $*" >> {tmp}/caffeinate.calls
trap 'echo caffeinate-term >> {tmp}/caffeinate.calls; exit 0' TERM
while [ "$1" ]; do [ "$1" = -w ] && w="$2"; shift; done
[ -n "$w" ] || exit 64
while kill -0 "$w" 2>/dev/null; do sleep 0.1; done
echo caffeinate-done >> {tmp}/caffeinate.calls
"""


@pytest.fixture
def awake(wrapper):
    r, env, tmp = wrapper
    bin_ = tmp / "awake-bin"
    bin_.mkdir()
    (bin_ / "caffeinate").write_text(FAKE_CAFFEINATE.format(tmp=tmp))
    (bin_ / "caffeinate").chmod(0o755)
    (r / "scripts/run_engine.sh").write_text(
        f"#!/bin/bash\necho $$ > {tmp}/engine.pid\ntrap 'echo got-int >> {tmp}/ran; exit 0' INT\n"
        f"echo up >> {tmp}/ran\nfor _ in $(seq ${{FAKE_SECONDS:-2}}0); do sleep 0.1; done\nexit ${{FAKE_CODE:-0}}\n")
    return r, {**env, "KAIBOT_TICKER_CHECKS": "0", "PATH": f"{bin_}:{env['PATH']}"}, tmp


def caffeinate_calls(tmp):
    return (tmp / "caffeinate.calls").read_text().splitlines()


def _wait_gone(pid, limit_s=5.0):
    end = time.time() + limit_s
    while time.time() < end and not gone(pid):
        time.sleep(0.1)
    return gone(pid)


@pytest.mark.parametrize("code", [0, 3])
def test_the_service_holds_a_system_sleep_assertion_for_the_engine_and_lets_go_when_it_ends(awake, code):
    """-s (PreventSystemSleep, honoured on AC power: a Mac mini always is) and -i (idle sleep, on any power), -w on
    the engine's own process (the run_engine.sh the service started), ended when the engine exits, cleanly or not."""
    r, env, tmp = awake
    x = sh(["bash", "scripts/service_run.sh", "engine"], r, {**env, "FAKE_CODE": str(code)})
    assert x.returncode == (0 if code == 0 else 1), x.stdout
    engine = (tmp / "engine.pid").read_text().strip()
    first = caffeinate_calls(tmp)[0].split()
    assert first[1:] == ["-i", "-s", "-w", engine]
    assert f"keeping the Mac awake while the engine runs (caffeinate -i -s -w {engine}, pid {first[0]})" in x.stdout
    assert _wait_gone(int(first[0]))  # never outlives the engine run


def test_a_stop_ends_the_assertion_with_the_engine(awake):
    r, env, tmp = awake
    p = subprocess.Popen(["bash", "scripts/service_run.sh", "engine"], cwd=r, stdout=subprocess.PIPE, text=True,
                         env={**{k: v for k, v in os.environ.items() if not k.startswith("KAIBOT_")}, **env,
                              "FAKE_SECONDS": "30"})
    for _ in range(100):
        if (tmp / "caffeinate.calls").exists() and (tmp / "engine.pid").exists():
            break
        time.sleep(0.1)
    time.sleep(0.5)
    pid = int(caffeinate_calls(tmp)[0].split()[0])
    assert not gone(pid)  # held while the engine runs
    p.send_signal(__import__("signal").SIGINT)
    out, _ = p.communicate(timeout=30)
    assert p.returncode == 0 and "stopped (on request)" in out, out
    assert _wait_gone(pid) and "got-int" in (tmp / "ran").read_text()


def test_without_caffeinate_the_service_says_so_once_and_runs_the_engine_as_before(wrapper):
    r, env, tmp = wrapper
    if shutil.which("caffeinate", path=env["PATH"]):
        pytest.skip("a real caffeinate is on PATH (macOS)")
    x = sh(["bash", "scripts/service_run.sh", "engine"], r, {**env, "KAIBOT_TICKER_CHECKS": "0"})
    assert x.returncode == 0 and (tmp / "ran").read_text().count("ran") == 1
    assert x.stdout.count("no caffeinate on this machine") == 1


PMSET = """#!/bin/bash
echo "$*" >> {tmp}/pmset.calls
case "$*" in
  "-g custom")
    [ -n "${{FAKE_NO_AC:-}}" ] && {{ echo " sleep                ${{FAKE_SLEEP:-0}}"; exit 0; }}
    printf 'AC Power:\\n Sleep On Power Button 1\\n disksleep            0\\n sleep                %s\\n' \\
      "${{FAKE_AC_SLEEP:-0}}"
    printf 'UPS Power:\\n sleep                10\\n' ;;
  -g)
    printf 'System-wide power settings:\\nCurrently in use:\\n standby              0\\n'
    printf ' sleep                %s (sleep prevented by caffeinate, powerd)\\n' "${{FAKE_SLEEP:-0}}"
    printf ' disksleep            0\\n displaysleep         10\\n' ;;
esac
"""


@pytest.fixture
def pmset(repo):
    r, env, tmp = repo
    bin_ = Path(env["PATH"].split(":")[0])
    (bin_ / "pmset").write_text(PMSET.format(tmp=tmp))
    (bin_ / "pmset").chmod(0o755)
    return r, env, tmp


def test_check_reports_the_macs_sleep_settings_and_passes_with_sleep_0_on_ac(pmset):
    r, env, tmp = pmset
    x = sh(["bash", "scripts/run_engine.sh", "--docker", "--check"], r, {**env, "FAKE_SLEEP": "0"})
    assert x.returncode == 0, x.stdout + x.stderr
    assert "mac_sleep (pmset -g):" in x.stdout and "  sleep                0 (sleep prevented" in x.stdout
    assert "  displaysleep         10" in x.stdout and "system sleep on AC power: 0 (never): ok" in x.stdout
    assert "FAILED" not in x.stdout
    run = [c for c in (tmp / "docker.calls").read_text().splitlines() if c.startswith("run --rm --name kaibot-engine")]
    assert run[-1].endswith("sh /app/scripts/engine_entry.sh --check")  # the engine's own check ran first, as before


@pytest.mark.parametrize("ac, now", [("10", "0"), ("1", "1")])
def test_check_fails_when_system_sleep_on_ac_is_not_0(pmset, ac, now):
    """The AC profile decides (pmset -g custom), not what happens to be in use: a Mac mini runs on AC."""
    r, env, tmp = pmset
    x = sh(["bash", "scripts/run_engine.sh", "--docker", "--check"], r, {**env, "FAKE_AC_SLEEP": ac, "FAKE_SLEEP": now})
    assert x.returncode == 1
    assert f"FAILED: system sleep on AC power is {ac}, not 0" in x.stdout
    assert "sudo pmset -c sleep 0 disksleep 0" in x.stdout
    assert x.stdout.rstrip().endswith("then run --check again.")


def test_check_reads_the_current_settings_on_a_mac_without_an_ac_section(pmset):
    r, env, tmp = pmset
    ok = sh(["bash", "scripts/run_engine.sh", "--docker", "--check"], r, {**env, "FAKE_NO_AC": "1", "FAKE_SLEEP": "0"})
    assert ok.returncode == 0 and "0 (never): ok" in ok.stdout
    bad = sh(["bash", "scripts/run_engine.sh", "--docker", "--check"], r, {**env, "FAKE_NO_AC": "1", "FAKE_SLEEP": "5"})
    assert bad.returncode == 1 and "is 5, not 0" in bad.stdout


def test_without_pmset_the_check_skips_the_sleep_report(repo):
    r, env, tmp = repo
    if shutil.which("pmset", path=env["PATH"]):
        pytest.skip("a real pmset is on PATH (macOS)")
    x = sh(["bash", "scripts/run_engine.sh", "--docker", "--check"], r, env, check=True)
    assert "no pmset (not a Mac): skipped" in x.stdout


def test_a_normal_start_never_asks_pmset(pmset):
    r, env, tmp = pmset
    sh(["bash", "scripts/run_engine.sh", "--docker"], r, {**env, "FAKE_AC_SLEEP": "10"}, check=True)
    assert not (tmp / "pmset.calls").exists()
