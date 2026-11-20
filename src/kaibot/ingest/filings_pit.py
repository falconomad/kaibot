"""Point-in-time reader and coverage report of every 8-K filing with its items (story S100, #374).

Research data only: the live engine does not read it (tests/unit/test_filings_8k.py holds the isolation test).

The table (data/filings/filings_8k.parquet, built by `./scripts/fetch_earnings_dates.sh --all-items`) has one row
per 8-K and 8-K/A since 2016 of every company that was in the S&P 500 since 2016, with the SEC acceptance time to
the second. `Filings.filings(symbol, as_of)` never returns a filing accepted after `as_of`: nobody could read a
filing before EDGAR accepted it, so the acceptance time is the time a decision could first have known it.

In research `accepted_ts` is the `available_ts` of a row. A live rule would need a poller with its own storage time
(design for M8, section 2: "when Kaibot's poller stored it"); that is not built here.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

import pandas as pd

from kaibot.ingest import nyse_sessions as nyse
from kaibot.ingest.earnings_pit import Announcements
from kaibot.ingest.earnings_sec import FILINGS_COLUMNS

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "data" / "filings"
TABLE = "filings_8k.parquet"
NO_ITEM = "(none)"  # the label of filings whose `items` is empty, in the coverage report


def split_items(items: object) -> list[str]:
    """"2.02, 9.01" -> ["2.02", "9.01"]. `items` is kept as filed in the table; this is how to read it."""
    return [i.strip() for i in str(items or "").split(",") if i.strip()]


def has_item(items: pd.Series, item: str) -> pd.Series:
    """True where the filing lists exactly this item ("2.02" does not match "12.02")."""
    return items.map(lambda s: item in split_items(s)).astype(bool)


class Filings:
    """`filings(symbol, as_of)`: the company's 8-K and 8-K/A filings accepted at or before `as_of`, oldest first.

    `symbol` may be a company id of sp500/changes.csv, a bar-store key (SYMBOL@ASOF) or a ticker as it was on the
    `as_of` day, exactly as in `earnings_pit.Announcements`, whose company lookup this class uses."""

    def __init__(self, table: pd.DataFrame, membership=None):
        self._known = Announcements(table, None, membership)
        self.table = self._known.table

    @classmethod
    def load(cls, data_dir: Path = DATA, with_membership: bool = True) -> Filings:
        m = None
        if with_membership:
            from kaibot.ingest.sp500_pit import Membership

            m = Membership.load()
        return cls(pd.read_parquet(Path(data_dir) / TABLE), m)

    def company(self, symbol: str, as_of) -> str | None:
        return self._known.company(symbol, as_of)

    def filings(self, symbol: str, as_of, items: Iterable[str] | None = None,
                forms: Iterable[str] | None = None) -> pd.DataFrame:
        """Rows with `accepted_ts <= as_of` (a timezone-aware decision time). `items`: keep filings that list at
        least one of these items; `forms`: keep these forms ("8-K", "8-K/A")."""
        k = self._known.known(symbol, as_of)
        if forms is not None:
            k = k[k["form"].isin(set(forms))]
        if items is not None:
            want = set(items)
            k = k[k["items"].map(lambda s: bool(want & set(split_items(s)))).astype(bool)]
        return k[[c for c in FILINGS_COLUMNS if c in k.columns]]


# ---------------------------------------------------------------- coverage (numbers only)


def _outside_regular_hours(accepted: pd.Series) -> pd.Series:
    """True where the acceptance time is not inside a regular NYSE session (09:30 to the close, 13:00 on
    early-close days): the filing could not be traded on before the next open."""
    et = accepted.dt.tz_convert(nyse.ET)
    mins = et.dt.hour * 60 + et.dt.minute
    day = pd.Series(et.dt.date, index=accepted.index)
    close = day.map({d: (13 * 60 if nyse.early_close(d) else 16 * 60) if nyse.is_session(d) else -1
                     for d in day.unique()})
    return ~((mins >= 9 * 60 + 30) & (mins < close))


def coverage(table: pd.DataFrame, mapped: Sequence[str] | None = None) -> dict:
    """Filings per year, per form, per item and per item and year (years are New York dates of acceptance), the
    mapped companies with no filing at all, and the share of acceptance times outside regular hours. A filing
    with three items counts once under each of them, so the item counts add up to more than the rows."""
    have = set(table["company"]) if len(table) else set()
    out: dict = {"rows": int(len(table)), "companies_with_rows": len(have),
                 "companies_with_none": sorted(set(mapped or ()) - have), "per_year": {}, "per_form": {},
                 "per_item": {}, "per_item_year": {}, "outside_regular_hours_rows": 0,
                 "outside_regular_hours_share": None}
    if not len(table):
        return out
    ts = pd.to_datetime(table["accepted_ts"], utc=True)
    year = ts.dt.tz_convert(nyse.ET).dt.year
    out["per_year"] = {int(k): int(v) for k, v in year.value_counts().sort_index().items()}
    out["per_form"] = {str(k): int(v) for k, v in table["form"].value_counts().sort_index().items()}
    ex = pd.DataFrame({"year": year.to_numpy(), "item": table["items"].map(lambda s: split_items(s) or [NO_ITEM])})
    ex = ex.explode("item")
    out["per_item"] = {str(k): int(v) for k, v in ex["item"].value_counts().sort_index().items()}
    for (item, y), n in ex.groupby(["item", "year"]).size().items():
        out["per_item_year"].setdefault(str(item), {})[int(y)] = int(n)
    outside = _outside_regular_hours(ts)
    out["outside_regular_hours_rows"] = int(outside.sum())
    out["outside_regular_hours_share"] = round(float(outside.mean()), 4)
    return out


def compare_item_202(table: pd.DataFrame, announcements: pd.DataFrame, item: str = "2.02") -> dict:
    """The 8-K rows with Item 2.02 against the S85 table (data/earnings/announcements.parquet), by accession.

    The two stores are fetched at different times, so `equal_up_to_cutoff` compares only filings accepted up to
    the newest filing in the announcements table; filings after it are new, not a difference. `value_mismatches`
    counts shared filings whose columns differ."""
    f = table[(table["form"] == "8-K") & has_item(table["items"], item)]
    a_ts = pd.to_datetime(announcements["accepted_ts"], utc=True)
    cutoff = a_ts.max() if len(announcements) else None
    a, b = set(announcements["accession"]), set(f["accession"])
    f_ts = pd.to_datetime(f["accepted_ts"], utc=True)
    b_cut = set(f.loc[f_ts <= cutoff, "accession"]) if cutoff is not None else set()
    shared = sorted(a & b)
    cols = [c for c in FILINGS_COLUMNS if c in announcements.columns]
    left = f.drop_duplicates("accession").set_index("accession").loc[shared, [c for c in cols if c != "accession"]]
    right = announcements.drop_duplicates("accession").set_index("accession").loc[shared, left.columns]
    left = left.assign(accepted_ts=pd.to_datetime(left["accepted_ts"], utc=True))
    right = right.assign(accepted_ts=pd.to_datetime(right["accepted_ts"], utc=True))
    mismatches = int((left.astype(str) != right.astype(str)).any(axis=1).sum()) if shared else 0
    return {"item": item, "announcements_rows": int(len(announcements)), "item_202_rows": int(len(f)),
            "item_202_rows_up_to_cutoff": len(b_cut), "cutoff": None if cutoff is None else cutoff.isoformat(),
            "n_only_in_announcements": len(a - b), "only_in_announcements": sorted(a - b)[:50],
            "n_only_in_filings": len(b - a), "n_only_in_filings_up_to_cutoff": len(b_cut - a),
            "only_in_filings_up_to_cutoff": sorted(b_cut - a)[:50], "value_mismatches": mismatches,
            "equal_up_to_cutoff": a == b_cut and mismatches == 0, "equal": a == b and mismatches == 0}


def markdown(cov: dict) -> str:
    """The item-by-year table as Markdown, ready to paste into the issue."""
    years = sorted({y for d in cov["per_item_year"].values() for y in d})
    lines = ["| item | " + " | ".join(str(y) for y in years) + " | all |",
             "|---|" + "---:|" * (len(years) + 1)]
    for item in sorted(cov["per_item_year"]):
        d = cov["per_item_year"][item]
        lines.append(f"| {item} | " + " | ".join(str(d.get(y, 0)) for y in years) + f" | {cov['per_item'][item]} |")
    lines.append("| **filings** | " + " | ".join(str(cov["per_year"].get(y, 0)) for y in years)
                 + f" | {cov['rows']} |")
    share = cov["outside_regular_hours_share"]
    tail = [f"Filings: {cov['rows']} for {cov['companies_with_rows']} companies; by form: {cov['per_form']}.",
            "A filing counts once under each of its items.",
            f"Accepted outside regular hours: {cov['outside_regular_hours_rows']}"
            + (f" ({share:.1%})." if share is not None else "."),
            f"Mapped companies with no 8-K at all: {len(cov['companies_with_none'])}"
            + (f" ({', '.join(cov['companies_with_none'][:40])})." if cov["companies_with_none"] else ".")]
    return "\n".join(lines) + "\n\n" + "\n".join(tail) + "\n"
