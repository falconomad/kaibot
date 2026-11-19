"""Company name + logo URL for every universe symbol, from Finnhub's free /stock/profile2.

Writes apps/web/src/data/symbols.json (public data only, no key). The dashboard loads each logo straight from
Finnhub's static image host and falls back to initials when there is none (ETFs usually have none).
Only `name`, `logo` and `industry` are this script's: any other field already in the file (`domain`, #330) is kept.
Run again when the universe changes:  python scripts/fetch_symbol_meta.py --env ~/Projects/kaibot-secrets/.env

With --fill-domains it also looks up each symbol whose `domain` is still null in AllInvestView's free search
(GET https://www.allinvestview.com/api/logo-search/?q=SYMBOL, no key, 60 a minute per IP; one call every 1.2 s
here, under the `allinvestview_logo_search` budget in config/sources.yaml). It takes only the result whose symbol
is exactly the ticker and whose exchange is a US one; anything else leaves `domain: null` (#331). A domain already
in the file is never replaced: use apps/web/scripts/logo_domains.mjs --all for that.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kaibot.common.http import HttpError, get_json  # noqa: E402
from kaibot.common.ratelimit import Budget  # noqa: E402

ETF_NAMES = {"SPY": "S&P 500 ETF", "XLK": "Technology ETF", "XLF": "Financials ETF", "XLV": "Health Care ETF",
             "XLY": "Consumer Discretionary ETF", "XLP": "Consumer Staples ETF", "XLE": "Energy ETF",
             "XLI": "Industrials ETF", "XLU": "Utilities ETF", "XLRE": "Real Estate ETF", "XLC": "Communication ETF",
             "XLB": "Materials ETF"}


OWN_FIELDS = ("name", "logo", "industry")  # what this script produces; every other field belongs to someone else


SEARCH_URL = "https://www.allinvestview.com/api/logo-search/"
SEARCH_SOURCE = "allinvestview_logo_search"
SEARCH_GAP_S = 1.2  # the same pace as apps/web/scripts/logo_domains.mjs
# NASDAQ, NYSE, NYSE Arca, NYSE American (AMEX), Cboe BZX (BATS), as the search API spells them; the same set as
# apps/web/api/logo-domain.ts.
US_EXCHANGES = frozenset({"NASDAQ", "NASDAQGS", "NASDAQGM", "NASDAQCM", "NYSE", "NYSE ARCA", "NYSEARCA", "ARCA",
                          "NYSE AMERICAN", "NYSE MKT", "NYSEAMERICAN", "AMEX", "BATS", "CBOE", "CBOE BZX", "BZX",
                          "CBOE BZX EXCHANGE"})
_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


def clean_domain(website: object) -> str | None:
    """A website as the search gives it -> its bare hostname, or None (cleanDomain's rules in api/logo-domain.ts)."""
    if not isinstance(website, str):
        return None
    s = website.strip().lower()
    if not s or re.search(r"[\s@#\\]", s):
        return None
    s = re.sub(r"^https?://", "", s)
    s = re.split(r"[/?]", s)[0]
    s = re.sub(r":\d{1,5}$", "", s)
    s = re.sub(r"^www\.", "", s)
    if len(s) > 253 or ":" in s:
        return None
    labels = s.split(".")
    if len(labels) < 2 or not all(_LABEL.match(lb) for lb in labels):
        return None
    if not re.fullmatch(r"[a-z]{2,63}", labels[-1]):  # a real TLD: no IP addresses, no "c0m"
        return None
    if any(lb.startswith("xn--") and len(lb) < 6 for lb in labels):
        return None
    return s


def pick_domain(symbol: str, results: object) -> str | None:
    """The domain of the first result whose symbol is exactly `symbol` and whose exchange is a US one; else None.

    Stricter than pickWebsite in apps/web (which falls back to an exact symbol on another exchange): the file only
    takes a domain we are sure of, and a miss is still looked up at runtime by /api/logo-domain.
    """
    if not isinstance(results, list):
        return None
    for r in results:
        if not isinstance(r, dict) or r.get("symbol") != symbol:
            continue
        ex = r.get("exchange")
        if not isinstance(ex, str) or ex.strip().upper() not in US_EXCHANGES:
            continue
        d = clean_domain(r.get("website"))
        if d:
            return d
    return None


def search_logo(symbol: str, *, opener=None, budget: Budget | None = None) -> object:
    """AllInvestView's answer for one ticker (public, no key), under its own budget in config/sources.yaml."""
    return get_json(SEARCH_URL, source=SEARCH_SOURCE, params={"q": symbol}, headers={"Accept": "application/json"},
                    timeout=15, attempts=3, max_bytes=1_000_000, opener=opener, budget=budget)


def fill_domains(out: dict[str, dict], search: Callable[[str], object] = search_logo,
                 sleep: Callable[[float], None] = time.sleep) -> dict[str, str]:
    """Look up every symbol whose `domain` is null and fill it when the pick is sure. Returns what was filled.

    A domain already set is never asked for or replaced. A failed or odd answer keeps null and the run goes on.
    """
    found: dict[str, str] = {}
    for n, sym in enumerate(s for s in sorted(out) if not out[s].get("domain")):
        if n:
            sleep(SEARCH_GAP_S)
        try:
            ans = search(sym)
        except HttpError as e:
            print(f"{sym}: search failed ({e}); domain stays null", file=sys.stderr)
            continue
        d = pick_domain(sym, ans.get("results") if isinstance(ans, dict) else None)
        if d:
            out[sym]["domain"] = found[sym] = d
        else:
            print(f"{sym}: no exact US match; domain stays null", file=sys.stderr)
    return found


def render(out: dict[str, dict]) -> str:
    return json.dumps(dict(sorted(out.items())), indent=1, ensure_ascii=False) + "\n"


def merge_existing(fresh: dict[str, dict], existing: dict[str, dict]) -> dict[str, dict]:
    """The fresh Finnhub rows, plus every field of the file's row that this script does not produce.

    `domain` (the company website for the ticker-logo CDN, #330) is entered by hand or by apps/web/scripts/
    logo_domains.mjs, so a rewrite must not drop it (#331). A symbol new to the file gets `domain: null`, which is what
    logo_domains.mjs fills in and what the dashboard's tests expect on every row. Which symbols are listed is still
    decided by the universe: a symbol no longer in it leaves the file, as before.
    """
    out: dict[str, dict] = {}
    for sym, row in fresh.items():
        old = existing.get(sym) or {}
        kept = {k: v for k, v in old.items() if k not in OWN_FIELDS}
        out[sym] = {**{k: row.get(k) for k in OWN_FIELDS}, "domain": kept.pop("domain", None), **kept}
    return out


def read_existing(dest: Path) -> dict[str, dict]:
    """The file as it stands; {} on a first run. A file that cannot be read stops the run: it is never overwritten."""
    if not dest.exists():
        return {}
    try:
        existing = json.loads(dest.read_text())
    except json.JSONDecodeError as e:
        raise ValueError(f"{dest} is not valid JSON; fix it by hand before rewriting it ({e})") from e
    if not isinstance(existing, dict) or not all(isinstance(v, dict) for v in existing.values()):
        raise ValueError(f"{dest} is not a symbol -> fields map; fix it by hand before rewriting it")
    return existing


def write_symbols(dest: Path, fresh: dict[str, dict], search: Callable[[str], object] | None = None,
                  sleep: Callable[[float], None] = time.sleep) -> dict[str, dict]:
    """Merge with the file, optionally fill missing domains with `search`, then write once."""
    out = merge_existing(fresh, read_existing(dest))
    if search is not None:
        fill_domains(out, search, sleep)
    dest.write_text(render(out))
    return out


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=None)
    ap.add_argument("--fill-domains", action="store_true",
                    help="look up missing `domain`s in AllInvestView's search (exact ticker, US exchange only)")
    return ap.parse_args(argv)


def main() -> None:
    a = parse_args()
    if a.env:
        for line in Path(a.env).expanduser().read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))
    key = os.environ["FINNHUB_API_KEY"]
    dest = ROOT / "apps" / "web" / "src" / "data" / "symbols.json"
    read_existing(dest)  # a broken file stops the run here, before a minute of Finnhub calls
    cfg = yaml.safe_load((ROOT / "config" / "universe_seed.yaml").read_text())
    syms = sorted({s for v in cfg["sectors"].values() for s in v})
    out: dict[str, dict] = {e: {"name": n, "logo": None, "industry": "ETF"} for e, n in ETF_NAMES.items()}
    for s in syms:
        url = "https://finnhub.io/api/v1/stock/profile2?" + urllib.parse.urlencode({"symbol": s, "token": key})
        with urllib.request.urlopen(url, timeout=20) as r:
            p = json.loads(r.read() or b"{}")
        out[s] = {"name": p.get("name") or s, "logo": p.get("logo") or None, "industry": p.get("finnhubIndustry")}
        time.sleep(1.1)  # free tier: 60 calls/minute
    out = write_symbols(dest, out, search_logo if a.fill_domains else None)
    print(f"{len(out)} symbols, {sum(1 for v in out.values() if v['logo'])} with logos, "
          f"{sum(1 for v in out.values() if v['domain'])} with a domain -> {dest}")


if __name__ == "__main__":
    main()
