"""Company name + logo URL for every universe symbol, from Finnhub's free /stock/profile2.

Writes apps/web/src/data/symbols.json (public data only, no key). The dashboard loads each logo straight from
Finnhub's static image host and falls back to initials when there is none (ETFs usually have none).
Only `name`, `logo` and `industry` are this script's: any other field already in the file (`domain`, #330) is kept.
Run again when the universe changes:  python scripts/fetch_symbol_meta.py --env ~/Projects/kaibot-secrets/.env
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ETF_NAMES = {"SPY": "S&P 500 ETF", "XLK": "Technology ETF", "XLF": "Financials ETF", "XLV": "Health Care ETF",
             "XLY": "Consumer Discretionary ETF", "XLP": "Consumer Staples ETF", "XLE": "Energy ETF",
             "XLI": "Industrials ETF", "XLU": "Utilities ETF", "XLRE": "Real Estate ETF", "XLC": "Communication ETF",
             "XLB": "Materials ETF"}


OWN_FIELDS = ("name", "logo", "industry")  # what this script produces; every other field belongs to someone else


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


def write_symbols(dest: Path, fresh: dict[str, dict]) -> dict[str, dict]:
    out = merge_existing(fresh, read_existing(dest))
    dest.write_text(render(out))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=None)
    a = ap.parse_args()
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
    out = write_symbols(dest, out)
    print(f"{len(out)} symbols, {sum(1 for v in out.values() if v['logo'])} with logos, "
          f"{sum(1 for v in out.values() if v['domain'])} with a domain -> {dest}")


if __name__ == "__main__":
    main()
