"""The multi-day book's stop: today's fixed line, or the Gate C bootstrap band (decision 17; M11 ticket 2.10, #461).

Pure: numbers in, a verdict out; no clock, no broker, no journal. The engine asks `verdict` at every book check
(`engine/core.Engine._book_end_check`, decision 17 section 7 step 3, #461). The switch is `mode` in
`config/book_stop.yaml`; it says `fixed`, and in mode fixed the engine's stop is today's, bit for bit. The engine
reads the file and the tables once, at its first book check, so a change takes effect at the next engine restart.

- `verdict(...)` in mode "fixed" is `gate.book_run_ended(peak, equity, lim)` exactly (a property test checks it on
  every input): the line `BookLimits.run_drawdown_end_pct` below the book's peak.
- In mode "band" it compares the live record's maximum drawdown (the current tick included) with the 99% line of
  the book's band table at the record's length in sessions (at least `min_sessions`), capped at `ceiling_pct`; the
  95% line is an alert. A book without a committed table falls back to the fixed line (and says so).
- The band's end line is never above `BOOK_HARD_CEILINGS.run_drawdown_end_pct` (27%, decision 15 section 11): a
  config whose `ceiling_pct` is above it is refused, and `verdict` caps the line there whatever config it is given.
  Raising that ceiling is a stakeholder decision that amends decision 15; this record does not make it.
- Fail closed: only `mode == "band"` with a table and finite inputs is judged by the band; any other mode, a
  non-finite peak, equity or record drawdown, or a bad session count gets the fixed line. `load_or_fixed` reads the
  config and gives the fixed mode (never "no stop") when the file is missing, unreadable or invalid.
- Point in time (`table_unusable`, `sessions_traded`): a table is used only when its research window ended before
  the decision's day, and the horizon counts only sessions that closed before that day, from the book's start.
- `BandTable` holds the 95% and 99% quantiles of resampled maximum drawdowns by horizon. It refuses a table that is
  empty, out of order, outside 0..1, with a 95% line above the 99% one, or whose 99% line falls as the horizon grows.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from kaibot.risk.gate import BOOK_HARD_CEILINGS, BookLimits, book_run_ended, drawdown_pct

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PATH = ROOT / "config" / "book_stop.yaml"
MODES = ("fixed", "band")
HORIZONS = (5, 10, 20, 40, 60, 90, 126, 189, 252, 378, 504, 756, 1008, 1260, 1512, 1764)
TOL = 1e-9


@dataclass(frozen=True)
class BandTable:
    rule: str
    level: str
    horizons: tuple[int, ...]
    q95: tuple[float, ...]  # drawdowns as fractions (0.2 = 20%)
    q99: tuple[float, ...]
    source: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        h, a, b = self.horizons, self.q95, self.q99
        if not h or not len(h) == len(a) == len(b):
            raise ValueError("a band table needs one 95% and one 99% value per horizon")
        if any(not isinstance(x, int) or isinstance(x, bool) or x < 1 for x in h) or list(h) != sorted(set(h)):
            raise ValueError("the horizons must be whole numbers >= 1, strictly increasing")
        for v in (*a, *b):
            if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v) or not 0 <= v <= 1:
                raise ValueError(f"a band value {v!r} is not a drawdown between 0 and 1")
        if any(not v > 0 for v in b):  # a 99% line at 0 (e.g. a zero-volatility run) would end a book at its peak
            raise ValueError("a 99% line of 0 is a degenerate band (no variance in the resampled returns)")
        if any(x > y + TOL for x, y in zip(a, b, strict=True)):
            raise ValueError("the 95% line is above the 99% line at some horizon")
        if any(b[i + 1] < b[i] - TOL for i in range(len(b) - 1)):
            raise ValueError("the 99% line falls as the horizon grows: not a drawdown band")

    def at(self, sessions: int) -> tuple[float, float]:
        """(95% line, 99% line) at the largest listed horizon at or below `sessions` (the first one below it)."""
        i = max(sum(1 for x in self.horizons if x <= sessions) - 1, 0)
        return float(self.q95[i]), float(self.q99[i])

    @classmethod
    def from_dict(cls, d: dict) -> BandTable:
        return cls(str(d["rule"]), str(d["level"]), tuple(int(x) for x in d["horizons"]),
                   tuple(float(x) for x in d["q95"]), tuple(float(x) for x in d["q99"]), dict(d.get("source") or {}))

    def to_dict(self) -> dict:
        return {"rule": self.rule, "level": self.level, "horizons": list(self.horizons), "q95": list(self.q95),
                "q99": list(self.q99), "source": dict(self.source)}


@dataclass(frozen=True)
class BookStopConfig:
    mode: str
    retire_quantile: float
    alert_quantile: float
    min_sessions: int
    ceiling_pct: float
    keep_index_core: bool
    review_sessions: int
    bootstrap: dict
    tables: dict  # rule -> level -> path (relative to the repository)


def load(path: Path | str = DEFAULT_PATH) -> BookStopConfig:
    cfg = yaml.safe_load(Path(path).read_text()) or {}
    mode = cfg.get("mode")
    if mode not in MODES:
        raise ValueError(f"book_stop.yaml: mode must be one of {MODES}, not {mode!r}")
    b = cfg.get("band") or {}
    try:
        out = BookStopConfig(mode, float(b["retire_quantile"]), float(b["alert_quantile"]), int(b["min_sessions"]),
                             float(b["ceiling_pct"]), bool(b["keep_index_core"]), int(b["review_sessions"]),
                             dict(b["bootstrap"]), dict(cfg.get("tables") or {}))
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"book_stop.yaml: the band block is incomplete ({e})") from None
    if not 0 < out.alert_quantile < out.retire_quantile < 1:
        raise ValueError("book_stop.yaml: need 0 < alert_quantile < retire_quantile < 1")
    if not 0 < out.ceiling_pct <= 100 or out.min_sessions < 1:
        raise ValueError("book_stop.yaml: ceiling_pct in (0, 100] and min_sessions >= 1")
    if out.ceiling_pct > BOOK_HARD_CEILINGS.run_drawdown_end_pct:
        raise ValueError(f"book_stop.yaml: ceiling_pct {out.ceiling_pct} loosens the hard ceiling "
                         f"run_drawdown_end_pct={BOOK_HARD_CEILINGS.run_drawdown_end_pct}")
    return out


# The config the stop falls back to when book_stop.yaml cannot be read: today's fixed line. The band settings are
# never used in this mode.
FIXED = BookStopConfig("fixed", 0.99, 0.95, 20, BOOK_HARD_CEILINGS.run_drawdown_end_pct, False, 5,
                       {"method": "stationary", "mean_block_sessions": 20, "runs": 2000, "seed": 15}, {})


def load_or_fixed(path: Path | str = DEFAULT_PATH) -> tuple[BookStopConfig, str | None]:
    """`load`, failing closed: a missing, unreadable or invalid file gives the fixed mode (today's stop) and the
    reason, never "no stop"."""
    try:
        return load(path), None
    except Exception as e:  # any failure to read the switch means the fixed line
        return FIXED, f"book_stop.yaml unusable, the fixed line: {type(e).__name__}: {e}"


def table_for(cfg: BookStopConfig, rule: str, level: str, root: Path = ROOT) -> BandTable | None:
    rel = (cfg.tables.get(rule) or {}).get(level)
    if not rel:
        return None
    t = BandTable.from_dict(json.loads((Path(root) / rel).read_text()))
    if (t.rule, t.level) != (rule, level):
        raise ValueError(f"{rel} is the table of {t.rule} at {t.level}, not of {rule} at {level}")
    return t


def table_unusable(t: BandTable, decision_day: date) -> str | None:
    """Why the table may not judge a decision on `decision_day` (None: it may). Point in time: the table is drawn
    from an engine + SimBroker run on a research window, and a window that has not ended before the decision's day
    would use returns not yet known then. A table without a readable window is refused too (fail closed)."""
    w = t.source.get("window")
    try:
        end = date.fromisoformat(str(w[1]))
    except (TypeError, IndexError, KeyError, ValueError):
        return f"the band table of {t.rule} at {t.level} records no research window"
    if not end < decision_day:
        return f"the band table of {t.rule} at {t.level} ends on {end}, not before {decision_day}"
    return None


def sessions_traded(sessions: Iterable[str], start: date, decision_day: date) -> int:
    """The book's live record length: the distinct sessions (ISO dates) that closed on or after the book's start
    and before `decision_day`. The session under way is not counted (its close is not known yet), and a session
    that cannot be read as a date is not counted either (a shorter record is a stricter line)."""
    out = set()
    for s in sessions:
        try:
            d = date.fromisoformat(str(s))
        except ValueError:
            continue
        if start <= d < decision_day:
            out.add(d)
    return len(out)


@dataclass(frozen=True)
class StopVerdict:
    end: bool
    alert: bool
    mode: str  # the mode that judged: fixed | band
    drawdown: float  # the measured drawdown, a fraction
    line: float  # the end line, a fraction
    alert_line: float | None
    horizon: int | None
    why: str


def verdict(peak: float, equity: float, lim: BookLimits, *, cfg: BookStopConfig, table: BandTable | None = None,
            record_max_drawdown: float = 0.0, sessions: int = 0) -> StopVerdict:
    """The book's stop at one check (module docstring). `record_max_drawdown`: the live record's largest fall from
    its running peak so far (a fraction); `sessions`: sessions the book has traded since its start."""
    now = drawdown_pct(peak, equity) / 100
    why = None
    if cfg.mode != "band":
        why = "fixed line" if cfg.mode == "fixed" else f"mode {cfg.mode!r} is not 'band': the fixed line"
    elif table is None:
        why = "mode band, but no band table is committed: the fixed line"
    elif not _finite(peak, equity, record_max_drawdown) or isinstance(sessions, bool) or not isinstance(sessions, int):
        why = "mode band, but an input is not a finite number: the fixed line"
    if why is not None:
        line = lim.run_drawdown_end_pct / 100
        return StopVerdict(book_run_ended(peak, equity, lim), False, "fixed", now, line, None, None, why)
    assert table is not None
    dd = max(float(record_max_drawdown), now)
    h = max(sessions, cfg.min_sessions)
    q95, q99 = table.at(h)
    cap = min(cfg.ceiling_pct, BOOK_HARD_CEILINGS.run_drawdown_end_pct) / 100  # never past the hard ceiling
    line, alert_line = min(q99, cap), min(q95, cap)
    end = dd >= line - TOL
    why = f"{dd:.2%} against the 99% line {line:.2%} at {h} sessions" + (" (ceiling)" if q99 > cap else "")
    return StopVerdict(end, dd >= alert_line - TOL, "band", dd, line, alert_line, h, why)


def _finite(*xs) -> bool:
    return all(not isinstance(x, bool) and isinstance(x, int | float) and math.isfinite(x) for x in xs)


def end_action(cfg: BookStopConfig, has_index_core: bool) -> str:
    """What the end sells (decision 17 section 4): "sleeves" (the index core is kept) or "all" (as today)."""
    return "sleeves" if cfg.mode == "band" and cfg.keep_index_core and has_index_core else "all"
