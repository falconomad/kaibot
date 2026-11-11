"""Databento historical data for VALIDATION only (story S54). Never imported by the engine or the live path.

Databento bills per byte from a one-time credit, so every download goes through `fetch()`, which:
  1. serves the request from the local Parquet cache if it was downloaded before (nothing is paid twice);
  2. otherwise asks Databento for the price first (`metadata.get_cost`, free) and prints it;
  3. refuses the request if the estimate is above the per-request cap (config/sources.yaml
     `validation.databento.max_cost_usd_per_request`, default $10) or would take the recorded spend past the credit;
  4. downloads only when the caller passed `confirm=True` (the CLI's `--confirm`); a dry run stops after step 2.

Plain HTTPS through `kaibot.common.http` (no `databento` package needed): HTTP Basic auth with the key as the
user name, under the `databento` request budget. The key travels only in the Authorization header, never in a
URL, and is never printed. Layout: data/databento/<dataset>/<schema>/<key>.parquet (+ .json request record),
data/databento/ledger.jsonl (one line per paid download, the estimated cost).
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq
import yaml

from kaibot.common import http
from kaibot.common.ratelimit import SOURCES_FILE, Budget, get_budget

SOURCE = "databento"  # budget name in config/sources.yaml and folder under data/
HIST = "https://hist.databento.com/v0"
SCHEMAS = ("ohlcv-1m", "bbo-1m")  # bars for cross-checks, 1-min top of book for spreads
DEFAULT_SCHEMA = "ohlcv-1m"
DEFAULT_CAP_USD = 10.0
SIP_DELAY = timedelta(minutes=15)  # same rule as Alpaca SIP: never ask for anything newer than 15 min
DOWNLOAD_MAX_BYTES = 256 * 1024 * 1024


class CostRefused(RuntimeError):
    """The estimate is above the cap or the remaining credit: nothing was downloaded."""


@dataclass(frozen=True)
class Policy:
    max_cost_usd: float = DEFAULT_CAP_USD
    credit_usd: float = 125.0

    @classmethod
    def load(cls, path: str | Path | None = None) -> Policy:
        p = Path(path or os.environ.get("KAIBOT_SOURCES_FILE") or SOURCES_FILE)
        doc = yaml.safe_load(p.read_text()) or {}
        d = next((sec[SOURCE] for sec in doc.values() if isinstance(sec, dict) and SOURCE in sec), None)
        if not isinstance(d, dict):
            raise ValueError(f"{p}: no {SOURCE} entry")
        if d.get("use") != "validation_only":
            raise ValueError(f"{p}: {SOURCE} must stay use: validation_only")
        cap = float(d.get("max_cost_usd_per_request", DEFAULT_CAP_USD))
        credit = float(d.get("credit_usd", cls.credit_usd))
        if not (0 < cap <= credit):
            raise ValueError(f"{p}: {SOURCE}.max_cost_usd_per_request must be > 0 and <= credit_usd")
        return cls(cap, credit)


@dataclass(frozen=True)
class Query:
    dataset: str
    schema: str
    symbols: tuple[str, ...]
    start: str  # ISO 8601, UTC unless an offset is given; inclusive
    end: str  # exclusive
    stype_in: str = "raw_symbol"

    def __post_init__(self) -> None:
        if self.schema not in SCHEMAS:
            raise ValueError(f"schema must be one of {SCHEMAS}, got {self.schema!r}")
        if not self.symbols or any(not s or "," in s for s in self.symbols):
            raise ValueError("give at least one symbol, without commas")
        if not self.dataset.replace(".", "").isalnum():
            raise ValueError(f"bad dataset {self.dataset!r}")
        s, e = _ts(self.start), _ts(self.end)
        if e <= s:
            raise ValueError("end must be after start")

    def check_age(self, now: datetime) -> None:
        if _ts(self.end) > now - SIP_DELAY:
            raise ValueError("end must be at least 15 minutes in the past (historical only)")

    def params(self) -> dict[str, str]:
        return {"dataset": self.dataset, "schema": self.schema, "symbols": ",".join(self.symbols),
                "start": self.start, "end": self.end, "stype_in": self.stype_in}

    @property
    def key(self) -> str:
        canon = json.dumps({**self.params(), "symbols": ",".join(sorted(self.symbols))}, sort_keys=True)
        return hashlib.sha256(canon.encode()).hexdigest()[:20]


def _ts(v: str) -> datetime:
    t = datetime.fromisoformat(v.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _as_post(req: urllib.request.Request, timeout: float):
    """Opener for `http.get` that sends the query string as a form POST (Databento's documented method for
    timeseries.get_range). Keeps http.get's budget, HTTPS check, timeout, bounded read and retries."""
    parts = urllib.parse.urlsplit(req.full_url)
    post = urllib.request.Request(
        urllib.parse.urlunsplit(parts._replace(query="")), data=parts.query.encode(), method="POST",
        headers={**dict(req.header_items()), "Content-Type": "application/x-www-form-urlencoded"})
    return urllib.request.urlopen(post, timeout=timeout)


class Databento:
    """Historical API client. Only metadata calls (free) and get_range (billed, only via `fetch`)."""

    def __init__(self, key: str, budget: Budget | None = None):
        if not key:
            raise ValueError("DATABENTO_API_KEY is empty")
        token = base64.b64encode(f"{key}:".encode()).decode()
        self._h = {"Authorization": f"Basic {token}", "User-Agent": "kaibot/0.1", "Accept": "application/json"}
        self.budget = budget or get_budget(SOURCE)

    def __repr__(self) -> str:  # never show the key
        return "Databento(key=***)"

    def _get(self, path: str, params: dict | None = None, **kw) -> http.Response:
        return http.get(f"{HIST}/{path}", source=SOURCE, params=params, headers=self._h, budget=self.budget,
                        timeout=kw.pop("timeout", 30), **kw)

    def list_datasets(self) -> list[str]:
        """Zero-cost call: proves the key authenticates."""
        d = json.loads(self._get("metadata.list_datasets").body)
        if not isinstance(d, list):
            raise http.HttpError(200, f"{HIST}/metadata.list_datasets", "expected a list")
        return [str(x) for x in d]

    def get_cost(self, q: Query) -> float:
        """Free estimate in USD of downloading `q` (Databento `metadata.get_cost`)."""
        body = self._get("metadata.get_cost", {**q.params(), "mode": "historical-streaming"}).body
        try:
            cost = float(json.loads(body))
        except (ValueError, TypeError):
            raise http.HttpError(200, f"{HIST}/metadata.get_cost", "cost is not a number") from None
        if cost < 0 or cost != cost:
            raise http.HttpError(200, f"{HIST}/metadata.get_cost", "negative or NaN cost")
        return cost

    def get_range_csv(self, q: Query) -> bytes:
        """BILLED. Call only through `fetch()`, after the cost check."""
        params = {**q.params(), "encoding": "csv", "compression": "none", "pretty_px": "true",
                  "pretty_ts": "true", "map_symbols": "true"}
        h = {**self._h, "Accept": "text/csv"}
        r = http.get(f"{HIST}/timeseries.get_range", source=SOURCE, params=params, headers=h, budget=self.budget,
                     timeout=120, attempts=2, max_bytes=DOWNLOAD_MAX_BYTES, opener=_as_post)
        return r.body


@dataclass(frozen=True)
class Result:
    status: str  # cached | dry_run | downloaded
    cost_usd: float
    path: Path
    rows: int | None = None


def paths(root: Path, q: Query) -> tuple[Path, Path]:
    base = Path(root) / SOURCE / q.dataset / q.schema / q.key
    return base.with_suffix(".parquet"), base.with_suffix(".json")


def ledger_path(root: Path) -> Path:
    return Path(root) / SOURCE / "ledger.jsonl"


def spent(root: Path) -> float:
    p = ledger_path(root)
    if not p.exists():
        return 0.0
    return sum(float(json.loads(line)["cost_usd"]) for line in p.read_text().splitlines() if line.strip())


def csv_to_table(body: bytes) -> pa.Table:
    if not body.strip():
        return pa.table({})
    return pacsv.read_csv(io.BytesIO(body))


def fetch(client: Databento, q: Query, *, root: Path, policy: Policy, confirm: bool = False,
          max_cost_usd: float | None = None, now: datetime | None = None,
          out: Callable[[str], None] = print) -> Result:
    """Cache -> cost estimate (always printed) -> cap and credit check -> download only if `confirm`."""
    q.check_age(now or datetime.now(UTC))
    pq_path, meta_path = paths(root, q)
    if pq_path.exists():
        out(f"cached: {pq_path} (no cost, nothing downloaded)")
        return Result("cached", 0.0, pq_path, pq.read_metadata(pq_path).num_rows)
    cap = policy.max_cost_usd if max_cost_usd is None else min(policy.max_cost_usd, max_cost_usd)
    cost = client.get_cost(q)
    used = spent(root)
    out(f"estimated cost: ${cost:.4f}  ({q.dataset} {q.schema} {len(q.symbols)} symbol(s) {q.start} -> {q.end}); "
        f"cap ${cap:.2f}/request; recorded spend ${used:.2f} of ${policy.credit_usd:.2f} credit")
    if cost > cap:
        raise CostRefused(f"estimate ${cost:.4f} is above the ${cap:.2f} cap: narrow the symbols or dates")
    if used + cost > policy.credit_usd:
        raise CostRefused(f"estimate ${cost:.4f} would take recorded spend past the ${policy.credit_usd:.2f} credit")
    if not confirm:
        out("dry run: nothing downloaded. Re-run with --confirm to download.")
        return Result("dry_run", cost, pq_path)
    body = client.get_range_csv(q)
    table = csv_to_table(body)
    pq_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = pq_path.with_suffix(".parquet.tmp")
    pq.write_table(table, tmp)
    os.replace(tmp, pq_path)
    record = {**asdict(q), "symbols": list(q.symbols), "cost_usd": cost, "rows": table.num_rows,
              "bytes": len(body), "downloaded_at": datetime.now(UTC).isoformat()}
    meta_path.write_text(json.dumps(record, indent=1))
    with ledger_path(root).open("a") as f:
        f.write(json.dumps({"key": q.key, "cost_usd": cost, "at": record["downloaded_at"]}) + "\n")
    out(f"downloaded {table.num_rows} rows ({len(body)} bytes) -> {pq_path}")
    return Result("downloaded", cost, pq_path, table.num_rows)
