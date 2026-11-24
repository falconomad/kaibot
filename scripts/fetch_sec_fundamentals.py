"""SEC EDGAR XBRL fundamentals for every company in the S&P 500 since 2016, every filed version kept (research store).

  PYTHONPATH=src python3 scripts/fetch_sec_fundamentals.py --env ~/Projects/kaibot-secrets/.env   fetch + build
  PYTHONPATH=src python3 scripts/fetch_sec_fundamentals.py --env ... --refresh                    re-download all
  PYTHONPATH=src python3 scripts/fetch_sec_fundamentals.py --build                                offline rebuild
  PYTHONPATH=src python3 scripts/fetch_sec_fundamentals.py --status                               what is stored

Needs KAIBOT_SEC_USER_AGENT ("Name email@domain", SEC fair access), from the environment or the --env file; refuses
to run without it and never prints it. One request per company to data.sec.gov (companyfacts), paced by the shared
`sec_edgar` budget (5 req/s; SEC allows 10), 429/5xx retried with backoff, a 403 stops the run. A company whose raw
file is younger than --max-age-days is not downloaded again. Company -> CIK comes from data/earnings/cik_map.csv
(scripts/fetch_earnings_dates.py) when it exists, else it is resolved here the same way (about 2,000 more requests).

Writes data/fundamentals/: facts/cik=##########.parquet, ciks.parquet, raw/companyfacts/*.json.gz, build.json.
Docs: docs/data/fundamentals.md.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from kaibot.common import http  # noqa: E402
from kaibot.ingest import earnings_sec as sec  # noqa: E402
from kaibot.ingest import sec_fundamentals as sf  # noqa: E402
from kaibot.ingest.sp500_pit import Membership  # noqa: E402

UA_VAR = "KAIBOT_SEC_USER_AGENT"
_UA = re.compile(r"^[^\s@][^@\n]*\s+[^\s@]+@[^\s@]+\.[A-Za-z]{2,}\s*$")


def user_agent(env_file: str | None, environ=None) -> str:
    """KAIBOT_SEC_USER_AGENT from the environment, else from the env file (only that one line is read)."""
    environ = os.environ if environ is None else environ
    ua = (environ.get(UA_VAR) or "").strip()
    if not ua and env_file and Path(env_file).is_file():
        for line in Path(env_file).read_text().splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip().removeprefix("export ").strip() == UA_VAR:
                ua = v.split(" #")[0].strip().strip('"').strip("'")
    if not _UA.match(ua):
        raise SystemExit(f"{UA_VAR} is missing or has no contact email. SEC fair access requires a declared "
                         f'User-Agent such as "Your Name you@example.com": add {UA_VAR}="..." to the env file '
                         "(or the environment) and run again. Nothing was downloaded.")
    return ua


def getter(ua: str):
    headers = {"User-Agent": ua, "Accept-Encoding": "gzip", "Accept": "application/json"}
    counter = {"n": 0}

    def get(url: str, ok=(404,)) -> tuple[int, bytes]:
        counter["n"] += 1
        r = http.get(url, source=sf.BUDGET, headers=headers, timeout=120, max_bytes=sf.MAX_BYTES, ok_statuses=ok)
        body = r.body
        if r.headers.get("content-encoding", "").lower() == "gzip":
            body = sf.gunzip_bounded(body)
        return r.status, body

    get.counter = counter  # type: ignore[attr-defined]
    return get


def mapping(data: Path, m: Membership, ua: str) -> tuple[list, str]:
    src = data / "earnings" / "cik_map.csv"
    if src.is_file():
        rows = list(csv.DictReader(src.open()))
        return [sec.Mapping_(r["company"], r["cik"], r["how"], r["verified"] == "1", r["note"]) for r in rows], str(src)
    import fetch_earnings_dates as fed  # same resolver as the earnings store (scripts/ is on sys.path)

    root = data / "fundamentals" / "sec"
    root.mkdir(parents=True, exist_ok=True)
    get = sec.sec_getter(ua)
    cache = sec.Cache(root / "submissions", get)
    rows = fed._resolve(root, m, get, cache, False, time.monotonic())
    sec.write_mapping(rows, data / "fundamentals" / "cik_map.csv")
    return rows, "resolved here (data/fundamentals/cik_map.csv)"


def fetch(data: Path, ua: str, refresh: bool, max_age_days: float, limit: int | None) -> dict:
    out = data / "fundamentals"
    t0 = time.monotonic()
    m = Membership.load()
    rows, mapping_from = mapping(data, m, ua)
    table = sf.ticker_spells(m, rows)
    sf.write_ciks(out, table)
    ciks = sf.ciks_of(table)[:limit] if limit else sf.ciks_of(table)
    print(f"{len(m.spells)} companies since 2016, {len(ciks)} with a CIK; companyfacts at 5 requests/s…", flush=True)
    get = getter(ua)
    reports, misses, errors, new_versions, skipped = {}, [], {}, 0, 0
    for i, cik in enumerate(ciks, 1):
        raw = out / "raw" / "companyfacts" / f"CIK{cik}.json.gz"
        fresh = raw.is_file() and (time.time() - raw.stat().st_mtime) < max_age_days * 86400
        try:
            if fresh and not refresh:
                skipped += 1
                body, seen = sf.read_raw(raw), datetime.fromtimestamp(raw.stat().st_mtime, UTC)
            else:
                status, body = get(sf.URL.format(cik=cik))
                if status == 404:
                    misses.append(cik)
                    continue
                sf.write_raw(out, cik, body)
                seen = datetime.now(UTC)
            t, rep = sf.parse_companyfacts(json.loads(body), cik, seen)
            _, added = sf.merge_into_store(out, cik, t)
            reports[cik], new_versions = rep, new_versions + added
        except http.HttpError as e:
            if e.status == 403:
                raise SystemExit("SEC answered 403 (User-Agent refused or rate-limited): stopped so as not to "
                                 "hammer it. Check KAIBOT_SEC_USER_AGENT and try again later.") from None
            errors[cik] = str(e)[:200]
        except (ValueError, OSError) as e:
            errors[cik] = f"{type(e).__name__}: {str(e)[:200]}"
        if i % 50 == 0:
            print(f"  {i}/{len(ciks)} companies, {get.counter['n']} requests, {time.monotonic() - t0:.0f} s",
                  flush=True)
    meta = {"built_at": datetime.now(UTC).isoformat(timespec="seconds"), "mapping_from": mapping_from,
            "companies": len(m.spells), "ciks": len(ciks), "requests_this_run": get.counter["n"],
            "cached_not_downloaded": skipped, "new_versions": new_versions, "errors": errors,
            "seconds": round(time.monotonic() - t0), **sf.summary(reports, misses)}
    (out / "build.json").write_text(json.dumps(meta, indent=1, default=str))
    return meta


def build(data: Path) -> dict:
    out = data / "fundamentals"
    reports, errors = {}, {}
    for raw in sorted((out / "raw" / "companyfacts").glob("CIK*.json.gz")):
        cik = raw.name[3:13]
        try:
            t, rep = sf.parse_companyfacts(json.loads(sf.read_raw(raw)), cik,
                                           datetime.fromtimestamp(raw.stat().st_mtime, UTC))
            sf.merge_into_store(out, cik, t)
            reports[cik] = rep
        except (ValueError, OSError) as e:
            errors[cik] = f"{type(e).__name__}: {str(e)[:200]}"
    meta = {"built_at": datetime.now(UTC).isoformat(timespec="seconds"), "offline": True, "errors": errors,
            **sf.summary(reports, [])}
    (out / "build.json").write_text(json.dumps(meta, indent=1, default=str))
    return meta


def status(data: Path) -> dict:
    from kaibot.data.fundamentals import Fundamentals

    f = Fundamentals(data / "fundamentals")
    df = f.facts
    return {"rows": len(df), "companies": int(df["cik"].nunique()) if len(df) else 0,
            "first_filed": str(df["filed"].min()) if len(df) else None,
            "last_filed": str(df["filed"].max()) if len(df) else None,
            "rows_by_metric": df["metric"].value_counts().to_dict() if len(df) else {},
            "ticker_rows": len(f.ciks)}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", help="env file holding KAIBOT_SEC_USER_AGENT (only that line is read)")
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--refresh", action="store_true", help="download every company again")
    ap.add_argument("--max-age-days", type=float, default=7.0, help="re-download raw files older than this")
    ap.add_argument("--limit", type=int, help="only the first N CIKs (a trial run)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--build", action="store_true", help="offline: rebuild the parquet from the raw cache")
    g.add_argument("--status", action="store_true", help="offline: what is stored")
    a = ap.parse_args(argv)
    data = Path(a.data)
    if a.status:
        res = status(data)
    elif a.build:
        res = build(data)
    else:
        res = fetch(data, user_agent(a.env), a.refresh, a.max_age_days, a.limit)
    print(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
