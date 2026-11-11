"""Company name + logo URL for every universe symbol, from Finnhub's free /stock/profile2.

Writes apps/web/src/data/symbols.json (public data only, no key). The dashboard loads each logo straight from
Finnhub's static image host and falls back to initials when there is none (ETFs usually have none).
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
    cfg = yaml.safe_load((ROOT / "config" / "universe_seed.yaml").read_text())
    syms = sorted({s for v in cfg["sectors"].values() for s in v})
    out: dict[str, dict] = {e: {"name": n, "logo": None, "industry": "ETF"} for e, n in ETF_NAMES.items()}
    for s in syms:
        url = "https://finnhub.io/api/v1/stock/profile2?" + urllib.parse.urlencode({"symbol": s, "token": key})
        with urllib.request.urlopen(url, timeout=20) as r:
            p = json.loads(r.read() or b"{}")
        out[s] = {"name": p.get("name") or s, "logo": p.get("logo") or None, "industry": p.get("finnhubIndustry")}
        time.sleep(1.1)  # free tier: 60 calls/minute
    dest = ROOT / "apps" / "web" / "src" / "data" / "symbols.json"
    dest.write_text(json.dumps(dict(sorted(out.items())), indent=1, ensure_ascii=False) + "\n")
    print(f"{len(out)} symbols, {sum(1 for v in out.values() if v['logo'])} with logos -> {dest}")


if __name__ == "__main__":
    main()
