"""scripts/research_runner.py: the allow-list is enforced, bad arguments are rejected, done jobs are skipped, output
is redacted, the lock holds and two SEC jobs never run at once. Subprocess fakes only: no network, no git, no gh."""

from __future__ import annotations

import json
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from kaibot.engine.config import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
import research_runner as rr  # noqa: E402


class FakeRun:
    """subprocess.run: gh missing or logged in, ps output, git; records every call."""

    def __init__(self, *, gh_ok=False, ps=""):
        self.calls, self.gh_ok, self.ps, self.posted = [], gh_ok, ps, []

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))
        rc, out = 0, ""
        if cmd[0] == "gh":
            if cmd[1] == "auth":
                rc = 0 if self.gh_ok else 1
            else:
                self.posted.append((cmd[4], json.loads(Path(cmd[cmd.index("--input") + 1]).read_text())["body"]))
        elif cmd[0] == "ps":
            out = self.ps
        elif cmd[0] == "git":
            out = "main" if "--abbrev-ref" in cmd else "a" * 40
        return subprocess.CompletedProcess(cmd, rc, out, "")


class FakeProc:
    def __init__(self, popen, cmd, out, hang):
        self.popen, self.cmd, self.pid, self.returncode, self.hang = popen, cmd, 4242, None, hang
        out.write(popen.output)
        out.flush()

    def wait(self, timeout=None):
        if self.hang and self.returncode is None:
            raise subprocess.TimeoutExpired(self.cmd, timeout)
        self.popen.running -= 1 if self.returncode is None else 0
        self.returncode = self.popen.rc if self.returncode is None else self.returncode
        return self.returncode


class FakePopen:
    """subprocess.Popen: writes `output` to the job's log and exits with `rc`; tracks concurrency."""

    def __init__(self, output="done\n", rc=0, hang=False):
        self.output, self.rc, self.hang = output, rc, hang
        self.cmds, self.kwargs, self.running, self.peak = [], [], 0, 0

    def __call__(self, cmd, **kw):
        self.cmds.append(cmd)
        self.kwargs.append(kw)
        self.running += 1
        self.peak = max(self.peak, self.running)
        return FakeProc(self, cmd, kw["stdout"], self.hang)


@pytest.fixture(autouse=True)
def _gh_on_path(monkeypatch):
    monkeypatch.setattr(rr.shutil, "which", lambda name: f"/usr/bin/{name}")


def write_queue(tmp_path, jobs) -> Path:
    q = tmp_path / "queue.yaml"
    q.write_text(yaml.safe_dump({"jobs": jobs}))
    return q


def tick(tmp_path, jobs, run=None, popen=None, **kw):
    run, popen = run or FakeRun(), popen or FakePopen()
    res = rr.tick(state=tmp_path / "state", queue=write_queue(tmp_path, jobs), pull=False, run=run, popen=popen,
                  python="/venv/python", **kw)
    return res, run, popen


def job(jid="j1", script="scripts/index_core.py", args=("--fixture",), issue=600):
    return {"id": jid, "script": script, "args": list(args), "posts_to_issue": issue}


# ---------------------------------------------------------------- the allow-list


def test_the_committed_allow_list_loads_and_lists_exactly_the_approved_scripts():
    cfg = rr.load_allowed()
    assert set(cfg.specs) == {"scripts/index_core.py", "scripts/index_core_book.py",
                              "scripts/fetch_sec_fundamentals.py", "scripts/trend_momentum_gbm.py",
                              "scripts/export_results.py", "scripts/market_alarm_report.py",
                              "kaibot.agents.evalset.golden"}
    assert cfg.repo == "falconomad/kaibot" and cfg.tail_lines == 150
    assert [t for t, s in cfg.specs.items() if s.sec] == ["scripts/fetch_sec_fundamentals.py"]
    assert [t for t, s in cfg.specs.items() if s.env] == ["scripts/fetch_sec_fundamentals.py"]
    assert not cfg.specs["kaibot.agents.evalset.golden"].flags  # no --live (money), no --record
    for s in cfg.specs.values():
        assert not set(s.flags) & rr.FORBIDDEN_FLAGS and 0 < s.timeout_min <= 240


def test_the_committed_queue_is_valid():
    cfg = rr.load_allowed()
    jobs = rr.load_queue()
    ids = [rr.check_job(j, cfg).id for j in jobs]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("target", ["scripts/service.sh", "scripts/run_engine.sh", "kaibot.engine",
                                    "kaibot.engine.lease", "scripts/../scripts/index_core.py", "/bin/sh",
                                    "scripts/go_live_paper.py", "scripts/nope.py"])
def test_an_allow_list_that_names_the_engine_or_anything_outside_scripts_is_refused(tmp_path, target):
    a = tmp_path / "allowed.yaml"
    a.write_text(yaml.safe_dump({"repo": "o/r", "scripts": {target: {"flags": {}}}}))
    with pytest.raises(rr.JobError):
        rr.load_allowed(a)


@pytest.mark.parametrize("flag", ["--env", "--data", "--out", "--live", "--open-holdout", "--d", "--en", "--o",
                                  "--open", "--univ", "--universe-f", "--made-up"])  # abbreviations: argparse expands
def test_an_allow_list_cannot_open_the_forbidden_flags(tmp_path, flag):
    a = tmp_path / "allowed.yaml"
    a.write_text(yaml.safe_dump({"repo": "o/r", "scripts": {"scripts/index_core.py": {"flags": {flag: ".*"}}}}))
    with pytest.raises(rr.JobError):
        rr.load_allowed(a)


@pytest.mark.parametrize("script", ["scripts/run_engine.sh", "scripts/service.sh", "bash", "scripts/index_core.py; rm",
                                    "../scripts/index_core.py", "kaibot.engine", None, 7])
def test_a_job_whose_script_is_not_on_the_allow_list_is_rejected_posted_and_never_run(tmp_path, script):
    res, run, popen = tick(tmp_path, [job(script=script)], run=FakeRun(gh_ok=True))
    assert res == {"j1": "rejected"} and popen.cmds == []
    assert run.posted and "rejected" in run.posted[0][1]
    assert run.posted[0][0] == "repos/falconomad/kaibot/issues/600/comments"
    assert json.loads((tmp_path / "state" / "done.json").read_text())["j1"]["status"] == "rejected"


# ---------------------------------------------------------------- arguments


@pytest.mark.parametrize("args", [
    ["--env", "/etc/passwd"], ["--data", "/"], ["--out", "/tmp/x"], ["--start", "2020-01-01; rm -rf ~"],
    ["--start", "$(id)"], ["--start"], ["--fixture=1"], ["--universe", "pit|today"], ["--universe", "all"],
    ["--start", "2020-01-01", "--start", "2021-01-01"], ["-c", "import os"], ["positional"], ["--start=2020-1-1"],
    ["--start", "٢٠٢٠-٠١-٠١"], ["--open-holdout", "x"], ["--trials", ""], "--fixture", [1], ["--fixture"] * 30,
])
def test_bad_arguments_are_rejected(args):
    spec = rr.load_allowed().specs["scripts/index_core.py"]
    with pytest.raises(rr.JobError):
        rr.check_args(spec, args)


def test_good_arguments_are_normalised_and_the_command_is_an_argv_without_a_shell():
    cfg = rr.load_allowed()
    j = rr.check_job(job(args=["--start=2016-01-04", "--universe", "pit", "--fixture"]), cfg)
    assert j.args == ("--start", "2016-01-04", "--universe", "pit", "--fixture")
    cmd = rr.argv(j, "/venv/python")
    assert cmd[:4] == ["/usr/bin/nice", "-n", "10", "/venv/python"] and cmd[4].endswith("scripts/index_core.py")
    assert "--env" not in cmd
    g = rr.check_job(job(script="kaibot.agents.evalset.golden", args=[]), cfg)
    assert rr.argv(g, "/venv/python") == ["/venv/python", "-m", "kaibot.agents.evalset.golden"]  # not heavy: no nice
    with pytest.raises(rr.JobError):
        rr.check_job(job(script="kaibot.agents.evalset.golden", args=["--live"]), cfg)


def test_only_scripts_marked_env_get_the_secrets_path_and_the_runner_never_opens_it(tmp_path, monkeypatch):
    opened = []
    real_open = Path.open

    def spy(self, *a, **k):
        opened.append(str(self))
        return real_open(self, *a, **k)

    monkeypatch.setattr(Path, "open", spy)
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: (opened.append(str(self)), real_open(self).read())[1])
    res, run, popen = tick(tmp_path, [job(script="scripts/fetch_sec_fundamentals.py", args=["--status"])])
    assert res == {"j1": "ok"}
    cmd = popen.cmds[0]
    assert cmd[-2:] == ["--env", str(Path("~/Projects/kaibot-secrets/.env").expanduser())]
    assert not [p for p in opened if "kaibot-secrets" in p]


def test_a_job_sees_a_short_fixed_environment_without_tokens(tmp_path, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ghp_secretsecretsecret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxxxxxxxxxxx")
    _, _, popen = tick(tmp_path, [job()])
    env = popen.kwargs[0]["env"]
    assert env["PYTHONPATH"] == "src" and "GH_TOKEN" not in env and "ANTHROPIC_API_KEY" not in env
    assert set(env) <= set(rr.JOB_ENV_KEYS) | {"PYTHONPATH", "PYTHONUNBUFFERED"}
    kw = popen.kwargs[0]
    assert kw.get("shell") is not True and kw["start_new_session"] is True and kw["stdin"] == subprocess.DEVNULL


# ---------------------------------------------------------------- done jobs, posting, fallback


def test_done_jobs_are_skipped_and_a_started_job_is_never_started_again(tmp_path):
    res, _, popen = tick(tmp_path, [job("a"), job("b")])
    assert res == {"a": "ok", "b": "ok"} and len(popen.cmds) == 2
    res, _, popen = tick(tmp_path, [job("a"), job("b"), job("c")])
    assert res == {"c": "ok"} and len(popen.cmds) == 1
    done = json.loads((tmp_path / "state" / "done.json").read_text())
    done["d"] = {"status": "started"}  # a tick that died mid-job (the Mac crashed)
    (tmp_path / "state" / "done.json").write_text(json.dumps(done))
    res, _, popen = tick(tmp_path, [job("d")])
    assert res == {} and popen.cmds == []


def test_a_duplicate_id_runs_once(tmp_path):
    res, _, popen = tick(tmp_path, [job("a"), job("a", args=["--universe", "today"])])
    assert res == {"a": "ok"} and len(popen.cmds) == 1


def test_at_most_max_jobs_per_tick(tmp_path):
    res, _, popen = tick(tmp_path, [job(f"j{i}") for i in range(8)])
    assert len(popen.cmds) == 5 and len(res) == 5


def test_without_gh_the_result_is_written_to_out_and_logged(tmp_path):
    res, run, _ = tick(tmp_path, [job()], popen=FakePopen(output="line\n" * 400, rc=3))
    assert res == {"j1": "failed"} and run.posted == []
    md = (tmp_path / "state" / "out" / "j1.md").read_text()
    assert "status **failed**, exit 3" in md and md.count("line\n") == 150
    assert "NOT posted" in (tmp_path / "state" / "runner.log").read_text()
    assert json.loads((tmp_path / "state" / "done.json").read_text())["j1"]["posted"] is False


def test_with_gh_the_redacted_tail_is_posted_on_the_issue_via_rest(tmp_path):
    out = "ok\nKAIBOT_SEC_USER_AGENT=Jane jane@example.com\npostgresql://u:hunter2@db.example.co/x\n```\n"
    _, run, _ = tick(tmp_path, [job(issue=474)], run=FakeRun(gh_ok=True), popen=FakePopen(output=out))
    (path, body), = run.posted
    assert path == "repos/falconomad/kaibot/issues/474/comments"
    assert "hunter2" not in body and "jane@example.com" not in body and "status **ok**" in body
    assert "````text" in body  # the fence is longer than any backtick run in the output
    post = next(c for c in run.calls if c[:2] == ["gh", "api"])
    assert post[2:4] == ["--method", "POST"] and "graphql" not in " ".join(post)


# ---------------------------------------------------------------- redaction


@pytest.mark.parametrize("secret", [
    "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123", "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    "github_pat_11ABCDEFG0123456789_abcdefghijklmnop", "eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoic2VydmljZSJ9.c2lnbmF0dXJlMTIz",
    "AKIA" + "ABCDEFGHIJKLMNOP", "PK" + "ABCDEFGHIJ12345678", "someone@example.org",
    "https://user:p4ss@example.com/path", "aB3dE5gH7jK9mN1pQ3sT5v" + "W7yZ9bC1dE3fG5hJ7k",
])  # split literals: fake values, kept out of the source so secret scanning does not flag the tests
def test_redaction_removes_keys_tokens_emails_and_credential_urls(secret):
    assert secret not in rr.redact(f"before {secret} after")


@pytest.mark.parametrize("line", ["APCA_API_SECRET_KEY=abcd1234", "SUPABASE_SERVICE_KEY: xyz.abc", "password=hunter2",
                                  'DATABASE_URL="postgres://x"', "Authorization: Bearer abcdefghijklmnop"])
def test_redaction_blanks_the_values_of_secret_looking_assignments(line):
    red = rr.redact(line)
    assert "[REDACTED" in red and not any(v in red for v in ("abcd1234", "xyz.abc", "hunter2", "postgres://x",
                                                             "abcdefghijklmnop"))


def test_the_home_directory_is_not_posted_and_a_rejection_is_fenced():
    red = rr.redact("wrote /Users/owner/Projects/kaibot/data/x", home="/Users/owner")
    assert red == "wrote ~/Projects/kaibot/data/x"
    body = rr.rejected_comment("j1", "script '@someone `x`' is not on the allow-list")
    assert "```text\nscript '@someone 'x'' is not" in body


def test_redaction_keeps_ordinary_research_output():
    text = ("main @ cc77204d3e2f1a0b9c8d7e6f5a4b3c2d1e0f9a8b  sharpe 0.81  2016-01-04..2024-12-31  input_tokens: 1200\n"
            "wrote data/reports/index_core_book_20261124T101500Z.md\n")
    assert rr.redact(text) == text


# ---------------------------------------------------------------- the lock, timeouts, SEC


def test_a_second_runner_does_nothing_while_the_lock_is_held(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    with rr.try_lock(state / "runner.lock") as held:
        assert held
        res, _, popen = tick(tmp_path, [job()])
        assert res == {"_locked": "busy"} and popen.cmds == []
    res, _, popen = tick(tmp_path, [job()])
    assert res == {"j1": "ok"}


def test_jobs_run_one_at_a_time(tmp_path):
    _, _, popen = tick(tmp_path, [job(f"j{i}") for i in range(4)])
    assert popen.peak == 1


def test_a_job_past_its_timeout_has_its_process_group_killed(tmp_path, monkeypatch):
    killed = []
    monkeypatch.setattr(rr.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    res, _, _ = tick(tmp_path, [job()], popen=FakePopen(hang=True))
    assert res == {"j1": "timeout"} and killed and killed[0][0] == 4242
    assert json.loads((tmp_path / "state" / "done.json").read_text())["j1"]["status"] == "timeout"  # not retried


def sec_job(jid):
    return job(jid, script="scripts/fetch_sec_fundamentals.py", args=["--status"])


def test_two_sec_jobs_never_run_at_once(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    with rr.try_lock(state / "sec.lock") as held:  # another runner process is mid SEC job
        assert held
        res, _, popen = tick(tmp_path, [sec_job("s1"), job("other")])
        assert res == {"s1": "deferred", "other": "ok"} and len(popen.cmds) == 1
        assert "s1" not in json.loads((state / "done.json").read_text())  # deferred, not done: runs next tick
    res, _, popen = tick(tmp_path, [sec_job("s1"), sec_job("s2")])
    assert res == {"s1": "ok", "s2": "ok"} and popen.peak == 1


def test_a_sec_job_waits_while_another_sec_fetcher_runs_on_the_mac(tmp_path):
    ps = " 101 /usr/bin/python3 scripts/fetch_earnings_dates.py --env x\n 102 /bin/zsh\n"
    res, _, popen = tick(tmp_path, [sec_job("s1"), job("other")], run=FakeRun(ps=ps))
    assert res == {"s1": "deferred", "other": "ok"} and len(popen.cmds) == 1


def test_the_runner_never_names_the_engine_its_lease_or_service_sh():
    src = (ROOT / "scripts" / "research_runner.py").read_text()
    code = src.split('"""', 2)[2]  # past the module docstring
    code = "\n".join(ln for ln in code.splitlines() if "FORBIDDEN" not in ln and not ln.lstrip().startswith("#"))
    for word in ("service.sh", "launchctl", "kaibot.engine", "engine.lease", "lease.py", "shell=True", "os.system",
                 "graphql"):
        assert word not in code


def test_the_plist_template_is_valid_and_runs_every_15_minutes(tmp_path):
    out = tmp_path / "agents"
    r = subprocess.run(["bash", str(ROOT / "scripts" / "install_runner.sh"), "--dry-run", "--out", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    p = plistlib.loads((out / "com.kaibot.research-runner.plist").read_bytes())
    assert p["StartInterval"] == 900 and p["Label"] == "com.kaibot.research-runner"
    assert p["ProgramArguments"] == [f"{ROOT}/.venv/bin/python", f"{ROOT}/scripts/research_runner.py"]
    assert p["StandardOutPath"].endswith("data/runner/runner.log")
