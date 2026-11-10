from pathlib import Path

from kaibot.ingest.edgar import cik_to_ticker, parse_current_feed

FIX = Path(__file__).parent.parent / "fixtures" / "edgar_current_8k.xml"


def test_parse_current_feed():
    [f] = parse_current_feed(FIX.read_text())
    assert f.form == "8-K" and f.cik == "0000123456" and f.company == "ACME CORP"
    assert f.accession == "0000123456-26-000001"
    assert f.accepted.utcoffset().total_seconds() == -5 * 3600


def test_cik_map():
    assert cik_to_ticker({"0": {"cik_str": 320193, "ticker": "AAPL"}}) == {"0000320193": "AAPL"}
