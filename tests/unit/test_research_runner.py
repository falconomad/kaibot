"""scripts/research_runner.py: the allow-list is enforced, bad arguments are rejected, done jobs are skipped, output
is redacted, the lock holds and two SEC jobs never run at once, and a job's results become a PR (never a push to
main) while any other change pauses the runner. Subprocess fakes for git and gh (no network); two tests use real git
against a local bare remote."""

from __future__ import annotations

import json
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from kaibot.engine.config import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
import research_runner as rr  # noqa: E402


class FakeRun:
    """subprocess.run: gh missing or logged in, ps output, and a small fake git checkout; records every call.

    `changes` maps a path to its two-letter `git status --porcelain` code at the start of the tick; `job_changes`
    are added when a job runs. A commit moves the staged paths onto the current branch; switching back to main
    leaves them there (as git does for files committed on another branch)."""

    def __init__(self, *, gh_ok=False, ps="", changes=None, job_changes=None, branch="main", head="a" * 40,
                 origin="a" * 40, pr_ok=True, push_ok=True):
        self.calls, self.gh_ok, self.ps, self.posted = [], gh_ok, ps, []
        self.changes, self.branch, self.head, self.origin = dict(changes or {}), branch, head, origin
        self.job_changes = dict(job_changes or {})
        self.branches, self.staged, self.commits, self.pushes, self.prs = {"main"}, set(), [], [], []
        self.pr_ok, self.push_ok, self.envs = pr_ok, push_ok, []

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))
        rc, out = 0, ""
        if cmd[0] == "gh":
            if cmd[1] == "auth":
                rc = 0 if self.gh_ok else 1
            else:
                payload = json.loads(Path(cmd[cmd.index("--input") + 1]).read_text())
                if cmd[4].endswith("/pulls"):
                    self.prs.append(payload)
                    rc = 0 if self.pr_ok else 1
                    out = json.dumps({"html_url": "https://github.com/falconomad/kaibot/pull/9001", "number": 9001})
                else:
                    self.posted.append((cmd[4], payload["body"]))
        elif cmd[0] == "ps":
            out = self.ps
        elif cmd[0] == "git":
            self.envs.append(kw.get("env"))
            rc, out = self.git(cmd)
        return subprocess.CompletedProcess(cmd, rc, out, "")

    def job_ran(self):
        self.changes |= self.job_changes

    def git(self, cmd):
        args = list(cmd[3:])  # past `git -C <root>`
        while args and args[0] == "-c":
            args = args[2:]
        verb = args[0]
        if verb == "status":
            return 0, "".join(f"{code} {p}\0" for p, code in self.changes.items())
        if verb == "rev-parse":
            if args[1:] == ["--abbrev-ref", "HEAD"]:
                return 0, self.branch
            if args[1:] == ["HEAD"]:
                return 0, self.head
            ref = args[-1]
            if ref == "refs/remotes/origin/main":
                return 0, self.origin
            return (0, self.head) if ref.removeprefix("refs/heads/") in self.branches else (1, "")
        if verb == "checkout":
            if "-b" in args:
                self.branches.add(args[-1])
            self.branch = args[-1]
            return 0, ""
        if verb == "add":
            self.staged |= set(args[args.index("--") + 1:])
            return 0, ""
        if verb == "commit":
            files = {p: self.changes.pop(p) for p in sorted(self.staged)}
            self.commits.append({"branch": self.branch, "message": args[args.index("-m") + 1], "files": files})
            self.staged = set()
            return 0, ""
        if verb == "push":
            self.pushes.append(args)
            return (0 if self.push_ok else 1), ""
        if verb == "branch":
            self.branches.discard(args[-1])
            return 0, ""
        return 0, ""

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

    def __init__(self, output="done\n", rc=0, hang=False, on_run=None):
        self.output, self.rc, self.hang, self.on_run = output, rc, hang, on_run
        self.cmds, self.kwargs, self.running, self.peak = [], [], 0, 0

    def __call__(self, cmd, **kw):
        self.cmds.append(cmd)
        self.kwargs.append(kw)
        self.running += 1
        self.peak = max(self.peak, self.running)
        if self.on_run:
            self.on_run()  # what the job changes in the checkout
        return FakeProc(self, cmd, kw["stdout"], self.hang)


@pytest.fixture(autouse=True)
def _gh_on_path(monkeypatch):
    monkeypatch.setattr(rr.shutil, "which", lambda name: f"/usr/bin/{name}")


TOKEN = "github_pat_" + "A1b2C3d4E5" * 8  # a made-up fine-grained token (#711)


@pytest.fixture(autouse=True)
def _runner_token(tmp_path_factory, monkeypatch):
    """#711: gh and the push work only with the runner's own token file; every test gets a good one (mode 600)."""
    f = tmp_path_factory.mktemp("secrets") / "runner_gh_token"
    f.write_text(TOKEN + "\n")
    f.chmod(0o600)
    monkeypatch.setenv("KAIBOT_RUNNER_GH_TOKEN_FILE", str(f))
    return f


@pytest.fixture(autouse=True)
def _outside_the_quiet_window(monkeypatch):
    """#718: heavy jobs wait out the 15:30-16:10 ET window. These tests run at any hour, so the runner's clock for
    that rule is a Saturday noon (no session); tests/unit/test_runner_quiet_window.py tests the window itself."""
    from datetime import UTC, datetime

    monkeypatch.setattr(rr, "wall_now", lambda: datetime(2026, 11, 28, 12, 0, tzinfo=UTC))


@pytest.fixture(autouse=True)
def _engine_elsewhere(monkeypatch, tmp_path_factory):
    """The engine's checkout is a folder of its own in every test (on the owner's Mac the repo these tests run in
    may BE the engine's checkout, which the runner refuses); the tests of that refusal name it themselves."""
    elsewhere = tmp_path_factory.mktemp("engine-elsewhere")
    monkeypatch.setenv(rr.ENGINE_CHECKOUT_ENV, str(elsewhere))
    monkeypatch.setattr(rr, "ENGINE_SERVICE_PLIST", str(elsewhere / "no.plist"))


def write_queue(tmp_path, jobs) -> Path:
    q = tmp_path / "queue.yaml"
    q.write_text(yaml.safe_dump({"jobs": jobs}))
    return q


def tick(tmp_path, jobs, run=None, popen=None, **kw):
    run, popen = run or FakeRun(), popen or FakePopen()
    if popen.on_run is None and isinstance(run, FakeRun):
        popen.on_run = run.job_ran
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
                              "kaibot.agents.evalset.golden", "scripts/shadow_results.py",  # #371: as the helper
                              "scripts/gate_v3_rescore.py",  # #456: reads reports, writes the re-score only
                              "scripts/near_close_1550_rerun.py",  # #454: the 15:50-price re-run
                              "scripts/fetch_listings.py",  # #376: the new-listing history and first bars
                              # #708: the re-runs of the unscored runs (research window only, strict patterns)
                              "scripts/walk_forward.py", "scripts/oneshots.py", "scripts/spy_cost_rerun.py",
                              "scripts/fetch_av_earnings.py"}  # #797: Alpha Vantage EARNINGS, free tier, research
    assert not cfg.specs["scripts/shadow_results.py"].flags and not cfg.specs["scripts/shadow_results.py"].writes
    assert cfg.repo == "falconomad/kaibot" and cfg.tail_lines == 150
    assert sorted(t for t, s in cfg.specs.items() if s.sec) == ["scripts/fetch_listings.py",
                                                                "scripts/fetch_sec_fundamentals.py"]
    assert sorted(t for t, s in cfg.specs.items() if s.env) == ["scripts/fetch_av_earnings.py",  # #797
                                                                "scripts/fetch_listings.py",
                                                                "scripts/fetch_sec_fundamentals.py",
                                                                "scripts/walk_forward.py"]  # #708: FRED (VIX)
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


def test_fetch_listings_allows_only_the_history_the_first_bars_and_offline_reads():
    """#376: `--since` (SEC history) and `--first-bars` (Alpaca, paper keys) plus the offline reads. Never --prices
    (thousands of SEC requests), --show/--eligible, the path flags or --env; no writes (data/listings is ignored)."""
    spec = rr.load_allowed().specs["scripts/fetch_listings.py"]
    assert set(spec.flags) == {"--since", "--first-bars", "--limit", "--status", "--build", "--report"}
    assert spec.env and spec.sec and spec.heavy and not spec.writes and spec.timeout_min == 120
    assert spec.flags["--first-bars"] is None and spec.flags["--status"] is None
    assert "fetch_listings" in rr.load_allowed().sec_patterns


@pytest.mark.parametrize("args", [
    ["--prices"], ["--show", "ANT"], ["--eligible", "2026-11-19"], ["--data", "/"], ["--daily", "/"], ["--root", "/"],
    ["--env", "/etc/passwd"], ["--since", "2016-1-1"], ["--since", "16-01-01"], ["--since", "2016-13-01"],
    ["--since", "2016-01-32"], ["--since", "1999-01-01"], ["--since", "2016-01-01 "], ["--since", "2016-01-01\n"],
    ["--since", "$(id)"], ["--since"], ["--first-bars=1"], ["--first-bars", "x"], ["--limit", "0"],
    ["--limit", "10000"], ["--limit", "-1"], ["--since", "2016-01-01", "--since", "2017-01-01"], ["--s", "2016"],
    ["--first"], ["--pri"], ["--since", "٢٠١٦-٠١-٠١"],
])
def test_bad_fetch_listings_arguments_are_rejected(args):
    spec = rr.load_allowed().specs["scripts/fetch_listings.py"]
    with pytest.raises(rr.JobError):
        rr.check_args(spec, args)


def test_the_listing_jobs_are_queued_history_first_then_first_bars_and_get_the_secrets_path():
    cfg = rr.load_allowed()
    mine = [rr.check_job(j, cfg) for j in rr.load_queue() if j["script"] == "scripts/fetch_listings.py"]
    assert [(j.id, j.args, j.issue) for j in mine] == [
        ("listings-history-2016-2026-11-25", ("--since", "2016-01-01"), 376),
        ("listings-first-bars-2026-11-25", ("--first-bars",), 376),
        ("listings-history-2016-2026-11-26", ("--since", "2016-01-01"), 376),  # again after #376's index fix
        ("listings-first-bars-2026-11-26", ("--first-bars",), 376)]
    for j in mine:
        cmd = rr.argv(j, "/venv/python")
        assert cmd[:4] == ["/usr/bin/nice", "-n", "10", "/venv/python"] and cmd[4].endswith("scripts/fetch_listings.py")
        assert cmd[-2:] == ["--env", str(Path("~/Projects/kaibot-secrets/.env").expanduser())]
        assert cmd.count("--env") == 1


def test_the_listing_jobs_run_in_queue_order_and_wait_while_another_sec_fetcher_runs(tmp_path):
    history = job("h", script="scripts/fetch_listings.py", args=["--since", "2016-01-01"], issue=376)
    bars = job("b", script="scripts/fetch_listings.py", args=["--first-bars"], issue=376)
    (tmp_path / "busy").mkdir()
    (tmp_path / "free").mkdir()
    res, _, popen = tick(tmp_path / "busy", [history, bars],
                         run=FakeRun(ps="4242 python scripts/fetch_sec_fundamentals.py --build"))
    assert res == {"h": "deferred", "b": "deferred"} and popen.cmds == []
    res, _, popen = tick(tmp_path / "free", [history, bars])
    assert res == {"h": "ok", "b": "ok"}
    assert ["--since" in c for c in popen.cmds] == [True, False] and "--first-bars" in popen.cmds[1]


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
    assert set(env) <= set(rr.JOB_ENV_KEYS) | {"PYTHONPATH", "PYTHONUNBUFFERED", "KAIBOT_RATELIMIT_DIR"}
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


def test_the_tail_of_a_huge_log_is_bounded(tmp_path):
    log = tmp_path / "x.log"
    log.write_text("x" * 5_000_000 + "\nlast\n")
    t = rr.tail(log, 150)
    assert t.endswith("last\n") and len(t) < 1000


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
    # the one exception: the path of the engine service's plist, READ to know the engine's checkout (QA-Ops audit)
    assert code.count("com.kaibot.engine.plist") == 1 and code.count("ENGINE_SERVICE_PLIST") == 2
    assert "read_bytes()).get(\"WorkingDirectory\")" in code
    code = code.replace('"~/Library/LaunchAgents/com.kaibot.engine.plist"', '""')
    code = "\n".join(ln for ln in code.splitlines() if "FORBIDDEN" not in ln and not ln.lstrip().startswith("#"))
    for word in ("service.sh", "launchctl", "kaibot.engine", "engine.lease", "lease.py", "shell=True", "os.system",
                 "graphql"):
        assert word not in code


def test_the_plist_template_is_valid_and_runs_every_15_minutes(tmp_path):
    out = tmp_path / "agents"
    runner, engine = (tmp_path / "kaibot-runner").resolve(), (tmp_path / "kaibot").resolve()
    engine.mkdir()
    env = {**_shell_env(tmp_path), "KAIBOT_RUNNER_CHECKOUT": str(runner), "KAIBOT_ENGINE_CHECKOUT": str(engine)}
    r = subprocess.run(["bash", str(ROOT / "scripts" / "install_runner.sh"), "--dry-run", "--out", str(out)],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    p = plistlib.loads((out / "com.kaibot.research-runner.plist").read_bytes())
    assert p["StartInterval"] == 900 and p["Label"] == "com.kaibot.research-runner"
    assert p["ProgramArguments"] == [f"{runner}/.venv/bin/python", f"{runner}/scripts/research_runner.py"]
    assert p["WorkingDirectory"] == str(runner) and p["StandardOutPath"] == f"{runner}/data/runner/runner.log"
    assert p["EnvironmentVariables"]["KAIBOT_ENGINE_CHECKOUT"] == str(engine)


def _shell_env(tmp_path) -> dict[str, str]:
    import os

    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {"PATH": os.environ["PATH"], "HOME": str(home), "LANG": "C.UTF-8", "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid"}


# ---------------------------------------------------------------- QA-Ops audit (25 Nov 2026): the runner's own checkout


def test_the_runner_refuses_to_run_in_the_engine_services_checkout(tmp_path):
    """The engine service's plist names the checkout the runner is in: the tick refuses before any git call (no
    pull) and before any job, and says so in the log; main() exits 3."""
    root = tmp_path / "kaibot"
    (root / "data" / "runner").mkdir(parents=True)
    agent = tmp_path / "com.kaibot.engine.plist"
    agent.write_bytes(plistlib.dumps({"Label": "com.kaibot.engine", "WorkingDirectory": str(root)}))
    engines = rr.engine_checkouts(env={rr.ENGINE_CHECKOUT_ENV: str(tmp_path / "other")}, plist=str(agent))
    assert root.resolve() in engines
    run, popen = FakeRun(), FakePopen()
    res = rr.tick(root=root, state=root / "data" / "runner", queue=write_queue(tmp_path, [job()]), run=run,
                  popen=popen, python="/venv/python", engines=engines)
    assert "_refused" in res and "is the engine's checkout" in res["_refused"]
    assert run.calls == [] and popen.cmds == []
    assert "refused:" in (root / "data" / "runner" / "runner.log").read_text()


def test_the_runner_refuses_its_checkout_named_by_kaibot_engine_checkout(tmp_path, monkeypatch):
    root = tmp_path / "kaibot"
    root.mkdir()
    monkeypatch.setenv(rr.ENGINE_CHECKOUT_ENV, str(root))
    assert rr.engine_checkouts() == [root.resolve()]
    assert "is the engine's checkout" in rr.own_checkout_problem(root, rr.engine_checkouts())
    res = rr.tick(root=root, state=tmp_path / "state", queue=write_queue(tmp_path, [job()]), run=FakeRun(),
                  popen=FakePopen(), python="/venv/python")
    assert "_refused" in res and not (tmp_path / "state").exists()  # nothing created either


def test_the_runner_checks_its_data_links_never_reach_the_engines_live_state(tmp_path):
    engine, runner = tmp_path / "kaibot", tmp_path / "kaibot-runner"
    for d in ("data/live", "data/runner", "data/daily", "src"):
        (engine / d).mkdir(parents=True)
    (runner / "data").mkdir(parents=True)
    engines = [engine.resolve()]
    assert rr.own_checkout_problem(runner, engines) is None
    (runner / "data" / "daily").symlink_to(engine / "data" / "daily")  # an input, read by the jobs: fine
    assert rr.own_checkout_problem(runner, engines) is None
    assert "nested" in rr.own_checkout_problem(engine / "data", engines)
    for name, target in (("live", "data/live"), ("old", "data/runner"), ("all", "data"), ("code", "src")):
        link = runner / "data" / name
        link.symlink_to(engine / target)
        assert rr.own_checkout_problem(runner, engines), name
        link.unlink()
    shutil.rmtree(runner / "data")
    (runner / "data").symlink_to(engine / "data")
    assert "is a link" in rr.own_checkout_problem(runner, engines)


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_install_runner_prepare_makes_a_separate_checkout_and_moves_an_old_install(tmp_path):
    """install_runner.sh --prepare (no launchd): a second clone of the engine's origin, the engine's .venv linked,
    data/ of its own with the old runner's state copied, the research outputs copied, the inputs linked and never
    data/live; the engine's checkout is not changed. A second run changes nothing and the checkout stays clean."""
    env = _shell_env(tmp_path)

    def git(*a, cwd=tmp_path):
        return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True, env=env).stdout.strip()

    bare, engine, runner = tmp_path / "origin.git", tmp_path / "kaibot", tmp_path / "kaibot-runner"
    seed = tmp_path / "seed"
    git("init", "-q", "-b", "main", str(seed))
    for rel in ("scripts/research_runner.py", "ops/runner/com.kaibot.research-runner.plist", ".gitignore"):
        (seed / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, seed / rel)
    git("add", "-A", cwd=seed)
    git("commit", "-q", "-m", "seed", cwd=seed)
    git("clone", "-q", "--bare", str(seed), str(bare))
    git("clone", "-q", str(bare), str(engine))
    for d in ("data/live", "data/runner/logs", "data/daily", "data/reports", ".venv/bin"):
        (engine / d).mkdir(parents=True)
    (engine / ".venv" / "bin" / "python").symlink_to(sys.executable)
    (engine / "data" / "runner" / "done.json").write_text(json.dumps({"j1": {"status": "ok"}}))
    (engine / "data" / "live" / "journal.sqlite").write_text("engine state")
    (engine / "data" / "reports" / "r.json").write_text("{}")
    (engine / "data" / "RELEASE").write_text("main\n")
    head0 = git("rev-parse", "HEAD", cwd=engine)
    env |= {"KAIBOT_ENGINE_CHECKOUT": str(engine), "KAIBOT_RUNNER_CHECKOUT": str(runner)}
    cmd = ["bash", str(ROOT / "scripts" / "install_runner.sh"), "--prepare"]
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=runner) == "main"
    assert (runner / ".git").is_dir() and git("remote", "get-url", "origin", cwd=runner) == str(bare)
    assert (runner / ".venv").resolve() == (engine / ".venv").resolve()
    data = runner / "data"
    assert not data.is_symlink() and not (data / "runner").is_symlink()
    assert json.loads((data / "runner" / "done.json").read_text()) == {"j1": {"status": "ok"}}
    assert not (data / "reports").is_symlink() and (data / "reports" / "r.json").exists()
    assert (data / "daily").is_symlink() and (data / "daily").resolve() == (engine / "data" / "daily").resolve()
    assert not (data / "live").exists() and not (data / "RELEASE").exists()
    assert rr.own_checkout_problem(runner, [engine.resolve()]) is None
    assert git("status", "--porcelain", cwd=runner) == ""  # the .venv link and data/ are ignored: the runner runs
    assert git("status", "--porcelain", cwd=engine) == "" and git("rev-parse", "HEAD", cwd=engine) == head0
    assert (engine / "data" / "live" / "journal.sqlite").read_text() == "engine state"
    r2 = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert r2.returncode == 0 and "copied" not in r2.stdout and "linked" not in r2.stdout
    bad = subprocess.run(cmd, capture_output=True, text=True, env=env | {"KAIBOT_RUNNER_CHECKOUT": str(engine)})
    assert bad.returncode == 1 and "is or is inside the engine's" in bad.stderr


# ---------------------------------------------------------------- the checkout after a job (#452 export)

RESULTS = "docs/research/results/"


def mini_root(tmp_path) -> tuple[Path, Path]:
    """A repository root with only scripts/export_results.py, and an allow-list naming it with `writes:`."""
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "scripts" / "export_results.py").write_text((ROOT / "scripts" / "export_results.py").read_text())
    allowed = tmp_path / "allowed.yaml"
    allowed.write_text(yaml.safe_dump({"repo": "falconomad/kaibot", "scripts": {"scripts/export_results.py": {
        "timeout_min": 20, "flags": {"--rule": "[a-z0-9_]{1,64}"}, "writes": [RESULTS]}}}))
    return root, allowed


def export_job(jid="results-export-2026-11-25"):
    return job(jid, script="scripts/export_results.py", args=[], issue=452)


def tick_in(tmp_path, jobs, run, popen=None, files=None):
    """A tick in mini_root, with `files` (path -> text) written into the checkout before settling."""
    root, allowed = mini_root(tmp_path)
    for rel, text in (files or {}).items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return tick(tmp_path, jobs, run=run, popen=popen, root=root, allowed=allowed)


def test_the_committed_allow_list_lets_only_the_results_scripts_write_and_only_under_docs_research_results():
    cfg = rr.load_allowed()
    assert {t: s.writes for t, s in cfg.specs.items() if s.writes} == {"scripts/export_results.py": (RESULTS,),
                                                                     "scripts/gate_v3_rescore.py": (RESULTS,)}  # #456
    assert not cfg.specs["scripts/gate_v3_rescore.py"].flags  # no --data / --out / --root: the defaults only
    assert "results-export-2026-11-25" in [j["id"] for j in rr.load_queue()]


@pytest.mark.parametrize("writes", ["data/", "docs/research/results", "/docs/x/", "docs/../src/", "src/kaibot/",
                                    "docs/", ".github/workflows/", ["docs/ok/", 7], "scripts/"])
def test_writes_must_be_a_folder_under_docs(tmp_path, writes):
    root, _ = mini_root(tmp_path)
    a = tmp_path / "bad.yaml"
    a.write_text(yaml.safe_dump({"repo": "o/r", "scripts": {"scripts/export_results.py": {"writes": writes}}}))
    with pytest.raises(rr.JobError):
        rr.load_allowed(a, root)


def test_changes_only_in_the_allowed_folder_become_a_branch_and_a_pr_and_the_checkout_is_clean_main(tmp_path):
    changes = {RESULTS + "README.md": " M", RESULTS + "runs/new.json": "??", RESULTS + "old.md": " D"}
    run = FakeRun(gh_ok=True, job_changes=changes)
    res, run, _ = tick_in(tmp_path, [export_job()], run,
                          files={RESULTS + "README.md": "# results\n", RESULTS + "runs/new.json": "{}\n"})
    assert res == {"results-export-2026-11-25": "ok"}
    (c,) = run.commits
    assert c["branch"] == "runner/results-export-2026-11-25" and set(c["files"]) == set(changes)
    assert c["message"].startswith("Research runner: results of job results-export-2026-11-25")
    assert "research runner" in c["message"].lower()
    (push,) = run.pushes
    assert push[-2:] == ["https://github.com/falconomad/kaibot.git",
                         "refs/heads/runner/results-export-2026-11-25:refs/heads/runner/results-export-2026-11-25"]
    (pr,) = run.prs
    assert pr["head"] == "runner/results-export-2026-11-25" and pr["base"] == "main"
    assert "research runner" in pr["body"] and "#452" in pr["body"]
    assert run.branch == "main" and run.changes == {}  # back on main, nothing left
    (path, body), = run.posted
    assert path == "repos/falconomad/kaibot/issues/452/comments"
    assert "pull request https://github.com/falconomad/kaibot/pull/9001" in body
    done = json.loads((tmp_path / "state" / "done.json").read_text())["results-export-2026-11-25"]
    assert done["checkout"] == "pr" and done["pr"].endswith("/pull/9001")
    assert not (tmp_path / "state" / rr.PAUSED).exists()
    commit = next(cmd for cmd in run.calls if "commit" in cmd)
    assert "user.email=research-runner@users.noreply.github.com" in commit and "--no-verify" in commit


def test_nothing_is_ever_pushed_to_main(tmp_path):
    run = FakeRun(gh_ok=True, job_changes={RESULTS + "a.md": "??"})
    tick_in(tmp_path, [export_job()], run, files={RESULTS + "a.md": "x\n"})
    pushes = [c for c in run.calls if c[0] == "git" and "push" in c]
    assert pushes and all(not any(a.endswith(":refs/heads/main") or a in ("main", "HEAD", "HEAD:main")
                                  for a in c[c.index("push"):]) for c in pushes)
    assert all(c[-1].startswith("refs/heads/runner/") for c in pushes)
    assert not [c for c in run.calls if c[0] == "git" and ("reset" in c and "--hard" in c or "clean" in c)]


@pytest.mark.parametrize("stray", ["src/kaibot/engine/x.py", "data.csv", "docs/research/other.md",
                                   "docs/research/results_x/a.md", "docs/research/results/a b.md"])
def test_a_change_anywhere_else_is_not_committed_the_checkout_is_left_and_the_runner_stops(tmp_path, stray):
    changes = {RESULTS + "README.md": " M", stray: "??"}
    run = FakeRun(gh_ok=True, job_changes=changes)
    res, run, popen = tick_in(tmp_path, [export_job("e1"), export_job("after")], run,
                              files={RESULTS + "README.md": "# results\n"})
    assert res == {"e1": "ok"} and len(popen.cmds) == 1  # the next job did not run
    assert run.commits == [] and run.pushes == [] and run.prs == [] and run.changes == changes
    assert not [c for c in run.calls if c[0] == "git" and c[3] in ("checkout", "add", "reset", "clean", "restore")]
    (_, body), = run.posted
    assert "**Warning:**" in body and "runs no more jobs" in body and stray.replace("`", "'") in body
    assert json.loads((tmp_path / "state" / rr.PAUSED).read_text())["job"] == "e1"
    assert json.loads((tmp_path / "state" / "done.json").read_text())["e1"]["checkout"] == "held"
    # the next ticks run nothing (and do not pull) while the checkout is dirty, then resume once it is clean
    res, run2, popen = tick_in(tmp_path, [export_job("e1"), export_job("after")], FakeRun(gh_ok=True, changes=changes))
    assert "_paused" in res and popen.cmds == [] and run2.posted == []
    res, _, popen = tick_in(tmp_path, [export_job("e1"), export_job("after")], FakeRun(gh_ok=True))
    assert res == {"after": "ok"} and not (tmp_path / "state" / rr.PAUSED).exists()


def test_a_job_without_writes_that_changes_the_checkout_pauses_the_runner(tmp_path):
    run = FakeRun(gh_ok=True, job_changes={RESULTS + "README.md": " M"})
    res, run, _ = tick(tmp_path, [job("a"), job("b")], run=run)
    assert res == {"a": "ok"} and run.commits == [] and (tmp_path / "state" / rr.PAUSED).exists()


@pytest.mark.parametrize("kw", [{"branch": "feature"}, {"origin": "b" * 40}])
def test_off_main_or_ahead_of_origin_the_changes_are_held(tmp_path, kw):
    run = FakeRun(gh_ok=True, job_changes={RESULTS + "a.md": "??"}, **kw)
    res, run, _ = tick_in(tmp_path, [export_job()], run, files={RESULTS + "a.md": "x\n"})
    assert run.commits == [] and run.pushes == [] and "**Warning:**" in run.posted[0][1]


@pytest.mark.parametrize("text", ["key: ghp_abcdefghijklmnopqrstuvwxyz0123456789\n", "wrote {home}/Projects/x\n",
                                  "contact someone@example.org\n"])
def test_a_written_file_with_a_secret_or_the_home_path_is_never_published(tmp_path, text):
    run = FakeRun(gh_ok=True, job_changes={RESULTS + "a.md": "??"})
    res, run, _ = tick_in(tmp_path, [export_job()], run, files={RESULTS + "a.md": text.format(home=Path.home())})
    assert run.commits == [] and run.pushes == [] and run.prs == []
    assert "may not be published" in run.posted[0][1] and str(Path.home()) not in run.posted[0][1]


def test_a_link_written_into_the_results_folder_is_never_committed(tmp_path):
    root, allowed = mini_root(tmp_path)
    (root / RESULTS).mkdir(parents=True)
    (root / RESULTS / "x.md").symlink_to(Path.home())
    run = FakeRun(gh_ok=True, job_changes={RESULTS + "x.md": "??"})
    tick(tmp_path, [export_job()], run=run, root=root, allowed=allowed)
    assert run.commits == [] and "not a regular file" in run.posted[0][1]


def test_the_commit_message_pr_and_comment_are_redacted(tmp_path):
    home = str(Path.home())
    run = FakeRun(gh_ok=True, job_changes={RESULTS + "a.md": "??"})
    out = f"wrote {home}/Projects/kaibot/docs/research/results/a.md\nANTHROPIC_API_KEY=sk-ant-api03-abcdefghijklmnop\n"
    tick_in(tmp_path, [export_job()], run, popen=FakePopen(output=out), files={RESULTS + "a.md": "x\n"})
    texts = [run.commits[0]["message"], run.prs[0]["title"], run.prs[0]["body"], run.posted[0][1]]
    for t in texts:
        assert home not in t and "sk-ant-" not in t and "api03" not in t
    assert "~/Projects/kaibot" in run.posted[0][1]


def test_without_gh_the_results_stay_on_a_local_branch_and_the_checkout_is_still_clean(tmp_path):
    run = FakeRun(gh_ok=False, job_changes={RESULTS + "a.md": "??"})
    res, run, _ = tick_in(tmp_path, [export_job(), job("after", script="scripts/export_results.py", args=[])],
                          run, files={RESULTS + "a.md": "x\n"})
    assert res == {"results-export-2026-11-25": "ok", "after": "ok"}  # not paused: nothing was lost
    assert run.pushes == [] and run.prs == [] and [c["branch"] for c in run.commits] == [
        "runner/results-export-2026-11-25", "runner/after"]
    assert {"runner/results-export-2026-11-25", "runner/after"} <= run.branches  # kept: not pushed
    assert run.branch == "main" and run.changes == {}
    assert "not pushed" in (tmp_path / "state" / "out" / "results-export-2026-11-25.md").read_text()


def test_the_push_uses_gh_credentials_and_the_runner_s_own_environment_never_reaches_a_job(tmp_path, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ghp_secretsecretsecret")
    run = FakeRun(gh_ok=True, job_changes={RESULTS + "a.md": "??"})
    _, run, popen = tick_in(tmp_path, [export_job()], run, files={RESULTS + "a.md": "x\n"})
    push = next(c for c in run.calls if c[0] == "git" and "push" in c)
    assert "credential.helper=!/usr/bin/gh auth git-credential" in push
    assert not [a for a in push if "ghp_" in a] and "GH_TOKEN" not in popen.kwargs[0]["env"]


def test_gh_and_the_push_use_only_the_runner_s_token_never_the_owner_s_login(tmp_path, monkeypatch):
    """#711: every gh call and the push get GH_TOKEN = the token file's token and an empty gh config folder of the
    runner's own (so gh cannot read the owner's hosts.yml or keychain entry); an owner's GH_TOKEN in the runner's own
    environment is not used; every other credential helper is switched off for the push."""
    monkeypatch.setenv("GH_TOKEN", "gho_ownerslogin0000000000")
    monkeypatch.setenv("GH_CONFIG_DIR", str(tmp_path / "owners-gh"))
    envs = []

    class Spy(FakeRun):
        def __call__(self, cmd, **kw):
            if cmd[0] == "gh" or (cmd[0] == "git" and "push" in cmd):
                envs.append((cmd[:2] if cmd[0] == "gh" else ["git", "push"], kw.get("env")))
            return super().__call__(cmd, **kw)

    run = Spy(gh_ok=True, job_changes={RESULTS + "a.md": "??"})
    tick_in(tmp_path, [export_job()], run, files={RESULTS + "a.md": "x\n"})
    kinds = {tuple(k) for k, _ in envs}
    assert {("gh", "auth"), ("gh", "api"), ("git", "push")} <= kinds
    for _, env in envs:
        assert env["GH_TOKEN"] == TOKEN and env["GH_CONFIG_DIR"] == str(tmp_path / "state" / "gh")
        assert "gho_" not in "".join(env.values()) and "PYTHONPATH" not in env
    push = next(c for c in run.calls if c[0] == "git" and "push" in c)
    assert "credential.https://github.com.helper=" in push and "credential.helper=" in push
    assert not [a for a in push if TOKEN in a]  # never on a command line
    assert (tmp_path / "state" / "gh").stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("problem", ["mode", "classic", "gh-login", "link", "dangling-link"])
def test_a_bad_runner_token_file_refuses_and_never_falls_back(tmp_path, monkeypatch, _runner_token, problem):
    """#711: a file others can read, a classic token (every repository of the account), gh's own login token (the
    owner's full login), a link or a dangling link: the runner uses nothing, never the owner's login (results stay
    local, said why). Only a file that is not there at all falls back (next test)."""
    f = _runner_token
    if problem == "dangling-link":
        f.unlink()
        f.symlink_to(f.with_name("gone"))
    elif problem == "mode":
        f.chmod(0o644)
    elif problem == "classic":
        f.write_text("ghp_" + "x" * 36)
    elif problem == "gh-login":
        f.write_text("gho_" + "x" * 36)
    else:
        real = f.with_name("real")
        f.rename(real)
        f.symlink_to(real)
    monkeypatch.setenv("GH_TOKEN", "gho_ownerslogin0000000000")
    run = FakeRun(gh_ok=True, job_changes={RESULTS + "a.md": "??"})
    tick_in(tmp_path, [export_job()], run, files={RESULTS + "a.md": "x\n"})
    assert run.pushes == [] and run.prs == [] and run.posted == []
    assert not [c for c in run.calls if c[0] == "gh"]  # not even `gh auth status` with the owner's login
    assert [c["branch"] for c in run.commits] == ["runner/results-export-2026-11-25"] and run.branch == "main"
    text = (tmp_path / "state" / "runner.log").read_text()
    assert "not pushed:" in text and "not posted to GitHub:" in text and "gho_" not in text and "ghp_" not in text
    assert "owner login in use" not in text and "the runner token is refused" in text


def _gh_envs(run_cls=None):
    envs = []

    class Spy(FakeRun):
        def __call__(self, cmd, **kw):
            if cmd[0] == "gh" or (cmd[0] == "git" and "push" in cmd):
                envs.append(kw.get("env"))
            return super().__call__(cmd, **kw)
    return Spy, envs


def test_without_a_token_file_the_owner_login_is_used_with_a_warning_each_tick(tmp_path, monkeypatch, _runner_token):
    """Product (26 Nov 2026): until the owner installs the token nothing stops. No token file at all, and none ever
    used: gh and the push run with the owner's own environment (gh's login) as before #711, and every tick says so."""
    _runner_token.unlink()
    monkeypatch.setenv("GH_CONFIG_DIR", str(tmp_path / "owners-gh"))
    Spy, envs = _gh_envs()
    run = Spy(gh_ok=True, job_changes={RESULTS + "a.md": "??"})
    tick_in(tmp_path, [export_job()], run, files={RESULTS + "a.md": "x\n"})
    assert run.pushes and run.prs and run.posted  # works as today
    assert envs and all(e["GH_CONFIG_DIR"] == str(tmp_path / "owners-gh") and e.get("GH_TOKEN") != TOKEN
                        for e in envs)
    text = (tmp_path / "state" / "runner.log").read_text()
    assert text.count("WARNING: owner login in use") == 1 and "runner_token.sh --install" in text
    assert not (tmp_path / "state" / rr.TOKEN_SEEN).exists()


def test_once_a_token_was_used_the_owner_login_is_never_used_again(tmp_path, monkeypatch, _runner_token):
    """The first tick with a valid token writes data/runner/runner_token_in_use; with the file gone afterwards the
    runner refuses (no gh call at all), it does not go back to the owner's login."""
    run = FakeRun(gh_ok=True, job_changes={RESULTS + "a.md": "??"})
    tick_in(tmp_path, [export_job()], run, files={RESULTS + "a.md": "x\n"})
    assert run.pushes and (tmp_path / "state" / rr.TOKEN_SEEN).exists()
    _runner_token.unlink()
    monkeypatch.setenv("GH_TOKEN", "gho_ownerslogin0000000000")
    run2 = FakeRun(gh_ok=True)
    tick(tmp_path, [job("next")], run=run2)
    assert not [c for c in run2.calls if c[0] == "gh"] and run2.posted == []
    text = (tmp_path / "state" / "runner.log").read_text()
    assert "a runner token was in use before" in text and text.count("owner login in use") == 0


def test_the_token_never_reaches_a_job_or_the_posted_text(tmp_path):
    run = FakeRun(gh_ok=True)
    _, run, popen = tick(tmp_path, [job()], run=run)
    assert TOKEN not in json.dumps(popen.kwargs[0]["env"]) and all(TOKEN not in b for _, b in run.posted)


def test_a_failed_pr_still_leaves_clean_main_and_says_so(tmp_path):
    run = FakeRun(gh_ok=True, job_changes={RESULTS + "a.md": "??"}, pr_ok=False)
    tick_in(tmp_path, [export_job()], run, files={RESULTS + "a.md": "x\n"})
    assert run.branch == "main" and run.changes == {} and "opening the pull request failed" in run.posted[0][1]


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_with_real_git_the_pr_branch_holds_only_the_results_and_data_is_untouched(tmp_path):
    """Real git against a local bare remote (gh still faked): the branch has only the results, main is unchanged,
    the checkout is clean main again, and ignored data/ files survive."""
    root, allowed = mini_root(tmp_path)
    bare = tmp_path / "remote.git"

    def git(*a, cwd=root):
        return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()

    git("init", "-q", "--bare", "-b", "main", str(bare), cwd=tmp_path)
    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")
    (root / ".gitignore").write_text("data/\n")
    (root / RESULTS).mkdir(parents=True)
    (root / RESULTS / "README.md").write_text("old\n")
    git("add", "-A")
    git("commit", "-q", "-m", "init")
    git("remote", "add", "origin", str(bare))
    git("push", "-q", "origin", "main")
    git("fetch", "-q", "origin")
    main0 = git("rev-parse", "HEAD")
    (root / "data").mkdir()
    (root / "data" / "keep.txt").write_text("owner's data\n")

    fake = FakeRun(gh_ok=True)

    def run(cmd, **kw):
        if cmd[0] == "git":
            fake.calls.append(list(cmd))
            if "push" in cmd:
                cmd = [str(bare) if a.startswith("https://github.com/") else a for a in cmd]
            return subprocess.run(cmd, **kw)
        return fake(cmd, **kw)

    class WritingPopen(FakePopen):
        def __call__(self, cmd, **kw):
            (root / RESULTS / "README.md").write_text("new\n")
            (root / RESULTS / "runs").mkdir(exist_ok=True)
            (root / RESULTS / "runs" / "r1.json").write_text("{}\n")
            return super().__call__(cmd, **kw)

    res = rr.tick(root=root, state=root / "data" / "runner", queue=write_queue(tmp_path, [export_job()]),
                  allowed=allowed, pull=False, run=run, popen=WritingPopen(), python="/venv/python")
    assert res == {"results-export-2026-11-25": "ok"}
    assert git("status", "--porcelain", "--untracked-files=all") == ""
    assert git("rev-parse", "--abbrev-ref", "HEAD") == "main" and git("rev-parse", "HEAD") == main0
    assert (root / RESULTS / "README.md").read_text() == "old\n" and (root / "data" / "keep.txt").exists()
    assert git("rev-parse", "main", cwd=bare) == main0  # nothing pushed to main
    br = "runner/results-export-2026-11-25"
    changed = git("diff", "--name-only", "main", br, cwd=bare).splitlines()
    assert sorted(changed) == [RESULTS + "README.md", RESULTS + "runs/r1.json"]
    assert git("log", "-1", "--format=%an <%ae>%n%s", br, cwd=bare).startswith(
        "Kaibot research runner <research-runner@users.noreply.github.com>\nResearch runner: results of job")
    assert fake.prs[0]["head"] == br and "pull/9001" in fake.posted[0][1]


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_with_real_git_a_stray_change_is_left_exactly_as_it_was(tmp_path):
    root, allowed = mini_root(tmp_path)
    for a in (["init", "-q", "-b", "main"], ["config", "user.email", "t@example.invalid"],
              ["config", "user.name", "t"]):
        subprocess.run(["git", *a], cwd=root, check=True)
    (root / "notes.md").write_text("tracked\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)
    fake = FakeRun(gh_ok=True)

    def run(cmd, **kw):
        return subprocess.run(cmd, **kw) if cmd[0] == "git" else fake(cmd, **kw)

    class WritingPopen(FakePopen):
        def __call__(self, cmd, **kw):
            (root / RESULTS).mkdir(parents=True, exist_ok=True)
            (root / RESULTS / "a.md").write_text("result\n")
            (root / "notes.md").write_text("edited by hand\n")
            (root / "scratch.txt").write_text("untracked\n")
            return super().__call__(cmd, **kw)

    before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True).stdout
    res = rr.tick(root=root, state=tmp_path / "state", queue=write_queue(tmp_path, [export_job(), job("b")]),
                  allowed=allowed, pull=False, run=run, popen=WritingPopen(), python="/venv/python")
    assert res == {"results-export-2026-11-25": "ok"}
    assert (root / "notes.md").read_text() == "edited by hand\n" and (root / "scratch.txt").exists()
    assert (root / RESULTS / "a.md").read_text() == "result\n"
    assert subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True).stdout == before
    assert subprocess.run(["git", "branch", "--list", "runner/*"], cwd=root, capture_output=True,
                          text=True).stdout == ""


def test_a_checkout_left_on_a_runner_branch_pauses_the_runner(tmp_path):
    res, _, popen = tick(tmp_path, [job()], run=FakeRun(branch="runner/x"))
    assert "_paused" in res and popen.cmds == []


def test_a_non_utf8_file_name_is_held_and_the_warning_is_still_written_and_posted(tmp_path):
    odd = RESULTS + "caf\udce9.md"  # what git status gives for a latin-1 name, decoded with surrogateescape
    run = FakeRun(gh_ok=True, job_changes={odd: "??"})
    res, run, _ = tick_in(tmp_path, [export_job()], run)
    assert run.commits == [] and "\\udce9" in run.posted[0][1]
    assert "\\udce9" in (tmp_path / "state" / "out" / "results-export-2026-11-25.md").read_text()


def test_a_corrupt_paused_file_does_not_crash_the_tick(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / rr.PAUSED).write_text("{not json")
    res, _, _ = tick(tmp_path, [job()], run=FakeRun(changes={"x.md": "??"}))
    assert "_paused" in res


# ---------------------------------------------------------------- #708 (audit 2 6.8): the registry in every PR


def test_trials_stamp_hashes_the_registry_and_counts_its_rows(tmp_path):
    import hashlib

    assert rr.trials_stamp(tmp_path) == {"path": "data/trials.jsonl", "present": False}
    assert "**missing**" in rr.trials_line(rr.trials_stamp(tmp_path))
    f = tmp_path / "data" / "trials.jsonl"
    f.parent.mkdir()
    raw = b'{"trial_id": "a"}\n\n{"trial_id": "b"}\n' * 40_000  # more than one read chunk
    f.write_bytes(raw)
    st = rr.trials_stamp(tmp_path)
    assert st == {"path": "data/trials.jsonl", "present": True, "sha256": hashlib.sha256(raw).hexdigest(),
                  "rows": 80_000, "bytes": len(raw), "ends_with_newline": True}
    line = rr.trials_line(st)
    assert st["sha256"] in line and "80000 rows" in line and "cut-off" not in line
    f.write_bytes(raw + b'{"trial_id": "c"')  # a crashed run's half row is counted and flagged
    st = rr.trials_stamp(tmp_path)
    assert st["rows"] == 80_001 and not st["ends_with_newline"] and "cut-off" in rr.trials_line(st)
    assert rr.redact(rr.trials_line(st)) == rr.trials_line(st)  # a lower-case hex hash is never redacted


def test_every_results_commit_and_pr_carries_the_registrys_hash_and_row_count(tmp_path):
    import hashlib

    raw = b'{"trial_id": "a"}\n{"trial_id": "b"}\n{"trial_id": "c"}\n'
    run = FakeRun(gh_ok=True, job_changes={RESULTS + "a.md": "??"})
    res, run, _ = tick_in(tmp_path, [export_job()], run,
                          files={RESULTS + "a.md": "x\n", "data/trials.jsonl": raw.decode()})
    assert res == {"results-export-2026-11-25": "ok"}
    want = f"sha256 `{hashlib.sha256(raw).hexdigest()}`, 3 rows, {len(raw)} bytes"
    (c,) = run.commits
    (pr,) = run.prs
    assert want in c["message"] and want in pr["body"]
    assert "data/trials.jsonl" not in c["files"]  # the registry itself is never committed, only its stamp


def test_a_results_pr_still_opens_when_the_registry_is_missing(tmp_path):
    run = FakeRun(gh_ok=True, job_changes={RESULTS + "a.md": "??"})
    tick_in(tmp_path, [export_job()], run, files={RESULTS + "a.md": "x\n"})
    (pr,) = run.prs
    assert "Trial registry (data/trials.jsonl): **missing**" in pr["body"]


ELEVEN = {"scripts/walk_forward.py": [["--experiment", e] for e in
                                      ("control", "high_vix", "mornings", "mornings_wide_stop", "wide_stop")],
          "scripts/oneshots.py": [["--rule", "news_continuation"], ["--rule", "orb_stocks_in_play"]],
          "scripts/spy_cost_rerun.py": [[]]}


def test_the_708_reruns_are_queued_on_the_research_window_only_and_say_they_count():
    cfg = rr.load_allowed()
    jobs = [rr.check_job(j, cfg) for j in rr.load_queue() if j.get("posts_to_issue") == 708]
    got = {}
    for j in jobs:
        got.setdefault(j.spec.target, []).append([a for a in j.args if a not in ("--end", "2024-12-31")])
        assert j.args[:2] == ("--end", "2024-12-31")
    assert got == ELEVEN  # 8 jobs: 5 walk-forward, 2 one-shots, the cost re-run (the 3 spy_engine runs: by hand)
    text = (ROOT / "ops" / "runner" / "queue.yaml").read_text()
    assert "EACH RE-RUN LOGS TRIAL ROWS AND COUNTS IN THE TRIAL REGISTRY" in text
    assert sum("counts in the" in ln for ln in text.splitlines() if "-exposure-2026-11-26" in ln) == 8


@pytest.mark.parametrize("script, args", [
    ("scripts/walk_forward.py", ["--end", "2025-01-02", "--experiment", "control"]),
    ("scripts/walk_forward.py", ["--end", "2024-12-30", "--experiment", "control"]),
    ("scripts/walk_forward.py", ["--end", "2024-12-31", "--experiment", "control|x"]),
    ("scripts/walk_forward.py", ["--end", "2024-12-31", "--costs", "v2"]),
    ("scripts/walk_forward.py", ["--end", "2024-12-31", "--start", "2010-01-01"]),
    ("scripts/oneshots.py", ["--end", "2024-12-31", "--rule", "trend_highs"]),
    ("scripts/oneshots.py", ["--end", "2024-12-31", "--rule", "news_continuation", "--control-runs", "1"]),
    ("scripts/oneshots.py", ["--summary"]),
    ("scripts/spy_cost_rerun.py", ["--end", "2026-11-06"]),
    ("scripts/spy_cost_rerun.py", ["--end", "2024-12-31", "--level", "aggressive"]),
])
def test_the_708_entries_take_nothing_but_their_own_runs(script, args):
    cfg = rr.load_allowed()
    with pytest.raises(rr.JobError):
        rr.check_job({"id": "x", "script": script, "args": args, "posts_to_issue": 708}, cfg)


def test_spy_engine_stays_off_the_allow_list():
    """The runner never runs a target named like the engine (FORBIDDEN_TARGET), so #708's three spy_engine runs
    are the owner's to start; the allow-list cannot name it."""
    assert rr.FORBIDDEN_TARGET.search("scripts/spy_engine.py")
    assert "scripts/spy_engine.py" not in rr.load_allowed().specs
    assert cfg_env("scripts/walk_forward.py") and not cfg_env("scripts/oneshots.py")


def cfg_env(target: str) -> bool:
    return rr.load_allowed().specs[target].env


def test_av_earnings_entry_caps_the_budget_and_stays_out_of_the_quiet_window():
    """#797: the Alpha Vantage batch is heavy only so that the quiet window around the close defers it; its budget
    flag cannot pass the free tier's shared 25 a day (the script refuses above 23 as well)."""
    cfg = rr.load_allowed()
    spec = cfg.specs["scripts/fetch_av_earnings.py"]
    assert spec.heavy and spec.env and not spec.sec and not spec.writes and set(spec.flags) == {"--budget", "--status"}
    for ok in ("0", "9", "19", "20", "23"):
        rr.check_job(job("av", "scripts/fetch_av_earnings.py", ("--budget", ok), 797), cfg)
    for bad in ("24", "25", "99", "-1", "200", "2 0"):
        with pytest.raises(rr.JobError):
            rr.check_job(job("av", "scripts/fetch_av_earnings.py", ("--budget", bad), 797), cfg)
    mine = [rr.check_job(j, cfg) for j in rr.load_queue() if j["script"] == "scripts/fetch_av_earnings.py"]
    assert mine and all(j.issue == 797 for j in mine)
