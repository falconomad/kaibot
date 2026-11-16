"""Does Alpaca's corporate-actions feed list an action under the symbol the #271 buy guard asks for? (#266, QA-Ops)

The guard (engine/corp_guard.py) asks GET /v1/corporate-actions with `symbols=<the stock it wants to buy>` and reads
a spin-off from `source_symbol` (the parent), a merger from `acquiree_symbol` (the company being bought) and a symbol
change from `old_symbol`. Nobody has checked that Alpaca files those rows under that symbol. This asks the feed,
read-only, for past cases whose facts are verified (sources in CASES), first exactly as the guard does (filter by the
parent / acquired / old symbol), then by the other party, and prints where each row was found.

    ./scripts/check_ca_feed.sh            on the Mac (reads the .env with the engine's loader)

Read-only: GET on Alpaca's market-data API only (https://data.alpaca.markets); no order, no trading API call. The
keys are read with the engine's own loader (kaibot.engine.config.load_env_file) and never printed.
Exit 0 when every case is seen the way the guard reads it, 3 when one is not, 2 when the keys are missing or the
base URL is not the paper one, 1 when the feed could not be read.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CA_PATH = "/v1/corporate-actions"

# The guard's reading of each list (engine/corp_guard.TYPES on #271): response list -> the fields that name the
# stock the guard protects. Used when corp_guard is not importable (before #271 merges); otherwise its own TYPES.
GUARD_FIELDS = {
    "forward_splits": ("symbol",), "reverse_splits": ("symbol",), "unit_splits": ("old_symbol", "symbol"),
    "spin_offs": ("source_symbol", "symbol"), "cash_mergers": ("acquiree_symbol",),
    "stock_mergers": ("acquiree_symbol",), "stock_and_cash_mergers": ("acquiree_symbol",),
    "name_changes": ("old_symbol",),
}
GUARD_TYPES = ("forward_split", "reverse_split", "unit_split", "spin_off", "cash_merger", "stock_merger",
               "stock_and_cash_merger", "name_change")


@dataclass(frozen=True)
class Case:
    label: str
    kind: str  # spin_off | cash_merger | name_change
    guard_symbol: str  # what the guard filters by: the parent, the company being acquired, the old symbol
    other: str  # the other party: the new company, the acquirer, the new symbol ("" when not known)
    on: date
    source: str


CASES = (
    Case("HON spin-off of Solstice", "spin_off", "HON", "SOLS", date(2025, 10, 30),
         "Honeywell / Solstice Advanced Materials separation; daily store: HON spin_off step 2025-10-30"),
    Case("DD spin-off of Qnity", "spin_off", "DD", "Q", date(2025, 11, 3),
         "https://www.sec.gov/Archives/edgar/data/2058873/000119312525240313/d21160dex991.htm"),
    Case("HON spin-off of Honeywell Aerospace (with a 1-for-2 reverse split)", "spin_off", "HON", "", date(2026, 6, 29),
         "https://www.honeywell.com/us/en/press/2026/06/honeywell-board-of-directors-sets-record-date-and-announces-"
         "expected-timing-for-spin-off-of-honeywell-aerospace-and-honeywell-reverse-stock-split"),
    Case("CTVA separation", "spin_off", "CTVA", "", date(2026, 10, 1),
         "daily store: CTVA spin_off step 2026-10-01 (Alpaca close factor 0.150)"),
    Case("JNPR cash merger into HPE ($40.00 a share)", "cash_merger", "JNPR", "HPE", date(2025, 7, 2),
         "HPE / Juniper completion, 2 Jul 2025 (all cash, $40.00 per share)"),
    Case("CHK renamed EXE (Expand Energy)", "name_change", "CHK", "EXE", date(2024, 10, 2),
         "https://www.sec.gov/Archives/edgar/data/895126/000110465924104976/tm2425151d1_8k.htm"),
)
SLACK = timedelta(days=10)  # the feed's ex / effective / process dates can sit a few days off the event


def guard_fields() -> tuple[dict[str, tuple[str, ...]], tuple[str, ...], str]:
    """The guard's own field map when #271 is on this checkout, else the copy above."""
    try:
        from kaibot.engine import corp_guard as cg

        return {v[0]: v[2] for v in cg.TYPES.values()}, tuple(cg.TYPES), "engine/corp_guard.py"
    except ImportError:
        return dict(GUARD_FIELDS), GUARD_TYPES, "the #271 field map (corp_guard.py not on this checkout)"


def ask(get: Callable[[str, dict], dict], symbol: str, on: date, types: tuple[str, ...]) -> dict[str, list[dict]]:
    """Every row the feed returns for `symbol` around `on` (all pages), by response list."""
    out: dict[str, list[dict]] = {}
    token = None
    for _ in range(10):
        p = {"symbols": symbol, "types": ",".join(types), "start": str(on - SLACK), "end": str(on + SLACK),
             "limit": 1000}
        if token:
            p["page_token"] = token
        d = get(CA_PATH, p)
        if not isinstance(d, dict) or "corporate_actions" not in d:
            raise ValueError("the answer is not Alpaca's corporate-actions shape")
        for name, rows in (d["corporate_actions"] or {}).items():
            if isinstance(rows, list):
                out.setdefault(name, []).extend(r for r in rows if isinstance(r, dict))
        token = d.get("next_page_token")
        if not token:
            return out
    raise ValueError("more than 10 pages")


def where(rows: dict[str, list[dict]], symbol: str) -> list[str]:
    """'list.field' for every field of every row whose value is `symbol`."""
    return sorted({f"{name}.{k}" for name, rs in rows.items() for r in rs for k, v in r.items()
                   if isinstance(v, str) and v.upper() == symbol})


WANT = {"spin_off": ("spin_offs",), "cash_merger": ("cash_mergers", "stock_and_cash_mergers"),
        "name_change": ("name_changes",)}


def check(case: Case, get, fields, types) -> tuple[bool, list[str]]:
    """(the guard sees it, the lines to print)."""
    rows = ask(get, case.guard_symbol, case.on, types)
    seen = [f"{name}.{f}" for name in WANT[case.kind] for r in rows.get(name, []) for f in fields.get(name, ())
            if str(r.get(f) or "").upper() == case.guard_symbol]
    lines = [f"{case.label} ({case.on}, guard asks symbols={case.guard_symbol}):"]
    n = sum(len(v) for v in rows.values())
    lines.append(f"  asked by {case.guard_symbol}: {n} row(s)"
                 + (f" in {', '.join(f'{k} {len(v)}' for k, v in sorted(rows.items()) if v)}" if n else "")
                 + (f"; {case.guard_symbol} appears as {', '.join(where(rows, case.guard_symbol))}"
                    if where(rows, case.guard_symbol) else ""))
    if case.other:
        alt = ask(get, case.other, case.on, types)
        m = sum(len(v) for v in alt.values())
        hits = where(alt, case.guard_symbol) + [f"{x} ({case.other})" for x in where(alt, case.other)]
        lines.append(f"  asked by {case.other}: {m} row(s)" + (f"; found as {', '.join(hits)}" if hits else ""))
    lines.append(f"  guard sees it: {'yes, as ' + ', '.join(sorted(set(seen))) if seen else 'NO'}")
    return bool(seen), lines


def main(argv: list[str] | None = None, get: Callable[[str, dict], dict] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env", default=os.environ.get("KAIBOT_ENV_FILE"), help=".env with the Alpaca keys")
    a = ap.parse_args(argv)
    if get is None:
        from kaibot.engine.alpaca import AlpacaData
        from kaibot.engine.config import PAPER_TRADING_URL, load_env_file

        load_env_file(a.env)  # the engine's loader: fills os.environ, never prints
        key = os.environ.get("ALPACA_API_KEY")
        secret = os.environ.get("ALPACA_SECRET") or os.environ.get("ALPACA_API_SECRET")
        if not key or not secret:
            print("ALPACA_API_KEY and ALPACA_SECRET must be set (in the .env)")
            return 2
        if os.environ.get("ALPACA_BASE_URL", PAPER_TRADING_URL).rstrip("/") != PAPER_TRADING_URL:
            print("Kaibot is paper-only: ALPACA_BASE_URL must be the paper endpoint")
            return 2
        get = AlpacaData(key, secret)._d  # GET https://data.alpaca.markets/... only
    fields, types, origin = guard_fields()
    print(f"Alpaca corporate-actions feed, read-only, against the #271 guard's reading ({origin}).")
    seen: dict[str, list[bool]] = {}
    for c in CASES:
        try:
            ok, lines = check(c, get, fields, types)
        except Exception as e:  # noqa: BLE001  the message is the redacted HTTP error, never a key
            print(f"{c.label}: the feed could not be read ({type(e).__name__}: {str(e)[:160]})")
            return 1
        print("\n".join(lines))
        seen.setdefault(c.kind, []).append(ok)
    names = {"spin_off": "spin-offs by parent", "cash_merger": "cash mergers by the acquired company",
             "name_change": "symbol changes by the old symbol"}
    print()
    for kind, oks in seen.items():
        print(f"guard sees {names[kind]}: {'yes' if all(oks) else 'no'} ({sum(oks)} of {len(oks)} cases)")
    return 0 if all(all(v) for v in seen.values()) else 3


if __name__ == "__main__":
    raise SystemExit(main())
