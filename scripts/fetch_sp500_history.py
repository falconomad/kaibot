"""Point-in-time S&P 500 membership (story S83, issue #123): build the former-member list, the hole log and the report.

    python scripts/fetch_sp500_history.py              offline, seconds: writes data/universe/former_members.csv
                                                       (`symbol,asof_date`, read by scripts/fetch_daily.sh of #124)
                                                       and data/universe/spells.csv from the committed change log
    python scripts/fetch_sp500_history.py --holes      after ./scripts/fetch_daily.sh: data/universe/holes.csv, every
                                                       membership spell without bars in data/daily (offline)
    python scripts/fetch_sp500_history.py --report     members, additions and removals per year (markdown)
    python scripts/fetch_sp500_history.py --crosscheck primary_doc.xml ...   compare with IVV's N-PORT holdings
                                                       (SEC EDGAR files; names only: see docs/ops/pit_universe.md)
    python scripts/fetch_sp500_history.py --update     Data team only: download the upstream file (5.5 MB from
                                                       raw.githubusercontent.com), add supplement.csv, rewrite
                                                       src/kaibot/ingest/sp500/changes.csv

Research data only; nothing here is read by the live engine. Method and limits: docs/ops/pit_universe.md.
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kaibot.ingest import sp500_pit as pit  # noqa: E402

OUT = ROOT / "data" / "universe"


def update(source: str) -> int:
    if source.startswith("http"):
        with urllib.request.urlopen(source, timeout=60) as r:  # noqa: S310 (fixed https URL)
            text = r.read().decode("utf-8")
    else:
        text = Path(source).read_text()
    snaps = pit.parse_source(text)
    log, skipped = pit.merge_supplement(pit.change_log(snaps), pit.read_supplement())
    pit.write_changes(log, pit.CHANGES)
    print(f"{pit.CHANGES.relative_to(ROOT)}: base list {log[0].date} ({len(log[0].added)} tickers), "
          f"{len(log) - 1} changes to {log[-1].date}; upstream ends {snaps[-1][0]}")
    for ch in skipped:
        print(f"  supplement row {ch.date} is covered by the upstream file now: check and delete it")
    return 0


def build(m: pit.Membership, out: Path) -> int:
    rows = m.former_members()
    n = pit.write_symbols_file(rows, out / "former_members.csv")
    pit.write_spells(m, out / "spells.csv")
    cur = m.current()
    kinds = Counter("left the index" if m.spells[c][-1].end else
                    ("288 name, renamed" if m.universe_ticker(c) else "current member outside the 288")
                    for c in m.spells if m.needs_request(c))
    print(f"{len(m.spells)} companies were in the S&P 500 between {m.first} and {m.today} ({len(cur)} today).")
    print(f"{out / 'former_members.csv'}: {n} lines (" + ", ".join(f"{v} {k}" for k, v in sorted(kinds.items())) + ")")
    print("Next: ./scripts/fetch_daily.sh  (reads that file; see docs/ops/pit_universe.md)")
    return 0


def holes(m: pit.Membership, store: Path, out: Path) -> int:
    if not (store / "bars").is_dir():
        print(f"No bar store at {store}. Run ./scripts/fetch_daily.sh first.")
        return 1
    rows = pit.hole_log(m, store)
    pit.write_hole_log(rows, out / "holes.csv")
    by = Counter(r["kind"] for r in rows)
    lost = sum(int(r["member_days_lost"]) for r in rows)
    total = sum(((sp.last_day or m.today) - max(sp.start, pit.START)).days + 1 for sps in m.spells.values()
                for sp in sps)
    print(f"{out / 'holes.csv'}: {len(rows)} spells with holes (" + ", ".join(f"{v} {k}" for k, v in by.items())
          + f"); {lost:,} of {total:,} company-days of membership ({lost / total:.1%}) have no bars.")
    ends = [r for r in rows if r["kind"] == "ends_early"]
    if ends:
        print(f"{len(ends)} spells end early: a position held then is closed at its last bar at the terminal return "
              f"({sum(r['terminal_return'] == pit.DEFAULT_TERMINAL_RETURN for r in ends)} at the default "
              f"{pit.DEFAULT_TERMINAL_RETURN:.0%}).")
    return 0


def report(m: pit.Membership) -> int:
    print("| Year | Companies on 1 Jan | On 31 Dec | Distinct in the year | Added | Removed |")
    print("|---|---|---|---|---|---|")
    for r in m.yearly():
        print(f"| {r['year']} | {r['start']} | {r['end']} | {r['distinct']} | {r['added']} | {r['removed']} |")
    uni = list(m.universe)
    first = m.companies_on(m.first)
    in_index = {u for c in m.spells if (u := m.universe_ticker(c))}
    late = sorted(u for u in uni if u in in_index and u not in {m.universe_ticker(c) for c in first})
    never = sorted(u for u in uni if u not in in_index)
    print(f"\nOf today's {len(uni)} research names: {len(late) + len(never)} were not members on {m.first}; "
          f"{len(late)} joined later ({', '.join(late)}); {len(never)} were never members ({', '.join(never)}).")
    left = sorted(c for c in m.spells if m.spells[c][-1].end)
    print(f"{len(m.spells)} distinct companies; {len(left)} have left the index; {len(m.former_members())} "
          f"lines in former_members.csv.")
    return 0


def crosscheck(m: pit.Membership, files: list[str], asof: str) -> int:
    bad = 0
    for f in files:
        if f.endswith(".xml"):
            d, names = pit.read_nport(Path(f))
        else:  # one issuer name per line (e.g. parsed from an N-Q), with --asof
            d, names = date.fromisoformat(asof), [n for n in Path(f).read_text().splitlines() if n.strip()]
        only_list, only_fund = pit.crosscheck(m, d, names)
        bad += bool(only_list or only_fund)
        print(f"{d}: list {len(m.members_on(d))} tickers, fund {len(names)} stock lines; only in the list: "
              f"{', '.join(only_list) or 'none'}; only in the fund: {'; '.join(only_fund) or 'none'}")
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--update", nargs="?", const=pit.SOURCE_URL, metavar="URL_OR_PATH",
                    help="rebuild the committed change log from the upstream file (Data team)")
    ap.add_argument("--holes", action="store_true", help="write data/universe/holes.csv from the daily bar store")
    ap.add_argument("--store", default=str(ROOT / "data" / "daily"), help="daily bar store of #124 (with --holes)")
    ap.add_argument("--report", action="store_true", help="print the per-year table")
    ap.add_argument("--crosscheck", nargs="+", metavar="FILE", help="N-PORT primary_doc.xml or a names file")
    ap.add_argument("--asof", default="", help="date of a names file (with --crosscheck)")
    ap.add_argument("--out", default=str(OUT), help="output folder (default data/universe)")
    ap.add_argument("--today", default="", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.update:
        return update(a.update)
    m = pit.Membership.load(today=date.fromisoformat(a.today) if a.today else None)
    out = Path(a.out)
    if a.report:
        return report(m)
    if a.crosscheck:
        return crosscheck(m, a.crosscheck, a.asof)
    if a.holes:
        return holes(m, Path(a.store), out)
    return build(m, out)


if __name__ == "__main__":
    raise SystemExit(main())
