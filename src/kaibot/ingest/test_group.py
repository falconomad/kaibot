"""Screen candidate tickers for a research-only test group (story S66): beta, liquidity, spread, price, history.

Everything comes from Alpaca SIP history that is >= 15 min old (free plan rule), under the shared `alpaca`
budget. The live engine never imports this module and never reads the `test_groups` block it writes into
config/universe_seed.yaml (tests/unit/test_screen_test_group.py guards both).

Definitions (kept close to the replay's, src/kaibot/replay/features.py):
  beta        slope of the symbol's daily close-to-close returns on SPY's, last `BETA_DAYS` common sessions
  ADV         mean of close x volume over the last `ADV_DAYS` sessions (the replay's adv20)
  spread      median of (ask - bid) / mid in bp over NBBO quotes sampled at fixed times of the last sessions
              (`nbbo`). When historical quotes are not served, a Roll (1984) estimate from 1-minute closes is
              used instead and labelled `roll`: it is a rough proxy, read it as an order of magnitude.
  first date  first daily bar Alpaca has; Alpaca history starts in 2016, so older listings show as "<=2016-01"
"""

from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from kaibot.common.http import HttpError

ET = ZoneInfo("America/New_York")
HISTORY_START = datetime(2016, 1, 1, tzinfo=UTC)  # Alpaca's SIP history begins here
SIP_DELAY = timedelta(minutes=20)  # free plan: SIP only when >= 15 min old; 20 leaves a margin
BETA_DAYS = 60
ADV_DAYS = 20
SPREAD_SESSIONS = 5
SPREAD_TIMES_ET = (time(10, 0), time(11, 30), time(13, 0), time(14, 30), time(15, 30))
QUOTES_PER_SAMPLE = 50
SHORT_HISTORY_SESSIONS = 252  # flagged (not failed) below one year of sessions
GROUP_HEADER = "# S66 test groups (research only): NOT read by the live engine, the Strategy page or default replays."


@dataclass(frozen=True)
class Filters:
    min_adv_usd: float = 50e6
    max_spread_bp: float = 5.0
    min_price_usd: float = 5.0
    min_sessions: int = 126  # about six months: enough for the 60-day beta and a first replay window
    min_beta: float | None = None  # optional; reported either way


@dataclass
class Screen:
    symbol: str
    beta: float | None = None
    adv_usd: float | None = None
    spread_bp: float | None = None
    spread_src: str = "-"
    price: float | None = None
    first_date: date | None = None
    sessions: int = 0
    reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.reasons


def sip_end(now: datetime | None = None) -> datetime:
    """Latest instant this module may ask SIP data for."""
    return (now or datetime.now(UTC)) - SIP_DELAY


def filters_from_profiles(path: Path, **overrides) -> Filters:
    """The existing universe filters (config/profiles.yaml `universe`), read-only."""
    import yaml

    u = yaml.safe_load(Path(path).read_text())["universe"]
    return Filters(min_adv_usd=float(u["min_adv_usd"]), max_spread_bp=float(u["max_spread_bp"]),
                   min_price_usd=float(u["min_price_usd"]), **{k: v for k, v in overrides.items() if v is not None})


def _paged(api, path: str, params: dict, key: str, max_rows: int | None = None) -> list[dict]:
    rows: list[dict] = []
    token = None
    while True:
        p = dict(params)
        if token:
            p["page_token"] = token
        d = api.get(path, p)
        rows += d.get(key) or []
        token = d.get("next_page_token")
        if not token or (max_rows and len(rows) >= max_rows):
            return rows


def fetch_daily(api, symbol: str, end: datetime, start: datetime = HISTORY_START) -> list[dict]:
    """Completed daily SIP bars (split-adjusted), oldest first. The session still running at `end` is dropped."""
    if end > sip_end() + timedelta(seconds=5):
        raise ValueError("SIP data must be >= 15 min old")
    bars = _paged(api, f"/v2/stocks/{symbol}/bars",
                  {"timeframe": "1Day", "start": start.isoformat(), "end": end.isoformat(), "limit": 10000,
                   "feed": "sip", "adjustment": "split"}, "bars")
    et = end.astimezone(ET)
    cutoff = et.date() if et.time() < time(16, 20) else et.date() + timedelta(days=1)
    return [b for b in bars if _bar_date(b) < cutoff]


def _bar_date(b: dict) -> date:
    return date.fromisoformat(b["t"][:10])


def fetch_spread_bp(api, symbol: str, sessions: list[date], end: datetime) -> tuple[float | None, str]:
    """Median NBBO spread in bp from quotes sampled at fixed ET times of `sessions`; Roll estimate as fallback."""
    samples: list[float] = []
    try:
        for d in sessions:
            for t in SPREAD_TIMES_ET:
                at = datetime.combine(d, t, ET).astimezone(UTC)
                if at + timedelta(minutes=1) > end:
                    continue
                q = api.get(f"/v2/stocks/{symbol}/quotes",
                            {"start": at.isoformat(), "end": (at + timedelta(minutes=1)).isoformat(),
                             "limit": QUOTES_PER_SAMPLE, "feed": "sip"}).get("quotes") or []
                bps = [(x["ap"] - x["bp"]) / ((x["ap"] + x["bp"]) / 2) * 1e4 for x in q
                       if 0 < x.get("bp", 0) <= x.get("ap", 0)]
                if bps:
                    samples.append(statistics.median(bps))
    except HttpError as e:
        if e.status not in (403, 404, 422):
            raise
        samples = []
    if samples:
        return statistics.median(samples), "nbbo"
    if not sessions:
        return None, "-"
    start = datetime.combine(sessions[0], time(9, 30), ET).astimezone(UTC)
    bars = _paged(api, f"/v2/stocks/{symbol}/bars",
                  {"timeframe": "1Min", "start": start.isoformat(), "end": end.isoformat(), "limit": 10000,
                   "feed": "sip", "adjustment": "split"}, "bars")
    by_day: dict[date, list[float]] = {}
    for b in bars:
        et = datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ET)
        if time(9, 30) <= et.time() < time(16, 0):
            by_day.setdefault(et.date(), []).append(float(b["c"]))
    est = [x for x in (roll_spread_bp(c) for c in by_day.values()) if x is not None]
    return (statistics.median(est), "roll") if est else (None, "-")


def roll_spread_bp(closes: list[float]) -> float | None:
    """Roll (1984): spread = 2 * sqrt(-cov(r_t, r_t-1)) of consecutive returns. None when the covariance is >= 0."""
    if len(closes) < 30:
        return None
    r = [math.log(b / a) for a, b in zip(closes, closes[1:], strict=False) if a > 0 and b > 0]
    x, y = r[:-1], r[1:]
    mx, my = sum(x) / len(x), sum(y) / len(y)
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True)) / (len(x) - 1)
    return 2 * math.sqrt(-cov) * 1e4 if cov < 0 else None


def beta_vs(sym: list[dict], mkt: list[dict], days: int = BETA_DAYS) -> float | None:
    """OLS slope of daily returns on the market's over the last `days` sessions both traded. None if fewer."""
    m = {_bar_date(b): float(b["c"]) for b in mkt}
    common = [(float(b["c"]), m[_bar_date(b)]) for b in sym if _bar_date(b) in m][-(days + 1):]
    if len(common) < days + 1:
        return None
    rs = [a[0] / b[0] - 1 for b, a in zip(common, common[1:], strict=False)]
    rm = [a[1] / b[1] - 1 for b, a in zip(common, common[1:], strict=False)]
    ms, mm = sum(rs) / days, sum(rm) / days
    var = sum((x - mm) ** 2 for x in rm)
    return sum((a - ms) * (b - mm) for a, b in zip(rs, rm, strict=True)) / var if var > 0 else None


def screen_symbol(api, symbol: str, mkt_daily: list[dict], f: Filters, end: datetime) -> Screen:
    s = Screen(symbol)
    try:
        daily = fetch_daily(api, symbol, end)
    except HttpError as e:
        s.reasons.append(f"no data (HTTP {e.status})")
        return s
    if not daily:
        s.reasons.append("no data (unknown or delisted ticker?)")
        return s
    s.sessions = len(daily)
    s.first_date = _bar_date(daily[0])
    s.price = float(daily[-1]["c"])
    last = daily[-ADV_DAYS:]
    s.adv_usd = sum(float(b["c"]) * float(b["v"]) for b in last) / len(last)
    s.beta = beta_vs(daily, mkt_daily)
    s.spread_bp, s.spread_src = fetch_spread_bp(api, symbol, [_bar_date(b) for b in daily[-SPREAD_SESSIONS:]], end)

    if s.sessions < f.min_sessions:
        s.reasons.append(f"history {s.sessions} sessions < {f.min_sessions}")
    elif s.sessions < SHORT_HISTORY_SESSIONS:
        s.notes.append(f"short history ({s.sessions} sessions)")
    if s.price < f.min_price_usd:
        s.reasons.append(f"price ${s.price:.2f} < ${f.min_price_usd:g}")
    if s.adv_usd < f.min_adv_usd:
        s.reasons.append(f"ADV ${s.adv_usd / 1e6:.0f}M < ${f.min_adv_usd / 1e6:.0f}M")
    if s.spread_bp is None:
        s.reasons.append("spread unknown")
    elif s.spread_bp > f.max_spread_bp:
        s.reasons.append(f"spread {s.spread_bp:.1f} bp > {f.max_spread_bp:g} bp")
    if s.spread_src == "roll":
        s.notes.append("spread is a Roll estimate, not NBBO")
    if s.beta is None:
        s.notes.append(f"beta needs {BETA_DAYS + 1} sessions")
    elif f.min_beta is not None and s.beta < f.min_beta:
        s.reasons.append(f"beta {s.beta:.2f} < {f.min_beta:g}")
    elif s.beta < 1:
        s.notes.append("beta below 1")
    return s


def screen(api, symbols: list[str], f: Filters, market: str = "SPY", now: datetime | None = None,
           progress=None) -> list[Screen]:
    end = sip_end(now)
    mkt = fetch_daily(api, market, end)
    out = []
    for i, sym in enumerate(symbols, 1):
        out.append(screen_symbol(api, sym, mkt, f, end))
        if progress:
            progress(i, len(symbols), sym)
    return out


def format_table(rows: list[Screen]) -> str:
    def n(v, fmt):
        return "-" if v is None else format(v, fmt)

    head = (f"{'symbol':<7}{'beta60':>7}{'ADV $M':>9}{'spread bp':>11}{'src':>6}{'price':>10}  {'first bar':<11}"
            f"{'sess':>5}  result")
    lines = [head, "-" * len(head)]
    for s in rows:
        first = "-" if s.first_date is None else str(s.first_date)
        if s.first_date and s.first_date <= date(2016, 1, 8):
            first = "<=2016-01"
        why = "; ".join(s.reasons) if s.reasons else ""
        note = f" [{'; '.join(s.notes)}]" if s.notes else ""
        lines.append(f"{s.symbol:<7}{n(s.beta, '.2f'):>7}{n(s.adv_usd and s.adv_usd / 1e6, ',.0f'):>9}"
                     f"{n(s.spread_bp, '.1f'):>11}{s.spread_src:>6}{n(s.price, ',.2f'):>10}  {first:<11}"
                     f"{s.sessions:>5}  {'PASS' if s.passed else 'FAIL: ' + why}{note}")
    lines.append(f"{sum(s.passed for s in rows)} of {len(rows)} pass")
    return "\n".join(lines)


_SYMBOL = re.compile(r"^[A-Z][A-Z.]{0,5}$")


def write_test_group(seed: Path, group: str, symbols: list[str], label: str, screened_on: date, f: Filters) -> None:
    """Add or replace `test_groups.<group>` at the end of the universe seed; every other line stays byte-identical.

    Refuses symbols already in the live `sectors`, so a test group can never be confused with the live universe."""
    import yaml

    if not re.fullmatch(r"[a-z][a-z0-9_]*", group):
        raise ValueError(f"bad group name {group!r}")
    bad = [s for s in symbols if not _SYMBOL.match(s)]
    if bad or not symbols:
        raise ValueError(f"bad or empty symbol list: {bad}")
    text = seed.read_text()
    cfg = yaml.safe_load(text)
    live = {x for v in cfg["sectors"].values() for x in v} | set(cfg["sectors"]) | {cfg["market"]}
    if live & set(symbols):
        raise ValueError(f"already in the live universe: {sorted(live & set(symbols))}")
    groups = dict(cfg.get("test_groups") or {})
    groups[group] = {"label": label, "screened_on": str(screened_on),
                     "filters": f"ADV>=${f.min_adv_usd / 1e6:.0f}M spread<={f.max_spread_bp:g}bp "
                                f"price>=${f.min_price_usd:g} sessions>={f.min_sessions}",
                     "symbols": list(symbols)}
    base = re.split(r"(?m)^(?:# S66 test groups[^\n]*\n)?test_groups:[ \t]*\n", text)[0].rstrip("\n")
    block = [GROUP_HEADER, "test_groups:"]
    for name, g in groups.items():
        block += [f"  {name}:", f"    label: {g['label']}", f"    screened_on: \"{g['screened_on']}\"",
                  f"    filters: \"{g['filters']}\"", f"    symbols: [{', '.join(g['symbols'])}]"]
    out = base + "\n\n" + "\n".join(block) + "\n"
    after = yaml.safe_load(out)
    if any(after[k] != cfg[k] for k in cfg if k != "test_groups") or after["test_groups"] != groups:
        raise RuntimeError("refusing to write: the rewritten seed would not round-trip")
    seed.write_text(out)
