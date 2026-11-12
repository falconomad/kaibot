#!/usr/bin/env python3
"""Free alarms for the always-on engine (issue #121, audit H3): pings a Healthchecks.io check.

Healthchecks.io (free plan) e-mails or pushes when the pings stop or a `/fail` ping arrives. This script is a
separate process next to the engine. It never imports the engine and never sits on the order path: it only reads
the engine's log file and makes short HTTP calls (10 s timeout, in a background thread).

    healthcheck.py start|success|fail|log [message]   one ping (fail/log carry the message)
    healthcheck.py watch --log FILE [--log FILE2] --pid PID
        start ping, then every 5 minutes a success ping while process PID is alive and the engine is not stalled;
        a /fail ping (with the log line) on a halt, drift, lease loss, refused start or refused session, an
        incomplete flatten, an unclean account or a watchdog trip; a /fail when the engine stops ticking mid-session.

The URL comes from HEALTHCHECKS_PING_URL in the environment, or from that one line of the secrets file given with
--env-file (no other line is read). Unset: one log line, then nothing. The URL is never printed.
Stdlib only, Python 3.9+ (macOS /usr/bin/python3 is enough in Docker mode). Runbook: docs/ops/always_on.md.
"""

from __future__ import annotations

import argparse
import os
import re
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


def mid_session(minute: int) -> bool:
    """A tick for this session minute should be followed by another one a minute later. The engine stops logging
    ticks when the market closes: after minute ~388 on a normal day, ~208 on an early close (13:00 ET). Silence
    after a tick in those windows is the close, not a stall."""
    return 0 <= minute < 200 or 210 <= minute < 380


class Monitor:
    """Turns engine log lines and the passing of time into pings. Pure apart from `ping` and `now`."""

    def __init__(self, ping, now=time.time, every_s=EVERY_S, stall_s=STALL_S, repeat_s=REPEAT_S):
        self.ping, self.now = ping, now
        self.every_s, self.stall_s, self.repeat_s = every_s, stall_s, repeat_s
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

    def periodic(self, alive: bool) -> None:
        if not alive:
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


def watch(pinger: Pinger, logs: list[str], pid: int, poll_s: float = 1.0) -> None:
    mon = Monitor(pinger.send)
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
    ap.add_argument("action", choices=["start", "success", "fail", "log", "watch"])
    ap.add_argument("message", nargs="?", default="")
    ap.add_argument("--env-file", default=os.environ.get("KAIBOT_ENV_FILE"))
    ap.add_argument("--log", action="append", default=[])
    ap.add_argument("--pid", type=int)
    a = ap.parse_args(argv)
    pinger = Pinger(url_from(dict(os.environ), a.env_file))
    if a.action == "watch":
        if not a.pid or not a.log:
            ap.error("watch needs --pid and --log")
        if pinger.url:
            watch(pinger, a.log, a.pid)
        return 0
    pinger.send(a.action, a.message)
    pinger.wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
