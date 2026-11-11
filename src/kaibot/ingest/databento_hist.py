"""Databento historical downloads for VALIDATION ONLY (story S54): one-time credit, never in the live path.

Rules (config/sources.yaml `market_data.databento`):
- A cost estimate (`metadata.get_cost`) runs and is printed before every download; a request estimated above
  `max_cost_usd` (default $10) is refused. Dry-run is the default: nothing is downloaded without `confirm=True`.
- Downloads are cached per symbol in Parquet; cached symbols are dropped from the request BEFORE the estimate,
  so nothing is paid twice. Layout: <root>/databento/<dataset>/<schema>/symbol=XYZ/<start>_<end>.parquet
  (`.empty` marker = Databento returned no rows for that symbol and window).
- Total-credit guard: every confirmed download appends its estimated cost to <root>/databento/ledger.jsonl, and
  a request that would take the recorded spend past `credit_usd` (default $125, the one-time credit) is refused,
  even with `confirm=True`. An unreadable ledger also refuses (fail closed). Delete a line only to correct it.
- Every call acquires the shared `databento` budget (kaibot.common.ratelimit) first.
- Windows must end >= 15 min before now (SIP rule). Each row gets `available_ts` = bar/interval end, so features
  that read it stay point-in-time.

The `databento` package is imported lazily (only `make_client` needs it), so tests and the engine never need it.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from kaibot.common.ratelimit import SOURCES_FILE, Budget, get_budget

SOURCE = "databento"
DEFAULT_DATASET = "EQUS.MINI"  # Databento US Equities Mini: consolidated across venues, has ohlcv-1m and bbo-1m
DEFAULT_SCHEMA = "ohlcv-1m"
SCHEMAS = {"ohlcv-1m": timedelta(minutes=1), "bbo-1m": timedelta(minutes=1)}
DEFAULT_MAX_COST_USD = 10.0
DEFAULT_CREDIT_USD = 125.0  # the one-time historical credit: total recorded spend never goes past it
SIP_DELAY = timedelta(minutes=15)
_SAFE = re.compile(r"^[A-Za-z0-9._\-]+$")


class CostCapExceeded(RuntimeError):
    pass


class CreditExceeded(CostCapExceeded):
    """The estimate would take the recorded spend past the total credit: nothing was downloaded."""


def load_config(path: str | Path | None = None) -> dict:
    doc = yaml.safe_load(Path(path or SOURCES_FILE).read_text()) or {}
    cfg = (doc.get("market_data") or {}).get(SOURCE)
    if not isinstance(cfg, dict):
        raise KeyError(f"no market_data.{SOURCE} entry in sources.yaml")
    if cfg.get("use") != "validation_only":
        raise ValueError(f"{SOURCE} must stay use: validation_only")
    return cfg


def max_cost_from_config(cfg: dict) -> float:
    v = cfg.get("max_cost_usd", DEFAULT_MAX_COST_USD)
    if isinstance(v, bool) or not isinstance(v, int | float) or v < 0:
        raise ValueError(f"max_cost_usd must be a number >= 0, got {v!r}")
    return float(v)


def credit_from_config(cfg: dict) -> float:
    v = cfg.get("credit_usd", DEFAULT_CREDIT_USD)
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v) or v < 0:
        raise ValueError(f"credit_usd must be a number >= 0, got {v!r}")
    return float(v)


def ledger_path(root: Path) -> Path:
    return Path(root) / "databento" / "ledger.jsonl"


def spent(root: Path) -> float:
    """Total estimated cost of every confirmed download recorded under `root`. Raises ValueError on a line it
    cannot read, so a damaged ledger refuses downloads instead of silently counting as $0."""
    p = ledger_path(root)
    if not p.exists():
        return 0.0
    total = 0.0
    for n, line in enumerate(p.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            cost = float(json.loads(line)["cost_usd"])
        except (ValueError, TypeError, KeyError):
            cost = math.nan
        if not math.isfinite(cost) or cost < 0:
            raise ValueError(f"unreadable spend ledger {p} (line {n}): fix or remove that line before downloading")
        total += cost
    return total


def record_spend(root: Path, req: Request, cost_usd: float, now: datetime | None = None) -> None:
    p = ledger_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = {"at": (now or datetime.now(UTC)).isoformat(), "cost_usd": cost_usd, "dataset": req.dataset,
           "schema": req.schema, "symbols": list(req.symbols), "start": req.start.isoformat(),
           "end": req.end.isoformat()}
    with p.open("a") as f:
        f.write(json.dumps(row) + "\n")


def _ts(v: str | datetime) -> datetime:
    t = v if isinstance(v, datetime) else datetime.fromisoformat(v.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=UTC)


@dataclass(frozen=True)
class Request:
    symbols: tuple[str, ...]
    start: datetime
    end: datetime
    dataset: str = DEFAULT_DATASET
    schema: str = DEFAULT_SCHEMA

    @classmethod
    def make(cls, symbols, start, end, dataset: str = DEFAULT_DATASET, schema: str = DEFAULT_SCHEMA,
             now: datetime | None = None) -> Request:
        syms = tuple(sorted({s.strip().upper() for s in symbols if s and s.strip()}))
        if not syms:
            raise ValueError("at least one symbol is required")
        for s in (*syms, dataset):
            if not _SAFE.match(s):
                raise ValueError(f"unsafe symbol/dataset {s!r}")
        if schema not in SCHEMAS:
            raise ValueError(f"schema must be one of {sorted(SCHEMAS)}, got {schema!r}")
        a, b = _ts(start), _ts(end)
        if a >= b:
            raise ValueError("start must be before end")
        latest = (now or datetime.now(UTC)) - SIP_DELAY
        if b > latest:
            raise ValueError(f"end {b.isoformat()} is less than 15 min old (latest allowed {latest.isoformat()})")
        return cls(syms, a, b, dataset, schema)

    def with_symbols(self, symbols: tuple[str, ...]) -> Request:
        return Request(symbols, self.start, self.end, self.dataset, self.schema)

    def kwargs(self) -> dict[str, Any]:
        return {"dataset": self.dataset, "symbols": list(self.symbols), "schema": self.schema,
                "start": self.start.isoformat(), "end": self.end.isoformat(), "stype_in": "raw_symbol"}


def cache_path(root: Path, req: Request, symbol: str) -> Path:
    span = f"{req.start.strftime('%Y%m%dT%H%M')}_{req.end.strftime('%Y%m%dT%H%M')}"
    return Path(root) / "databento" / req.dataset / req.schema / f"symbol={symbol}" / f"{span}.parquet"


def is_cached(root: Path, req: Request, symbol: str) -> bool:
    p = cache_path(root, req, symbol)
    return p.exists() or p.with_suffix(".empty").exists()


@dataclass
class Result:
    request: Request
    cached: tuple[str, ...]
    estimated_usd: float = 0.0
    downloaded: dict[str, int] = field(default_factory=dict)  # symbol -> rows written
    dry_run: bool = True


def estimate_cost(client, req: Request, budget: Budget | None = None) -> float:
    (budget or get_budget(SOURCE)).acquire()
    return float(client.metadata.get_cost(**req.kwargs()))


def _frame(store, req: Request) -> pd.DataFrame:
    df = store.to_df()
    if df is None or len(df) == 0:
        return pd.DataFrame()
    df = df.reset_index()
    if "ts_event" in df.columns:
        df["ts_event"] = pd.to_datetime(df["ts_event"], utc=True)
        df["available_ts"] = df["ts_event"] + SCHEMAS[req.schema]  # bar / interval end: point-in-time
    return df


def fetch(client, req: Request, root: Path, max_cost_usd: float, confirm: bool = False,
          out: Callable[[str], None] = print, budget: Budget | None = None,
          credit_usd: float = DEFAULT_CREDIT_USD) -> Result:
    """Estimate, print, enforce the per-request cap and the total credit, then (only with confirm) download the
    uncached symbols to Parquet and record the estimated spend in the ledger."""
    budget = budget or get_budget(SOURCE)
    cached = tuple(s for s in req.symbols if is_cached(root, req, s))
    todo = tuple(s for s in req.symbols if s not in cached)
    res = Result(req, cached, dry_run=not confirm)
    if cached:
        out(f"cached (not paid again): {', '.join(cached)}")
    if not todo:
        out("estimated cost: $0.00 (everything cached)")
        return res
    sub = req.with_symbols(todo)
    res.estimated_usd = estimate_cost(client, sub, budget)
    if not math.isfinite(res.estimated_usd) or res.estimated_usd < 0:
        raise CostCapExceeded(f"cost estimate {res.estimated_usd!r} is not a usable number: refused")
    used = spent(root)
    out(f"estimated cost: ${res.estimated_usd:.4f}  dataset={sub.dataset} schema={sub.schema} "
        f"symbols={len(todo)} {sub.start.isoformat()} -> {sub.end.isoformat()}  (cap ${max_cost_usd:.2f}; "
        f"recorded spend ${used:.2f} of ${credit_usd:.2f} credit)")
    if res.estimated_usd > max_cost_usd:
        raise CostCapExceeded(f"estimated ${res.estimated_usd:.4f} is above the cap ${max_cost_usd:.2f}: refused")
    if used + res.estimated_usd > credit_usd:
        raise CreditExceeded(f"estimated ${res.estimated_usd:.4f} would take recorded spend ${used:.2f} past the "
                             f"${credit_usd:.2f} credit: refused")
    if not confirm:
        out("dry run: nothing downloaded (add --confirm to download)")
        return res
    budget.acquire()
    store = client.timeseries.get_range(**sub.kwargs())
    record_spend(root, sub, res.estimated_usd)  # first thing after the billed call, before any local write can fail
    df = _frame(store, sub)
    for s in todo:
        p = cache_path(root, sub, s)
        p.parent.mkdir(parents=True, exist_ok=True)
        part = df[df["symbol"] == s] if len(df) and "symbol" in df.columns else pd.DataFrame()
        if len(part) == 0:
            p.with_suffix(".empty").touch()
            res.downloaded[s] = 0
        else:
            tmp = p.with_suffix(".tmp")
            part.to_parquet(tmp, index=False)
            tmp.replace(p)
            res.downloaded[s] = len(part)
        out(f"{s}: {res.downloaded[s]} rows -> {p}")
    return res


def make_client(key: str):
    """Databento historical client. Needs `pip install databento` (validation tooling only, not the engine)."""
    if not key:
        raise ValueError("DATABENTO_API_KEY is not set")
    try:
        import databento as db
    except ImportError as e:  # pragma: no cover - depends on the machine
        raise SystemExit("pip install databento  (validation tooling only; the engine never needs it)") from e
    return db.Historical(key)
