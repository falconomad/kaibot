"""The research runner on the owner's Mac: runs approved research jobs that Product queues in the repo.

    .venv/bin/python scripts/research_runner.py            # one tick (launchd runs this every 15 minutes)
    .venv/bin/python scripts/research_runner.py --check    # validate queue.yaml against allowed.yaml; run nothing

One tick: `git pull --ff-only` (only when the checkout is on `main`), read ops/runner/queue.yaml, and run each job
whose id is not in data/runner/done.json, one at a time (a lock), with .venv/bin/python and PYTHONPATH=src. A job
may name only a script listed in ops/runner/allowed.yaml and only the flags listed there for it, each value matching
its pattern. No shell is ever used: every job is an argv list. The runner never reads the secrets file; a script
marked `env` gets `--env ~/Projects/kaibot-secrets/.env` and opens it itself. The environment a job sees is a short
fixed list (no tokens of the runner's own). Heavy jobs run under `nice -n 10`; every job has a timeout that kills
its process group; SEC jobs never run two at once (a second lock, and a check for other SEC fetchers on the Mac).

The last lines of output are redacted (keys, tokens, emails, URLs with credentials) and posted as a comment on the
job's issue with `gh api` when gh is installed and logged in; the same text is always kept in data/runner/out/<id>.md.
The runner does not touch the engine, its lease or scripts/service.sh. Runbook: docs/ops/research_runner.md.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT / "ops" / "runner" / "queue.yaml"
ALLOWED = ROOT / "ops" / "runner" / "allowed.yaml"
STATE = ROOT / "data" / "runner"
ENV_FILE = "~/Projects/kaibot-secrets/.env"  # only its path is passed on (argv expands ~); never opened here

ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
SCRIPT_RE = re.compile(r"scripts/[a-z0-9_]{1,64}\.py")
MODULE_RE = re.compile(r"kaibot(\.[a-z0-9_]{1,64}){1,6}")
# Never allowed, whatever allowed.yaml says: the engine, its service, its lease, and flags that move files or secrets.
FORBIDDEN_TARGET = re.compile(r"(^|[./_])(engine|service|lease|run_engine|watchdog|go_live)([./_]|$)")
FORBIDDEN_FLAGS = {"--env", "--data", "--out", "--root", "--trials", "--open-holdout", "--universe-file", "--live",
                   "--record"}
MAX_ARGS, MAX_ARG_LEN = 24, 128
JOB_ENV_KEYS = ("HOME", "PATH", "LANG", "LC_ALL", "TMPDIR", "TZ", "SSL_CERT_FILE")
NICE = ("/usr/bin/nice", "-n", "10")


class JobError(ValueError):
    """A queued job that may not run (not on the allow-list, a bad argument, a malformed entry)."""


@dataclass(frozen=True)
class Spec:
    target: str                      # scripts/x.py or kaibot.module
    flags: dict[str, re.Pattern | None]
    env: bool = False
    heavy: bool = False
    sec: bool = False
    timeout_min: float = 60.0


@dataclass(frozen=True)
class Job:
    id: str
    spec: Spec
    args: tuple[str, ...]            # normalised: --flag [value] ...
    issue: int


@dataclass
class Config:
    repo: str
    specs: dict[str, Spec]
    max_jobs: int = 5
    tail_lines: int = 150
    sec_patterns: tuple[str, ...] = ()
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------- allow-list and queue


def load_allowed(path: Path = ALLOWED, root: Path = ROOT) -> Config:
    doc = yaml.safe_load(path.read_text()) or {}
    repo = str(doc.get("repo", ""))
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise JobError(f"allowed.yaml: bad repo {repo!r}")
    specs: dict[str, Spec] = {}
    for target, raw in (doc.get("scripts") or {}).items():
        raw = raw or {}
        if FORBIDDEN_TARGET.search(target):
            raise JobError(f"allowed.yaml: {target} is never allowed (engine, service or lease)")
        if SCRIPT_RE.fullmatch(target):
            f = root / target
            if not f.is_file() or f.is_symlink() or f.resolve().parent != (root / "scripts").resolve():
                raise JobError(f"allowed.yaml: {target} is not a file in scripts/")
        elif MODULE_RE.fullmatch(target):
            rel = Path("src", *target.split("."))
            if not ((root / rel).with_suffix(".py").is_file() or (root / rel / "__main__.py").is_file()):
                raise JobError(f"allowed.yaml: module {target} not found under src/")
        else:
            raise JobError(f"allowed.yaml: {target!r} is neither scripts/<name>.py nor a kaibot module")
        flags: dict[str, re.Pattern | None] = {}
        for flag, pat in (raw.get("flags") or {}).items():
            if not re.fullmatch(r"--[a-z][a-z0-9-]{0,40}", str(flag)) or flag in FORBIDDEN_FLAGS:
                raise JobError(f"allowed.yaml: {target}: flag {flag!r} may not be allowed")
            flags[flag] = None if pat is None else re.compile(str(pat), re.ASCII)
        specs[target] = Spec(target, flags, bool(raw.get("env")), bool(raw.get("heavy")), bool(raw.get("sec")),
                             float(raw.get("timeout_min", 60)))
    return Config(repo, specs, int(doc.get("max_jobs_per_run", 5)), int(doc.get("tail_lines", 150)),
                  tuple(str(p) for p in doc.get("sec_process_patterns") or ()))


def load_queue(path: Path = QUEUE) -> list:
    doc = yaml.safe_load(path.read_text()) or {}
    jobs = doc.get("jobs") or []
    if not isinstance(jobs, list):
        raise JobError("queue.yaml: `jobs` must be a list")
    return jobs


def check_args(spec: Spec, args) -> tuple[str, ...]:
    """The job's arguments, normalised to `--flag [value]` tokens, or JobError. Only listed flags, values must match."""
    if args is None:
        args = []
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise JobError("args must be a list of strings")
    if len(args) > MAX_ARGS or any(len(a) > MAX_ARG_LEN for a in args):
        raise JobError("too many or too long arguments")
    out: list[str] = []
    seen: set[str] = set()
    it = iter(args)
    for tok in it:
        flag, eq, value = tok.partition("=")
        if flag not in spec.flags:
            raise JobError(f"argument {tok[:40]!r} is not allowed for {spec.target}")
        if flag in seen:
            raise JobError(f"{flag} given twice")
        seen.add(flag)
        pat = spec.flags[flag]
        if pat is None:
            if eq:
                raise JobError(f"{flag} takes no value")
            out.append(flag)
            continue
        if not eq:
            value = next(it, None)
            if value is None:
                raise JobError(f"{flag} needs a value")
        if not pat.fullmatch(value):
            raise JobError(f"value {value[:40]!r} for {flag} does not match {pat.pattern!r}")
        out += [flag, value]
    return tuple(out)


def check_job(raw, cfg: Config) -> Job:
    if not isinstance(raw, dict):
        raise JobError("a job must be a mapping")
    unknown = set(raw) - {"id", "script", "args", "posts_to_issue", "note"}
    if unknown:
        raise JobError(f"unknown keys {sorted(unknown)}")
    jid = raw.get("id")
    if not isinstance(jid, str) or not ID_RE.fullmatch(jid):
        raise JobError(f"bad id {str(jid)[:40]!r}")
    issue = raw.get("posts_to_issue")
    if isinstance(issue, bool) or not isinstance(issue, int) or not 0 < issue < 10**7:
        raise JobError("posts_to_issue must be an issue number")
    script = raw.get("script")
    if not isinstance(script, str) or script not in cfg.specs:
        raise JobError(f"script {str(script)[:80]!r} is not on the allow-list (ops/runner/allowed.yaml)")
    spec = cfg.specs[script]
    return Job(jid, spec, check_args(spec, raw.get("args")), issue)


def argv(job: Job, python: str, env_file: str = ENV_FILE) -> list[str]:
    cmd = [*NICE] if job.spec.heavy else []
    cmd.append(python)
    if job.spec.target.startswith("scripts/"):
        cmd.append(str(ROOT / job.spec.target))
    else:
        cmd += ["-m", job.spec.target]
    cmd += list(job.args)
    if job.spec.env:
        cmd += ["--env", str(Path(env_file).expanduser())]  # the path only; the runner never opens the file
    return cmd


def job_env(base: dict | None = None) -> dict[str, str]:
    """What a job sees: a short fixed list from the runner's environment (never GH_TOKEN or other secrets)."""
    base = os.environ if base is None else base
    env = {k: base[k] for k in JOB_ENV_KEYS if k in base}
    env["PYTHONPATH"] = "src"
    env["PYTHONUNBUFFERED"] = "1"
    return env


# ---------------------------------------------------------------- redaction and output


_REDACTIONS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S),
     "[REDACTED private key]"),
    (re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/@:]*:[^\s@]*@\S*", re.I), "[REDACTED url]"),           # user:pass@
    (re.compile(r"(?i)\b(bearer|token|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 [REDACTED]"),
    (re.compile(r"(?i)\b([A-Z0-9_.-]*(?:key|token|secret|passw(?:or)?d|pwd|auth|cookie|dsn|credentials?|"
                r"user_agent|database_url))(\"?\s*[:=]\s*\"?)[^\s\"',]+"), r"\1\2[REDACTED]"),
    (re.compile(r"\b(?:sk-ant-|sk-|ghp_|gho_|ghu_|ghs_|ghr_|github_pat_|xox[abprs]-|AKIA|ASIA|AIza|"
                r"sbp_|pk_live_|sk_live_|rk_live_)[A-Za-z0-9_\-]{8,}"), "[REDACTED token]"),
    (re.compile(r"\b[PA]K[A-Z0-9]{16,}\b"), "[REDACTED key id]"),                                     # Alpaca
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "[REDACTED jwt]"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[REDACTED email]"),
    # long high-entropy runs (mixed case and digits); plain hex such as git shas is kept
    (re.compile(r"(?<![A-Za-z0-9+/-])(?=[A-Za-z0-9+-]*[A-Z])(?=[A-Za-z0-9+-]*[a-z])(?=[A-Za-z0-9+-]*[0-9])"
                r"[A-Za-z0-9+-]{36,}={0,2}"), "[REDACTED]"),
]


def redact(text: str) -> str:
    for pat, repl in _REDACTIONS:
        text = pat.sub(repl, text)
    return text


def tail(path: Path, n: int = 150, width: int = 400) -> str:
    with path.open("r", errors="replace") as f:
        lines = deque(f, maxlen=n)
    return "".join(ln if len(ln) <= width else ln[:width] + "...\n" for ln in lines)


def comment(job: Job, res: dict, sha: str) -> str:
    out = redact(res.get("tail", ""))
    fence = "`" * max(3, max((len(m) for m in re.findall(r"`+", out)), default=0) + 1)
    head = (f"**Research runner** job `{job.id}`: `{job.spec.target}` {' '.join(job.args)}\n\n"
            f"status **{res['status']}**, exit {res.get('rc')}, {res.get('minutes', 0):.1f} min, main @ `{sha[:12]}`, "
            f"finished {res.get('finished', '')}\n\n")
    return head + f"Last {len(out.splitlines())} lines (redacted):\n\n{fence}text\n{out}\n{fence}\n"


def rejected_comment(jid: str, reason: str) -> str:
    return f"**Research runner** rejected job `{jid}` and did not run it: {redact(reason)}\n"


# ---------------------------------------------------------------- state, locks, posting


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(state: Path, msg: str) -> None:
    line = f"{now()} {msg}"
    if sys.stdout.isatty():  # by hand; under launchd stdout is runner.log itself (no line twice)
        print(line, flush=True)
    with (state / "runner.log").open("a") as f:
        f.write(line + "\n")


def load_done(state: Path) -> dict:
    p = state / "done.json"
    return json.loads(p.read_text()) if p.exists() else {}


def save_done(state: Path, done: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=state, prefix=".done.")
    with os.fdopen(fd, "w") as f:
        json.dump(done, f, indent=1, sort_keys=True)
    os.replace(tmp, state / "done.json")


@contextlib.contextmanager
def try_lock(path: Path):
    """A non-blocking exclusive flock; yields True when held, False when another process holds it."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        os.close(fd)


def other_sec_running(patterns, run=subprocess.run) -> str | None:
    """The command line of another bulk SEC fetcher on this machine (from `ps`), else None."""
    if not patterns:
        return None
    try:
        ps = run(["ps", "-Ao", "pid=,command="], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return "ps failed"  # unknown: treat as busy
    me = os.getpid()
    for line in ps.stdout.splitlines():
        pid, _, cmd = line.strip().partition(" ")
        if pid.isdigit() and int(pid) != me and "research_runner" not in cmd and any(p in cmd for p in patterns):
            return cmd[:120]
    return None


def gh_ready(run=subprocess.run) -> bool:
    if not shutil.which("gh"):
        return False
    try:
        return run(["gh", "auth", "status", "--hostname", "github.com"], capture_output=True, text=True,
                   timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def post(repo: str, issue: int, body: str, state: Path, run=subprocess.run) -> bool:
    """POST the comment through the REST API with gh (body from a JSON file, never on a command line)."""
    if not gh_ready(run):
        return False
    fd, tmp = tempfile.mkstemp(dir=state, prefix=".comment.", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump({"body": body[:60000]}, f)
        r = run(["gh", "api", "--method", "POST", f"repos/{repo}/issues/{int(issue)}/comments", "--input", tmp],
                capture_output=True, text=True, timeout=60)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)


def git_pull(root: Path, run=subprocess.run) -> str:
    env = {**job_env(), "GIT_TERMINAL_PROMPT": "0"}
    env.pop("PYTHONPATH", None)
    try:
        br = run(["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True,
                 timeout=30, env=env).stdout.strip()
        if br != "main":
            return f"not pulled: the checkout is on {br!r}, not main"
        r = run(["git", "-C", str(root), "pull", "--ff-only", "--quiet"], capture_output=True, text=True,
                timeout=120, env=env)
        return "pulled" if r.returncode == 0 else f"pull failed ({redact(r.stderr.strip())[:300]}); using local queue"
    except (OSError, subprocess.SubprocessError) as e:
        return f"pull failed ({type(e).__name__}); using local queue"


def head_sha(root: Path, run=subprocess.run) -> str:
    try:
        return run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True,
                   timeout=30).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


# ---------------------------------------------------------------- running a job


def run_job(job: Job, cmd: list[str], logfile: Path, popen=subprocess.Popen, env: dict | None = None) -> dict:
    t0 = datetime.now(UTC)
    status, rc = "ok", None
    with logfile.open("w") as out:
        p = popen(cmd, cwd=str(ROOT), env=job_env() if env is None else env, stdin=subprocess.DEVNULL, stdout=out,
                  stderr=subprocess.STDOUT, start_new_session=True)
        try:
            rc = p.wait(timeout=job.spec.timeout_min * 60)
            status = "ok" if rc == 0 else "failed"
        except subprocess.TimeoutExpired:
            status = "timeout"
            _kill_group(p)
            rc = p.returncode
    return {"status": status, "rc": rc, "minutes": (datetime.now(UTC) - t0).total_seconds() / 60,
            "finished": now()}


def _kill_group(p) -> None:
    for sig, wait in ((signal.SIGTERM, 15), (signal.SIGKILL, 15)):
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(p.pid, sig)
        try:
            p.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


def tick(*, root: Path = ROOT, state: Path = STATE, queue: Path = QUEUE, allowed: Path = ALLOWED, pull: bool = True,
         run=subprocess.run, popen=subprocess.Popen, python: str | None = None) -> dict:
    """One pass over the queue. Returns {id: status} for what this tick did (tests read it)."""
    state.mkdir(parents=True, exist_ok=True)
    (state / "out").mkdir(exist_ok=True)
    (state / "logs").mkdir(exist_ok=True)
    did: dict[str, str] = {}
    with try_lock(state / "runner.lock") as held:
        if not held:
            log(state, "another runner holds the lock; nothing to do")
            return {"_locked": "busy"}
        if pull:
            log(state, git_pull(root, run))
        try:
            cfg = load_allowed(allowed, root)
            jobs = load_queue(queue)
        except (JobError, yaml.YAMLError, OSError) as e:
            log(state, f"cannot read queue or allow-list: {redact(str(e))[:300]}")
            return {"_error": "config"}
        python = python or str(root / ".venv" / "bin" / "python")
        sha = head_sha(root, run)
        done = load_done(state)
        ran = 0
        for raw in jobs:
            jid = raw.get("id") if isinstance(raw, dict) else None
            key = jid if isinstance(jid, str) and ID_RE.fullmatch(jid) else None
            if key and key in done:
                continue
            if ran >= cfg.max_jobs:
                log(state, f"max {cfg.max_jobs} jobs per tick reached; the rest wait for the next tick")
                break
            try:
                job = check_job(raw, cfg)
            except JobError as e:
                if key is None:
                    log(state, f"skipping a job without a valid id: {e}")
                    continue
                done[key] = {"status": "rejected", "reason": str(e)[:300], "finished": now()}
                save_done(state, done)
                did[key] = "rejected"
                _deliver(cfg, state, key, raw.get("posts_to_issue"), rejected_comment(key, str(e)), run)
                log(state, f"{key}: rejected: {e}")
                continue
            if job.spec.sec:
                with try_lock(state / "sec.lock") as sec_held:
                    other = None if not sec_held else other_sec_running(cfg.sec_patterns, run)
                    if not sec_held or other:
                        did[job.id] = "deferred"
                        log(state, f"{job.id}: deferred, another SEC job is running ({other or 'sec.lock held'})")
                        continue
                    _execute(job, cfg, state, done, did, sha, python, run, popen)
            else:
                _execute(job, cfg, state, done, did, sha, python, run, popen)
            ran += 1
    return did


def _execute(job, cfg, state, done, did, sha, python, run, popen) -> None:
    done[job.id] = {"status": "started", "started": now(), "script": job.spec.target}
    save_done(state, done)  # before the run: a job that crashes the Mac is not started again by the next tick
    cmd = argv(job, python)
    log(state, f"{job.id}: start {job.spec.target} {' '.join(job.args)}{' (nice 10)' if job.spec.heavy else ''}")
    logfile = state / "logs" / f"{job.id}.log"
    try:
        res = run_job(job, cmd, logfile, popen)
    except OSError as e:
        res = {"status": "failed", "rc": None, "minutes": 0.0, "finished": now(), "error": type(e).__name__}
    res["tail"] = tail(logfile, cfg.tail_lines) if logfile.exists() else ""
    body = comment(job, res, sha)
    posted = _deliver(cfg, state, job.id, job.issue, body, run)
    done[job.id] = {k: v for k, v in res.items() if k != "tail"} | {"script": job.spec.target, "posted": posted}
    save_done(state, done)
    did[job.id] = res["status"]
    log(state, f"{job.id}: {res['status']} rc={res['rc']}; "
               + (f"posted on #{job.issue}" if posted else f"NOT posted (gh missing or not logged in); "
                  f"see {state / 'out' / (job.id + '.md')}"))


def _deliver(cfg, state, jid, issue, body, run) -> bool:
    (state / "out" / f"{jid}.md").write_text(body)
    ok_issue = isinstance(issue, int) and not isinstance(issue, bool) and 0 < issue < 10**7
    return ok_issue and post(cfg.repo, issue, body, state, run)


def main(argv_: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="validate the queue against the allow-list; run nothing")
    ap.add_argument("--no-pull", action="store_true", help="do not git pull first")
    a = ap.parse_args(argv_)
    if a.check:
        cfg = load_allowed()
        bad = 0
        for raw in load_queue():
            try:
                j = check_job(raw, cfg)
                print(f"ok      {j.id}: {j.spec.target} {' '.join(j.args)} -> #{j.issue}")
            except JobError as e:
                bad += 1
                print(f"REJECT  {raw.get('id') if isinstance(raw, dict) else raw!r}: {e}")
        return 1 if bad else 0
    res = tick(pull=not a.no_pull)
    return 1 if "_error" in res else 0


if __name__ == "__main__":
    sys.exit(main())
