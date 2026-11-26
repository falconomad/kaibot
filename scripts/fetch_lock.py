"""Run a command while holding an exclusive lock on a file; a second caller does not wait (audit 2, 5.12, #692).

    python3 scripts/fetch_lock.py <lock file> -- <command> [args...]

scripts/fetch_daily.sh runs itself under this, so the service's night job (com.kaibot.daily) and the engine's own
fetch (engine/fetch.py) never download at the same time. The lock is the operating system's (`flock`): it ends with
the process that holds it, so a killed or crashed fetch never leaves a stale lock behind (the pid in the file is
only for the message). Standard library only: it runs before fetch_daily.sh has made its .venv.

Exit code: the command's; 0 without running it when another fetch holds the lock (that one is doing the work).
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import sys


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] != "--":
        print(__doc__.splitlines()[2].strip(), file=sys.stderr)
        return 2
    path, cmd = argv[0], argv[2:]
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        held = os.pread(fd, 32, 0).decode(errors="replace").strip() or "?"
        print(f"Another daily fetch is running (pid {held}, lock {path}): this one is not started.", flush=True)
        return 0
    os.ftruncate(fd, 0)
    os.pwrite(fd, str(os.getpid()).encode(), 0)
    try:
        return subprocess.call(cmd)  # close_fds: the command does not inherit the lock
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
