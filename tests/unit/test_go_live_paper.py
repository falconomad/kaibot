"""scripts/go_live_paper.sh and .py: the one go-live command for the trend_momentum paper experiment. Fakes only: git,
launchctl, the Alpaca shorting script, the engine guard, the check and the service installer are stand-ins that record
their calls; an "engine started by hand" is a real child process."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[2]
NY = ZoneInfo("America/New_York")
SECRET = "sk-do-not-print-0123456789"
CHECK = {"multi_day": {"KAIBOT_MULTIDAY": "on", "allow_overnight": True, "rules_loaded": ["trend_momentum"],
                       "level": "balanced", "overnight_cap_pct": {"trend_momentum": 60.0},
                       "experiments": [{"strategy": "trend_momentum", "max_exposure_pct": 60.0,
                                        "retire_drawdown_pct": 21.0}]}}


def load():
    spec = importlib.util.spec_from_file_location("go_live_paper", ROOT / "scripts" / "go_live_paper.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _exe(p: Path, body: str) -> None:
    p.write_text("#!/bin/bash\n" + body + "\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "kaibot"
    (r / "scripts").mkdir(parents=True)
    for n in ("go_live_paper.sh", "go_live_paper.py"):
        shutil.copy(ROOT / "scripts" / n, r / "scripts" / n)
    (r / "src").symlink_to(ROOT / "src")
    calls = tmp_path / "calls"
    rec = f'echo "$(basename "$0") $*" >> {calls}'
    _exe(r / "scripts" / "alpaca_no_shorting.sh", f'{rec}\nexit "${{FAKE_SHORT:-0}}"')
    _exe(r / "scripts" / "install_service.sh", rec)
    _exe(r / "scripts" / "service.sh", rec)
    _exe(r / "scripts" / "engine_guard.sh",
         f'f={tmp_path}/engines; live=""\n'
         'if [ -f "$f" ]; then while read -r k p; do kill -0 "$p" 2>/dev/null && live="$live$k $p\n"; done < "$f"; fi\n'
         '[ "$1" = --list ] && printf "$live"\n[ -z "$live" ]')
    report = json.dumps(CHECK, indent=1)
    (tmp_path / "report.json").write_text(report + "\n")
    _exe(r / "scripts" / "run_engine.sh",
         f'{rec}\necho "env MULTIDAY=${{KAIBOT_MULTIDAY:-unset}} RELEASE=${{KAIBOT_RELEASE:-unset}}" >> {calls}\n'
         f'cat "${{FAKE_REPORT:-{tmp_path}/report.json}}"\necho "${{FAKE_CHECK:-ALL GOOD}}"\n'
         'exit "${FAKE_CHECK_CODE:-0}"')
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    _exe(bin_ / "git", f'{rec}\n[ "$1" = symbolic-ref ] && exit "${{FAKE_DETACHED:-0}}"\n'
         '[ "$1" = show ] && { cat "$FAKE_TAG_PROFILES" 2>/dev/null || exit 128; }\nexit 0')
    _exe(bin_ / "launchctl", f'{rec}\n'
         '[ "$1" = print ] && [ -z "$FAKE_SERVICE_LOADED$FAKE_SERVICE_RUNNING" ] && exit 113\n'
         '[ -n "$FAKE_SERVICE_RUNNING" ] && echo "state = running"; exit 0')
    _exe(bin_ / "python3", f'exec "{sys.executable}" "$@"')  # (a symlink would lose a virtualenv)
    env_file = tmp_path / "secret.env"
    env_file.write_text(f"ALPACA_API_KEY=PK123\nALPACA_SECRET={SECRET}\nKAIBOT_MULTIDAY=off\nFRED_API_KEY=x\n")
    env_file.chmod(0o600)
    env = {"PATH": f"{bin_}:{os.environ['PATH']}", "HOME": str(tmp_path), "KAIBOT_ENV_FILE": str(env_file),
           "GO_LIVE_NOW": "6 1000", "GO_LIVE_STOP_WAIT": "3", "GO_LIVE_NOW_ISO": "2026-11-14T10:00:00-05:00"}
    return r, env, env_file, calls, tmp_path


def run(r, env, **extra):
    return subprocess.run(["bash", "scripts/go_live_paper.sh"], cwd=r, env={**env, **extra}, capture_output=True,
                          text=True, timeout=60)


def calls_of(calls: Path) -> list[str]:
    return calls.read_text().split("\n")[:-1] if calls.exists() else []


def test_the_happy_path_in_order_with_a_plain_summary_and_no_secret_printed(repo):
    r, env, env_file, calls, tmp = repo
    x = run(r, env)
    assert x.returncode == 0, x.stdout + x.stderr
    order = [c.split()[0] for c in calls_of(calls) if not c.startswith(("launchctl", "env ", "git symbolic-ref"))]
    assert order == ["git", "alpaca_no_shorting.sh", "run_engine.sh", "install_service.sh"]
    assert "git pull --ff-only -q" in calls_of(calls)
    lines = env_file.read_text().splitlines()
    assert lines.count("KAIBOT_MULTIDAY=on") == 1 and "KAIBOT_MULTIDAY=off" not in lines
    assert lines[:2] == ["ALPACA_API_KEY=PK123", f"ALPACA_SECRET={SECRET}"]  # other lines untouched
    assert len(list(tmp.glob("secret.env.bak.*"))) == 1 and stat.S_IMODE(env_file.stat().st_mode) == 0o600
    out = x.stdout + x.stderr
    assert SECRET not in out and "PK123" not in out
    for want in ("ALL GOOD", "Shorting:         off", "Multi-day:        on", "Overnight:        allowed",
                 "Balanced", "60.0%", "falls 21.0% from its peak", "Tuesday 17 Nov after the close",
                 "Wednesday 18 Nov, in the closing auction", "./scripts/service.sh hold"):
        assert want in out, want


def test_running_it_again_changes_nothing_more(repo):
    r, env, env_file, calls, tmp = repo
    assert run(r, env).returncode == 0
    before = env_file.read_text()
    x = run(r, env)
    assert x.returncode == 0 and env_file.read_text() == before and "unchanged" in x.stdout
    assert len(list(tmp.glob("secret.env.bak.*"))) == 1  # no new backup when nothing changes


def test_it_refuses_while_the_market_is_open(repo):
    r, env, env_file, calls, _ = repo
    x = run(r, env, GO_LIVE_NOW="3 1200")
    assert x.returncode == 1 and "market is open" in x.stdout and calls_of(calls) == []
    assert "KAIBOT_MULTIDAY=off" in env_file.read_text()


def test_a_shorting_failure_stops_before_anything_changes(repo):
    r, env, env_file, calls, tmp = repo
    x = run(r, env, FAKE_SHORT="2")
    assert x.returncode == 1 and "did not confirm that shorting is off" in x.stdout
    assert "KAIBOT_MULTIDAY=off" in env_file.read_text() and not list(tmp.glob("secret.env.bak.*"))
    assert not any(c.startswith(("install_service", "run_engine")) for c in calls_of(calls))


def test_a_failed_check_installs_nothing(repo):
    r, env, _, calls, _ = repo
    x = run(r, env, FAKE_CHECK="Fix the FAILED lines above")
    assert x.returncode == 1 and "did not end with ALL GOOD" in x.stdout
    assert not any(c.startswith("install_service") for c in calls_of(calls))


def test_an_engine_started_by_hand_is_stopped_cleanly_first(repo):
    r, env, _, calls, tmp = repo
    import threading

    eng = subprocess.Popen([sys.executable, "-c", "import signal, sys, time; "  # stops on SIGINT, as the engine does
                            "signal.signal(signal.SIGINT, lambda *a: sys.exit(0)); time.sleep(60)"])
    threading.Thread(target=eng.wait, daemon=True).start()  # reaped at once (a zombie would still answer kill -0)
    (tmp / "engines").write_text(f"python {eng.pid}\n")
    try:
        import time

        time.sleep(0.5)  # the handler is installed
        x = run(r, env)
        assert x.returncode == 0, x.stdout + x.stderr
        assert eng.returncode == 0 and "Stopping the engine started by hand" in x.stdout
    finally:
        eng.kill()


def test_an_engine_that_does_not_stop_is_left_to_ctrl_c(repo):
    r, env, _, calls, tmp = repo
    eng = subprocess.Popen(["bash", "-c", "trap '' INT; sleep 60"])
    (tmp / "engines").write_text(f"python {eng.pid}\n")
    try:
        x = run(r, env)
        assert x.returncode == 1 and "Press Ctrl-C in its terminal window" in x.stdout
        assert eng.poll() is None  # never killed hard
        assert not any(c.startswith("install_service") for c in calls_of(calls))
    finally:
        eng.kill()


def test_the_service_engine_is_stopped_through_the_service(repo):
    r, env, _, calls, _ = repo
    x = run(r, env, FAKE_SERVICE_RUNNING="1")
    assert x.returncode == 0 and "service.sh stop" in calls_of(calls)


# ---------------------------------------------------------------- the helpers


def test_set_flag_adds_replaces_and_deduplicates(tmp_path):
    m = load()
    f = tmp_path / ".env"
    f.write_text("A=1\nKAIBOT_MULTIDAY=off\nB=2\nexport KAIBOT_MULTIDAY=maybe\n")
    assert m.set_flag(f, "KAIBOT_MULTIDAY", "on", datetime(2026, 11, 14, 9)).startswith("changed (backup .env.bak.")
    assert f.read_text() == "A=1\nB=2\nKAIBOT_MULTIDAY=on\n"
    assert m.set_flag(f, "KAIBOT_MULTIDAY", "on") == "unchanged"
    g = tmp_path / "g.env"
    g.write_text("A=1")
    m.set_flag(g, "KAIBOT_MULTIDAY", "on")
    assert g.read_text() == "A=1\nKAIBOT_MULTIDAY=on\n"


@pytest.mark.parametrize("now,dec,fill", [
    ("2026-11-14T10:00", date(2026, 11, 17), date(2026, 11, 18)),  # Saturday: decide Tue 17, buy Wed 18
    ("2026-11-16T07:00", date(2026, 11, 17), date(2026, 11, 18)),  # Monday morning
    ("2026-11-17T17:00", date(2026, 11, 17), date(2026, 11, 18)),  # Tuesday after the close
    ("2026-11-18T17:00", date(2026, 11, 24), date(2026, 11, 25)),  # Wednesday evening: next week
])
def test_the_next_decision_is_the_evening_before_the_first_session_from_wednesday_on(now, dec, fill):
    assert load().next_decision(datetime.fromisoformat(now).replace(tzinfo=NY)) == (dec, fill)


def test_the_scripts_calendar_is_the_rules_calendar():
    """The go-live helper runs before the engine's environment exists (standard library only): its copy of the
    weekly decision rule equals strategies/momentum's on every pair of consecutive weekdays of three years."""
    from datetime import timedelta

    from kaibot.strategies import momentum as mom

    m = load()
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(3 * 366)]
    days = [d for d in days if d.weekday() < 5]
    for prev, fill in zip(days, days[1:], strict=False):
        assert m.is_weekly_decision(prev, fill) == mom.is_weekly_decision(prev, fill), (prev, fill)
        assert m.week_number(fill) == mom.week_number(fill)


def test_the_helper_needs_only_the_standard_library():
    import ast

    tree = ast.parse((ROOT / "scripts" / "go_live_paper.py").read_text())
    mods = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not {m.split(".")[0] for m in mods} & {"kaibot", "numpy", "pandas"}


# ---------------------------------------------------------------- QA-Ops second key on #218


def test_the_check_must_exit_cleanly_and_say_all_good_on_its_own_line(repo):
    r, env, _, calls, _ = repo
    for extra in ({"FAKE_CHECK_CODE": "1"}, {"FAKE_CHECK": "NOT ALL GOOD"}):
        x = run(r, env, **extra)
        assert x.returncode == 1 and "did not end with ALL GOOD" in x.stdout, extra
        assert not any(c.startswith("install_service") for c in calls_of(calls))


@pytest.mark.parametrize("change,why", [
    ({"KAIBOT_MULTIDAY": "off"}, "KAIBOT_MULTIDAY is not on"),
    ({"allow_overnight": False}, "allow_overnight is not true"),
    ({"rules_loaded": []}, "no multi-day rule"),
])
def test_all_good_is_not_enough_the_experiment_must_be_on(repo, change, why):
    r, env, _, calls, tmp = repo
    bad = tmp / "bad.json"
    bad.write_text(json.dumps({"multi_day": {**CHECK["multi_day"], **change}}) + "\n")
    x = run(r, env, FAKE_REPORT=str(bad))
    assert x.returncode == 1 and why in x.stdout and "Nothing was installed" in x.stdout
    assert not any(c.startswith("install_service") for c in calls_of(calls))


def test_the_check_runs_as_the_service_will_without_this_shells_switches(repo):
    r, env, _, calls, _ = repo
    x = run(r, env, KAIBOT_MULTIDAY="off", KAIBOT_RELEASE="v2026.01.01")
    assert x.returncode == 0, x.stdout + x.stderr
    assert "env MULTIDAY=unset RELEASE=unset" in calls_of(calls)


def test_a_release_pin_without_the_go_live_stops_before_anything_changes(repo):
    r, env, env_file, calls, tmp = repo
    (r / "data").mkdir()
    (r / "data" / "RELEASE").write_text("v2026.11.10\n")
    old = tmp / "old_profiles.yaml"
    old.write_text("all_levels:\n  allow_overnight: false\n")
    x = run(r, env, FAKE_TAG_PROFILES=str(old))
    assert x.returncode == 1 and "pinned to release v2026.11.10" in x.stdout and "release.sh main" in x.stdout
    assert "KAIBOT_MULTIDAY=off" in env_file.read_text()
    assert not any(c.startswith(("git pull", "alpaca", "run_engine", "install_service")) for c in calls_of(calls))
    new = tmp / "new_profiles.yaml"
    new.write_text("all_levels:\n  allow_overnight: true          # comment\n")
    x = run(r, env, FAKE_TAG_PROFILES=str(new))  # a pin that contains the go-live: no pull, the rest goes on
    assert x.returncode == 0, x.stdout + x.stderr
    assert not any(c.startswith("git pull") for c in calls_of(calls)) and "install_service.sh " in calls_of(calls)


def test_a_detached_checkout_without_a_pin_stops_with_a_plain_message(repo):
    r, env, env_file, calls, _ = repo
    x = run(r, env, FAKE_DETACHED="1")
    assert x.returncode == 1 and "not on a branch" in x.stdout and "KAIBOT_MULTIDAY=off" in env_file.read_text()


def test_a_service_standing_by_is_unloaded_so_it_cannot_start_a_second_engine(repo):
    """The service is loaded but not running (it stands by while a hand-started engine runs): it would start by
    itself a minute after that engine stops. It is unloaded first and only step 6 loads it again."""
    r, env, _, calls, _ = repo
    x = run(r, env, FAKE_SERVICE_LOADED="1")
    assert x.returncode == 0, x.stdout + x.stderr
    c = calls_of(calls)
    boot = [i for i, v in enumerate(c) if v.startswith("launchctl bootout") and "com.kaibot.engine" in v]
    assert boot and boot[0] < c.index("install_service.sh ") and "service.sh stop" not in c
    x = run(r, env, FAKE_SERVICE_LOADED="1", FAKE_CHECK_CODE="1")  # stopped after that: it says so
    assert x.returncode == 1 and "stopped until this command completes" in x.stdout


def test_a_hold_is_said_in_the_summary(repo):
    r, env, _, _, _ = repo
    (r / "data").mkdir()
    (r / "data" / "HOLD").write_text("x")
    x = run(r, env)
    assert x.returncode == 0 and "ON HOLD" in x.stdout and "service.sh unhold" in x.stdout


def test_set_flag_keeps_every_other_byte_and_never_exposes_the_file(tmp_path):
    m = load()
    f = tmp_path / ".env"
    body = b"A=1\r\nWEIRD=x\x0by\nKAIBOT_MULTIDAY=off\r\nB=\"q\"  # c\r\n"
    f.write_bytes(body)
    f.chmod(0o600)
    m.set_flag(f, "KAIBOT_MULTIDAY", "on", datetime(2026, 11, 14, 9))
    assert f.read_bytes() == body.replace(b"KAIBOT_MULTIDAY=off", b"KAIBOT_MULTIDAY=on")
    (bak,) = tmp_path.glob(".env.bak.*")
    assert bak.read_bytes() == body and stat.S_IMODE(bak.stat().st_mode) == 0o600
    assert stat.S_IMODE(f.stat().st_mode) == 0o600 and not list(tmp_path.glob("*.tmp"))
    g = tmp_path / "crlf.env"
    g.write_bytes(b"A=1\r\n")
    m.set_flag(g, "KAIBOT_MULTIDAY", "on")
    assert g.read_bytes() == b"A=1\r\nKAIBOT_MULTIDAY=on\r\n"


def test_set_flag_edits_the_target_of_a_symlinked_env(tmp_path):
    m = load()
    real = tmp_path / "real.env"
    real.write_text("A=1\n")
    link = tmp_path / "link.env"
    link.symlink_to(real)
    m.set_flag(link, "KAIBOT_MULTIDAY", "on")
    assert link.is_symlink() and real.read_text() == "A=1\nKAIBOT_MULTIDAY=on\n"
