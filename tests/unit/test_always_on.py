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
    assert p["ProgramArguments"][-1] == "daily" and p["RunAtLoad"] is False and "KeepAlive" not in p
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
    for cmd in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "one"], ["tag", "v1"]):
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
    return sh(["git", "rev-parse", "--short", ref], r, check=True).stdout.strip()


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
    assert run and run[-1].endswith("python -m kaibot.engine")


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
