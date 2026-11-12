"""Code fingerprints: one per rule plus a shared core (decision 15 section 7; audit G1 "frozen code" and G2).

Evidence counts only while the code that produced it is unchanged. Until now one hash covered the whole engine,
so a change to rule B restarted rule A's clock, and `strategies/`, `broker/` and `evaluation/` were not hashed
at all. The spec in `config/gate.yaml` is now a mapping:

    fingerprint:
      core: [glob, ...]              # shared by every rule
      strategies: {rule: [glob, ...]}   # a rule's own files

- A file claimed by any rule leaves the core. Everything else matched by a `core` glob is core, so a new file in
  `strategies/` that no rule claims is shared: touching it restarts every clock (the safe side).
- A rule's fingerprint = hash(core files + that rule's files). It changes when the core or the rule changes and
  not when another rule changes. A rule without an entry has the core fingerprint.
- The engine keeps logging ONE string at start-up, so the parts travel inside it:

      <all>;core=<core>;<rule>=<rule fingerprint>;...        (rules sorted by name)

  `<all>` hashes every file and stays the first 16 characters, which is what the dashboard shows.
  `for_strategy` reads a rule's part back. A string without parts (written before this change, or by a spec
  that is still a plain list) is its own fingerprint for every rule, so old starts never match new ones.

Each hash is the first 16 hex characters of sha256 over the sorted relative paths and file contents, as before.
Standard library only: the engine imports this module through `gate_report`.
"""

from __future__ import annotations

import glob
import hashlib
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

UNKNOWN = "unknown"  # what the engine logs when the fingerprint could not be computed


def _files(root: Path, patterns: Iterable[str]) -> set[str]:
    """Relative POSIX paths of the files matched by the globs."""
    out = set()
    for pat in patterns:
        for f in glob.glob(str(root / pat), recursive=True):
            if Path(f).is_file():
                out.add(Path(f).relative_to(root).as_posix())
    return out


def _hash(root: Path, files: Iterable[str]) -> str:
    h = hashlib.sha256()
    for rel in sorted(files):
        h.update(rel.encode() + b"\0" + (root / rel).read_bytes() + b"\0")
    return h.hexdigest()[:16]


def fileset(root: Path, spec: Mapping) -> dict:
    """{"core": files, "strategies": {rule: files}} for a mapping spec (a claimed file is not core)."""
    strategies = {str(k): _files(root, v or []) for k, v in (spec.get("strategies") or {}).items()}
    claimed = set().union(*strategies.values()) if strategies else set()
    return {"core": _files(root, spec.get("core") or []) - claimed, "strategies": strategies}


def fingerprints(root: Path | str, spec: Mapping) -> dict:
    """{"all": h, "core": h, "strategies": {rule: h}} for a mapping spec."""
    root = Path(root)
    fs = fileset(root, spec)
    every = set(fs["core"]).union(*fs["strategies"].values()) if fs["strategies"] else set(fs["core"])
    return {"all": _hash(root, every), "core": _hash(root, fs["core"]),
            "strategies": {k: _hash(root, fs["core"] | v) for k, v in fs["strategies"].items()}}


def compose(fps: Mapping) -> str:
    """The one string the engine logs: `<all>;core=<core>;<rule>=<fingerprint>;...`."""
    for name in fps["strategies"]:
        if name == "core" or ";" in name or "=" in name:
            raise ValueError(f"rule name {name!r} cannot be used in a fingerprint")
    return ";".join([fps["all"], f"core={fps['core']}", *(f"{k}={v}" for k, v in sorted(fps["strategies"].items()))])


def fingerprint(root: Path | str, spec: Mapping | Sequence[str]) -> str:
    """The code fingerprint for a spec: a mapping gives the composed string, a plain list of globs gives one
    16-character hash over all matched files (the format before per-rule fingerprints)."""
    root = Path(root)
    if isinstance(spec, Mapping):
        return compose(fingerprints(root, spec))
    return _hash(root, _files(root, spec))


def parse(fp: str | None) -> dict | None:
    """The parts of a composed fingerprint, or None for a string without parts."""
    if not fp or ";" not in fp:
        return None
    head, *rest = fp.split(";")
    parts = dict(p.split("=", 1) for p in rest if "=" in p)
    if "core" not in parts:
        return None
    return {"all": head, "core": parts.pop("core"), "strategies": parts}


def for_strategy(fp: str | None, strategy: str | None) -> str | None:
    """The fingerprint that decides `strategy`'s evidence clock: its own part, the core for a rule without
    files of its own, or the whole string when it has no parts (or when no rule is named)."""
    parts = parse(fp)
    if parts is None or strategy is None:
        return fp
    return parts["strategies"].get(strategy, parts["core"])


def uncovered(root: Path | str, spec: Mapping, folders: Iterable[str], suffix: str = ".py") -> list[str]:
    """Files under `folders` (relative to root) that no glob of the spec covers. Empty is the goal (audit G2)."""
    root = Path(root)
    fs = fileset(root, spec)
    covered = set(fs["core"]).union(*fs["strategies"].values()) if fs["strategies"] else set(fs["core"])
    out = []
    for folder in folders:
        for f in sorted((root / folder).rglob(f"*{suffix}")):
            rel = f.relative_to(root).as_posix()
            if "__pycache__" not in rel and rel not in covered:
                out.append(rel)
    return out
