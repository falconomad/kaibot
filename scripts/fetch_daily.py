"""Download the daily bar store (story S84, #124): RAW daily bars from 2016 plus splits and dividends. Resumable.

    ./scripts/fetch_daily.sh                  the one command: universe, former members, corporate actions, checks
    python scripts/fetch_daily.py             SPY, the sector ETFs and every stock of config/universe_history.yaml
    python scripts/fetch_daily.py --symbols-file data/universe/former_members.csv
                                              ... plus every line `symbol,asof_date` of that file (story S83): the
                                              ticker is requested as of that date, so a retired or reused ticker
                                              returns the company that had it then; stored as SYMBOL@DATE
    python scripts/fetch_daily.py --symbols NBIS,CRWV    SPY plus exactly these instead of the universe
    python scripts/fetch_daily.py --status    what is stored
    python scripts/fetch_daily.py --check     offline checks: minute history against daily bars on a sample, and
                                              the corporate-actions table against Alpaca's adjusted bars

A key that is already complete is skipped, so stopping and starting again costs nothing. Per key: one request for
the raw bars and one for Alpaca's own adjusted series (used only by the check; --no-reference skips it). The
corporate actions of every stored key are fetched again on every run (a few dozen requests).
Writes only under <data>/daily, which the live engine does not read. Data: Alpaca SIP history >= 15 min old under
the shared `alpaca` budget (config/sources.yaml). Never prints key values.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from fetch_history import DEFAULT_ENV, load_env, select_symbols  # noqa: E402

from kaibot.common.http import HttpError  # noqa: E402
from kaibot.ingest import daily_bars as db  # noqa: E402
from kaibot.ingest.alpaca_history import ET, Alpaca  # noqa: E402
from kaibot.ingest.certs import CERT_HINT, ensure_ca_bundle  # noqa: E402

REFUSED_IN_A_ROW = 3  # this many refusals before anything was stored means the request is wrong, not the ticker


def build_requests(a: argparse.Namespace, today: date) -> list[db.Request]:
    """Everything the command line asks for, checked before any key is read or any request is sent."""
    try:
        start = date.fromisoformat(a.start)
    except ValueError:
        raise SystemExit(f"--start: expected YYYY-MM-DD, got {a.start!r}") from None
    if start >= today:
        raise SystemExit(f"--start {a.start} is not in the past")
    reqs: dict[str, db.Request] = {}
    if not a.no_universe:
        cfg = yaml.safe_load(Path(a.universe).read_text())
        for s in select_symbols(cfg, a.symbols):
            reqs.setdefault(s, db.Request(s))
    elif a.symbols:
        raise SystemExit("--symbols and --no-universe do not go together")
    for f in a.symbols_file or []:
        if not Path(f).is_file():
            raise SystemExit(f"--symbols-file: no file {f}")
        try:
            for r in db.read_symbols_file(f, start, today):
                reqs.setdefault(r.key, r)
        except ValueError as e:
            raise SystemExit(f"--symbols-file: {e}") from None
    if not reqs:
        raise SystemExit("nothing to download (--no-universe without --symbols-file)")
    return list(reqs.values())


def print_status(root: Path, reqs: list[db.Request] | None = None) -> None:
    st = db.store_status(root)
    done = ""
    if reqs is not None:
        have = set(db.store_keys(root))
        holes = {p.name[len("symbol="):-len(".empty")] for p in (root / "bars").glob("symbol=*.empty")}
        n = sum(r.key in have or r.key in holes for r in reqs)
        done = f"{n}/{len(reqs)} requested keys answered; "
    print(f"Daily bar store {root}: {done}{st.keys} keys with bars, {st.rows:,} sessions {st.first}..{st.last}, "
          f"{st.references} adjusted references, {st.holes} holes (holes.csv), {st.bytes / 1e6:.1f} MB")
    ca = st.actions
    if ca:
        kinds = ", ".join(f"{k} {n:,}" for k, n in sorted((ca.get("by_type") or {}).items()))
        print(f"Corporate actions: {ca.get('rows', 0):,} rows ({kinds}); ex-dates {ca.get('first_ex_date')}.."
              f"{ca.get('last_ex_date')}; {ca.get('duplicates', 0)} flagged as repeats; fetched "
              f"{ca.get('fetched_at')} ({ca.get('requests')} requests, {ca.get('mode')})")
    else:
        print("Corporate actions: not fetched yet (prices cannot be adjusted without them)")


def main(argv: list[str] | None = None, api=None, now: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--env", default=os.environ.get("KAIBOT_ENV_FILE") or DEFAULT_ENV, help=".env with the Alpaca keys")
    ap.add_argument("--data", default="data")
    ap.add_argument("--start", default=db.START.isoformat())
    ap.add_argument("--universe", default=str(ROOT / "config" / "universe_history.yaml"))
    ap.add_argument("--symbols", default="", help="comma-separated tickers: SPY + exactly these, not the universe")
    ap.add_argument("--symbols-file", action="append", help="CSV with the header symbol,asof_date: each ticker is "
                                                             "requested as of that date (may be given more than once)")
    ap.add_argument("--no-universe", action="store_true", help="download only the --symbols-file lines")
    ap.add_argument("--per-min", type=int, default=60, help="request budget for this run")
    ap.add_argument("--no-reference", action="store_true", help="skip Alpaca's adjusted series (no adjustment check)")
    ap.add_argument("--no-actions", action="store_true", help="skip the corporate actions")
    ap.add_argument("--refresh", action="store_true", help="download every key again")
    ap.add_argument("--retry-holes", action="store_true", help="ask again for keys that returned nothing")
    ap.add_argument("--status", action="store_true", help="show what is stored and exit")
    ap.add_argument("--check", action="store_true", help="run the offline checks and exit")
    ap.add_argument("--sample", default="", help="--check: compare exactly these symbols with the minute history")
    a = ap.parse_args(argv)

    now = now or datetime.now(UTC)
    root = Path(a.data) / db.STORE
    if a.check:
        from kaibot.ingest.daily_check import run_check
        if not db.store_keys(root):
            print(f"No daily bars under {root}: run ./scripts/fetch_daily.sh first")
            return 1
        run_check(Path(a.data), [s.strip().upper() for s in a.sample.split(",") if s.strip()] or None)
        print(f"Details: {root / 'check'}")
        return 0
    reqs = build_requests(a, now.astimezone(ET).date())
    if a.status:
        print_status(root, reqs)
        return 0
    if a.per_min <= 0:
        raise SystemExit("--per-min must be positive")

    if api is None:
        env_path = Path(a.env).expanduser()
        if not env_path.is_file():
            print(f"No .env found at {env_path} (set KAIBOT_ENV_FILE=/path/to/.env or pass --env)")
            return 2
        env = load_env(env_path)
        key, secret = env.get("ALPACA_API_KEY"), env.get("ALPACA_API_SECRET") or env.get("ALPACA_SECRET")
        if not key or not secret:
            print("ALPACA_API_KEY and ALPACA_API_SECRET (or ALPACA_SECRET) must be set in the .env")
            return 2
        ensure_ca_bundle()
        api = Alpaca(key, secret, per_min=a.per_min)

    start = date.fromisoformat(a.start)
    through = db.complete_through(now)
    n_asof = sum(r.asof is not None for r in reqs)
    print(f"Daily bars for {len(reqs)} keys ({n_asof} as of a date), {a.start} -> {through} (complete sessions "
          f"only), raw prices, into {root}. Finished keys are skipped.", flush=True)
    counts = {"fetched": 0, "fresh": 0, "kept": 0, "hole": 0, "hole_known": 0}
    refused: list[str] = []  # keys Alpaca answered with 400 / 404 / 422 in this run
    for i, r in enumerate(reqs, 1):
        try:
            out = db.fetch_key(api, r, root, start, now, reference=not a.no_reference, refresh=a.refresh,
                               retry_holes=a.retry_holes)
        except HttpError as err:
            db.write_holes(root)
            print(f"Alpaca request failed at {r.key}: HTTP {err.status}. Stopped; run the same command again to "
                  f"resume." + (f" {CERT_HINT}" if err.status == 0 else ""), flush=True)
            return 1
        except (ValueError, KeyError, TypeError) as err:  # an answer that is not daily bars in Alpaca's shape
            db.write_holes(root)
            print(f"Alpaca's answer for {r.key} could not be read ({type(err).__name__}: {err}). Stopped; nothing "
                  "was stored for it. Tell the Data team.", flush=True)
            return 1
        counts[out.status] += 1
        if out.status == "fetched":
            print(f"[{i}/{len(reqs)}] {r.key}: {out.rows} sessions {out.first}..{out.last}"
                  + (" + adjusted reference" if out.reference else ""), flush=True)
        elif out.status == "kept":
            print(f"[{i}/{len(reqs)}] {r.key}: Alpaca returned nothing usable now ({out.reason}); the stored bars "
                  f"{out.first}..{out.last} are kept", flush=True)
        elif out.status == "hole":
            print(f"[{i}/{len(reqs)}] {r.key}: no usable bars ({out.reason}"
                  + (f"; Alpaca returned {out.first}..{out.last}, not the company of {r.asof}" if out.first else "")
                  + ")", flush=True)
            refused += [r.key] if out.reason.startswith("http_") else []
            plain = [k for k in refused if "@" not in k]  # today's tickers: a retired one may well be unknown
            if len(plain) >= REFUSED_IN_A_ROW and not counts["fetched"] and not counts["fresh"]:
                # an unknown ticker is refused like this, but so is a request Alpaca does not accept at all
                for key in refused:
                    db.bars_path(root, key).with_suffix(".empty").unlink(missing_ok=True)
                db.write_holes(root)
                print(f"Alpaca refused the first {len(plain)} requests ({out.reason}). That is not a list of "
                      "unknown tickers: the request itself is not accepted. Stopped; nothing was marked as a hole. "
                      "Tell the Data team.", flush=True)
                return 1
    holes = db.write_holes(root)
    print(f"Bars: {counts['fetched']} downloaded, {counts['fresh']} already complete, {counts['kept']} kept as "
          f"stored, {counts['hole']} new holes ({holes} keys without usable bars are listed in "
          f"{db.holes_path(root)}).", flush=True)
    if len(refused) > max(5, len(reqs) // 10):
        print(f"WARNING: Alpaca refused {len(refused)} of {len(reqs)} keys as invalid. If these are not all "
              "retired tickers, tell the Data team (holes.csv lists them; --retry-holes asks again).", flush=True)

    if not a.no_actions:
        if not db.store_keys(root):
            print("No key has bars, so there are no corporate actions to fetch.")
            return 1
        try:
            info = db.sync_actions(api, root, start, now, log=lambda s: print(s, flush=True))
        except HttpError as err:
            print(f"Corporate actions failed: HTTP {err.status}. The bars are stored, but prices cannot be adjusted "
                  "until this works. Run the same command again."
                  + (" (403: this key's plan does not serve /v1/corporate-actions; tell the Data team.)"
                     if err.status == 403 else "") + (f" {CERT_HINT}" if err.status == 0 else ""), flush=True)
            return 1
        except ValueError as err:
            print(f"Corporate actions failed: {err}. The bars are stored. Tell the Data team.", flush=True)
            return 1
        if info["unusable_rows"]:
            print(f"corporate actions: {info['unusable_rows']} rows had no usable symbol, date or rate and were "
                  "left out", flush=True)
    print_status(root, reqs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
