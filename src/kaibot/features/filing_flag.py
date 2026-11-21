"""8-K filing flags as a logged "do not buy" observer (story S101, #375). Shadow only: it changes no order.

Pre-registered groups and the study: docs/prereg/filing_flags_study.md; settings: config/filing_flags.yaml.

    flags(symbols, as_of, session, filings, cfg) -> {symbol: [{group, day0, accepted_ts, sessions_since, counted}]}

`flags` is pure: it reads only the filings it is given and, of those, only rows with `accepted_ts <= as_of`
(`available_ts` in research is SEC's acceptance time). A group flags a symbol for N sessions after day 0 (the
first session whose open is after the acceptance): N from `counted` for a group the study found to count, else
`shadow_sessions`, labelled `counted: false`.

The live engine never imports this module. With `KAIBOT_FILING_FLAG=shadow` the engine's entry point hands
`ShadowFilingFlags` to the daily path, which writes one `filing_flag_shadow` event after each decision and reads
nothing back (tests/unit/test_filing_flag.py). Off by default. The rows come from the research file
`data/filings/filings_8k.parquet`, so the log is only as fresh as that file's last refresh; every event carries
the file's newest acceptance time so that a stale file shows.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import yaml

from kaibot.ingest import nyse_sessions as nyse

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "config" / "filing_flags.yaml"
FILINGS = ROOT / "data" / "filings" / "filings_8k.parquet"
FLAG_ENV = "KAIBOT_FILING_FLAG"


@dataclass(frozen=True)
class Filing:
    company: str
    symbol: str
    session: date  # day 0
    accepted_ts: datetime  # available_ts
    items: tuple[str, ...]


def split_items(items: object) -> tuple[str, ...]:
    return tuple(i.strip() for i in str(items or "").split(",") if i.strip())


def load_config(path: Path = CONFIG) -> dict:
    cfg = yaml.safe_load(Path(path).read_text()) or {}
    cfg.setdefault("exclude_items", ["2.02"])
    cfg["counted"] = dict(cfg.get("counted") or {})
    cfg.setdefault("shadow_sessions", 20)
    return cfg


def groups_of(items: Iterable[str], cfg: Mapping) -> list[str]:
    """The pre-registered groups a filing belongs to; none if it also lists an excluded item (2.02)."""
    its = set(items)
    if its & set(cfg.get("exclude_items") or ()):
        return []
    return [g for g, members in (cfg.get("groups") or {}).items() if its & set(members)]


def sessions_since(day0: date, session: date) -> int:
    """0 on day 0, 1 on the next session, ... (negative if `session` is before day 0)."""
    if session < day0:
        return -len(nyse.sessions(session, day0)) + 1
    return len(nyse.sessions(day0, session)) - 1


def flags(symbols: Sequence[str], as_of: datetime, session: date, filings: Iterable[Filing], cfg: Mapping) -> dict:
    """{symbol: [flag, ...]} for the symbols with a flag on `session`, from filings accepted by `as_of`."""
    want = set(symbols)
    counted = {g: int(n) for g, n in (cfg.get("counted") or {}).items()}
    shadow_n = int(cfg.get("shadow_sessions") or 20)
    out: dict[str, list[dict]] = {}
    for f in filings:
        if f.symbol not in want or f.accepted_ts > as_of or f.session > session:
            continue
        gs = groups_of(f.items, cfg)
        if not gs:
            continue
        k = sessions_since(f.session, session)
        for g in gs:
            n = counted.get(g, shadow_n)
            if 0 <= k <= n:
                out.setdefault(f.symbol, []).append({"group": g, "day0": f.session.isoformat(),
                                                     "accepted_ts": f.accepted_ts.astimezone(UTC).isoformat(),
                                                     "sessions_since": k, "sessions": n,
                                                     "counted": g in counted})
    for v in out.values():
        v.sort(key=lambda x: (x["day0"], x["group"]))
    return dict(sorted(out.items()))


def read_filings(path: Path = FILINGS) -> list[Filing]:
    """The 8-K rows (amendments left out, as pre-registered) of the research file."""
    import pandas as pd

    t = pd.read_parquet(path, columns=["company", "symbol", "session", "accepted_ts", "form", "items"])
    t = t[t["form"].astype(str) == "8-K"]
    acc = pd.to_datetime(t["accepted_ts"], utc=True)
    out = []
    for c, s, ses, a, it in zip(t["company"], t["symbol"], t["session"], acc, t["items"], strict=True):
        if ses is None or str(ses) in ("NaT", "nan", "None"):
            continue
        out.append(Filing(str(c), str(s), pd.Timestamp(ses).date(), a.to_pydatetime(), split_items(it)))
    return out


class ShadowFilingFlags:
    """The observer the daily path calls: (symbols, as_of, session) -> the event body. Reloads the file when it
    changes on disk; a missing or unreadable file gives `{"error": ...}`, never an exception."""

    def __init__(self, path: Path = FILINGS, config: Path = CONFIG) -> None:
        self.path, self.config = Path(path), Path(config)
        self._mtime: float | None = None
        self._rows: list[Filing] = []

    def _load(self) -> None:
        m = self.path.stat().st_mtime
        if m != self._mtime:
            self._rows, self._mtime = read_filings(self.path), m

    def __call__(self, symbols: Sequence[str], as_of: datetime, session: date) -> dict:
        try:
            cfg = load_config(self.config)
            self._load()
        except Exception as e:  # noqa: BLE001 - an observer never stops the daily path
            return {"error": f"{type(e).__name__}: {e}"[:200], "flags": {}}
        last = max((f.accepted_ts for f in self._rows), default=None)
        return {"flags": flags(symbols, as_of, session, self._rows, cfg),
                "counted_groups": sorted(cfg["counted"]), "symbols_checked": len(set(symbols)),
                "file_last_accepted": last.astimezone(UTC).isoformat() if last else None}


def from_env(env: Mapping[str, str]) -> ShadowFilingFlags | None:
    """`KAIBOT_FILING_FLAG=shadow` -> the observer; anything else (the default) -> None."""
    if str(env.get(FLAG_ENV, "off")).strip().lower() != "shadow":
        return None
    return ShadowFilingFlags()
