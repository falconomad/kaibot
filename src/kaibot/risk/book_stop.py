"""The multi-day book's stop: today's fixed line, or the Gate C bootstrap band (decision 17; M11 ticket 2.10, #461).

Pure: numbers in, a verdict out; no clock, no broker, no journal. Nothing in the engine imports this module yet:
`config/book_stop.yaml` says `mode: fixed`, and the engine still calls `risk/gate.book_run_ended` itself. The engine
wiring is a later, separate change (decision 17 section 7, after #534 / #536 / #538), behind this file's switch.

- `verdict(...)` in mode "fixed" is `gate.book_run_ended(peak, equity, lim)` exactly (a property test checks it on
  every input): the line `BookLimits.run_drawdown_end_pct` below the book's peak.
- In mode "band" it compares the live record's maximum drawdown (the current tick included) with the 99% line of
  the book's band table at the record's length in sessions (at least `min_sessions`), capped at `ceiling_pct`; the
  95% line is an alert. A book without a committed table falls back to the fixed line (and says so).
- `BandTable` holds the 95% and 99% quantiles of resampled maximum drawdowns by horizon. It refuses a table that is
  empty, out of order, outside 0..1, with a 95% line above the 99% one, or whose 99% line falls as the horizon grows.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from kaibot.risk.gate import BookLimits, book_run_ended, drawdown_pct

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
    return out


def table_for(cfg: BookStopConfig, rule: str, level: str, root: Path = ROOT) -> BandTable | None:
    rel = (cfg.tables.get(rule) or {}).get(level)
    if not rel:
        return None
    t = BandTable.from_dict(json.loads((Path(root) / rel).read_text()))
    if (t.rule, t.level) != (rule, level):
        raise ValueError(f"{rel} is the table of {t.rule} at {t.level}, not of {rule} at {level}")
    return t


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
    if cfg.mode == "fixed" or table is None:
        why = "fixed line" if cfg.mode == "fixed" else "mode band, but no band table is committed: the fixed line"
        line = lim.run_drawdown_end_pct / 100
        return StopVerdict(book_run_ended(peak, equity, lim), False, "fixed", now, line, None, None, why)
    dd = max(float(record_max_drawdown), now)
    h = max(int(sessions), cfg.min_sessions)
    q95, q99 = table.at(h)
    cap = cfg.ceiling_pct / 100
    line, alert_line = min(q99, cap), min(q95, cap)
    end = dd >= line - TOL
    why = f"{dd:.2%} against the 99% line {line:.2%} at {h} sessions" + (" (ceiling)" if q99 > cap else "")
    return StopVerdict(end, dd >= alert_line - TOL, "band", dd, line, alert_line, h, why)


def end_action(cfg: BookStopConfig, has_index_core: bool) -> str:
    """What the end sells (decision 17 section 4): "sleeves" (the index core is kept) or "all" (as today)."""
    return "sleeves" if cfg.mode == "band" and cfg.keep_index_core and has_index_core else "all"
