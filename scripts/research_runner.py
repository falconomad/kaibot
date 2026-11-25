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
After each job the checkout is checked: changes only inside the job's `writes:` folders (allowed.yaml) are committed
on runner/<job id>, pushed (that branch only, never main) and opened as a pull request, and the checkout goes back to
main; any other change is left as it is and the runner runs no more jobs until the checkout is clean.
The runner does not touch the engine, its lease or scripts/service.sh. Runbook: docs/ops/research_runner.md.

QA-Ops audit (25 Nov 2026): the runner works only in its OWN checkout (~/Projects/kaibot-runner, made by
scripts/install_runner.sh), never in the engine's. A tick refuses to run (nothing pulled, nothing run) when its
checkout is the engine's, or inside it, or holds it, or when data/ links into the engine's data/live or data/runner
(the engine's checkout: KAIBOT_ENGINE_CHECKOUT, ~/Projects/kaibot, and the WorkingDirectory of the engine service's
launchd plist, which is only read).

QA-Ops audit 2 7.4 (#714): each tick rotates data/runner/*.log at 10 MB (5 kept: runner.log.1 .. .5) and pings the
runner's own Healthchecks.io check, if one is set up (HEALTHCHECKS_RUNNER_PING_URL in the environment, or the one
line of data/runner/healthchecks_url; never the secrets file): a success ping at the end of a good tick and every 10
minutes while a long job runs, a /fail ping when the tick was refused, paused or could not read its queue, or when
the disk has less than 10 GB free. StartInterval 900: the check's period is 15 minutes (docs/ops/research_runner.md).
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import plistlib
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml
from healthcheck import Pinger, disk_free, disk_problem  # scripts/healthcheck.py (stdlib only, the same folder)

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
# A job's own output folder in the checkout (allowed.yaml `writes:`): only under docs/, always ending in "/".
WRITES_RE = re.compile(r"docs/[a-z0-9_-]{1,64}(/[a-z0-9_-]{1,64}){0,5}/")
# A changed path the runner may commit: plain ASCII names only (no spaces, quotes, backticks or "..").
SAFE_PATH_RE = re.compile(r"[A-Za-z0-9_.-]{1,128}(/[A-Za-z0-9_.-]{1,128}){0,15}")
MAX_COMMIT_FILES, MAX_FILE_BYTES = 400, 2_000_000
BRANCH_PREFIX = "runner/"
PAUSED = "paused.json"           # in data/runner: set while the checkout holds changes the runner will not commit
GIT_USER = ("Kaibot research runner", "research-runner@users.noreply.github.com")  # never the owner's own email

# The engine's checkout, which the runner never works in (QA-Ops audit, 25 Nov 2026): install_runner.sh passes it
# as KAIBOT_ENGINE_CHECKOUT; ~/Projects/kaibot is the default; the engine service's plist is read (never written).
ENGINE_CHECKOUT_ENV = "KAIBOT_ENGINE_CHECKOUT"
DEFAULT_ENGINE_CHECKOUT = "~/Projects/kaibot"
ENGINE_SERVICE_PLIST = "~/Library/LaunchAgents/com.kaibot.engine.plist"
# Folders of the engine's data/ a link in the runner's data/ may never point into (the engine's state, the old runner's)
ENGINE_DATA_PRIVATE = ("live", "runner")


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
    writes: tuple[str, ...] = ()     # folders the job may change in the checkout; committed to a PR, never to main


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
            source = f.read_text()
        elif MODULE_RE.fullmatch(target):
            rel = Path("src", *target.split("."))
            f = next((p for p in ((root / rel).with_suffix(".py"), root / rel / "__main__.py") if p.is_file()), None)
            if f is None:
                raise JobError(f"allowed.yaml: module {target} not found under src/")
            source = f.read_text()
        else:
            raise JobError(f"allowed.yaml: {target!r} is neither scripts/<name>.py nor a kaibot module")
        flags: dict[str, re.Pattern | None] = {}
        for flag, pat in (raw.get("flags") or {}).items():
            if not re.fullmatch(r"--[a-z][a-z0-9-]{0,40}", str(flag)) or flag in FORBIDDEN_FLAGS:
                raise JobError(f"allowed.yaml: {target}: flag {flag!r} may not be allowed")
            # argparse expands unambiguous prefixes (--d -> --data), but an exact option always wins: so every
            # allowed flag must be a real option of the script, never an abbreviation
            if not re.search(r"add_argument\(\s*['\"]" + re.escape(flag) + r"['\"]", source):
                raise JobError(f"allowed.yaml: {target}: {flag} is not an add_argument option of the script")
            flags[flag] = None if pat is None else re.compile(str(pat), re.ASCII)
        writes = raw.get("writes") or []
        writes = [writes] if isinstance(writes, str) else writes
        if not isinstance(writes, list) or not all(isinstance(w, str) and WRITES_RE.fullmatch(w) for w in writes):
            raise JobError(f"allowed.yaml: {target}: writes must be folders under docs/ ending in '/'")
        specs[target] = Spec(target, flags, bool(raw.get("env")), bool(raw.get("heavy")), bool(raw.get("sec")),
                             float(raw.get("timeout_min", 60)), tuple(writes))
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


def redact(text: str, home: str | None = None) -> str:
    for pat, repl in _REDACTIONS:
        text = pat.sub(repl, text)
    home = str(Path.home()) if home is None else home
    return text.replace(home, "~") if len(home) > 1 else text  # the owner's user name stays off GitHub


def tail(path: Path, n: int = 150, width: int = 400, max_bytes: int = 256_000) -> str:
    """The last n lines, reading at most the last max_bytes of the log (a runaway job's log can be huge)."""
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - max_bytes))
        chunk = f.read().decode("utf-8", errors="replace")
    lines = deque(chunk.splitlines(keepends=True), maxlen=n)
    return "".join(ln if len(ln) <= width else ln[:width] + "...\n" for ln in lines)


def comment(job: Job, res: dict, sha: str) -> str:
    out = redact(res.get("tail", ""))
    fence = "`" * max(3, max((len(m) for m in re.findall(r"`+", out)), default=0) + 1)
    head = (f"**Research runner** job `{job.id}`: `{job.spec.target}` {' '.join(job.args)}\n\n"
            f"status **{res['status']}**, exit {res.get('rc')}, {res.get('minutes', 0):.1f} min, main @ `{sha[:12]}`, "
            f"finished {res.get('finished', '')}\n\n")
    note = f"{res['checkout_note']}\n" if res.get("checkout_note") else ""
    return head + note + f"Last {len(out.splitlines())} lines (redacted):\n\n{fence}text\n{out}\n{fence}\n"


def rejected_comment(jid: str, reason: str) -> str:
    why = redact(reason).replace("`", "'")
    return f"**Research runner** rejected job `{jid}` and did not run it:\n\n```text\n{why}\n```\n"


# ---------------------------------------------------------------- the runner's own checkout


def engine_checkouts(env: dict | None = None, plist: str | None = None) -> list[Path]:
    """The engine's checkout(s): KAIBOT_ENGINE_CHECKOUT (else ~/Projects/kaibot) and the WorkingDirectory of the
    engine service's launchd plist when there is one. Read only."""
    env = os.environ if env is None else env
    found = [Path(env.get(ENGINE_CHECKOUT_ENV) or DEFAULT_ENGINE_CHECKOUT).expanduser()]
    with contextlib.suppress(OSError, ValueError, plistlib.InvalidFileException, AttributeError, TypeError):
        wd = plistlib.loads(Path(plist or ENGINE_SERVICE_PLIST).expanduser().read_bytes()).get("WorkingDirectory")
        if wd:
            found.append(Path(wd).expanduser())
    out: list[Path] = []
    for p in found:
        r = p.resolve()
        if r not in out:
            out.append(r)
    return out


def _same(a: Path, b: Path) -> bool:
    """The same folder: by device and inode when both exist (macOS folders are case-insensitive), else by path."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return a == b


def _inside(p: Path, folder: Path) -> bool:
    return any(_same(x, folder) for x in (p, *p.parents))


def own_checkout_problem(root: Path, engines: list[Path]) -> str | None:
    """Why this checkout may not run jobs: it is (or holds, or sits in) the engine's checkout, or its data/ links
    into the engine's data/live or data/runner, or data/ itself is a link. None when it is the runner's own."""
    r = root.resolve()
    for e in engines:
        if _same(r, e):
            return (f"the runner's checkout {r} is the engine's checkout: the runner works only in its own checkout "
                    f"(./scripts/install_runner.sh makes ~/Projects/kaibot-runner; docs/ops/research_runner.md)")
        if _inside(r, e) or _inside(e, r):
            return f"the runner's checkout {r} and the engine's checkout {e} are nested"
    data = root / "data"
    if data.is_symlink():
        return f"{data} is a link (the runner keeps its own data/; only folders inside it may link to the engine's)"
    if not data.is_dir():
        return None
    for child in data.iterdir():
        if not child.is_symlink():
            continue
        target = child.resolve()
        for e in engines:
            private = [(e / "data" / x).resolve() for x in ENGINE_DATA_PRIVATE]
            if _same(target, e) or _same(target, e / "data") or any(_inside(target, x) for x in private):
                return f"data/{child.name} links to the engine's {target}: never the engine's data/live or data/runner"
            if _inside(target, e) and not _inside(target, e / "data"):
                return f"data/{child.name} links into the engine's checkout outside its data/ ({target})"
    return None


# ---------------------------------------------------------------- state, locks, posting

LOG_MAX_BYTES = 10 * 1024 * 1024  # rotate data/runner/*.log at 10 MB
LOG_KEEP = 5  # runner.log.1 (newest) .. runner.log.5
PING_ENV = "HEALTHCHECKS_RUNNER_PING_URL"
PING_FILE = "healthchecks_url"  # in data/runner: one line, the runner check's ping URL (chmod 600)
KEEPALIVE_S = 600  # success ping while a tick runs a long job (a heavy job may take 240 minutes)


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(state: Path, msg: str) -> None:
    line = f"{now()} {msg}"
    if sys.stdout.isatty():  # by hand; under launchd stdout is runner.log itself (no line twice)
        print(line, flush=True)
    with (state / "runner.log").open("a") as f:
        f.write(line + "\n")


def rotate_logs(state: Path, max_bytes: int = LOG_MAX_BYTES, keep: int = LOG_KEEP) -> list[str]:
    """Rotate every data/runner/*.log over `max_bytes`: x.log -> x.log.1 -> ... -> x.log.<keep> (the oldest goes).
    Copy, then truncate in place: launchd's stdout and stderr for this tick are open on runner.log in append mode,
    and they keep writing to the (now empty) file instead of following a renamed one. Returns the names rotated."""
    done = []
    for path in sorted(state.glob("*.log")):
        try:
            if not path.is_file() or path.is_symlink() or path.stat().st_size < max_bytes:
                continue
            for i in range(keep, 1, -1):
                older = path.with_name(f"{path.name}.{i - 1}")
                if older.exists():
                    os.replace(older, path.with_name(f"{path.name}.{i}"))
            shutil.copyfile(path, path.with_name(f"{path.name}.1"))
            with path.open("r+b") as f:
                f.truncate(0)
            done.append(path.name)
        except OSError as e:  # a rotation problem must never stop the tick
            print(f"{now()} log rotation of {path.name} failed: {type(e).__name__}", file=sys.stderr, flush=True)
    return done


def ping_url(state: Path, env: dict | None = None) -> str:
    """The runner check's ping URL: the environment, else the one line of data/runner/healthchecks_url; '' if none."""
    url = ((os.environ if env is None else env).get(PING_ENV) or "").strip()
    if not url:
        with contextlib.suppress(OSError, UnicodeDecodeError):
            url = (state / PING_FILE).read_text().splitlines()[0].strip() if (state / PING_FILE).exists() else ""
    return url.rstrip("/") if url.startswith("https://") else ""


def tick_problem(res: dict, free: int | None) -> str | None:
    """What a person should look at after this tick, else None. A tick that found the lock busy is no problem (a
    tick by hand beside launchd's); everything else that kept jobs from running is."""
    for key, what in (("_refused", "refused"), ("_paused", "paused"), ("_error", "cannot read queue or allow-list")):
        if key in res:
            return f"runner {what}: {redact(str(res[key]))[:300]}"
    return disk_problem(free)


@contextlib.contextmanager
def keepalive(pinger, every_s: float = KEEPALIVE_S):
    """A success ping every `every_s` while the block runs (a long job), from a daemon thread."""
    stop = threading.Event()

    def beat():
        while not stop.wait(every_s):
            pinger.send("success", "research runner: a job is running")

    t = threading.Thread(target=beat, daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()
        t.join(5)


class _NoPing:
    """No runner check set up: the alarms are off, and the tick says nothing about it (every 15 minutes)."""

    def send(self, kind: str, body: str = "") -> None:
        pass

    def wait(self, timeout: float = 0) -> None:
        pass


def report(pinger, res: dict, free: int | None) -> str | None:
    """The tick's ping: /fail with the reason, else success with what it did. Returns the problem, if any."""
    why = tick_problem(res, free)
    if why:
        pinger.send("fail", f"kaibot {why}")
    elif "_locked" not in res:
        ran = ", ".join(f"{k} {v}" for k, v in sorted(res.items())) or "nothing to run"
        pinger.send("success", f"research runner tick: {ran}"[:1000])
    pinger.wait()
    return why


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


def gh_post(endpoint: str, payload: dict, state: Path, run=subprocess.run) -> str | None:
    """POST to the REST API with gh (the payload from a JSON file, never on a command line). Its stdout, or None."""
    fd, tmp = tempfile.mkstemp(dir=state, prefix=".gh.", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f)
        r = run(["gh", "api", "--method", "POST", endpoint, "--input", tmp], capture_output=True, text=True,
                timeout=60)
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)


def post(repo: str, issue: int, body: str, state: Path, run=subprocess.run) -> bool:
    """POST the comment on the issue (REST)."""
    if not gh_ready(run):
        return False
    return gh_post(f"repos/{repo}/issues/{int(issue)}/comments", {"body": body[:60000]}, state, run) is not None


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


# ---------------------------------------------------------------- the checkout after a job


def _git_env(full: bool = False) -> dict[str, str]:
    """git's environment: the job's short list (or, for the push only, the runner's own, so gh can log in)."""
    env = dict(os.environ) if full else job_env()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONUNBUFFERED", None)
    env |= {"GIT_TERMINAL_PROMPT": "0", "GIT_LITERAL_PATHSPECS": "1"}
    return env


def _git(root: Path, *args: str, run=subprocess.run, full_env: bool = False, timeout: int = 120):
    """One git command (an argv, no shell). A CompletedProcess; returncode -1 when git could not be run."""
    cmd = ["git", "-C", str(root), *args]
    try:
        return run(cmd, capture_output=True, text=True, errors="surrogateescape", timeout=timeout,
                   env=_git_env(full_env))
    except (OSError, subprocess.SubprocessError) as e:
        return subprocess.CompletedProcess(cmd, -1, "", type(e).__name__)


def checkout_changes(root: Path, run=subprocess.run) -> list[str] | None:
    """Every changed path in the checkout: tracked (staged or not), deleted, renamed (both names) and untracked.
    Files git ignores (data/, .venv/) are not changes. None when git status fails (treated as not clean)."""
    r = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=none", run=run)
    if r.returncode != 0:
        return None
    paths: list[str] = []
    tokens = iter(r.stdout.split("\0"))
    for entry in tokens:
        if len(entry) < 4:
            continue
        paths.append(entry[3:])
        if "R" in entry[:2] or "C" in entry[:2]:  # a rename or copy: the next token is the old name
            paths.append(next(tokens, ""))
    return paths


def inside_writes(path: str, writes: tuple[str, ...]) -> bool:
    return (bool(SAFE_PATH_RE.fullmatch(path)) and not {"..", "."} & set(path.split("/"))
            and any(path.startswith(w) for w in writes))


def _file_problem(root: Path, rel: str, home: str | None) -> str | None:
    """Why a file the job wrote may not be published (a link, too big, not text, or something redaction would hide)."""
    p = root / rel
    if not os.path.lexists(p):
        return None  # deleted: committing the deletion publishes nothing
    st = os.lstat(p)
    if not stat.S_ISREG(st.st_mode):
        return "not a regular file (a link or a folder)"
    if st.st_size > MAX_FILE_BYTES:
        return f"larger than {MAX_FILE_BYTES // 1_000_000} MB"
    try:
        text = p.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return "not UTF-8 text"
    if redact(text, home) != text:
        return "holds text that redaction would hide (a key, token, email or the home path)"
    return None


def _fenced(lines: list[str]) -> str:
    # a non-UTF-8 file name arrives surrogate-escaped from git: shown as \udcxx, never written out raw
    text = "\n".join(lines).encode("utf-8", "backslashreplace").decode("utf-8")
    text = redact(text).replace("`", "'")
    return f"```text\n{text}\n```"


def _hold(paths: list[str], why: str) -> dict:
    shown = paths[:20] + ([f"... and {len(paths) - 20} more"] if len(paths) > 20 else [])
    note = (f"**Warning:** the job left changes in the checkout on the research Mac that the runner will not "
            f"commit: {why}. The runner left them as they are and **runs no more jobs** until the checkout is "
            f"clean again (the owner commits, moves or removes them; nothing was deleted).\n\n{_fenced(shown)}\n")
    return {"checkout": "held", "note": note, "reason": redact(why)[:300]}


def _pr_text(job: Job, res: dict, sha: str, paths: list[str]) -> tuple[str, str, str]:
    """The commit message, the PR title and the PR body (all redacted; no home path, no secrets)."""
    folders = ", ".join(job.spec.writes)
    title = f"Research runner: results of job {job.id} ({job.spec.target})"
    cmd = " ".join([job.spec.target, *job.args])
    what = (f"The research runner on the research Mac ran `{cmd}` (job {job.id}, status {res['status']}, exit "
            f"{res.get('rc')}, main @ {sha[:12]}) and committed the {len(paths)} changed file(s) it wrote under "
            f"{folders}. The runner never pushes to main; review and merge this pull request to keep the results.")
    message = f"{title}\n\n{what}\n"
    shown = paths[:60] + ([f"... and {len(paths) - 60} more"] if len(paths) > 60 else [])
    body = f"{what}\n\nJob comment: #{job.issue}\n\nFiles:\n\n{_fenced(shown)}\n"
    return redact(message), redact(title), redact(body)


def settle_checkout(job: Job, res: dict, sha: str, cfg: Config, root: Path, state: Path,
                    run=subprocess.run, home: str | None = None) -> dict:
    """After a job: a clean checkout stays as it is. Changes only inside the job's `writes` folders are committed on
    runner/<id>, pushed with gh's credentials to that branch (never main), opened as a PR, and the checkout goes
    back to main (only those paths change on disk). Any other change, or anything unexpected, is held: nothing is
    committed or removed, and the runner pauses until the checkout is clean."""
    paths = checkout_changes(root, run)
    if paths is None:
        return _hold([], "git status failed")
    if not paths:
        return {"checkout": "clean", "note": ""}
    outside = [p for p in paths if not inside_writes(p, job.spec.writes)]
    if outside:
        where = ", ".join(job.spec.writes) or "nothing (no writes: folder in allowed.yaml)"
        return _hold(outside, f"{len(outside)} changed path(s) outside the job's output folders ({where})")
    if len(paths) > MAX_COMMIT_FILES:
        return _hold(paths, f"more than {MAX_COMMIT_FILES} changed files")
    if (b := _git(root, "rev-parse", "--abbrev-ref", "HEAD", run=run).stdout.strip()) != "main":
        return _hold(paths, f"the checkout is on {b!r}, not main")
    head = _git(root, "rev-parse", "HEAD", run=run).stdout.strip()
    upstream = _git(root, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/main", run=run).stdout.strip()
    if not head or head != upstream:
        return _hold(paths, "local main is not origin/main (local commits, or the last pull failed)")
    bad = [f"{p}: {why}" for p in paths if (why := _file_problem(root, p, home))]
    if bad:
        return _hold(bad, f"{len(bad)} file(s) may not be published")
    branch = BRANCH_PREFIX + job.id
    if _git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", run=run).returncode == 0:
        return _hold(paths, f"a local branch {branch} already exists")
    message, title, body = _pr_text(job, res, sha, paths)

    if _git(root, "checkout", "-q", "-b", branch, run=run).returncode != 0:
        return _hold(paths, f"could not create the branch {branch}")
    committed = all(_git(root, "add", "-A", "--", *paths[i:i + 100], run=run).returncode == 0
                    for i in range(0, len(paths), 100))
    committed = committed and _git(
        root, "-c", f"user.name={GIT_USER[0]}", "-c", f"user.email={GIT_USER[1]}", "-c", "commit.gpgsign=false",
        "commit", "-q", "--no-verify", "-m", message, run=run).returncode == 0
    if not committed:  # back to main with the changes still on disk (same commit), and pause
        _git(root, "reset", "-q", run=run)
        _git(root, "checkout", "-q", "main", run=run)
        _git(root, "branch", "-q", "-D", branch, run=run)
        return _hold(paths, "git could not commit them")

    pushed, url = False, None
    gh = shutil.which("gh")
    if gh and gh_ready(run):
        helper = f"!{shlex.quote(gh)} auth git-credential"
        dest = f"https://github.com/{cfg.repo}.git"
        refspec = f"refs/heads/{branch}:refs/heads/{branch}"
        if not branch.startswith(BRANCH_PREFIX) or not refspec.startswith(f"refs/heads/{BRANCH_PREFIX}"):
            raise RuntimeError("the runner never pushes to main")  # unreachable: ids cannot make "main"
        pushed = _git(root, "-c", "credential.helper=", "-c", f"credential.helper={helper}", "push", "--no-verify",
                      "--porcelain", dest, refspec, run=run, full_env=True, timeout=180).returncode == 0
        if pushed:
            out = gh_post(f"repos/{cfg.repo}/pulls", {"title": title, "head": branch, "base": "main", "body": body},
                          state, run)
            with contextlib.suppress(ValueError, TypeError, AttributeError):
                u = json.loads(out or "")["html_url"]
                url = u if re.fullmatch(rf"https://github\.com/{re.escape(cfg.repo)}/pull/[0-9]+", u) else None

    # back to clean main: the branch holds the files, and main == origin/main, so switching back changes only them
    _git(root, "checkout", "-q", "main", run=run)
    after = checkout_changes(root, run)
    on = _git(root, "rev-parse", "--abbrev-ref", "HEAD", run=run).stdout.strip()
    if after is None or after or on != "main":
        return _hold(after or [], f"the checkout is not clean main after the commit (branch {branch} kept)")
    if pushed:
        _git(root, "branch", "-q", "-D", branch, run=run)
    files = f"{len(paths)} file(s) under {', '.join(job.spec.writes)}"
    if url:
        note = f"Results: {files} committed on `{branch}`, pull request {url}. The checkout is back on clean main."
        return {"checkout": "pr", "note": note + "\n", "pr": url}
    if pushed:
        note = (f"Results: {files} pushed to the branch `{branch}`, but opening the pull request failed; open it "
                f"from that branch. The checkout is back on clean main.")
        return {"checkout": "pushed", "note": note + "\n"}
    note = (f"Results: {files} committed on the local branch `{branch}` on the research Mac, **not pushed** (gh is "
            f"missing, not logged in, or the push failed). The checkout is back on clean main.")
    return {"checkout": "committed", "note": note + "\n"}


def paused_reason(root: Path, state: Path, run=subprocess.run) -> str | None:
    """Why no job may start: the checkout is not clean. Clears data/runner/paused.json once it is clean again."""
    paths = checkout_changes(root, run)
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD", run=run).stdout.strip()
    if branch.startswith(BRANCH_PREFIX):  # a results commit that did not get back to main
        return f"the checkout is on the runner's branch {branch!r}, not main"
    if paths is None or paths:
        what = "git status failed" if paths is None else f"{len(paths)} changed path(s)"
        held = None
        with contextlib.suppress(OSError, ValueError, AttributeError):
            held = json.loads((state / PAUSED).read_text()).get("job")
        return f"the checkout is not clean ({what}{f', held after job {held}' if held else ''})"
    with contextlib.suppress(FileNotFoundError):
        (state / PAUSED).unlink()
        log(state, "the checkout is clean again; jobs resume")
    return None


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
         run=subprocess.run, popen=subprocess.Popen, python: str | None = None,
         engines: list[Path] | None = None) -> dict:
    """One pass over the queue. Returns {id: status} for what this tick did (tests read it). Refused, before any
    pull or job, in the engine's checkout (`own_checkout_problem`; `engines` default: `engine_checkouts()`)."""
    if why := own_checkout_problem(root, engine_checkouts() if engines is None else engines):
        try:
            log(state, f"refused: {why}; nothing pulled, no job run")
        except OSError:  # no state folder (a checkout the runner never ran in): stderr only, nothing created
            print(f"{now()} research runner refused: {why}", file=sys.stderr, flush=True)
        return {"_refused": why}
    state.mkdir(parents=True, exist_ok=True)
    (state / "out").mkdir(exist_ok=True)
    (state / "logs").mkdir(exist_ok=True)
    did: dict[str, str] = {}
    with try_lock(state / "runner.lock") as held:
        if not held:
            log(state, "another runner holds the lock; nothing to do")
            return {"_locked": "busy"}
        for name in rotate_logs(state):
            log(state, f"{name} reached {LOG_MAX_BYTES // 2**20} MB: rotated ({LOG_KEEP} kept)")
        if why := paused_reason(root, state, run):  # before the pull: a dirty checkout is never pulled or run in
            log(state, f"paused: {why}; no job runs until it is clean")
            return {"_paused": why}
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
                    held = _execute(job, cfg, state, done, did, sha, python, run, popen, root)
            else:
                held = _execute(job, cfg, state, done, did, sha, python, run, popen, root)
            ran += 1
            if held:
                log(state, f"paused after {job.id}: the checkout holds changes the runner will not commit")
                break
    return did


def _execute(job, cfg, state, done, did, sha, python, run, popen, root) -> bool:
    """Run one job, settle the checkout, post the result. True when the runner must pause (changes held)."""
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
    settled = settle_checkout(job, res, sha, cfg, root, state, run)
    res["checkout_note"] = settled["note"]
    if settled["checkout"] == "held":
        (state / PAUSED).write_text(json.dumps({"job": job.id, "since": now(), "reason": settled["reason"]}))
    body = comment(job, res, sha)
    posted = _deliver(cfg, state, job.id, job.issue, body, run)
    done[job.id] = ({k: v for k, v in res.items() if k not in ("tail", "checkout_note")}
                    | {"script": job.spec.target, "posted": posted, "checkout": settled["checkout"]}
                    | ({"pr": settled["pr"]} if settled.get("pr") else {}))
    save_done(state, done)
    did[job.id] = res["status"]
    log(state, f"{job.id}: {res['status']} rc={res['rc']}; "
               + (f"posted on #{job.issue}" if posted else f"NOT posted (gh missing or not logged in); "
                  f"see {state / 'out' / (job.id + '.md')}") + f"; checkout {settled['checkout']}")
    return settled["checkout"] == "held"


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
    url = ping_url(STATE)
    pinger = Pinger(url) if url else _NoPing()
    with keepalive(pinger):
        res = tick(pull=not a.no_pull)
    if why := report(pinger, res, disk_free(str(ROOT))):
        with contextlib.suppress(OSError):
            log(STATE, f"alarm: {why}")
    return 3 if "_refused" in res else 1 if "_error" in res else 0


if __name__ == "__main__":
    sys.exit(main())
