import json
from datetime import UTC, date, datetime

import pytest
from alt_helpers import FIX, FakeNet

from kaibot.common import safexml
from kaibot.ingest.alt import form4, store

UA = "Kaibot Research ops@example.com"
AAPL = "0000320193"
NOW = datetime(2026, 11, 10, 23, 40, tzinfo=UTC)
SUB = (FIX / "sec_submissions_aapl.json").read_bytes()
F4 = (FIX / "form4_aapl.xml").read_bytes()

DERIV = b"""<?xml version="1.0"?><ownershipDocument><documentType>4/A</documentType>
<issuer><issuerCik>0000320193</issuerCik><issuerTradingSymbol>AAPL</issuerTradingSymbol></issuer>
<reportingOwner><reportingOwnerId><rptOwnerCik>0000000001</rptOwnerCik><rptOwnerName>Doe Jane</rptOwnerName>
</reportingOwnerId><reportingOwnerRelationship><isDirector>1</isDirector></reportingOwnerRelationship></reportingOwner>
<derivativeTable><derivativeTransaction><securityTitle><value>RSU</value></securityTitle>
<transactionDate><value>2026-11-02</value></transactionDate><transactionCoding><transactionCode>M</transactionCode>
</transactionCoding><transactionAmounts><transactionShares><value>1000</value></transactionShares>
<transactionPricePerShare><footnoteId id="F1"/></transactionPricePerShare>
<transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode></transactionAmounts>
</derivativeTransaction><derivativeTransaction><transactionCoding><transactionCode>??</transactionCode>
</transactionCoding></derivativeTransaction></derivativeTable></ownershipDocument>"""


def _routes(sub=SUB, doc=F4):
    return [("submissions/CIK0000320193.json", sub), ("000114036126042149/form4.xml", doc),
            ("000114036126041201/form4.xml", 500)]


def test_parse_real_form4():
    info, rows = form4.parse_form4(F4, AAPL)
    assert info["form"] == "4" and info["owner_name"] == "Newstead Jennifer" and info["is_officer"] is True
    assert info["aff10b5one"] is True
    [r] = rows
    assert (r["code"], r["shares"], r["price"], r["acquired_disposed"]) == ("S", 2399.0, 325.75, "D")
    assert r["transaction_date"] == date(2026, 11, 3) and r["shares_after"] == 29997.0 and not r["derivative"]


def test_parse_derivative_and_skip_bad_rows():
    info, rows = form4.parse_form4(DERIV, AAPL)
    assert info["form"] == "4/A" and info["is_director"] is True
    [r] = rows  # the malformed second transaction is skipped
    assert r["derivative"] and r["code"] == "M" and r["price"] is None


def test_parse_rejects_wrong_issuer_and_dtds():
    with pytest.raises(form4.NotIssuer, match="issuer CIK"):
        form4.parse_form4(F4, "0000789019")
    bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><x>&b;</x>'
    with pytest.raises(safexml.UnsafeXML):
        form4.parse_form4(bomb, AAPL)
    ext = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>'
    with pytest.raises(safexml.UnsafeXML):
        safexml.fromstring(ext)
    with pytest.raises(safexml.UnsafeXML):
        safexml.fromstring("<a>" * 10, max_bytes=5)


def test_submissions_candidates_filter_form_and_dates():
    doc = json.loads(SUB)
    c = form4.candidates_from_submissions(doc, "AAPL", AAPL, date(2026, 11, 1), date(2026, 11, 10))
    assert [x.accession for x in c] == ["0001140361-26-042149"]
    assert c[0].doc == "form4.xml" and c[0].accepted == datetime(2026, 11, 5, 23, 30, 13, tzinfo=UTC)
    with pytest.raises(ValueError):
        form4.candidates_from_submissions(doc, "MSFT", "0000789019", date(2026, 1, 1), date(2026, 12, 1))
    doc["filings"]["recent"]["form"].pop()
    with pytest.raises(ValueError, match="ragged"):
        form4.candidates_from_submissions(doc, "AAPL", AAPL, date(2026, 1, 1), date(2026, 12, 1))


def test_available_ts_follows_the_1730_rule():
    before = datetime(2026, 11, 5, 21, 0, tzinfo=UTC)  # 16:00 ET
    assert form4.available_ts(before) == before
    friday_late = datetime(2026, 11, 6, 23, 30, tzinfo=UTC)  # Fri 18:30 ET -> Mon 00:00 ET
    assert form4.available_ts(friday_late) == datetime(2026, 11, 9, 5, 0, tzinfo=UTC)


def test_collect_writes_once_and_retries_failed_fetches(tmp_path):
    net = FakeNet(_routes())
    n = form4.collect(tmp_path, {"AAPL": AAPL}, UA, date(2026, 10, 20), date(2026, 11, 10), now=NOW, **net.kw)
    assert n == 1
    assert all(h.get("User-agent") == UA for _, h in net.calls)
    ledger = store.read_source(tmp_path, form4.LEDGER)
    assert list(ledger["accession"]) == ["0001140361-26-042149"]  # the 500 one is not recorded: retried next run
    tx = store.read_source(tmp_path, form4.SOURCE)
    assert tx.loc[0, "symbol"] == "AAPL" and tx.loc[0, "available_ts"] == pd_ts("2026-11-06 05:00Z")

    net2 = FakeNet(_routes())
    assert form4.collect(tmp_path, {"AAPL": AAPL}, UA, date(2026, 10, 20), date(2026, 11, 10), now=NOW,
                         **net2.kw) == 0
    fetched = [u for u, _ in net2.calls if u.endswith(".xml")]
    assert fetched == ["https://www.sec.gov/Archives/edgar/data/320193/000114036126041201/form4.xml"] * 5


def test_invalid_document_is_logged_and_not_refetched(tmp_path):
    net = FakeNet(_routes(doc=b"<html>oops</html>"))
    assert form4.collect(tmp_path, {"AAPL": AAPL}, UA, date(2026, 11, 5), date(2026, 11, 5), now=NOW, **net.kw) == 0
    led = store.read_source(tmp_path, form4.LEDGER)
    assert list(led["status"]) == ["invalid"]


def test_filing_where_our_company_is_the_reporting_owner_is_skipped(tmp_path):
    other = F4.replace(b"<issuerCik>0000320193</issuerCik>", b"<issuerCik>0000900422</issuerCik>")
    net = FakeNet(_routes(doc=other))
    assert form4.collect(tmp_path, {"AAPL": AAPL}, UA, date(2026, 11, 5), date(2026, 11, 5), now=NOW, **net.kw) == 0
    assert list(store.read_source(tmp_path, form4.LEDGER)["status"]) == ["not_issuer"]


def test_bad_submissions_payload_does_not_crash(tmp_path):
    net = FakeNet(_routes(sub=b'{"cik": "320193", "filings": {"recent": {"form": "4"}}}'))
    assert form4.collect(tmp_path, {"AAPL": AAPL}, UA, date(2026, 11, 1), date(2026, 11, 10), now=NOW, **net.kw) == 0


def test_feed_mode_reuses_edgar_parser_and_index(tmp_path):
    routes = [("action=getcurrent", (FIX / "edgar_current_form4.xml").read_bytes()),
              ("000093905726000303/index.json", (FIX / "sec_filing_index.json").read_bytes()),
              ("000093905726000303/primary_doc.xml", (FIX / "form4_fsbw.xml").read_bytes())]
    net = FakeNet(routes)
    n = form4.collect(tmp_path, {"FSBW": "0001530249"}, UA, date(2026, 11, 10), date(2026, 11, 10), mode="feed",
                      now=NOW, **net.kw)
    assert n == 1
    tx = store.read_source(tmp_path, form4.SOURCE)
    assert tx.loc[0, "code"] == "J" and tx.loc[0, "owner_name"] == "Nielsen Kelli" and tx.loc[0, "shares"] == 17.2216


def test_user_agent_required():
    for bad in (None, "", "kaibot", "x\nInjected: 1 a@b"):
        with pytest.raises(SystemExit):
            form4.check_user_agent(bad)


def test_ticker_map_and_universe(tmp_path):
    net = FakeNet([("company_tickers.json", (FIX / "sec_company_tickers.json").read_bytes())])
    m = form4.ticker_map(tmp_path, UA, **net.kw)
    assert m["AAPL"] == AAPL and m["BRK-B"]
    assert form4.ticker_map(tmp_path, UA, **net.kw) == m and len(net.calls) == 1  # cached
    assert set(form4.universe_ciks(["AAPL", "BRK.B", "ZZZZ"], m)) == {"AAPL", "BRK.B"}


def pd_ts(s):
    import pandas as pd

    return pd.Timestamp(s)
