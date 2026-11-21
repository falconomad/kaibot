#!/usr/bin/env python3
"""The two Data tasks of S98 (#372): what `data/news` covers, and whether Alpaca still serves headlines for former
index members. Read-only for the store. Nothing here runs or changes a rule.

    .venv/bin/python scripts/news_coverage.py                         # the coverage table, offline
    .venv/bin/python scripts/news_coverage.py --probe-former --env ~/Projects/kaibot-secrets/.env   # + 5 requests

Coverage: headlines per company per year from `data/news/symbol=XYZ/*.parquet`, and the complete news months as
`docs/prereg/news_continuation.md` defines them (`replay.oneshots_intraday.complete_months`, with the months that
have minute bars in `data/bars_1m`). It says plainly which point-in-time members have no stored headline at all.

Probe (only with --probe-former): five former S&P 500 members, chosen by rule before anything is asked: for each
of the removal years 2019 to 2023, the company that left the index that year whose last ticker sorts first. One
request each to `/v1beta1/news` (the endpoint the engine's poller reads) for the 30 days before its last day in
the index, `limit=50`. It prints the count and the sources, and estimates the requests for every former member
if headlines come back. Nothing is fetched beyond those five requests (the story says: do not fetch them here).

Writes data/reports/news_coverage.md.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PROBE_YEARS = (2019, 2020, 2021, 2022, 2023)


def headline_counts(news_dir: Path) -> dict[str, dict[str, int]]:
    """symbol -> {YYYY-MM: headlines} from the store (each symbol's own files; a story about two symbols counts
    for both, as the rules read it)."""
    import pyarrow.parquet as pq

    out: dict[str, dict[str, int]] = {}
    for f in sorted(Path(news_dir).glob("symbol=*/*.parquet")):
        sym = f.parent.name[len("symbol="):]
        ts = pq.read_table(f, columns=["created_at"]).column("created_at").to_pylist()
        c = out.setdefault(sym, {})
        for t in ts:
            if t is not None:
                k = f"{t.year:04d}-{t.month:02d}"
                c[k] = c.get(k, 0) + 1
    return out


def universe_stocks(path: Path = ROOT / "config" / "universe_history.yaml") -> set[str]:
    import yaml

    cfg = yaml.safe_load(Path(path).read_text())
    return {s for syms in cfg["sectors"].values() for s in syms}


def bar_months(bars_dir: Path, stocks: set[str] | None = None) -> dict[str, set[str]]:
    """symbol -> months with minute bars, for the universe's stocks only (ETFs have no company news), as the
    harness passes them (scripts/oneshots.py)."""
    out: dict[str, set[str]] = {}
    for f in Path(bars_dir).glob("symbol=*/*.parquet"):
        sym = f.parent.name[len("symbol="):]
        if stocks is None or sym in stocks:
            out.setdefault(sym, set()).add(f.stem)
    return out


def last_headline(news_dir: Path):
    import numpy as np
    import pyarrow.parquet as pq

    best = None
    for f in Path(news_dir).glob("symbol=*/*.parquet"):
        ts = [t for t in pq.read_table(f, columns=["created_at"]).column("created_at").to_pylist() if t is not None]
        if ts:
            m = max(ts)
            best = m if best is None or m > best else best
    return None if best is None else np.datetime64(best.date(), "D")


def by_year(counts: dict[str, dict[str, int]]) -> dict[str, dict[int, int]]:
    out: dict[str, dict[int, int]] = {}
    for s, months in counts.items():
        for mon, k in months.items():
            y = int(mon[:4])
            out.setdefault(s, {})[y] = out.setdefault(s, {}).get(y, 0) + k
    return out


def probe_choice(ms) -> list[tuple[str, str, date]]:
    """(company, last ticker, last day in the index) for the five pre-declared former members."""
    gone = []
    for c, spells in ms.spells.items():
        sp = spells[-1]
        if sp.end is not None and sp.last_day is not None:
            gone.append((sp.last_day.year, sp.tickers[-1], c, sp.last_day))
    out = []
    for y in PROBE_YEARS:
        cands = sorted((t, c, d) for yy, t, c, d in gone if yy == y)
        if cands:
            t, c, d = cands[0]
            out.append((c, t, d))
    return out


def probe(env_file: str, ms) -> list[dict]:
    from kaibot.engine.config import load_env_file
    from kaibot.ingest.alpaca_history import Alpaca

    load_env_file(env_file)
    key = os.environ.get("ALPACA_API_KEY") or ""
    secret = os.environ.get("ALPACA_SECRET") or os.environ.get("ALPACA_API_SECRET") or ""
    if not key or not secret:
        raise SystemExit("missing ALPACA_API_KEY / ALPACA_SECRET in the .env (values are never printed)")
    api = Alpaca(key, secret, per_min=10)
    rows = []
    for company, ticker, last in probe_choice(ms):
        start = last - timedelta(days=30)
        d = api.get("/v1beta1/news", {"symbols": ticker, "start": f"{start}T00:00:00Z", "end": f"{last}T23:59:59Z",
                                      "limit": 50, "sort": "asc", "include_content": "false"})
        news = d.get("news") or []
        rows.append({"company": company, "ticker": ticker, "last_day": str(last), "headlines": len(news),
                     "more_pages": bool(d.get("next_page_token")),
                     "sources": dict(Counter(str(n.get("source") or "(none)") for n in news))})
    return rows


def markdown(counts, complete, members_without, rows, n_former) -> str:
    years = sorted({y for v in by_year(counts).values() for y in v})
    L = ["# data/news coverage (S98, #372)", "",
         f"{len(counts)} symbols with stored headlines. Complete news months (news_continuation definition): "
         + (", ".join(complete) or "none") + ".", "",
         "| Symbol | " + " | ".join(str(y) for y in years) + " |", "|---|" + "---|" * len(years)]
    for s, v in sorted(by_year(counts).items()):
        L.append(f"| {s} | " + " | ".join(str(v.get(y, 0)) for y in years) + " |")
    L += ["", f"Point-in-time S&P 500 companies since 2016 with **no** stored headline: {members_without}. "
          "The headline version of `news_gap_week` therefore runs on today's list only (survivorship bias); only "
          "`info8k` can run on the full point-in-time universe."]
    if rows is not None:
        L += ["", "## Alpaca news for five former members (one request each)", "",
              "| Company | Ticker | Last day in the index | Headlines (30 days before) | More pages | Sources |",
              "|---|---|---|---|---|---|"]
        L += [f"| {r['company']} | {r['ticker']} | {r['last_day']} | {r['headlines']} | {r['more_pages']} | "
              f"{r['sources']} |" for r in rows]
        if any(r["headlines"] for r in rows):
            L += ["", f"Headlines came back. Estimate for all {n_former} former members: one paged request per "
                  "company and year of membership (`ingest/alpaca_history.fetch_news`, 50 a page), so at least "
                  f"{n_former} x the years each was a member. Not fetched in this story."]
        else:
            L += ["", "No headline came back for any of the five: the former members' history is not available "
                  "from this endpoint, and the headline version stays on today's list."]
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", type=Path, default=ROOT / "data")
    ap.add_argument("--probe-former", action="store_true")
    ap.add_argument("--env", default=str(Path.home() / "Projects" / "kaibot-secrets" / ".env"))
    a = ap.parse_args(argv)
    from kaibot.ingest.sp500_pit import Membership
    from kaibot.replay.oneshots_intraday import complete_months

    counts = headline_counts(a.data / "news")
    from kaibot.replay.oneshots_intraday import month_ranges

    complete = month_ranges(complete_months(counts, bar_months(a.data / "bars_1m", universe_stocks()),
                                            last_headline(a.data / "news")))
    ms = Membership.load()
    have = set(counts)
    without = sum(1 for c, sp in ms.spells.items() if not ({t for s in sp for t in s.tickers} & have))
    n_former = sum(1 for sp in ms.spells.values() if sp[-1].end is not None)
    rows = probe(a.env, ms) if a.probe_former else None
    md = markdown(counts, complete, without, rows, n_former)
    out = a.data / "reports"
    out.mkdir(parents=True, exist_ok=True)
    (out / "news_coverage.md").write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
