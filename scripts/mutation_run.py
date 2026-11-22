"""Mutation testing of one money-critical module within a time budget (M12 ticket 3.5, #467).

    PYTHONPATH=src python scripts/mutation_run.py run TARGET --minutes 40 [--jobs 4] [--out DIR]
    python scripts/mutation_run.py summary DIR... [--out summary.md]

`run` copies the checkout (without .git, apps/, data/) into a scratch directory (the checkout itself is never
touched), adds a `[tool.mutmut]` section for the target of tests/mutation/targets.toml (mutate only that file, only
its lines the tests reach; run only the target's tests), and runs mutmut (the `mutation` dependency group) until
it is done or the budget is spent. mutmut records each mutant's result as it goes, so a run that runs out of time
still reports every mutant it tried; the rest are "not checked". It writes `<out>/<target>.json` (the counts and
every survivor with its diff) and `<out>/<target>.md`.

`summary` folds the JSON files of several targets into one Markdown table and the survivors' list: the body of the
tracking issue the weekly `mutation` workflow keeps up to date.

A survivor is a line the tests do not notice changing. Each one becomes a test (the issue names the mutant) or is
documented as equivalent (it cannot change behaviour) in tests/mutation/equivalent.toml with the reason, after which
it is not reported again.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
from fnmatch import fnmatchcase
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ROOT / "tests" / "mutation" / "targets.toml"
EQUIVALENT = ROOT / "tests" / "mutation" / "equivalent.toml"
SKIP = {".git", ".venv", ".tmp", "apps", "data", "logs", "mutants", "mutation-results", "results", ".mutmut-cache"}
SURVIVORS_SHOWN = 300  # per target, in the JSON (the Markdown shows fewer)


def load_targets() -> dict[str, dict]:
    return tomllib.loads(TARGETS.read_text())


def load_equivalent() -> list[dict]:
    if not EQUIVALENT.exists():
        return []
    out = tomllib.loads(EQUIVALENT.read_text()).get("equivalent", [])
    for e in out:
        if not str(e.get("reason") or "").strip() or not e.get("mutant"):
            raise ValueError(f"{EQUIVALENT}: every [[equivalent]] needs mutant and a reason: {e}")
    return out


def config(target: dict) -> str:
    def lst(xs):
        return "[" + ", ".join(json.dumps(x) for x in xs) + "]"

    return ("\n[tool.mutmut]\n"
            'source_paths = ["src/kaibot"]\n'  # the whole package is copied: the rest of kaibot imports from it
            f"only_mutate = {lst([target['source']])}\n"
            f"pytest_add_cli_args_test_selection = {lst(target['tests'])}\n"
            'pytest_add_cli_args = ["-p", "no:cacheprovider", "-q"]\n'
            'also_copy = ["config", "tests", "migrations", "docs", "scripts"]\n'
            "mutate_only_covered_lines = true\n")


def prepare(name: str, target: dict, work: Path) -> None:
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", "mutants", "node_modules")
    for p in ROOT.iterdir():  # everything the tests may read (migrations, docs, scripts), never the checkout's state
        if p.name in SKIP or p.name == "pyproject.toml":
            continue
        if p.is_dir():
            shutil.copytree(p, work / p.name, ignore=ignore, symlinks=True)
        else:
            shutil.copy2(p, work / p.name)
    text = (ROOT / "pyproject.toml").read_text()
    if "[tool.mutmut]" in text:
        raise SystemExit("pyproject.toml has its own [tool.mutmut]: this script writes it per target")
    (work / "pyproject.toml").write_text(text + config(target))


def mutmut_bin() -> list[str]:
    exe = shutil.which("mutmut")
    if exe is None:
        raise SystemExit("mutmut is not installed: `uv sync --locked --group mutation`")
    return [exe]


SNAPSHOT_S = 30.0  # how often the results file is copied while mutmut runs (see `results`)


def _codes(meta: Path) -> dict | None:
    """mutmut's results (exit code by mutant), or None while the file is being written or is damaged."""
    try:
        return json.loads(meta.read_text()).get("exit_code_by_key", {})
    except (OSError, ValueError):
        return None


def _snapshot(meta: Path) -> None:
    """Keep the text that was read and checked whole (a second read could catch mutmut mid-rewrite), written to a
    temporary name and moved into place, so the copy itself is never torn either."""
    try:
        text = meta.read_text()
        json.loads(text)
    except (OSError, ValueError):
        return
    good = meta.with_name(meta.name + ".good")
    tmp = good.with_name(good.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, good)


def run_mutmut(work: Path, minutes: float, jobs: int, log, meta: Path | None = None) -> tuple[bool, float]:
    """True: every mutant was tried; False: the budget ran out first. While it runs, the results file (`meta`) is
    copied every SNAPSHOT_S seconds when it reads whole: #561's dispatch showed that the stop at the end of the budget
    can land while mutmut rewrites it, leaving it empty. The stop is an interrupt first (mutmut finishes the write),
    then SIGTERM, then SIGKILL."""
    env = {**os.environ, "PYTHONPATH": "src"}  # inside the copy: `src` is the copy's, never an installed kaibot
    t0 = time.monotonic()
    p = subprocess.Popen([*mutmut_bin(), "run", "--max-children", str(jobs)], cwd=work, env=env, stdout=log,
                         stderr=subprocess.STDOUT, start_new_session=True)
    end = t0 + minutes * 60
    while True:
        try:
            p.wait(timeout=max(0.1, min(SNAPSHOT_S, end - time.monotonic())))
            return p.returncode == 0, time.monotonic() - t0
        except subprocess.TimeoutExpired:
            if meta is not None:
                _snapshot(meta)
            if time.monotonic() >= end:
                break
    for sig, wait in ((signal.SIGINT, 30), (signal.SIGTERM, 30), (signal.SIGKILL, None)):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(p.pid, sig)
        try:
            p.wait(timeout=wait)
            break
        except subprocess.TimeoutExpired:
            continue
    return False, time.monotonic() - t0


SURVIVING = ("survived", "no tests")  # nothing noticed the change: both are survivors


# mutmut 3.8.1's own reading (mutmut.stats.status_by_exit_code), for where mutmut is not installed (the ordinary
# test run); with mutmut installed its map is used
_STATUS = {1: "killed", 3: "killed", 0: "survived", 5: "no tests", 2: "check was interrupted by user",
           None: "not checked", 33: "no tests", 34: "skipped", 35: "suspicious", 36: "timeout",
           37: "caught by type check", -24: "timeout", 24: "timeout", 152: "timeout", 255: "timeout", -11: "segfault",
           -9: "segfault"}


def status_of(code) -> str:
    """mutmut's own reading of a mutant's exit code (mutmut.stats.status_by_exit_code)."""
    try:
        from mutmut.stats import status_by_exit_code
    except ImportError:
        return _STATUS.get(code, "suspicious")
    return status_by_exit_code[code]


def results(work: Path, source: str) -> dict[str, str]:
    """Each mutant's status. The results file as mutmut left it, or, when the stop left it unreadable, the last whole
    copy `run_mutmut` took (a few mutants fewer)."""
    meta = work / "mutants" / (source + ".meta")
    codes = _codes(meta)
    if codes is None:
        codes = _codes(meta.with_name(meta.name + ".good"))
    return {k: status_of(v) for k, v in sorted((codes or {}).items())}


def diff_of(work: Path, mutant: str) -> str:
    env = {**os.environ, "PYTHONPATH": "src"}
    try:
        out = subprocess.run([*mutmut_bin(), "show", mutant], cwd=work, env=env, capture_output=True, text=True,
                             timeout=60)
        return out.stdout.strip()[-4000:]
    except (OSError, subprocess.SubprocessError) as e:
        return f"(no diff: {e})"


def run(name: str, minutes: float, jobs: int, out: Path, work: Path | None = None) -> dict:
    targets = load_targets()
    if name not in targets:
        raise SystemExit(f"unknown target {name}: {', '.join(targets)}")
    target = targets[name]
    own = work is None
    work = Path(tempfile.mkdtemp(prefix=f"kb-mut-{name}-")) if own else work
    out.mkdir(parents=True, exist_ok=True)
    try:
        prepare(name, target, work)
        with open(out / f"{name}.log", "w") as log:
            complete, took = run_mutmut(work, minutes, jobs, log, work / "mutants" / (target["source"] + ".meta"))
        res = results(work, target["source"])
        if not res or all(v == "not checked" for v in res.values()) and took < minutes * 60 - 5:
            tail = (out / f"{name}.log").read_text()[-3000:]
            raise SystemExit(f"mutmut tried nothing for {name} (its clean test run failed?):\n{tail}")
        equivalent = {e["mutant"]: e["reason"] for e in load_equivalent()}
        survivors = []
        for k, s in res.items():
            if s not in SURVIVING:
                continue
            why = next((r for pat, r in equivalent.items() if fnmatchcase(k, pat)), None)
            if why is not None:
                continue
            survivors.append({"mutant": k, "diff": diff_of(work, k) if len(survivors) < SURVIVORS_SHOWN else ""})
        counts: dict[str, int] = {}
        for s in res.values():
            counts[s] = counts.get(s, 0) + 1
        tried = sum(v for k, v in counts.items() if k != "not checked")
        report = {"target": name, "source": target["source"], "complete": complete, "minutes": round(took / 60, 1),
                  "mutants": len(res), "counts": counts, "tried": tried,
                  "score": round(counts.get("killed", 0) / tried, 3) if tried else None,
                  "equivalent": sum(1 for k, s in res.items() if s in SURVIVING
                                    and any(fnmatchcase(k, p) for p in equivalent)),
                  "survivors": survivors}
        (out / f"{name}.json").write_text(json.dumps(report, indent=1))
        (out / f"{name}.md").write_text(summary([report]))
        return report
    finally:
        if own:
            shutil.rmtree(work, ignore_errors=True)


def summary(reports: list[dict], shown: int = 25) -> str:
    lines = ["| target | file | mutants | tried | killed | survived | timeout | not checked | score | run |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(reports, key=lambda r: r["target"]):
        c = r["counts"]
        lines.append(f"| {r['target']} | `{r['source']}` | {r['mutants']} | {r['tried']} | {c.get('killed', 0)} | "
                     f"{len(r['survivors'])} | {c.get('timeout', 0)} | {c.get('not checked', 0)} | "
                     f"{'-' if r['score'] is None else format(r['score'], '.0%')} | "
                     f"{r['minutes']} min{'' if r['complete'] else ', budget spent'} |")
    lines += ["", "Survivors: each becomes a test, or an `[[equivalent]]` entry in tests/mutation/equivalent.toml "
                  "with the reason. Diffs of all survivors are in the run's artifact."]
    for r in sorted(reports, key=lambda r: r["target"]):
        if not r["survivors"]:
            continue
        lines += ["", f"<details><summary>{r['target']}: {len(r['survivors'])} survivors</summary>", ""]
        for s in r["survivors"][:shown]:
            lines += [f"- `{s['mutant']}`", "", "```diff", *(s["diff"].splitlines()[-12:] or ["(no diff)"]), "```"]
        if len(r["survivors"]) > shown:
            lines.append(f"- ... and {len(r['survivors']) - shown} more (artifact `{r['target']}.json`)")
        lines += ["", "</details>"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("target")
    r.add_argument("--minutes", type=float, default=40)
    r.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    r.add_argument("--out", default="mutation-results")
    r.add_argument("--work", help="keep the scratch directory here")
    s = sub.add_parser("summary")
    s.add_argument("dirs", nargs="+")
    s.add_argument("--out")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        rep = run(a.target, a.minutes, a.jobs, Path(a.out), Path(a.work) if a.work else None)
        print(summary([rep], shown=5))
        return 0
    reports = [json.loads(p.read_text()) for d in a.dirs for p in sorted(Path(d).rglob("*.json"))]
    text = summary(reports)
    if a.out:
        Path(a.out).write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
