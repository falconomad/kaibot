"""Synthetic Alpaca answers for the daily bar store tests (story S84, #124). Offline: `FakeAlpaca` stands in for
urllib's urlopen, so no request leaves the machine and no key is needed."""

from __future__ import annotations

import importlib.util
import io
import json
import urllib.error
import urllib.parse
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from kaibot.common.ratelimit import Budget, BudgetSpec
from kaibot.ingest import alpaca_history as ah

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 12, 3, 0, tzinfo=UTC)  # 22:00 ET on Wed 11 Nov 2026: that session is complete
THROUGH = date(2026, 11, 11)
START = "2026-09-01"  # most tests download ten weeks, not ten years
ARRAY_OF = {"forward_split": "forward_splits", "reverse_split": "reverse_splits", "stock_dividend": "stock_dividends",
            "cash_dividend": "cash_dividends", "spin_off": "spin_offs", "name_change": "name_changes"}


def load_script(name: str = "fetch_daily"):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s


def api(key: str = "k", secret: str = "s", spec: BudgetSpec | None = None, clock: FakeClock | None = None):
    return ah.Alpaca(key, secret, budget=Budget("alpaca", spec or BudgetSpec(), clock or FakeClock()))


def sessions(first: str, n: int | None = None, last: date = THROUGH) -> list[date]:
    """Monday-Friday dates from `first`: `n` of them, or all up to `last`."""
    a = np.datetime64(first, "D")
    n = int(np.busday_count(a, np.datetime64(last, "D") + np.timedelta64(1, "D"))) if n is None else n
    return [d.astype(object) for d in np.busday_offset(a, np.arange(n), roll="forward")]


def stamp(d: date) -> str:
    """Alpaca stamps a daily bar with midnight in New York (04:00 or 05:00 UTC)."""
    return datetime(d.year, d.month, d.day, tzinfo=ET).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def bars(days: list[date], closes, volume=1_000_000) -> list[dict]:
    """Daily bars in Alpaca's shape: open = previous close, high and low 1% outside."""
    closes = [float(c) for c in closes]
    volume = list(volume) if hasattr(volume, "__len__") else [volume] * len(days)
    out = []
    for i, (d, c) in enumerate(zip(days, closes, strict=True)):
        o = closes[i - 1] if i else c
        out.append({"t": stamp(d), "o": o, "h": round(max(o, c) * 1.01, 4), "l": round(min(o, c) * 0.99, 4), "c": c,
                    "v": int(volume[i]), "n": 1000 + i, "vw": round((o + c) / 2, 4)})
    return out


def day_of(b: dict) -> date:
    return datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ET).date()


def adjusted(raw: list[dict], splits: dict | None = None, cash: dict | None = None, other: dict | None = None,
             decimals: int | None = None) -> list[dict]:
    """What `adjustment=all` returns for `raw`, as of its last bar: every bar before an ex-date is scaled.
    splits {ex_date: new shares per old share} (price / ratio, volume x ratio); cash {ex_date: dividend per
    share} (price x (1 - dividend / previous raw close)); other {ex_date: price factor} (a spin-off).
    `decimals` rounds the prices the way a vendor might."""
    days = [day_of(b) for b in raw]
    price, vol = np.ones(len(raw)), np.ones(len(raw))
    for ex, ratio in (splits or {}).items():
        before = np.array([d < ex for d in days])
        price[before] /= ratio
        vol[before] *= ratio
    for ex, amount in (cash or {}).items():
        before = np.array([d < ex for d in days])
        if before.any():
            price[before] *= 1.0 - amount / raw[int(np.flatnonzero(before)[-1])]["c"]
    for ex, f in (other or {}).items():
        price[np.array([d < ex for d in days])] *= f
    out = []
    for b, p, v in zip(raw, price, vol, strict=True):
        px = {k: b[k] * p for k in ("o", "h", "l", "c", "vw")}
        if decimals is not None:
            px = {k: round(x, decimals) for k, x in px.items()}
        out.append({**b, **px, "v": int(round(b["v"] * v))})
    return out


class _Resp(io.BytesIO):
    status = 200
    headers: dict = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeAlpaca:
    """Answers GET /v2/stocks/<symbol>/bars (1Day, raw or all, optional asof) and GET /v1/corporate-actions the
    way the documentation describes them, and records every request."""

    def __init__(self):
        self.raw: dict[tuple[str, str | None], list[dict]] = {}
        self.adj: dict[tuple[str, str | None], list[dict]] = {}
        self.actions: dict[str, list[dict]] = {}  # array name -> rows, as in the "corporate_actions" object
        self.status: dict[str, int] = {}  # symbol -> HTTP status of its bars
        self.ca_status = None  # function(query) -> HTTP status or None
        self.page = 10000
        self.ca_page = 1000
        self.requests: list[tuple[str, dict, dict]] = []

    def add(self, symbol: str, raw: list[dict], adj: list[dict] | None = None, asof: str | None = None) -> None:
        self.raw[(symbol, asof)] = raw
        self.adj[(symbol, asof)] = raw if adj is None else adj

    def act(self, kind: str, **row) -> None:
        self.actions.setdefault(ARRAY_OF[kind], []).append(row)

    # -- urlopen

    def __call__(self, req, timeout):
        u = urllib.parse.urlsplit(req.full_url)
        q = dict(urllib.parse.parse_qsl(u.query))
        self.requests.append((u.path, q, dict(req.header_items())))
        if u.netloc != "data.alpaca.markets" or u.scheme != "https":
            raise AssertionError(f"unexpected host {req.full_url}")
        if u.path == "/v1/corporate-actions":
            return self._actions(req, q)
        parts = u.path.split("/")
        assert parts[:3] == ["", "v2", "stocks"] and parts[4] == "bars", u.path
        return self._bars(req, parts[3], q)

    def _fail(self, req, status: int):
        raise urllib.error.HTTPError(req.full_url, status, "err", {}, io.BytesIO(b"{}"))

    def _bars(self, req, sym: str, q: dict):
        if sym in self.status:
            self._fail(req, self.status[sym])
        book = {"raw": self.raw, "all": self.adj}[q["adjustment"]]
        rows = book.get((sym, q.get("asof")))
        if rows is None:  # not found on the as-of date: Alpaca answers for the symbol alone
            rows = book.get((sym, None), [])
        lo, hi = datetime.fromisoformat(q["start"]), datetime.fromisoformat(q["end"])
        rows = [b for b in rows if lo <= datetime.fromisoformat(b["t"].replace("Z", "+00:00")) <= hi]
        size = min(int(q["limit"]), self.page)
        at = int(q.get("page_token", 0))
        more = at + size < len(rows)
        return _Resp(json.dumps({"bars": rows[at:at + size] or None, "symbol": sym,
                                 "next_page_token": str(at + size) if more else None}).encode())

    def _actions(self, req, q: dict):
        status = self.ca_status(q) if self.ca_status else None
        if status:
            self._fail(req, status)
        want, kinds = set(q["symbols"].split(",")), set(q["types"].split(","))
        lo, hi = q["start"], q["end"]
        hits = []
        for kind in sorted(kinds):
            for r in self.actions.get(ARRAY_OF[kind], []):
                names = {r.get(k) for k in ("symbol", "source_symbol", "old_symbol", "new_symbol")}
                when = r.get("process_date") or r.get("ex_date") or ""
                if names & want and (not when or lo <= when <= hi):
                    hits.append((when, ARRAY_OF[kind], r))
        hits.sort(key=lambda h: (h[0], h[1], json.dumps(h[2], sort_keys=True)))
        size = min(int(q["limit"]), self.ca_page)
        at = int(q.get("page_token", 0))
        block: dict[str, list] = {}
        for _, name, r in hits[at:at + size]:
            block.setdefault(name, []).append(r)
        return _Resp(json.dumps({"corporate_actions": block,
                                 "next_page_token": str(at + size) if at + size < len(hits) else None}).encode())

    # -- what was asked

    def bar_requests(self) -> list[tuple[str, dict]]:
        return [(p.split("/")[3], q) for p, q, _ in self.requests if p.endswith("/bars")]

    def action_requests(self) -> list[dict]:
        return [q for p, q, _ in self.requests if p == "/v1/corporate-actions"]


def run(tmp_path: Path, *args: str, now: datetime = NOW, start: str = START, client=None) -> int:
    """scripts/fetch_daily.py against whatever stands in for urlopen (see the `alpaca` fixture)."""
    return load_script().main(["--data", str(tmp_path / "data"), "--start", start, *args], api=client or api(), now=now)
