"""One trial registry (decision 15 section 7; audit F5).

Every replay, walk-forward and ablation logs one row per RUN. A row's `n_trials` is the number of CONFIGURATIONS
that run searched (a tuned walk-forward: grid size x folds; a plain replay: 1). The deflated Sharpe needs the
number of configurations, so every consumer counts with `count_configurations` here: the walk-forward scripts
(`scripts/walk_forward.py::logged_trials`), `scripts/trial_report.py`, the M4 gate report and gate v2. Before this
module the walk-forward summed `n_trials` (8,286) while the live gate counted rows (a few dozen), so the live
deflated Sharpe was barely deflated.

Where the registry lives
- `data/trials.jsonl` on the research machine (scripts only ever open it with "a");
- the table `kaibot.trial_registry` (migration 0015, append-only by trigger) once the stakeholder has applied it;
- the older `kaibot.trial` table (config replays), read as one more source.
`load` merges what it can read and removes duplicates by trial id, so a run that is in the file and in the
database counts once. Nothing is ever undercounted on purpose: a line that cannot be parsed still counts as one
configuration, and a duplicated id keeps the larger `n_trials`.

Inside the engine container the file is looked for at `$KAIBOT_TRIALS_FILE`, then `<repo>/data/trials.jsonl`,
then `$KAIBOT_DATA_DIR/trials.jsonl` (docs/ops/gate_v2.md describes the mount).

Standard library only: the engine imports this module through `gate_report`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

TRIALS_ENV = "KAIBOT_TRIALS_FILE"
DATA_ENV = "KAIBOT_DATA_DIR"
TABLE = "trial_registry"  # migration 0015
LEGACY_TABLE = "trial"  # migration 0001: config replays only
PAGE = 1000  # PostgREST returns at most 1,000 rows a request on Supabase
MAX_PAGES = 500
SQRT_252 = math.sqrt(252)


# ---------------------------------------------------------------- one row


def configurations_of(row: Mapping[str, Any]) -> int:
    """Configurations one registry row stands for: its `n_trials` when that is a whole number >= 1, else 1."""
    v = row.get("n_trials")
    if isinstance(v, bool) or v is None:
        return 1
    try:
        n = int(v)
    except (TypeError, ValueError, OverflowError):
        return 1
    return n if n >= 1 else 1


def trial_id(row: Mapping[str, Any]) -> str | None:
    """The id that makes a run unique across the file and the database, or None when the row has none.

    A config replay is written twice by `scripts/run_config_replays.py`: to `kaibot.trial` without an id and to
    the file as `config_r<run>_v<version>`. Both get that id here, so the replay counts once."""
    tid = row.get("trial_id")
    if isinstance(tid, str) and tid.strip():
        return tid.strip()
    p = row.get("params")
    if isinstance(p, Mapping) and p.get("kind") == "config" and p.get("run_id") is not None \
            and p.get("config_version") is not None:
        return f"config_r{p['run_id']}_v{p['config_version']}"
    return None


def _content_id(row: Mapping[str, Any], seen: dict[str, int]) -> str:
    """Id for a row without one: a hash of its content plus how many identical rows came before it in the same
    source. Two identical lines in one file are two runs; the same line read from two sources is one."""
    h = hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()[:32]
    k = seen.get(h, 0)
    seen[h] = k + 1
    return f"sha256:{h}#{k}"


def with_ids(rows: Iterable[Mapping[str, Any]]) -> list[dict]:
    """The rows of ONE source, each with a `trial_id` (its own, a derived one, or a content hash)."""
    seen: dict[str, int] = {}
    out = []
    for r in rows:
        r = dict(r)
        r["trial_id"] = trial_id(r) or _content_id(r, seen)
        out.append(r)
    return out


def merge(*sources: Iterable[Mapping[str, Any]]) -> list[dict]:
    """Rows of several sources as one registry: one row per trial id, keeping the larger `n_trials`."""
    out: dict[str, dict] = {}
    for src in sources:
        for r in with_ids(src):
            have = out.get(r["trial_id"])
            if have is None or configurations_of(r) > configurations_of(have):
                out[r["trial_id"]] = r
    return list(out.values())


def count_configurations(rows: Iterable[Mapping[str, Any]], unreadable: int = 0) -> int:
    """THE trial count: configurations tried, not rows. Sum of `n_trials` (1 when absent) over the distinct
    trials in `rows`, plus one for every line that could not be read."""
    return sum(configurations_of(r) for r in merge(rows)) + int(unreadable)


# ---------------------------------------------------------------- the file


def read_jsonl(path: Path | str | None) -> tuple[list[dict], int]:
    """(rows, unreadable): the JSON objects of a registry file and the number of other non-blank lines."""
    if path is None or not Path(path).is_file():
        return [], 0
    rows, bad = [], 0
    for line in Path(path).read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            rows.append(obj)
        else:
            bad += 1
    return rows, bad


def count_file(path: Path | str | None) -> int:
    """Configurations logged in one registry file (0 when the file does not exist)."""
    rows, bad = read_jsonl(path)
    return count_configurations(rows, bad)


def registry_file(root: Path | str, env: Mapping[str, str] | None = None) -> Path:
    """Where the registry file is on this machine: `$KAIBOT_TRIALS_FILE` when set, else the first that exists
    of `<root>/data/trials.jsonl` and `$KAIBOT_DATA_DIR/trials.jsonl`, else `<root>/data/trials.jsonl`."""
    env = os.environ if env is None else env
    if env.get(TRIALS_ENV):
        return Path(env[TRIALS_ENV])
    default = Path(root) / "data" / "trials.jsonl"
    if default.is_file():
        return default
    if env.get(DATA_ENV) and (Path(env[DATA_ENV]) / "trials.jsonl").is_file():
        return Path(env[DATA_ENV]) / "trials.jsonl"
    return default


def append(path: Path | str, row: Mapping[str, Any]) -> dict:
    """Append one run to the registry file and return the row as written. Never rewrites the file.

    `strategy` is required; `n_trials` must be a whole number >= 1 when given (default 1). A file whose last
    line was cut off (a crashed run) gets a newline first, so the new row is not glued to the damaged one."""
    row = dict(row)
    if not isinstance(row.get("strategy"), str) or not row["strategy"].strip():
        raise ValueError("a trial row needs the rule's name in `strategy`")
    n = row.get("n_trials", 1)
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError(f"n_trials must be a whole number >= 1 (configurations searched), got {n!r}")
    row["n_trials"] = n
    line = json.dumps(row, default=str)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    glue = ""
    if path.is_file() and path.stat().st_size:
        with path.open("rb") as fh:
            fh.seek(-1, os.SEEK_END)
            glue = "" if fh.read(1) == b"\n" else "\n"
    with path.open("a") as fh:
        fh.write(glue + line + "\n")
    return json.loads(line)


# ---------------------------------------------------------------- the registry


@dataclass(frozen=True)
class Registry:
    rows: tuple[dict, ...]  # one per trial
    sources: tuple[str, ...] = ()  # what was read, for the report
    unreadable: int = 0  # lines that were not JSON objects; each counts as one configuration

    @property
    def configurations(self) -> int:
        return sum(configurations_of(r) for r in self.rows) + self.unreadable

    def by_strategy(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.rows:
            k = r.get("strategy") or "?"
            out[k] = out.get(k, 0) + configurations_of(r)
        return out

    def daily_sharpes(self) -> dict[str, list[float]]:
        """Per strategy, the daily (not annualised) Sharpe of every trial that logged one
        (`metrics.daily_sharpe_ann`). Their variance is the V of the deflated Sharpe."""
        out: dict[str, list[float]] = {}
        for r in self.rows:
            m = r.get("metrics")
            try:
                v = float((m if isinstance(m, Mapping) else {}).get("daily_sharpe_ann"))
            except (TypeError, ValueError):
                continue
            if math.isfinite(v):
                out.setdefault(r.get("strategy") or "?", []).append(v / SQRT_252)
        return out

    def summary(self) -> dict:
        return {"configurations": self.configurations, "rows": len(self.rows), "unreadable_lines": self.unreadable,
                "by_strategy": dict(sorted(self.by_strategy().items())), "sources": list(self.sources)}


def _select_all(store, table: str, columns: str) -> list[dict]:
    """Every row of a table through PostgREST, a page at a time (a single request is capped at 1,000 rows)."""
    out, seen = [], set()
    for page in range(MAX_PAGES):
        rows = store.select(table, {"select": columns, "order": "id.asc", "limit": str(PAGE),
                                    "offset": str(page * PAGE)})
        new = [r for r in rows if r.get("id") not in seen]
        seen.update(r.get("id") for r in new)
        out += new
        if len(rows) < PAGE or not new:
            break
    return out


def load(store=None, trials_file: Path | str | None = None) -> Registry:
    """The registry from every source that can be read: the database tables (when a store is given) and the
    file. A source that fails is skipped and named in `sources`; it never raises."""
    sources: list[list[dict]] = []
    notes: list[str] = []
    if store is not None:
        # The older table first: when the database cannot be reached at all, the second read is skipped, so a
        # network outage costs the day-end hook no more time than it did before the registry table existed.
        reachable = False
        try:
            rows = _select_all(store, LEGACY_TABLE, "id,strategy,params,metrics")
            sources.append([{k: v for k, v in r.items() if k != "id"} for r in rows])
            notes.append(f"table {LEGACY_TABLE}: {len(rows)} rows")
            reachable = True
        except Exception:  # noqa: BLE001
            log.warning("trial table unreadable; using the local trial log only")
            notes.append(f"table {LEGACY_TABLE}: not readable")
        if reachable:
            try:
                rows = [{**(r.get("row") or {}), "trial_id": r.get("trial_id"), "strategy": r.get("strategy"),
                         "n_trials": r.get("n_trials")}
                        for r in _select_all(store, TABLE, "id,trial_id,strategy,n_trials,row")]
                sources.append(rows)
                notes.append(f"table {TABLE}: {len(rows)} rows")
            except Exception as e:  # noqa: BLE001 - before migration 0015 the table does not exist
                log.debug("trial registry table not readable (%s)", type(e).__name__)
                notes.append(f"table {TABLE}: not readable (migration 0015 not applied?)")
    bad = 0
    if trials_file is not None:
        rows, bad = read_jsonl(trials_file)
        if Path(trials_file).is_file():
            sources.append(rows)
            notes.append(f"file {trials_file}: {len(rows)} rows" + (f", {bad} unreadable lines" if bad else ""))
        else:
            notes.append(f"file {trials_file}: not found")
    return Registry(tuple(merge(*sources)), tuple(notes), bad)


# ---------------------------------------------------------------- the database copy (after migration 0015)


def _json_safe(x: Any) -> Any:
    """NaN and the infinities are not JSON (Postgres refuses them): null."""
    if isinstance(x, float) and not math.isfinite(x):
        return None
    if isinstance(x, Mapping):
        return {str(k): _json_safe(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [_json_safe(v) for v in x]
    return x


def _timestamp(v: Any) -> str | None:
    """The run's time when it is an ISO timestamp, else None (some old rows carry free text)."""
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def db_row(row: Mapping[str, Any]) -> dict:
    """One registry row in the shape of `kaibot.trial_registry` (the row must already carry a `trial_id`)."""
    return {"trial_id": str(row["trial_id"])[:300], "ts": _timestamp(row.get("ts")),
            "strategy": str(row.get("strategy") or "?")[:200], "n_trials": configurations_of(row),
            "row": _json_safe(dict(row))}


def push(store, rows: Iterable[Mapping[str, Any]]) -> int:
    """Copy the trials the database does not have yet into `kaibot.trial_registry`; returns how many were added.

    Insert only: the table refuses updates and deletes, so a trial that is already there is left alone.
    Needs migration 0015. Run it from the research machine (scripts/trial_registry.py push)."""
    have = {r.get("trial_id") for r in _select_all(store, TABLE, "id,trial_id")}
    new = [db_row(r) for r in merge(rows) if r["trial_id"] not in have]  # one row per trial: the id is unique
    for i in range(0, len(new), 200):
        store.insert(TABLE, new[i:i + 200], returning=False)
    return len(new)
