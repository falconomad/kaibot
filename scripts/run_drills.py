"""Run the scripted fail-safe drills and write their pass or fail record (M12 ticket 3.7, #469).

    PYTHONHASHSEED=0 PYTHONPATH=src python scripts/run_drills.py [NAME ...] [--out drills.md] [--json drills.json]

The drills are in tests/drills/drills.py (the simulated venue, the real engine, watchdog and dead-man; no broker,
no Supabase, no key). The record is one table row per drill (pass or fail, how long, the documents it holds the
engine to) and every check under it. Exit code 1 when a drill fails, unless the drill names an open engine issue
(`known`) it is expected to fail on; a known drill that now passes is a failure too (remove the issue from DRILLS).
docs/ops/drills.md explains the drills and the plan for running them on a second paper account.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "drills"))

import drills  # noqa: E402


def record(results: list, commit: str | None) -> str:
    when = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"Drills of {when} on `{commit or '?'}` (simulated venue; docs/ops/drills.md)", "",
             "| drill | result | checks | seconds | holds the engine to |", "|---|---|---|---|---|"]
    for r in results:
        _fn, docs, _known = drills.DRILLS[r.name]
        if r.ok and r.known is None:
            verdict = "PASS"
        elif r.ok:
            verdict = f"**PASS, but #{r.known} is marked open**"
        elif r.known is not None:
            verdict = f"FAIL (known: #{r.known})"
        else:
            verdict = "**FAIL**"
        n_ok = sum(c.ok for c in r.checks)
        where = ", ".join(sorted({d for d, _ in docs}))
        lines.append(f"| {r.name} | {verdict} | {n_ok}/{len(r.checks)} | {r.seconds} | {where} |")
    for r in results:
        lines += ["", f"#### {r.name}"]
        if r.error:
            lines += ["", "```", r.error.strip()[-1500:], "```"]
        lines += [f"- {'PASS' if c.ok else '**FAIL**'} {c.what}: {c.seen}" for c in r.checks]
    return "\n".join(lines) + "\n"


def failed(results: list) -> list[str]:
    bad = []
    for r in results:
        if r.known is None and not r.ok:
            bad.append(r.name)
        if r.known is not None and r.ok:
            bad.append(f"{r.name} (passes: #{r.known} is fixed? remove it from DRILLS)")
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("names", nargs="*", help=f"drills to run (default all: {', '.join(drills.DRILLS)})")
    ap.add_argument("--out", help="write the record (Markdown) here")
    ap.add_argument("--json", help="write the results (JSON) here")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.CRITICAL)
    names = a.names or list(drills.DRILLS)
    unknown = [n for n in names if n not in drills.DRILLS]
    if unknown:
        ap.error(f"unknown drills: {unknown}")
    results = []
    with tempfile.TemporaryDirectory(prefix="kb-drills-") as d:
        for i, n in enumerate(names):
            tmp = Path(d) / f"{i}"
            tmp.mkdir()
            results.append(drills.run(n, tmp))
            print(f"{n}: {'PASS' if results[-1].ok else 'FAIL'} ({results[-1].seconds} s)", file=sys.stderr)
    try:
        commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True,
                                text=True, timeout=10).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        commit = None
    text = record(results, commit)
    print(text)
    if a.out:
        Path(a.out).write_text(text)
    if a.json:
        Path(a.json).write_text(json.dumps([{"name": r.name, "ok": r.ok, "known": r.known, "seconds": r.seconds,
                                             "error": r.error, "checks": [c.__dict__ for c in r.checks]}
                                            for r in results], indent=1))
    bad = failed(results)
    if bad:
        print(f"failed: {', '.join(bad)}", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
