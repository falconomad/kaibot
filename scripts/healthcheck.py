#!/usr/bin/env python3
"""Free alarms for the always-on engine (issue #121, audit H3): pings a Healthchecks.io check.

Healthchecks.io (free plan) e-mails or pushes when the pings stop or a `/fail` ping arrives. This script is a
separate process next to the engine. It never imports the engine and never sits on the order path: it only reads
the engine's log file and makes short HTTP calls (10 s timeout, in a background thread).

    healthcheck.py start|success|fail|log [message]   one ping (fail/log carry the message)
    healthcheck.py watch --log FILE [--log FILE2] --pid PID
        start ping, then every 5 minutes a success ping while process PID is alive and the engine is not stalled;
        a /fail ping (with the log line) on a halt, drift, lease loss, refused start or refused session, an
        incomplete flatten, an unclean account or a watchdog trip; a /fail when the engine stops ticking mid-session;
        a /fail when the disk holding --disk (default: the repo) has less than 10 GB free, and no success ping
        while it stays that low (so the check stays red), repeated at most every 30 minutes.
    healthcheck.py disk [--disk PATH] [--min-gb 10]
        one disk check: a /fail ping and exit 1 when free space is under the limit; else nothing sent, exit 0
        (never a success ping: that would mark a stopped engine as up).

The URL comes from HEALTHCHECKS_PING_URL in the environment, or from that one line of the secrets file given with
--env-file (no other line is read). Unset: one log line, then nothing. The URL is never printed.
Stdlib only, Python 3.9+ (macOS /usr/bin/python3 is enough in Docker mode). Runbook: docs/ops/always_on.md.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import threading
import time
import urllib.request

ENV_KEY = "HEALTHCHECKS_PING_URL"
TIMEOUT_S = 10
EVERY_S = 300  # success ping while alive
STALL_S = 600  # no tick for this long, mid-session: stalled
REPEAT_S = 1800  # the same kind of alarm at most once per 30 minutes
SUFFIX = {"success": "", "start": "/start", "fail": "/fail", "log": "/log"}
DISK_MIN_GB = 10  # audit 2 7.4 (#714): a full disk stops journal writes, and the engine then refuses to start
DISK_EVERY_S = 60  # how often `watch` looks at the disk
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Log lines that mean "a person should look". Texts come from kaibot.engine (core, lease, __main__, watchdog).
ALERTS = [
    ("halt", re.compile(r"entries halted: ")),  # every fail-closed halt, drift included ("entries halted: drift")
    ("lease_lost", re.compile(r"The engine lease is no longer ours|engine stopped: lease lost")),
    ("lease_refused", re.compile(r"Another Kaibot engine holds the lease|The engine lease could not be checked")),
    ("session_refused", re.compile(r"session refused: ")),
    ("flatten_incomplete", re.compile(r"flatten \(.*\) incomplete")),
    ("account_not_clean", re.compile(r"account not clean: ")),
    ("watchdog_tripped", re.compile(r"engine heartbeat .* old: entries canceled")),
    ("watchdog_flatten", re.compile(r"15:55 with the engine down")),
    # QA-Ops (#523): M9's alerts are ERROR lines and health events; without these they reach only someone who opens
    # the Health page. Each is logged once per occurrence (a poison cause at most hourly, a skew when it starts, a
    # calendar once per session), and the same kind pings at most once per 30 minutes.
    ("replica_poison", re.compile(r"replica poison: ")),
    ("replica_backlog", re.compile(r"replica backlog: ")),
    ("clock_skew", re.compile(r"clock skew: ")),  # not "clock skew over: ..."
    ("calendar_expiring", re.compile(r"calendar expiring: ")),
    # QA-Ops (24-25 Nov 2026 incident): the engine was down or frozen (the Mac asleep) for more than an hour
    ("engine_gap", re.compile(r"engine gap: ")),
]
TICK = re.compile(r" tick \{")
MINUTE = re.compile(r"'minute': (-?\d+)")


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S") + " healthcheck " + msg, file=sys.stderr, flush=True)


def url_from(env: dict, env_file: str | None) -> str:
    url = (env.get(ENV_KEY) or "").strip()
    if not url and env_file and os.path.isfile(env_file):
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                if line.startswith(ENV_KEY + "="):
                    url = line.split("=", 1)[1].split(" #", 1)[0].strip().strip("'\"")
                    break
    return url.rstrip("/")


class Pinger:
    """Sends pings without blocking the caller. No URL: logs once and does nothing."""

    def __init__(self, url: str, opener=urllib.request.urlopen):
        self.url, self.opener = url, opener
        self.threads: list[threading.Thread] = []
        if not url:
            log(f"{ENV_KEY} is not set: alarms are off (docs/ops/always_on.md, step 1)")

    def send(self, kind: str, body: str = "") -> None:
        if not self.url:
            return
        t = threading.Thread(target=self._post, args=(kind, body), daemon=True)
        t.start()
        self.threads.append(t)
        self.threads = [x for x in self.threads if x.is_alive()]

    def _post(self, kind: str, body: str) -> None:
        req = urllib.request.Request(self.url + SUFFIX[kind], data=body[:2000].encode("utf-8", "replace"),
                                     method="POST", headers={"User-Agent": "kaibot-healthcheck"})
        try:
            with self.opener(req, timeout=TIMEOUT_S) as r:
                r.read()
        except Exception as e:  # an alarm channel must never take anything down
            log(f"{kind} ping failed: {type(e).__name__}")

    def wait(self, timeout: float = TIMEOUT_S + 1) -> None:
        for t in list(self.threads):
            t.join(timeout)


def disk_free(path: str) -> int | None:
    """Free bytes on the volume holding `path` (for an unprivileged user); None when it cannot be read."""
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def disk_problem(free: int | None, min_gb: float = DISK_MIN_GB, path: str = "") -> str | None:
    """The alarm text when `free` bytes is under `min_gb` GB (10^9 bytes, as macOS counts), else None. Unreadable
    is not an alarm: the alarm channel must not cry wolf on a transient error."""
    if free is None or free >= min_gb * 1e9:
        return None
    return f"disk low: {free / 1e9:.1f} GB free{' on ' + path if path else ''} (limit {min_gb:g} GB)"


def mid_session(minute: int) -> bool:
    """A tick for this session minute should be followed by another one a minute later. The engine stops logging
    ticks when the market closes: after minute ~388 on a normal day, ~208 on an early close (13:00 ET). Silence
    after a tick in those windows is the close, not a stall."""
    return 0 <= minute < 200 or 210 <= minute < 380


class Monitor:
    """Turns engine log lines and the passing of time into pings. Pure apart from `ping` and `now`."""

    def __init__(self, ping, now=time.time, every_s=EVERY_S, stall_s=STALL_S, repeat_s=REPEAT_S,
                 disk=None, disk_min_gb: float = DISK_MIN_GB, disk_every_s: float = DISK_EVERY_S):
        self.ping, self.now = ping, now
        self.every_s, self.stall_s, self.repeat_s = every_s, stall_s, repeat_s
        self.disk, self.disk_min_gb, self.disk_every_s = disk, disk_min_gb, disk_every_s  # disk(): free bytes
        self.disk_low: str | None = None
        self.disk_checked: float | None = None
        self.last_success = now()  # the start ping counts: a crash loop must not flip the check up every minute
        self.last_tick: tuple[float, int] | None = None
        self.sent: dict[str, float] = {}
        self.stalled = False

    def line(self, text: str) -> None:
        text = text.rstrip("\n")
        if TICK.search(text):
            m = MINUTE.search(text)
            self.last_tick = (self.now(), int(m.group(1)) if m else -1)
            return
        for kind, pat in ALERTS:
            if pat.search(text):
                self.alarm(kind, text)
                return

    def alarm(self, kind: str, text: str) -> None:
        t = self.now()
        if kind in self.sent and t - self.sent[kind] < self.repeat_s:
            return
        self.sent[kind] = t
        self.ping("fail", f"kaibot {kind}: {text}"[:1000])

    def is_stalled(self) -> bool:
        if self.last_tick is None:
            return False
        t0, minute = self.last_tick
        age = self.now() - t0
        return mid_session(minute) and self.stall_s <= age < 6 * 3600

    def check_disk(self) -> None:
        t = self.now()
        if self.disk is None or (self.disk_checked is not None and t - self.disk_checked < self.disk_every_s):
            return
        self.disk_checked = t
        self.disk_low = disk_problem(self.disk(), self.disk_min_gb)
        if self.disk_low:
            self.alarm("disk_low", self.disk_low)

    def periodic(self, alive: bool) -> None:
        if not alive:
            return
        self.check_disk()
        if self.disk_low:  # no success ping while the disk is low: the check stays red until there is room
            return
        if self.is_stalled():
            if not self.stalled:
                self.stalled = True
                age = int((self.now() - self.last_tick[0]) / 60)
                self.ping("fail", f"kaibot stalled: no engine tick for {age} minutes during the session")
            return
        self.stalled = False
        t = self.now()
        if t - self.last_success >= self.every_s:
            self.last_success = t
            self.ping("success", "")


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Tail:
    """Follows a file from its current end; survives truncation (copy-truncate rotation) and replacement."""

    def __init__(self, path: str):
        self.path, self.f, self.ino = path, None, None
        self._open(at_end=True)

    def _open(self, at_end: bool) -> None:
        try:
            self.f = open(self.path, encoding="utf-8", errors="replace")  # noqa: SIM115
            self.ino = os.fstat(self.f.fileno()).st_ino
            if at_end:
                self.f.seek(0, os.SEEK_END)
        except OSError:
            self.f = None

    def lines(self) -> list[str]:
        if self.f is None:
            self._open(at_end=False)
            if self.f is None:
                return []
        try:
            st = os.stat(self.path)
            if st.st_ino != self.ino:
                self.f.close()
                self._open(at_end=False)
            elif st.st_size < self.f.tell():
                self.f.seek(0)
        except OSError:
            return []
        return self.f.readlines() if self.f else []


def watch(pinger: Pinger, logs: list[str], pid: int, poll_s: float = 1.0, disk_path: str = REPO,
          disk_min_gb: float = DISK_MIN_GB) -> None:
    mon = Monitor(pinger.send, disk=lambda: disk_free(disk_path), disk_min_gb=disk_min_gb)
    tails = [Tail(p) for p in logs]
    pinger.send("start", "kaibot engine starting")
    while True:
        alive = pid_alive(pid)
        for t in tails:
            for ln in t.lines():
                mon.line(ln)
        if not alive:
            break
        mon.periodic(True)
        time.sleep(poll_s)
    pinger.wait()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("action", choices=["start", "success", "fail", "log", "watch", "disk"])
    ap.add_argument("message", nargs="?", default="")
    ap.add_argument("--env-file", default=os.environ.get("KAIBOT_ENV_FILE"))
    ap.add_argument("--log", action="append", default=[])
    ap.add_argument("--pid", type=int)
    ap.add_argument("--disk", default=REPO, help="a path on the volume to watch (default: the repo)")
    ap.add_argument("--min-gb", type=float, default=DISK_MIN_GB)
    a = ap.parse_args(argv)
    pinger = Pinger(url_from(dict(os.environ), a.env_file))
    if a.action == "watch":
        if not a.pid or not a.log:
            ap.error("watch needs --pid and --log")
        if pinger.url:
            watch(pinger, a.log, a.pid, disk_path=a.disk, disk_min_gb=a.min_gb)
        return 0
    if a.action == "disk":
        why = disk_problem(disk_free(a.disk), a.min_gb, a.disk)
        if why:
            log(why)
            pinger.send("fail", f"kaibot disk_low: {why}")
            pinger.wait()
        return 1 if why else 0
    pinger.send(a.action, a.message)
    pinger.wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
