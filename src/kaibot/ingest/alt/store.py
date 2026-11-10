"""Append-only, point-in-time Parquet store for alt data: data/alt/<source>/date=YYYY-MM-DD/part-*.parquet.

- Idempotent: a row is written only if its key is new in that day's partition, or its values changed (a provider
  revision). Re-running a day with the same data writes nothing.
- Point-in-time: every row has `available_ts` (when Kaibot could first have known it) and `ingested_ts`. A revision
  is a NEW row with a later available_ts, so a reader at decision_ts sees exactly what was knowable then
  (`read_pit`). Nothing is ever rewritten in place.
- Crash-safe: each part is written to a temp file and atomically renamed; one collector per source at a time
  (POSIX flock on data/alt/<source>/.lock, works on macOS and Linux).
"""

from __future__ import annotations

import contextlib
import fcntl
import logging
import math
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

log = logging.getLogger(__name__)
TS = pa.timestamp("us", tz="UTC")


def source_dir(root: Path, source: str) -> Path:
    if not source.replace("_", "").isalnum():
        raise ValueError(f"bad source name {source!r}")
    return Path(root) / "alt" / source


def partition_dir(root: Path, source: str, day: date) -> Path:
    return source_dir(root, source) / f"date={day.isoformat()}"


@contextlib.contextmanager
def source_lock(root: Path, source: str) -> Iterator[None]:
    d = source_dir(root, source)
    d.mkdir(parents=True, exist_ok=True)
    with open(d / ".lock", "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(f"another collector is writing {d}") from None
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def read_partition(root: Path, source: str, day: date) -> pd.DataFrame:
    d = partition_dir(root, source, day)
    parts = sorted(d.glob("part-*.parquet")) if d.exists() else []
    if not parts:
        return pd.DataFrame()
    return pd.concat([pq.read_table(p).to_pandas() for p in parts], ignore_index=True)


def read_source(root: Path, source: str, columns: list[str] | None = None) -> pd.DataFrame:
    d = source_dir(root, source)
    parts = sorted(d.glob("date=*/part-*.parquet")) if d.exists() else []
    if not parts:
        return pd.DataFrame(columns=columns or [])
    return pd.concat([pq.read_table(p, columns=columns).to_pandas() for p in parts], ignore_index=True)


def _norm(v):
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, pd.Timestamp | datetime):
        return pd.Timestamp(v).isoformat()
    if v is pd.NaT:
        return None
    return v


def _latest(existing: pd.DataFrame, key: list[str], values: list[str]) -> dict[tuple, tuple]:
    if existing.empty:
        return {}
    e = existing.sort_values(["available_ts", "ingested_ts"], kind="stable")
    out: dict[tuple, tuple] = {}
    for row in e[key + values].itertuples(index=False, name=None):
        out[tuple(_norm(v) for v in row[: len(key)])] = tuple(_norm(v) for v in row[len(key):])
    return out


def append_versions(
    root: Path,
    source: str,
    day: date,
    table: pa.Table,
    key: list[str],
    values: list[str],
    revision_available_ts: datetime | None = None,
) -> int:
    """Write the rows of `table` that are new or changed vs the latest stored version. Returns rows written.

    `table` must have available_ts and ingested_ts columns. Rows that REVISE an existing key get
    available_ts = max(own, revision_available_ts) so a revision is never back-dated to the first publication.
    """
    if table.num_rows == 0:
        return 0
    for c in ("available_ts", "ingested_ts", *key, *values):
        if c not in table.column_names:
            raise ValueError(f"{source}: missing column {c}")
    new = table.to_pandas()
    new = new.drop_duplicates(subset=key, keep="last")
    latest = _latest(read_partition(root, source, day), key, values)
    keep, revised = [], []
    for i, row in enumerate(new[key + values].itertuples(index=False, name=None)):
        k = tuple(_norm(v) for v in row[: len(key)])
        v = tuple(_norm(x) for x in row[len(key):])
        if k not in latest:
            keep.append(i)
        elif latest[k] != v:
            keep.append(i)
            revised.append(i)
    if not keep:
        return 0
    if revised and revision_available_ts is not None:
        rts = pd.Timestamp(revision_available_ts).tz_convert(UTC)
        col = new.columns.get_loc("available_ts")
        for i in revised:
            new.iat[i, col] = max(new.iat[i, col], rts)
    out = pa.Table.from_pandas(new.iloc[keep].reset_index(drop=True), schema=table.schema, preserve_index=False)
    d = partition_dir(root, source, day)
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    final = d / f"part-{stamp}-{uuid.uuid4().hex[:8]}.parquet"
    tmp = d / f".{final.name}.tmp"
    pq.write_table(out, tmp)
    os.replace(tmp, final)
    if revised:
        log.info("%s %s: %d revised rows", source, day, len(revised))
    return out.num_rows


def read_pit(root: Path, source: str, decision_ts: datetime, key: list[str]) -> pd.DataFrame:
    """Point-in-time view: rows with available_ts <= decision_ts, latest version per key."""
    df = read_source(root, source)
    if df.empty:
        return df
    df = df[df["available_ts"] <= pd.Timestamp(decision_ts)]
    df = df.sort_values(["available_ts", "ingested_ts"], kind="stable")
    return df.drop_duplicates(subset=key, keep="last").reset_index(drop=True)
