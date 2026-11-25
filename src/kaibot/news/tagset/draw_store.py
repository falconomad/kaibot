"""Sample v2 for the headline tags, drawn on the Mac from the research store (story S105, #379). Read-only.

v1 (`draw.py`) covers 29 Apr to 18 Nov 2026 of one feed only, because the Alpaca endpoint served nothing older to
the paper connector on 19 Nov 2026. The Mac's research store (`data/news/symbol=XYZ/YYYY.parquet`, written by
`ingest/alpaca_history.fetch_news`) holds the headlines downloaded earlier for the research stocks. v2 is drawn from
it, so the three open points of #379 can be measured: across years, guidance on earnings days against other days,
and enough positives of the rare tags.

    python -m kaibot.news.tagset.draw_store history            # what the store holds: per year, count and sources
    python -m kaibot.news.tagset.draw_store probe --env FILE   # 3 news requests with the engine's key: old Benzinga?
    python -m kaibot.news.tagset.draw_store draw               # writes headlines_v2.jsonl and draw_v2.jsonl here

The draw (SEED_V2, frozen before any v2 headline was seen):

1. **random**: per calendar year in the store, RANDOM_PER_YEAR headlines drawn uniformly (all of them if fewer).
   This stratum measures recall and the base rates. Every tag's precision is also reported on it.
2. **fired:<tag>**: for each tag that did not ship in v1, FIRED_PER_TAG headlines drawn uniformly from those on
   which the v1 rule (RULES_VERSION 1, unchanged) fires and that are not already drawn. For `guidance_up` and
   `guidance_down`, FIRED_PER_TAG on earnings days and FIRED_PER_TAG on other days. This stratum measures precision:
   it is a uniform sample of what the rule tags in the store.

The two files are written separately on purpose. `headlines_v2.jsonl` (shuffled with the seed; `n`, `created_at`,
`symbols`, `source`, `headline`, `earnings_day`) is what the labeller sees. `draw_v2.jsonl` (`n`, `stratum`) is not
opened until every headline is labelled, so a label cannot depend on which rule picked the headline. The labels go
to `labels_v2.jsonl` (`n`, `tags`), labelled for all eleven tags; then `measure.py --v2` reads all three.

**Earnings day** (point in time, research only): a headline is on an earnings day when one of its symbols has an
8-K with Item 2.02 in `data/earnings/announcements.parquet` (S85) whose `session` is the headline's New York date,
or whose acceptance time falls on that New York date. Without that file every headline gets `earnings_day: null`
and the split is not reported.

Headlines are untrusted text: they are copied, never interpreted. Nothing here places, changes or reads an order.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from kaibot.news.tags import ALL_TAGS, RULES_VERSION, all_tags

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
NEWS = ROOT / "data" / "news"
ANNOUNCEMENTS = ROOT / "data" / "earnings" / "announcements.parquet"
ET = ZoneInfo("America/New_York")

SEED_V2 = 379205
RANDOM_PER_YEAR = 50
FIRED_PER_TAG = 25
GUIDANCE = ("guidance_up", "guidance_down")
DRAWN_WITH_RULES_VERSION = 1
# The tags v2 draws a fired stratum for: the ten v1 left out (all but analyst_up), in this order (the order draws
# from the seeded generator). Frozen with the declared draw of #399; not tags.NOT_SHIPPED, which follows the newest
# measurement and so would change this draw.
DRAWN_FOR: tuple[str, ...] = tuple(t for t in ALL_TAGS if t != "analyst_up")
# Three probe requests: one January week each; enough to see whether old headlines and which sources come back.
PROBES = (("AAPL", "2016-01-04T00:00:00Z", "2016-01-09T00:00:00Z"),
          ("JPM", "2021-01-04T00:00:00Z", "2021-01-09T00:00:00Z"),
          ("MSFT", "2025-01-06T00:00:00Z", "2025-01-11T00:00:00Z"))


# ---------------------------------------------------------------- reading the store
def read_store(news_dir: Path | None = None) -> list[dict]:
    """Every stored headline once: [{created_at (aware UTC), headline, source, symbols (list)}], oldest first.

    The same story is stored once per symbol file it was fetched for; it is kept once (same headline and time)."""
    import pyarrow.parquet as pq

    seen: dict[tuple[str, datetime], dict] = {}
    for f in sorted((NEWS if news_dir is None else news_dir).glob("symbol=*/*.parquet")):
        t = pq.read_table(f, columns=["created_at", "headline", "source", "symbols"]).to_pylist()
        for r in t:
            ts, h = r.get("created_at"), r.get("headline")
            if not isinstance(h, str) or not h.strip() or ts is None:
                continue
            ts = ts if ts.tzinfo else ts.replace(tzinfo=UTC)
            ts = ts.astimezone(UTC)
            key = (h, ts)
            syms = [s for s in str(r.get("symbols") or "").split(",") if s]
            if key in seen:
                seen[key]["symbols"] = sorted(set(seen[key]["symbols"]) | set(syms))
                continue
            seen[key] = {"created_at": ts, "headline": h, "source": str(r.get("source") or ""),
                         "symbols": sorted(set(syms))}
    return sorted(seen.values(), key=lambda r: (r["created_at"], r["headline"]))


def earnings_days(path: Path | None = None) -> set[tuple[str, str]] | None:
    """{(symbol, 'YYYY-MM-DD' New York date)} of every Item 2.02 filing: its session and its acceptance date."""
    path = ANNOUNCEMENTS if path is None else path
    if not path.exists():
        return None
    import pandas as pd

    t = pd.read_parquet(path, columns=["symbol", "accepted_ts", "session"])
    out: set[tuple[str, str]] = set()
    for sym, acc, ses in zip(t["symbol"], pd.to_datetime(t["accepted_ts"], utc=True), t["session"], strict=True):
        out.add((str(sym), acc.tz_convert(ET).date().isoformat()))
        if ses is not None and not (isinstance(ses, float)):
            out.add((str(sym), pd.Timestamp(ses).date().isoformat()))
    return out


def on_earnings_day(row: dict, days: set[tuple[str, str]] | None) -> bool | None:
    if days is None:
        return None
    d = row["created_at"].astimezone(ET).date().isoformat()
    return any((s, d) in days for s in row["symbols"])


# ---------------------------------------------------------------- what the store holds
def history(rows: list[dict]) -> str:
    by_year: dict[int, Counter] = {}
    for r in rows:
        by_year.setdefault(r["created_at"].year, Counter())[r["source"] or "(none)"] += 1
    lines = ["| Year | Headlines | Sources (count) |", "|---|---|---|"]
    for y in sorted(by_year):
        c = by_year[y]
        lines.append(f"| {y} | {sum(c.values())} | " + ", ".join(f"{s} {n}" for s, n in c.most_common()) + " |")
    if rows:
        lines += ["", f"Oldest: {rows[0]['created_at'].isoformat()}; newest: {rows[-1]['created_at'].isoformat()}; "
                      f"{len(rows)} distinct headlines."]
    else:
        lines += ["", "The store holds no headlines (data/news is empty or missing)."]
    return "\n".join(lines)


# ---------------------------------------------------------------- the draw
def draw(rows: list[dict], days: set[tuple[str, str]] | None, seed: int = SEED_V2,
         per_year: int = RANDOM_PER_YEAR, per_tag: int = FIRED_PER_TAG) -> tuple[list[dict], list[dict]]:
    """(headlines, strata). Deterministic for the same store and seed. Rules run only to choose the fired strata."""
    if RULES_VERSION != DRAWN_WITH_RULES_VERSION:
        raise SystemExit(f"the rules are version {RULES_VERSION}; v2 is declared for version "
                         f"{DRAWN_WITH_RULES_VERSION}. A new rule version needs a new, declared sample.")
    rng = random.Random(seed)
    picked: dict[int, str] = {}  # index into rows -> stratum
    years: dict[int, list[int]] = {}
    for i, r in enumerate(rows):
        years.setdefault(r["created_at"].year, []).append(i)
    for y in sorted(years):
        idx = years[y]
        for i in (idx if len(idx) <= per_year else rng.sample(idx, per_year)):
            picked[i] = "random"
    fired = [all_tags(r["headline"]) for r in rows]
    eday = [on_earnings_day(r, days) for r in rows]
    for tag in DRAWN_FOR:
        groups = ([("earnings", True), ("other", False)] if tag in GUIDANCE and days is not None else [("", None)])
        for name, want in groups:
            pool = [i for i in range(len(rows)) if tag in fired[i] and i not in picked
                    and (want is None or eday[i] is want)]
            for i in (pool if len(pool) <= per_tag else rng.sample(pool, per_tag)):
                picked[i] = f"fired:{tag}" + (f":{name}" if name else "")
    order = sorted(picked)
    rng.shuffle(order)
    heads, strata = [], []
    for n, i in enumerate(order, start=1):
        r = rows[i]
        heads.append({"n": n, "created_at": r["created_at"].strftime("%Y-%m-%dT%H:%M:%SZ"), "symbols": r["symbols"],
                      "source": r["source"], "headline": r["headline"], "earnings_day": eday[i]})
        strata.append({"n": n, "stratum": picked[i]})
    return heads, strata


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


# ---------------------------------------------------------------- the probe (engine's key, 3 requests)
def probe(env_file: str) -> str:
    from kaibot.engine.config import load_env_file
    from kaibot.ingest.alpaca_history import Alpaca

    load_env_file(env_file)
    key = os.environ.get("ALPACA_API_KEY") or ""
    secret = os.environ.get("ALPACA_SECRET") or os.environ.get("ALPACA_API_SECRET") or ""
    if not key or not secret:
        raise SystemExit("missing ALPACA_API_KEY / ALPACA_SECRET in the .env (values are never printed)")
    api = Alpaca(key, secret, per_min=10)
    lines = ["| Symbol | Window (UTC) | Headlines | Sources | Oldest |", "|---|---|---|---|---|"]
    for sym, start, end in PROBES:
        d = api.get("/v1beta1/news", {"symbols": sym, "start": start, "end": end, "limit": 50, "sort": "asc",
                                      "include_content": "false"})
        news = d.get("news") or []
        src = Counter(str(n.get("source") or "(none)") for n in news)
        oldest = min((str(n.get("created_at")) for n in news), default="-")
        lines.append(f"| {sym} | {start[:10]} to {end[:10]} | {len(news)} | "
                     + (", ".join(f"{s} {c}" for s, c in src.most_common()) or "-") + f" | {oldest} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("history")
    p = sub.add_parser("probe")
    p.add_argument("--env", default=str(Path.home() / "Projects" / "kaibot-secrets" / ".env"))
    d = sub.add_parser("draw")
    d.add_argument("--out", default=str(HERE))
    a = ap.parse_args(argv)
    if a.cmd == "probe":
        print(probe(a.env))
        return 0
    rows = read_store()
    if a.cmd == "history":
        print(history(rows))
        return 0
    if not rows:
        print("data/news holds no headlines: nothing to draw", file=sys.stderr)
        return 1
    days = earnings_days()
    heads, strata = draw(rows, days)
    out = Path(a.out)
    write_jsonl(out / "headlines_v2.jsonl", heads)
    write_jsonl(out / "draw_v2.jsonl", strata)
    print(f"{len(heads)} headlines -> {out / 'headlines_v2.jsonl'} (strata in draw_v2.jsonl: do not open before "
          f"labelling); earnings days {'from ' + str(ANNOUNCEMENTS) if days is not None else 'not available'}")
    print(Counter(s["stratum"] for s in strata).most_common())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
