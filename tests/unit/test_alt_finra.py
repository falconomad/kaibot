from datetime import UTC, date, datetime

import pytest
from alt_helpers import FIX, FakeNet

from kaibot.ingest.alt import finra, store

DAY = date(2026, 11, 9)
NIGHT = datetime(2026, 11, 9, 23, 45, tzinfo=UTC)  # 18:45 ET
BODY = (FIX / "finra_cnms_20261109.txt").read_bytes()


def test_parse_cnms_skips_bad_rows_and_checks_header():
    rows = finra.parse_cnms(BODY.decode(), DAY)
    assert [r[0] for r in rows] == ["A", "AAPL", "BRK.B", "MSFT", "NVDA", "SPY", "ZVZZT"]
    assert rows[1] == ("AAPL", 9876543, 45678, 21345678, "B,Q,N")
    with pytest.raises(finra.BadFile):
        finra.parse_cnms("Date|Symbol|Short|Total\n", DAY)
    with pytest.raises(finra.BadFile):  # mostly garbage -> reject the whole file
        finra.parse_cnms("|".join(finra.HEADER) + "\n" + "junk\n" * 50, DAY)


def test_rows_from_another_date_are_rejected():
    text = "|".join(finra.HEADER) + "\n20261106|AAPL|1|0|2|Q\n20261109|MSFT|1|0|2|Q\n2\n"
    assert [r[0] for r in finra.parse_cnms(text, DAY)] == ["MSFT"]


def test_collect_is_idempotent_and_point_in_time(tmp_path):
    net = FakeNet([("CNMSshvol20261109.txt", BODY)])
    assert finra.collect(tmp_path, DAY, now=NIGHT, **net.kw) == 7
    assert finra.collect(tmp_path, DAY, now=NIGHT, **net.kw) == 0  # same file again: nothing new
    assert net.calls[0][0] == "https://cdn.finra.org/equity/regsho/daily/CNMSshvol20261109.txt"
    df = store.read_partition(tmp_path, finra.SOURCE, DAY)
    assert len(df) == 7 and (df["available_ts"] == finra.publication_ts(DAY)).all()
    assert str(finra.publication_ts(DAY)) == "2026-11-09 23:00:00+00:00"  # 18:00 ET


def test_reposted_file_adds_only_changed_rows_as_new_versions(tmp_path):
    net = FakeNet([("CNMSshvol", BODY)])
    finra.collect(tmp_path, DAY, now=NIGHT, **net.kw)
    updated = BODY.replace(b"20261109|AAPL|9876543", b"20261109|AAPL|9999999")
    lm = "Tue, 10 Nov 2026 14:00:00 GMT"
    net2 = FakeNet([("CNMSshvol", updated, {"Last-Modified": lm})])
    later = datetime(2026, 11, 10, 23, 45, tzinfo=UTC)
    assert finra.collect(tmp_path, DAY, now=later, **net2.kw) == 1
    before = store.read_pit(tmp_path, finra.SOURCE, datetime(2026, 11, 10, 13, tzinfo=UTC), finra.KEY)
    after = store.read_pit(tmp_path, finra.SOURCE, datetime(2026, 11, 10, 15, tzinfo=UTC), finra.KEY)
    aapl = lambda df: int(df.loc[df.symbol == "AAPL", "short_volume"].iloc[0])  # noqa: E731
    assert aapl(before) == 9876543 and aapl(after) == 9999999 and len(after) == 7


def test_not_published_yet_and_missing_files(tmp_path):
    net = FakeNet([("CNMSshvol", BODY)])
    assert finra.collect(tmp_path, DAY, now=datetime(2026, 11, 9, 21, tzinfo=UTC), **net.kw) == 0
    assert net.calls == []  # before 18:00 ET: no request at all
    missing = FakeNet([("CNMSshvol", 403)])  # the CDN answers 403 for a file that does not exist
    assert finra.collect(tmp_path, date(2026, 11, 8), now=NIGHT, **missing.kw) == 0


def test_oversized_file_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(finra, "MAX_BYTES", 100)
    net = FakeNet([("CNMSshvol", BODY)])
    with pytest.raises(Exception, match="bytes"):
        finra.collect(tmp_path, DAY, now=NIGHT, **net.kw)


def test_default_days_are_recent_weekdays_after_publication():
    days = finra.default_days(datetime(2026, 11, 10, 22, tzinfo=UTC), lookback=3)  # Tue 17:00 ET
    assert days == [date(2026, 11, 5), date(2026, 11, 6), date(2026, 11, 9)]
