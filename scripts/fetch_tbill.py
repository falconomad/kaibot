"""The 3-month Treasury-bill rate from FRED, cached for the evaluation reports (M11 ticket 2.4, issue #455).

python scripts/fetch_tbill.py --env ~/Projects/kaibot-secrets/.env     fetch DTB3 since 2015-06-01 (one request)
python scripts/fetch_tbill.py --status                                   what is cached (offline)

Writes <data>/inputs/fred_dtb3.json (kaibot.evaluation.tbill). One request to FRED under the `fred` budget of
config/sources.yaml (free; the key is FRED_API_KEY in the .env, the same key the engine uses for VIX). Research
input only: the engine does not read it and nothing here trades.

The reports turn the file into each session's cash return with `kaibot.evaluation.mtm.tbill_daily`: session t
gets the last rate dated before t (the rate is published after its day's close), so a re-run with a newer file
gives the same numbers for the sessions both files cover.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kaibot.evaluation import tbill  # noqa: E402

URL = "https://api.stlouisfed.org/fred/series/observations"
START = "2015-06-01"  # as the VIX cache: before the first session any ten-year run uses


def load_env(path: str) -> dict[str, str]:
    env = {}
    for line in Path(path).expanduser().read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    return env


def status(data: Path) -> dict:
    f = data / tbill.FILE
    if not f.is_file():
        return {"file": str(f), "cached": False}
    s = tbill.read_cache(f)
    return {"file": str(f), "cached": True, "fetched_at": json.loads(f.read_text()).get("fetched_at"),
            "observations": int(s.size), "first": str(s.index[0].date()), "last": str(s.index[-1].date()),
            "last_rate_pct": float(s.iloc[-1])}


def main(argv: list[str] | None = None, fetch=None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env", help="the .env with FRED_API_KEY")
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--start", default=START)
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args(argv)
    data = Path(a.data)
    if not a.status:
        if fetch is None:
            if not a.env:
                sys.exit("needs --env <file with FRED_API_KEY> (or --status)")
            key = load_env(a.env).get("FRED_API_KEY", "")
            if not key:
                sys.exit(f"no FRED_API_KEY in {a.env}")
            from kaibot.common import http
            from kaibot.ingest.certs import ensure_ca_bundle

            ensure_ca_bundle()  # python.org's macOS Python has no root certificates

            def fetch() -> list[dict]:
                params = {"series_id": tbill.SERIES, "observation_start": a.start, "file_type": "json",
                          "api_key": key}
                return http.get_json(URL, source="fred", params=params, timeout=30)["observations"]

        try:
            rows = fetch()
        except Exception as e:  # no traceback: the request's URL holds the key (common.http redacts its own)
            from kaibot.common.http import HttpError

            sys.exit(f"FAILED: {e if isinstance(e, HttpError) else type(e).__name__}; nothing was written")
        tbill.write_cache(data / tbill.FILE, rows)
    out = status(data)
    print(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    main()
