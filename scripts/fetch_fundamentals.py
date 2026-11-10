"""Download SEC XBRL companyfacts for the seed universe (needs SEC_USER_AGENT; <= 5 req/s).

python scripts/fetch_fundamentals.py --env .env
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from fetch_history import load_env  # noqa: E402


def get(url: str, ua: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True)
    ap.add_argument("--data", default=str(ROOT / "data"))
    a = ap.parse_args()
    ua = load_env(a.env)["SEC_USER_AGENT"]
    cfg = yaml.safe_load((ROOT / "config" / "universe_seed.yaml").read_text())
    syms = [s for v in cfg["sectors"].values() for s in v]
    tickers = json.loads(get("https://www.sec.gov/files/company_tickers.json", ua))
    cik = {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in tickers.values()}
    out = Path(a.data) / "fundamentals" / "companyfacts"
    out.mkdir(parents=True, exist_ok=True)
    for s in syms:
        f = out / f"{s}.json"
        if f.exists():
            continue
        c = cik.get(s.replace(".", "-"))
        if not c:
            print("no CIK for", s)
            continue
        f.write_bytes(get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{c}.json", ua))
        print(s, c, f.stat().st_size // 1024, "KB", flush=True)
        time.sleep(0.25)


if __name__ == "__main__":
    main()
