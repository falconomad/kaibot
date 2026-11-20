"""New-listing watch (story S102, #376): the nightly shadow collector (Alpaca assets, EDGAR daily index, Nasdaq
Trader), its point-in-time store, the classification, `listing_age_sessions`, the "first eligible for" table, the
IPO history from EDGAR's quarterly indexes, the nightly hook and the isolation rule.

Offline. EVERY fixture under tests/fixtures/listings is HAND-BUILT (see its README.md): no recorded response, no
network. The parsers have not been checked against a live response."""

from __future__ import annotations

import ast
import importlib.util
import json
import random
import urllib.request
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
import yaml
from alt_helpers import FakeClock, FakeNet

from kaibot.common import ratelimit
from kaibot.ingest import listings_pit as lp
from kaibot.ingest import nyse_sessions as nyse
from kaibot.ingest.alt import listings as ls
from kaibot.ingest.alt import store

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "kaibot"
FIX = ROOT / "tests" / "fixtures" / "listings"
D = date
N1 = datetime(2026, 11, 18, 23, 45, tzinfo=UTC)  # 18:45 ET, the evening before the listing
N2, N3 = N1 + timedelta(days=1), N1 + timedelta(days=2)
CREDS = ls.Creds("paper-key", "paper-secret", "Kaibot research ops@example.com")


def fx(name: str) -> bytes:
    return (FIX / name).read_bytes()


def utc(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def net(night: int, **extra) -> FakeNet:
    """The three sources as they would answer on night 1, 2 or 3. Anything not listed answers 404."""
    sym = "" if night == 1 else "_night2"
    routes = [("/v2/assets", fx(f"alpaca_assets_night{night}.json")),
              ("nasdaqlisted.txt", fx(f"nasdaqlisted{sym}.txt")), ("otherlisted.txt", fx(f"otherlisted{sym}.txt")),
              ("rss.aspx", fx("nasdaq_dtn.xml")),
              ("TraderNews.aspx?id=DTN2026-28", fx("nasdaq_notice_DTN2026-28.html")),
              ("CIK0009990001.json", fx("CIK0009990001.json")),
              ("0009990001-26-000004.txt", fx("prospectus_ant.htm")),
              ("/v2/stocks/ANT/bars", fx("alpaca_bars_ANT.json")), ("/v2/stocks/OLD/bars", fx("alpaca_bars_OLD.json")),
              ("/v2/stocks/", fx("alpaca_bars_none.json"))]
    days = {1: ["20261009", "20261102", "20261116"], 2: ["20261118"], 3: ["20261119"]}
    for n in range(1, night + 1):  # an index stays on EDGAR once it is built
        routes += [(f"master.{d}.idx", fx(f"master.{d}.idx")) for d in days[n]]
    return FakeNet([*extra.get("first", []), *routes])


def three_nights(tmp_path) -> list[FakeNet]:
    nets = []
    for i, now in enumerate((N1, N2, N3), 1):
        nets.append(net(i))
        ls.collect(tmp_path, CREDS, now=now, **nets[-1].kw)
    return nets


def table(tmp_path, as_of) -> pd.DataFrame:
    return lp.Listings.load(tmp_path).table(as_of).set_index("symbol")


# ---------------------------------------------------------------- parsing (every input is untrusted)


def test_parse_assets_keeps_us_equities_and_skips_malformed():
    rows = ls.parse_assets(json.loads(fx("alpaca_assets_night1.json")))
    assert [r["symbol"] for r in rows] == ["AAPL", "OLDT", "PRNT", "RNMO", "SPY"]  # no crypto, no broken row
    assert rows[0]["attributes"] == "has_options" and rows[0]["tradable"] is True and rows[0]["exchange"] == "NASDAQ"
    with pytest.raises(ValueError):
        ls.parse_assets({"message": "forbidden"})


def test_parse_master_index_daily_and_quarterly_dates():
    rows = ls.parse_master_index(fx("master.20261116.idx").decode())
    assert [(r["cik"], r["form"], r["filed"]) for r in rows] == [
        ("0009990001", "8-A12B", D(2026, 11, 16)), ("0009990006", "8-A12B", D(2026, 11, 16)),
        ("0009990003", "8-A12B", D(2026, 11, 16))]  # the 8-K and the two broken lines are not kept
    assert rows[0]["company"] == "Anthropic, PBC" and rows[0]["accession"] == "0009990001-26-000003"
    assert rows[0]["path"] == "edgar/data/9990001/0009990001-26-000003.txt"
    q = ls.parse_master_index(fx("master_2026QTR2.idx").decode())
    assert q[0]["filed"] == D(2026, 5, 20) and {r["form"] for r in q} == {"S-1", "S-1/A", "8-A12B", "424B4"}
    with pytest.raises(ValueError):
        ls.parse_master_index("<html>Your Request Originates from an Undeclared Automated Tool</html>")


def test_index_available_ts_is_never_before_the_index_could_exist():
    day = D(2026, 11, 16)
    floor = utc("2026-11-17T03:00:00")  # 22:00 ET: the nightly build starts
    assert ls.index_available_ts(day, utc("2026-11-17T04:10:00"), None) == utc("2026-11-17T04:10:00")  # seen that night
    assert ls.index_available_ts(day, N3, None) == utc("2026-11-17T11:00:00")  # fetched days later: 06:00 ET
    assert ls.index_available_ts(day, N3, utc("2026-11-17T05:20:00")) == utc("2026-11-17T05:20:00")  # Last-Modified
    assert ls.index_available_ts(day, N3, utc("2026-11-16T22:00:00")) == floor  # a header before the build
    assert ls.index_days(N1, set(), 7)[-1] == D(2026, 11, 17)  # the 18th is not built at 18:45 ET
    assert D(2026, 11, 14) not in ls.index_days(N1, set(), 7)  # a Saturday has no index


def test_parse_symdir_reads_columns_by_name():
    rows, made = ls.parse_symdir(fx("nasdaqlisted_night2.txt").decode())
    by = {r["symbol"]: r for r in rows}
    assert set(by) == {"AAPL", "ANT", "MYST", "QQQX", "RGHT", "ZVZZT", "ZXZZT"}
    assert by["QQQX"]["etf"] and by["ZVZZT"]["test"] and not by["ANT"]["etf"] and not by["ANT"]["test"]
    assert by["ANT"]["company"] == "Anthropic, PBC - Class A Common Stock"
    assert made == datetime(2026, 11, 19, 18, 30, tzinfo=nyse.ET).astimezone(UTC)
    other, _ = ls.parse_symdir(fx("otherlisted.txt").decode())
    assert [r["symbol"] for r in other] == ["OLDT", "PRNT", "RNMO", "SPY"] and other[-1]["etf"]
    with pytest.raises(ValueError):
        ls.parse_symdir("<html>blocked</html>")


def test_parse_notices_and_the_listing_sentence():
    items = ls.parse_notices(fx("nasdaq_dtn.xml"))
    assert [i["url"].rsplit("=", 1)[-1] for i in items] == ["DTN2026-29", "DTN2026-28"]  # only listing notices
    assert items[1]["published"] == utc("2026-11-11T19:05:00") and "Anthropic, PBC" in items[1]["title"]
    page = ls.parse_notice_text(ls.strip_tags(fx("nasdaq_notice_DTN2026-28.html").decode()))
    assert page == {"symbol": "ANT", "first_trade_date": D(2026, 11, 19), "kind": "ipo"}
    assert ls.parse_notice_text(items[0]["description"])["symbol"] == "EXBI"
    assert ls.parse_notice_text("Example Corp will list by direct listing on the Nasdaq Global Market.") == {
        "symbol": "", "first_trade_date": None, "kind": "direct_listing"}  # nothing to read: nothing guessed
    assert ls.parse_notice_text("under the ticker symbol ANT on November 31, 2026")["first_trade_date"] is None
    assert ls.notice_company(items[1]["title"]) == "Anthropic, PBC"


def test_prospectus_cover_is_read_only_when_it_is_unambiguous():
    cover = ls.parse_prospectus_cover(fx("prospectus_ant.htm").decode())
    assert cover == {"offer_price": 83.0, "shares_offered": 980_000_000.0, "prospectus_date": D(2026, 11, 18)}
    assert ls.parse_prospectus_cover(fx("prospectus_ambiguous.htm").decode()) == {
        "offer_price": None, "shares_offered": None, "prospectus_date": None}  # two prices, two counts: left empty
    assert ls.parse_prospectus_cover("")["offer_price"] is None


# ---------------------------------------------------------------- the nightly collector


def test_first_night_is_a_baseline_and_reports_no_new_listing_from_alpaca(tmp_path):
    n = net(1)
    assert ls.collect(tmp_path, CREDS, now=N1, **n.kw) > 0
    ev = store.read_source(tmp_path, ls.SOURCE)
    alp = ev[ev["source"] == "alpaca_assets"]
    assert len(alp) == 5 and alp["baseline"].all() and set(alp["kind"]) == {"first_seen"}
    assert (alp["available_ts"] == N1).all()  # Alpaca gives no listing date: the first sighting is Kaibot's date
    assert sorted(p.name for p in (tmp_path / "alt" / "listings").glob("date=*"))[-1] == "date=2026-11-18"
    t = table(tmp_path, N1)
    assert set(t.index) == {"ANT", "EXBI"}  # from the notices and EDGAR: both still to come
    ant = t.loc["ANT"]
    assert (ant["kind"], ant["is_new_listing"], ant["status"]) == ("ipo", True, "expected")
    assert (ant["first_session"], ant["first_session_source"]) == (D(2026, 11, 19), "nasdaq_notice")
    assert ant["cik"] == "0009990001" and ant["known_since"] == utc("2026-10-10T10:00:00")  # the S-1, 06:00 ET
    assert t.loc["EXBI", "first_session"] == D(2026, 11, 24)
    assets = [u for u, _ in n.calls if "/v2/assets" in u]
    assert assets == ["https://paper-api.alpaca.markets/v2/assets?status=active&asset_class=us_equity"]
    assert not any("/v2/stocks/" in u for u, _ in n.calls)  # nothing has traded yet: no bar is asked for


def test_ant_is_in_the_store_with_its_filings_and_the_notice(tmp_path):
    """Acceptance: first session 2026-11-19, the EDGAR filings and Nasdaq's notice of 2026-11-11, each with the
    time Kaibot could first have known it."""
    three_nights(tmp_path)
    L = lp.Listings.load(tmp_path)
    ant = L.listing("ANT", N3)
    assert (ant["kind"], ant["is_new_listing"], ant["status"]) == ("ipo", True, "listed")
    assert (ant["first_session"], ant["first_session_source"]) == (D(2026, 11, 19), "daily_bar")
    assert (ant["company"], ant["cik"], ant["exchange"]) == ("Anthropic, PBC", "0009990001", "Nasdaq")
    assert (ant["offer_price"], ant["shares_offered"]) == (83.0, 980_000_000.0)
    ev = L.evidence("ANT", N3).set_index("key")
    forms = ev[ev["source"] == "edgar_index"].set_index("form")
    assert list(forms.index) == ["S-1", "S-1/A", "8-A12B", "424B4"]
    assert forms.loc["424B4", "accepted_ts"] == utc("2026-11-19T11:30:15")
    assert forms.loc["8-A12B", "available_ts"] == utc("2026-11-17T11:00:00")  # the index of the 16th, 06:00 ET
    assert forms.loc["424B4", "available_ts"] == utc("2026-11-20T11:00:00")
    notice = ev[ev["source"] == "nasdaq_notice"].iloc[0]
    assert (notice["filed"], notice["first_trade_date"]) == (D(2026, 11, 11), D(2026, 11, 19))
    assert notice["available_ts"] == notice["published_ts"] == utc("2026-11-11T19:05:00")
    bar = ev[ev["source"] == "alpaca_bars"].iloc[0]
    assert bar["available_ts"] == utc("2026-11-19T21:15:00")  # that session's close plus 15 minutes
    seen = ev.loc["alpaca:a0000000-0000-4000-8000-000000000010"]
    assert seen["available_ts"] == N2 and not seen["baseline"]
    assert (ev["available_ts"] <= N3).all() and ev["ingested_ts"].isin([N1, N2, N3]).all()


def test_only_ipos_and_direct_listings_count_as_new_listings(tmp_path):
    """Acceptance (on hand-built nights): no fund, unit, warrant, right, preferred share, spin-off or ticker
    change is reported as a new listing."""
    three_nights(tmp_path)
    t = table(tmp_path, N3)
    kinds = t["kind"].to_dict()
    assert kinds == {"ANT": "ipo", "EXBI": "ipo", "PREI": "ipo", "NEWT": "ticker_change", "RNM": "ticker_change",
                     "OLD": "ticker_change", "QQQX": "fund", "SPAQ.U": "unit_warrant_right_preferred",
                     "SPAQ.WS": "unit_warrant_right_preferred", "BNK.PRA": "unit_warrant_right_preferred",
                     "RGHT": "unit_warrant_right_preferred", "SPNC": "spin_off", "MYST": "unknown", "": "spac"}
    assert sorted(t.index[t["is_new_listing"]]) == ["ANT", "EXBI", "PREI"]
    assert t.loc["PREI", "status"] == "pre_listing" and t.loc["PREI", "first_session"] is None  # `ipo` attribute
    assert t.loc["", "company"] == "Spaq Acquisition Corp." and t.loc["", "status"] == "priced"
    assert "FOLLOW ON CORP" not in set(t["company"].str.upper())  # a 424B4 without an 8-A12B is a follow-on
    assert "OTCC" not in t.index  # a symbol that appears over the counter is stored, and is no exchange listing
    ev = store.read_source(tmp_path, ls.SOURCE)
    assert "OTCC" in set(ev["symbol"]) and t.loc["OLD", "known_since"] == N2  # known from the night it was checked
    old = ev[ev["key"] == "bar:OLD"].iloc[0]
    assert (old["kind"], old["first_trade_date"], old["available_ts"]) == ("traded_before", None, N2)
    rep = lp.report(lp.Listings.load(tmp_path), N3, nights=30)
    assert rep["alpaca_new_symbols"] == 12 and rep["reported_as_new_listing"] == ["ANT", "PREI"]  # OTCC not counted
    assert rep["by_kind"]["ticker_change"] == 3 and rep["by_kind"]["unknown"] == 1
    assert rep["nights_collected"] == 3 and rep["nights_asked"] == 30


def test_collect_is_idempotent_and_does_not_ask_twice(tmp_path):
    nets = three_nights(tmp_path)
    again = net(3)
    assert ls.collect(tmp_path, CREDS, now=N3, **again.kw) == 0  # the same night again: nothing new
    urls = [u for u, _ in again.calls]
    assert not any("master." in u for u in urls)  # every index day is in the ledger
    assert not any(u.endswith(".txt") and "edgar/data" in u for u in urls)  # the prospectus was read
    assert not any("TraderNews.aspx" in u for u in urls) and not any("/ANT/bars" in u for u in urls)
    assert sum("CIK0009990001.json" in u for u in urls) == 0
    first = [u for u, _ in nets[0].calls]
    assert sum("master." in u for u in first) == len(ls.index_days(N1, set(), ls.FIRST_RUN_DAYS))
    assert all(h.get("User-agent") == CREDS.sec_user_agent for u, h in nets[0].calls if "sec.gov" in u)
    assert all("Apca-api-key-id" not in h for u, h in nets[0].calls if "alpaca.markets" not in u)  # Alpaca only


def test_first_run_after_the_listing_still_finds_ant(tmp_path):
    """The stakeholder's first run comes after 19 Nov: ANT is then part of Alpaca's baseline list. The notice,
    the filings and the first daily bar still put it in the store with its first session."""
    ls.collect(tmp_path, CREDS, now=N3, **net(3).kw)
    L = lp.Listings.load(tmp_path)
    ant = L.listing("ANT", N3)
    assert (ant["kind"], ant["first_session"], ant["first_session_source"]) == ("ipo", D(2026, 11, 19), "daily_bar")
    assert ant["offer_price"] == 83.0 and set(L.evidence("ANT", N3)["form"]) >= {"S-1", "8-A12B", "424B4"}
    t = table(tmp_path, N3)
    assert sorted(t.index[t["is_new_listing"]]) == ["ANT", "EXBI"]  # no baseline symbol is called new
    assert L.listing("ANT", utc("2026-11-11T19:04:59")) is None  # an S-1 alone is recorded, not reported
    early = L.listing("ANT", utc("2026-11-11T19:05:00"))  # the notice; the filings join it by the company's name
    assert (early["first_session"], early["status"], early["cik"]) == (D(2026, 11, 19), "expected", "0009990001")


def test_a_notice_older_than_the_feed_can_be_named_once(tmp_path):
    url = "https://www.nasdaqtrader.com/TraderNews.aspx?id=DTN2026-28"
    empty = [("rss.aspx", b'<rss version="2.0"><channel><title>t</title></channel></rss>')]
    n = net(3, first=empty)
    ls.collect(tmp_path, CREDS, now=N3, notice_urls=[url, url, "https://example.com/x"], **n.kw)
    ev = store.read_source(tmp_path, ls.SOURCE)
    row = ev[ev["source"] == "nasdaq_notice"].iloc[0]
    assert (row["symbol"], row["company"], row["attributes"]) == ("ANT", "Anthropic, PBC", "ipo")
    assert (row["filed"], row["first_trade_date"]) == (D(2026, 11, 11), D(2026, 11, 19))
    assert row["published_ts"] is pd.NaT and row["available_ts"] == utc("2026-11-12T05:00:00")  # a date only
    assert sum("TraderNews.aspx" in u for u, _ in n.calls) == 1 and not any("example.com" in u for u, _ in n.calls)
    assert lp.Listings.load(tmp_path).listing("ANT", N3)["first_session"] == D(2026, 11, 19)
    again = net(3)  # the next night the feed has it after all: the stored notice is kept, the page is not read again
    assert ls.collect(tmp_path, CREDS, now=N3 + timedelta(days=1), notice_urls=[url], **again.kw) >= 0
    assert not any("DTN2026-28" in u for u, _ in again.calls)


def test_a_value_learned_later_is_a_new_version_from_that_night(tmp_path):
    """The prospectus could not be read on the night its filing was stored. The offer price is added the next
    night as a new version: a decision in between does not see it."""
    down = net(3, first=[("CIK0009990001.json", 500), ("0009990001-26-000004.txt", 500)])
    ls.collect(tmp_path, CREDS, now=N3, **down.kw)  # a company lookup that fails is retried, not an input failure
    n4 = N3 + timedelta(days=3)
    ls.collect(tmp_path, CREDS, now=n4, **net(3).kw)
    L = lp.Listings.load(tmp_path)
    assert L.listing("ANT", n4 - timedelta(seconds=1))["offer_price"] is None
    assert L.listing("ANT", n4)["offer_price"] == 83.0
    ev = store.read_source(tmp_path, ls.SOURCE)
    v = ev[ev["accession"] == "0009990001-26-000004"].sort_values("available_ts")
    assert list(v["available_ts"]) == [utc("2026-11-20T11:00:00"), n4]
    assert v["offer_price"].isna().tolist() == [True, False] and v["accepted_ts"].isna().tolist() == [True, False]


def test_one_failing_input_does_not_stop_the_others(tmp_path):
    bad = net(1, first=[("rss.aspx", b"<html>blocked</html>"), ("nasdaqlisted.txt", 500)])
    with pytest.raises(ls.InputsFailed) as e:
        ls.collect(tmp_path, CREDS, now=N1, **bad.kw)
    assert set(e.value.failed) == {"nasdaq_symdir", "nasdaq_notices"}
    ev = store.read_source(tmp_path, ls.SOURCE)
    assert set(ev["source"]) == {"alpaca_assets", "edgar_index", "sec_submissions", "collector"}  # stored first
    assert ev[ev["source"] == "collector"]["attributes"].iloc[0] == (
        "ok=alpaca_assets,edgar_index,first_bars;failed=nasdaq_symdir,nasdaq_notices")
    ls.collect(tmp_path, CREDS, now=N1, **net(1).kw)  # the next run catches up
    assert {"nasdaq_symdir", "nasdaq_notice"} <= set(store.read_source(tmp_path, ls.SOURCE)["source"])


def test_missing_credentials_skip_only_their_input(tmp_path):
    n = net(1)
    with pytest.raises(ls.InputsFailed) as e:
        ls.collect(tmp_path, ls.Creds("", "", "no contact"), now=N1, **n.kw)
    assert set(e.value.failed) == {"alpaca_assets", "edgar_index", "first_bars"}
    assert not any("alpaca.markets" in u or "sec.gov" in u for u, _ in n.calls)  # nothing sent without them
    assert set(store.read_source(tmp_path, ls.SOURCE)["source"]) == {"nasdaq_symdir", "nasdaq_notice", "collector"}


def test_a_truncated_asset_list_is_refused(tmp_path):
    ls.collect(tmp_path, CREDS, now=N1, **net(1).kw)
    short = net(2, first=[("/v2/assets", json.dumps(json.loads(fx("alpaca_assets_night1.json"))[:1]).encode())])
    with pytest.raises(ls.InputsFailed) as e:
        ls.collect(tmp_path, CREDS, now=N2, **short.kw)
    assert "alpaca_assets" in e.value.failed
    ev = store.read_source(tmp_path, ls.SOURCE)
    assert not (ev[ev["source"] == "alpaca_assets"]["kind"] == "gone").any()  # four symbols did not all vanish


def test_only_the_paper_host_is_ever_asked_for_assets():
    assert ls.ASSETS_URL == "https://paper-api.alpaca.markets/v2/assets"
    assert "api.alpaca.markets/v2/assets" not in Path(ls.__file__).read_text().replace("paper-api.alpaca", "")


# ---------------------------------------------------------------- point in time


def test_point_in_time_invariance(tmp_path):
    """Deleting every input that became available after a decision time never changes what is reported for it,
    and nothing reported is newer than the decision time."""
    three_nights(tmp_path)
    ev = store.read_source(tmp_path, ls.SOURCE)
    full = lp.Listings(ev)
    rng = random.Random(5)
    lo, hi = utc("2026-10-01T00:00:00").timestamp(), utc("2026-11-25T00:00:00").timestamp()
    times = [datetime.fromtimestamp(rng.uniform(lo, hi), UTC) for _ in range(40)]
    stamps = ev["available_ts"].drop_duplicates()
    times += [t.to_pydatetime() + timedelta(seconds=s) for t in stamps for s in (-1, 0, 1)]
    for t in times:
        cut = lp.Listings(ev[ev["available_ts"] <= t])
        a, b = full.table(t), cut.table(t)
        assert a.equals(b), t
        assert (a["known_since"] <= t).all()
        for s in ("ANT", "SPNC", "MYST"):
            assert full.listing_age_sessions(s, t) == cut.listing_age_sessions(s, t)
    live = full.table(utc("2026-11-12T00:00:00"), live=True)  # what this machine had really stored by then
    assert len(live) == 0 and len(full.table(utc("2026-11-12T00:00:00"))) == 1


def test_listing_age_sessions(tmp_path):
    three_nights(tmp_path)
    L = lp.Listings.load(tmp_path)
    assert L.listing_age_sessions("ANT", utc("2026-11-10T00:00:00")) is None  # nothing known yet
    assert L.listing_age_sessions("ANT", N1) == 0  # known, not trading yet
    assert L.listing_age_sessions("ANT", utc("2026-11-19T14:29:59")) == 0  # one second before its first open
    assert L.listing_age_sessions("ANT", utc("2026-11-19T14:30:00")) == 1
    assert L.listing_age_sessions("ANT", N3) == 2
    assert L.listing_age_sessions("ANT", utc("2026-12-10T15:00:00")) == 15  # Thanksgiving is not a session
    assert L.listing_age_sessions("MYST", N3) is None and L.listing_age_sessions("NOPE", N3) is None
    with pytest.raises(ValueError):
        L.listing_age_sessions("ANT", datetime(2026, 11, 20))
    assert lp.listing_age_sessions("ANT", N3, data_root=tmp_path) == 2


def test_history_answers_listing_age_for_older_listings():
    hist = pd.DataFrame([{"symbol": "SPCX", "company": "Space Exploration Technologies Corp", "kind": "ipo",
                          "first_session": D(2026, 6, 12), "available_ts": utc("2026-06-12T20:15:00")},
                         {"symbol": "NOBAR", "company": "No Bar Inc", "kind": "ipo", "first_session": None,
                          "available_ts": utc("2026-06-13T10:00:00")}])
    L = lp.Listings(pd.DataFrame(columns=ls.SCHEMA.names), hist)
    assert L.listing_age_sessions("SPCX", utc("2026-06-12T20:14:59")) is None  # its first bar is not known yet
    assert L.listing_age_sessions("SPCX", utc("2026-06-12T20:15:00")) == 1
    assert L.listing_age_sessions("SPCX", utc("2026-06-16T15:00:00")) == 3
    assert L.listing_age_sessions("NOBAR", N3) is None


# ---------------------------------------------------------------- "first eligible for", per rule


def test_first_eligible_for_ant_matches_section_3_3_of_the_design():
    rows = {r["rule"]: r for r in lp.first_eligible(D(2026, 11, 19))}
    assert rows["orb_stocks_in_play"]["first_eligible"] == D(2026, 12, 10)  # 15th session
    assert rows["news_continuation"]["first_eligible"] == D(2026, 12, 18)  # 21st session
    assert rows["owner_tickers"]["first_eligible"] == D(2027, 2, 17)  # 60th session
    tm = rows["trend_momentum"]
    assert (tm["history_complete"], tm["index_earliest"], tm["first_eligible"]) == (
        D(2027, 4, 15), D(2027, 11, 19), D(2027, 11, 19))  # 100th session; then 12 months of trading for the index
    assert rows["mom_12_1_band"]["first_eligible"] == rows["mom_12_1_band"]["history_complete"] == D(2027, 12, 21)
    for rule in ("post_earnings_week", "news_gap_week"):
        assert rows[rule]["first_eligible"] == D(2027, 11, 19) and rows[rule]["history_complete"] is None
    assert len(nyse.sessions(D(2026, 11, 19), D(2027, 11, 19))) == 252  # the design: "252nd session and 12 months"
    assert [r["rule"] for r in lp.first_eligible(D(2026, 11, 19))] == [r["rule"] for r in lp.RULES]
    assert all(r["needs"] for r in lp.RULES)
    assert lp.first_eligible(D(2024, 2, 29))[3]["index_earliest"] == D(2025, 2, 28)  # a leap day


def test_the_warm_ups_are_the_ones_in_the_rules_own_code():
    """The table is kept by hand in the data package (which may not import the rules); this test fails when a
    rule's own number moves."""
    from kaibot.engine import tickercheck
    from kaibot.strategies import momentum, news_continuation, orb

    need = {r["rule"]: r["sessions"] for r in lp.RULES}
    assert need["trend_momentum"] == momentum.SMA_STOCK == 100
    assert need["mom_12_1_band"] == momentum.MomBand().warmup == 273
    assert need["orb_stocks_in_play"] == orb.DEFAULT.lookback + 1 == 15
    assert need["news_continuation"] == news_continuation.DEFAULT.lookback + 1 == 21
    assert need["owner_tickers"] == tickercheck.MIN_SESSIONS == 60
    assert need["post_earnings_week"] == need["news_gap_week"] == 0
    assert {r["rule"] for r in lp.RULES if r["needs_sp500"]} == {
        "trend_momentum", "mom_12_1_band", "post_earnings_week", "news_gap_week"}


# ---------------------------------------------------------------- history from EDGAR's quarterly indexes


def _hist(first_bar=None) -> pd.DataFrame:
    rows = ls.parse_master_index(fx("master_2026QTR2.idx").decode()) + ls.parse_master_index(
        fx("master_2026QTR4.idx").decode())
    return lp.ipo_history(rows, lp.cik_tickers(json.loads(fx("company_tickers.json"))), first_bar).set_index("company")


def test_history_has_spacex_and_leaves_unknowns_empty():
    h = _hist(lambda symbol, filed: D(2026, 6, 12) if symbol == "SPCX" else None)
    assert list(h.index) == ["Orbit Acquisition Corp II", "Gone Since Inc", "Space Exploration Technologies Corp",
                             "Anthropic, PBC"]  # by prospectus date; the follow-on without an 8-A12B is not in it
    x = h.loc["Space Exploration Technologies Corp"]
    assert (x["symbol"], x["cik"], x["kind"]) == ("SPCX", "0009990002", "ipo")
    assert (x["first_filed"], x["registration_filed"], x["prospectus_filed"]) == (
        D(2026, 5, 20), D(2026, 6, 8), D(2026, 6, 12))
    assert (x["first_session"], x["first_session_source"]) == (D(2026, 6, 12), "daily_bar")
    assert x["available_ts"] == utc("2026-06-12T20:15:00")  # that session's close plus 15 minutes
    assert pd.isna(x["offer_price"]) and pd.isna(x["shares_offered"]) and x["pricing_date"] is None  # never guessed
    ant = h.loc["Anthropic, PBC"]
    assert ant["symbol"] == "ANT" and ant["first_session"] is None and ant["first_session_source"] == ""
    assert ant["available_ts"] == utc("2026-11-20T11:00:00")  # no bar: when the prospectus was in the index
    orbit = h.loc["Orbit Acquisition Corp II"]
    assert (orbit["kind"], orbit["symbol"]) == ("spac", "ORBT")
    assert h.loc["Gone Since Inc", "symbol"] == "" and h.loc["Gone Since Inc", "prospectus_filed"] == D(2026, 4, 22)
    assert list(_hist().columns) == [c for c in lp.HISTORY_COLUMNS if c != "company"]


def _script():
    spec = importlib.util.spec_from_file_location("fetch_listings", ROOT / "scripts" / "fetch_listings.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fetch_listings_is_resumable_and_reads_one_file_a_quarter(tmp_path, monkeypatch):
    script = _script()
    urls: list[str] = []

    def get(url: str) -> bytes:
        urls.append(url)
        name = url.rsplit("/", 1)[-1]
        if name == "company_tickers.json":
            return fx(name)
        y, q = url.split("/full-index/")[1].split("/")[:2]
        p = FIX / f"master_{y}{q}.idx"
        return p.read_bytes() if p.is_file() else b"CIK|Company Name|Form Type|Date Filed|File Name\n"

    monkeypatch.setattr(script, "sec_getter", lambda ua: get)
    data = tmp_path / "listings"
    meta = script.fetch(data, tmp_path / "daily", "Kaibot ops@example.com", D(2016, 1, 1), today=D(2026, 11, 20))
    assert len(urls) == 45 and meta["requests_this_run"] == 45  # 44 quarters and the ticker table
    assert urls[0] == "https://www.sec.gov/Archives/edgar/full-index/2016/QTR1/master.idx"
    assert meta["rows"] == 4 and meta["by_kind"] == {"ipo": 3, "spac": 1} and meta["with_first_session"] == 0
    df = pd.read_parquet(data / "ipos.parquet")
    assert list(df.columns) == lp.HISTORY_COLUMNS and "SPCX" in set(df["symbol"])
    n = len(urls)
    script.fetch(data, tmp_path / "daily", "Kaibot ops@example.com", D(2016, 1, 1), today=D(2026, 11, 20))
    assert [u.rsplit("/", 3)[-3:] for u in urls[n:]] == [["2026", "QTR4", "master.idx"],
                                                         ["www.sec.gov", "files", "company_tickers.json"]]
    assert script.build(data, tmp_path / "daily")["rows"] == 4  # offline, from the extracts
    assert script.quarters(D(2016, 1, 1), D(2026, 11, 20))[-1] == (2026, 4) and len(script.quarters(
        D(2026, 5, 1), D(2026, 11, 20))) == 3


def test_show_and_report_read_local_files_only(tmp_path, monkeypatch, capsys):
    three_nights(tmp_path)
    script = _script()
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("no request is expected"))
    monkeypatch.setattr(script, "datetime", type("T", (datetime,), {"now": classmethod(lambda cls, tz=None: N3)}))
    assert script.main(["--show", "ant", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "first_session          2026-11-19" in out and "424B4" in out and "nasdaq_notice" in out
    assert "2027-11-19  trend_momentum (on paper)" in out and "2026-12-10  orb_stocks_in_play (shadow)" in out
    assert script.main(["--show", "NOPE", "--root", str(tmp_path)]) == 1
    assert script.main(["--report", "--root", str(tmp_path)]) == 0
    assert '"reported_as_new_listing": [' in capsys.readouterr().out
    assert script.main(["--eligible", "2026-11-19"]) == 0
    assert "2027-12-21  mom_12_1_band (off)" in capsys.readouterr().out


def test_fetch_listings_needs_the_declared_user_agent(tmp_path, monkeypatch, capsys):
    script = _script()
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    env = tmp_path / "empty.env"
    env.write_text("# nothing\n")
    assert script.main(["--env", str(env), "--since", "2016-01-01", "--data", str(tmp_path / "l")]) == 1
    assert "SEC_USER_AGENT" in capsys.readouterr().out and not (tmp_path / "l" / "ipos.parquet").exists()


# ---------------------------------------------------------------- the nightly hook, budgets, isolation


def _collect_alt():
    spec = importlib.util.spec_from_file_location("collect_alt", ROOT / "scripts" / "collect_alt.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_collect_alt_runs_the_listings_source(tmp_path, monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(ratelimit, "REGISTRY", ratelimit.Registry(clock=clock))
    n = net(1)
    monkeypatch.setattr(urllib.request, "urlopen", n)
    for k in ("SEC_USER_AGENT", "ALPACA_API_KEY", "ALPACA_SECRET", "ALPACA_API_SECRET"):
        monkeypatch.delenv(k, raising=False)
    env = tmp_path / "secrets.env"
    env.write_text("SEC_USER_AGENT=Kaibot ops@example.com\nALPACA_API_KEY=k\nALPACA_SECRET=s\n")
    cli = _collect_alt()
    assert "listings" in cli.RUNNERS  # so the nightly `--source all` job collects it too
    monkeypatch.setattr(cli, "datetime", type("T", (datetime,), {"now": classmethod(lambda cls, tz=None: N1)}))
    assert cli.main(["--env", str(env), "--source", "listings", "--data", str(tmp_path)]) == 0
    assert list((tmp_path / "alt" / "listings").glob("date=*/part-*.parquet"))
    hosts = {u.split("/")[2] for u, _ in n.calls}
    assert hosts == {"paper-api.alpaca.markets", "www.sec.gov", "data.sec.gov", "www.nasdaqtrader.com"}
    gaps = [s for s in clock.sleeps if s > 0]
    assert gaps and max(gaps) <= 1.0 + 1e-9  # Nasdaq Trader: at most one request a second; SEC: 5 a second


def test_sources_yaml_has_the_three_inputs_with_budgets():
    cfg = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text())
    specs = ratelimit.load_specs()
    assert specs[ls.BUDGET_NASDAQ].min_interval >= 1.0  # at most one request a second
    assert specs[ls.BUDGET_SEC].windows == ((5, 1.0),) and specs[ls.BUDGET_ASSETS].windows == ((150, 60.0),)
    assert "requests count against alpaca_trading" in cfg["market_data"]["alpaca_assets"]["note"]
    assert "requests count against sec_edgar" in cfg["fundamentals_filings"]["sec_listings"]["note"]
    assert "requests count against alpaca" in cfg["market_data"]["alpaca_first_bar"]["note"]


def _imports(path: Path) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            out |= {f"{node.module}.{a.name}" for a in node.names}
    return out


def test_trading_code_never_imports_the_listing_watch():
    """A shadow collector: nothing under engine, risk, strategies, broker, features or replay imports it or
    names its stores."""
    offenders = []
    for pkg in ("engine", "risk", "strategies", "broker", "features", "replay"):
        for f in (SRC / pkg).rglob("*.py"):
            text = f.read_text()
            if (any("listings" in m.split(".") or "listings_pit" in m for m in _imports(f))
                    or "alt/listings" in text or "data/listings" in text or "ipos.parquet" in text
                    or "listing_age_sessions" in text):
                offenders.append(str(f.relative_to(SRC)))
    assert offenders == []
    for mod in (ls, lp):  # and the watch itself imports no trading code
        assert not any(m.startswith(("kaibot.engine", "kaibot.risk", "kaibot.strategies", "kaibot.broker",
                                     "kaibot.features", "kaibot.replay")) for m in _imports(Path(mod.__file__)))
