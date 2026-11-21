"""scripts/news_coverage.py (#372, the Data tasks of S98): store coverage and the pre-declared former-member probe."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("news_coverage", ROOT / "scripts" / "news_coverage.py")
N = importlib.util.module_from_spec(spec)
spec.loader.exec_module(N)


def _news(root: Path, sym: str, times: list[datetime]) -> None:
    p = root / "news" / f"symbol={sym}" / "all.parquet"
    p.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"created_at": pa.array(times, pa.timestamp("s", tz="UTC")),
                             "headline": ["h"] * len(times), "source": ["benzinga"] * len(times),
                             "symbols": [sym] * len(times)}), p)


def test_counts_per_symbol_and_month_and_the_report(tmp_path):
    _news(tmp_path, "AAPL", [datetime(2025, 1, 3, tzinfo=UTC), datetime(2025, 1, 9, tzinfo=UTC),
                             datetime(2026, 2, 1, tzinfo=UTC)])
    c = N.headline_counts(tmp_path / "news")
    assert c == {"AAPL": {"2025-01": 2, "2026-02": 1}}
    assert N.by_year(c) == {"AAPL": {2025: 2, 2026: 1}}
    assert N.main(["--data", str(tmp_path)]) == 0
    md = (tmp_path / "reports" / "news_coverage.md").read_text()
    assert "| AAPL | 2 | 1 |" in md and "survivorship" in md and "Alpaca news for five" not in md


def test_bar_months_keep_only_universe_stocks(tmp_path):
    for sym, mon in (("AAPL", "2025-01"), ("XLK", "2025-01")):
        p = tmp_path / f"symbol={sym}" / f"{mon}.parquet"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")
    assert N.bar_months(tmp_path, {"AAPL"}) == {"AAPL": {"2025-01"}}


def test_the_probe_picks_five_former_members_by_rule_before_asking():
    from kaibot.ingest.sp500_pit import Membership

    ms = Membership.load()
    a, b = N.probe_choice(ms), N.probe_choice(ms)
    assert a == b and len(a) == 5
    assert [d.year for _, _, d in a] == list(N.PROBE_YEARS)
    assert all(ms.spells[c][-1].end is not None for c, _, _ in a)
