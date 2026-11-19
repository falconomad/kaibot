"""scripts/fetch_symbol_meta.py keeps what it does not produce (#331).

The script owns `name`, `logo` and `industry` (Finnhub). `domain` in apps/web/src/data/symbols.json is entered by hand
or by apps/web/scripts/logo_domains.mjs (#330), so a new run must carry it over. No network: only the write step runs.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fetch_symbol_meta", ROOT / "scripts" / "fetch_symbol_meta.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

EXISTING = {
    "AAPL": {"name": "Apple Inc", "logo": "https://old/AAPL.png", "industry": "Tech", "domain": "apple.com"},
    "DIS": {"name": "Walt Disney Co", "logo": None, "industry": "Media", "domain": None},
    "GONE": {"name": "Left the universe", "logo": None, "industry": "Retail", "domain": "gone.com"},
    "SPY": {"name": "S&P 500 ETF", "logo": None, "industry": "ETF", "domain": "ssga.com", "tile": "#fff"},
}
FRESH = {
    "SPY": {"name": "S&P 500 ETF", "logo": None, "industry": "ETF"},
    "NEW": {"name": "New Co", "logo": "https://new/NEW.png", "industry": "Banking"},
    "DIS": {"name": "Walt Disney Co", "logo": "https://new/DIS.png", "industry": "Media"},
    "AAPL": {"name": "Apple Inc.", "logo": "https://new/AAPL.png", "industry": "Technology"},
}


def run(tmp_path: Path, existing=EXISTING, fresh=FRESH) -> tuple[Path, dict]:
    dest = tmp_path / "symbols.json"
    if existing is not None:
        dest.write_text(json.dumps(existing, indent=1) + "\n")
    mod.write_symbols(dest, {s: dict(v) for s, v in fresh.items()})
    return dest, json.loads(dest.read_text())


def test_a_rewrite_keeps_the_existing_domain(tmp_path):
    _, out = run(tmp_path)
    assert out["AAPL"]["domain"] == "apple.com"
    assert out["SPY"]["domain"] == "ssga.com"
    assert out["DIS"]["domain"] is None  # a known miss stays a miss, for logo_domains.mjs to fill


def test_the_script_still_owns_its_own_fields(tmp_path):
    _, out = run(tmp_path)
    assert out["AAPL"] == {"name": "Apple Inc.", "logo": "https://new/AAPL.png", "industry": "Technology",
                           "domain": "apple.com"}
    assert out["DIS"]["logo"] == "https://new/DIS.png"


def test_any_other_field_the_script_does_not_produce_is_kept(tmp_path):
    _, out = run(tmp_path)
    assert out["SPY"]["tile"] == "#fff"


def test_a_new_symbol_gets_a_null_domain_and_a_dropped_one_goes(tmp_path):
    _, out = run(tmp_path)
    assert out["NEW"] == {"name": "New Co", "logo": "https://new/NEW.png", "industry": "Banking", "domain": None}
    assert "GONE" not in out  # the universe decides which symbols are listed, as before
    assert all("domain" in v for v in out.values())  # apps/web logos.test.tsx requires the field on every symbol


def test_layout_is_unchanged_sorted_keys_fields_in_order(tmp_path):
    dest, out = run(tmp_path)
    assert list(out) == ["AAPL", "DIS", "NEW", "SPY"]
    assert list(out["AAPL"]) == ["name", "logo", "industry", "domain"]
    assert list(out["SPY"]) == ["name", "logo", "industry", "domain", "tile"]
    assert dest.read_text() == json.dumps(out, indent=1, ensure_ascii=False) + "\n"


def test_first_run_without_a_file_works(tmp_path):
    _, out = run(tmp_path, existing=None)
    assert out["AAPL"] == {"name": "Apple Inc.", "logo": "https://new/AAPL.png", "industry": "Technology",
                           "domain": None}


def test_an_unreadable_file_is_not_overwritten(tmp_path):
    dest = tmp_path / "symbols.json"
    dest.write_text("{ not json")
    with pytest.raises(ValueError):
        mod.write_symbols(dest, {s: dict(v) for s, v in FRESH.items()})
    assert dest.read_text() == "{ not json"  # the hand-entered domains are still there to rescue


def test_the_real_file_round_trips_byte_for_byte():
    """Same Finnhub answers over today's symbols.json must change nothing: every domain survives."""
    real = ROOT / "apps" / "web" / "src" / "data" / "symbols.json"
    before = json.loads(real.read_text())
    fresh = {s: {k: v[k] for k in mod.OWN_FIELDS} for s, v in before.items()}
    assert mod.render(mod.merge_existing(fresh, before)) == real.read_text()


# --- filling a missing `domain` from AllInvestView's search (#331, second half) -------------------------------------
# Answers of GET https://www.allinvestview.com/api/logo-search/?q=SYMBOL, one file per query, no network
# (tests/fixtures/allinvestview/README.md says how they were made).

AIV = ROOT / "tests" / "fixtures" / "allinvestview"


def answer(sym: str) -> dict:
    return json.loads((AIV / f"{sym}.json").read_text())


def recorded_search(asked: list[str]):
    def search(sym: str) -> dict:
        asked.append(sym)
        return answer(sym)
    return search


@pytest.mark.parametrize("sym, want", [
    ("AAPL", "apple.com"),                 # the exact NASDAQ row; AAPL.MX and the XETRA row are ignored
    ("SPY", "ssga.com"),                   # NYSE ARCA beats the XETRA listing with the same symbol
    ("DIS", "thewaltdisneycompany.com"),   # a missing and a non-hostname website are skipped; the next exact US row
    ("NBIS", None),                        # exact ticker, not a US exchange: no fallback, unlike logo_domains.mjs
    ("META", None),                        # METV, META.TO and lower-case "meta" are not the ticker: never a fuzzy match
    ("ZZZZ", None),                        # no results
])
def test_pick_domain_takes_only_the_exact_ticker_on_a_us_exchange(sym, want):
    assert mod.pick_domain(sym, answer(sym)["results"]) == want


def test_pick_domain_refuses_answers_that_are_not_a_result_list():
    assert mod.pick_domain("AAPL", None) is None
    assert mod.pick_domain("AAPL", "x") is None
    no_exchange = {"symbol": "AAPL", "exchange": None, "website": "apple.com"}
    assert mod.pick_domain("AAPL", [None, 3, "AAPL", no_exchange]) is None


def test_clean_domain_matches_the_web_rules():
    """The cases of apps/web/src/apiLogoDomain.test.ts, so the script and api/logo-domain.ts agree on a hostname."""
    assert mod.clean_domain("https://www.Apple.com/investor?x=1") == "apple.com"
    assert mod.clean_domain("http://t-mobile.com:443/") == "t-mobile.com"
    assert mod.clean_domain("ir.example.co.uk") == "ir.example.co.uk"
    for bad in ["", "   ", "localhost", "10.0.0.1", "apple", "apple..com", "-apple.com", "apple-.com", "ap ple.com",
                "javascript:alert(1)", "user@apple.com", "apple.com#x", "xn--.com", "a" * 64 + ".com", "apple.c0m",
                42, None, {}]:
        assert mod.clean_domain(bad) is None, bad


def test_fill_asks_only_for_missing_domains_and_never_replaces_one():
    rows = {
        "AAPL": {"name": "Apple Inc.", "logo": None, "industry": "Technology", "domain": "hand-entered.com"},
        "SPY": {"name": "S&P 500 ETF", "logo": None, "industry": "ETF", "domain": None},
        "NBIS": {"name": "Nebius", "logo": None, "industry": "Technology", "domain": None},
        "DIS": {"name": "Walt Disney Co", "logo": None, "industry": "Media", "domain": None},
    }
    asked: list[str] = []
    found = mod.fill_domains(rows, recorded_search(asked), sleep=lambda s: None)
    assert asked == ["DIS", "NBIS", "SPY"]  # sorted, and AAPL (already has one) is never looked up
    assert rows["AAPL"]["domain"] == "hand-entered.com"
    assert rows["SPY"]["domain"] == "ssga.com"
    assert rows["DIS"]["domain"] == "thewaltdisneycompany.com"
    assert rows["NBIS"]["domain"] is None  # a miss stays null, for logo_domains.mjs or the runtime lookup
    assert found == {"SPY": "ssga.com", "DIS": "thewaltdisneycompany.com"}


def test_a_failed_search_keeps_null_and_the_run_goes_on():
    rows = {s: {"name": s, "logo": None, "industry": None, "domain": None} for s in ("AAPL", "DIS", "SPY")}

    def search(sym: str) -> dict:
        if sym == "DIS":
            raise mod.HttpError(429, "https://www.allinvestview.com/api/logo-search/?q=DIS", "rate limited")
        if sym == "SPY":
            return {"results": "not a list"}
        return answer(sym)

    found = mod.fill_domains(rows, search, sleep=lambda s: None)
    assert found == {"AAPL": "apple.com"}
    assert rows["DIS"]["domain"] is None and rows["SPY"]["domain"] is None


def test_the_search_is_paced_one_call_every_1_2_seconds():
    rows = {s: {"name": s, "logo": None, "industry": None, "domain": None} for s in ("AAPL", "DIS", "SPY")}
    waits: list[float] = []
    mod.fill_domains(rows, recorded_search([]), sleep=waits.append)
    assert waits == [mod.SEARCH_GAP_S] * 2 and mod.SEARCH_GAP_S >= 1.0  # 60 a minute per IP is AllInvestView's limit


class _FakeResponse:
    def __init__(self, body: bytes):
        self.status, self.headers, self._body = 200, {"Content-Type": "application/json"}, body

    def read(self, n: int = -1) -> bytes:
        body, self._body = self._body, b""
        return body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_search_calls_the_logo_search_api_under_its_own_budget():
    seen = []

    def opener(req, timeout):
        seen.append(req)
        return _FakeResponse((AIV / "AAPL.json").read_bytes())

    from kaibot.common.ratelimit import get_budget

    budget = get_budget(mod.SEARCH_SOURCE)
    got = mod.search_logo("AAPL", opener=opener, budget=budget)
    assert got == answer("AAPL")
    assert [r.full_url for r in seen] == ["https://www.allinvestview.com/api/logo-search/?q=AAPL"]
    assert mod.SEARCH_SOURCE == "allinvestview_logo_search"


def test_the_search_source_is_in_sources_yaml_with_a_budget_under_the_free_limit():
    import yaml

    cfg = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text())
    entry = next(v[mod.SEARCH_SOURCE] for v in cfg.values() if isinstance(v, dict) and mod.SEARCH_SOURCE in v)
    assert entry["auth"] == "none"
    assert 0 < entry["budget_per_min"] <= 50  # AllInvestView allows 60 a minute per IP; api/logo-domain.ts uses 50 too


def test_a_plain_run_does_not_search():
    """Filling is opt-in (--fill-domains): the usual Finnhub run makes no AllInvestView call."""
    assert mod.parse_args([]).fill_domains is False
    assert mod.parse_args(["--fill-domains"]).fill_domains is True


def test_write_with_search_fills_then_writes_once_in_the_same_layout(tmp_path):
    dest = tmp_path / "symbols.json"
    dest.write_text(json.dumps(EXISTING, indent=1) + "\n")
    fresh = {"AAPL": FRESH["AAPL"], "DIS": FRESH["DIS"], "SPY": FRESH["SPY"]}
    asked: list[str] = []
    out = mod.write_symbols(dest, {s: dict(v) for s, v in fresh.items()}, recorded_search(asked), sleep=lambda s: None)
    assert asked == ["DIS"]  # AAPL and SPY already have a domain in the file
    assert out["DIS"]["domain"] == "thewaltdisneycompany.com"
    assert out["AAPL"]["domain"] == "apple.com" and out["SPY"]["domain"] == "ssga.com"
    assert dest.read_text() == mod.render(out)
    assert list(out["DIS"]) == ["name", "logo", "industry", "domain"]


# --- a lookup that dies below HttpError keeps null and the run goes on (#383) ----------------------------------------
# These three are not turned into HttpError by kaibot.common.http: a body cut short and a TLS EOF are raised by
# r.read() (neither is a URLError, TimeoutError or ConnectionError, so `get` does not retry or wrap them), and a very
# deeply nested body makes json.loads raise RecursionError, which is not the ValueError `get_json` wraps. Each one
# goes through the real search_logo with a fake opener, so the test fails where the script would really fail.


class _BrokenRead(_FakeResponse):
    def __init__(self, exc: BaseException):
        super().__init__(b"")
        self._exc = exc

    def read(self, n: int = -1) -> bytes:
        raise self._exc


def _incomplete_read() -> _FakeResponse:
    import http.client

    return _BrokenRead(http.client.IncompleteRead(b'{"results": [', 512))


def _tls_eof() -> _FakeResponse:
    import ssl

    return _BrokenRead(ssl.SSLEOFError(8, "EOF occurred in violation of protocol (_ssl.c:2427)"))


def _nested_json() -> _FakeResponse:
    return _FakeResponse(b"[" * 200_000)  # 200 kB, under search_logo's max_bytes: it is the parse that gives up


def _search_failing_for(bad_sym: str, broken, asked: list[str]):
    """search_logo itself (kaibot.common.http underneath), answering from the fixtures except for `bad_sym`."""
    from kaibot.common.ratelimit import Budget, BudgetSpec

    budget = Budget("test_allinvestview", BudgetSpec())  # this test's own, unlimited: no waiting, no shared state

    def opener(req, timeout):
        sym = req.full_url.rsplit("=", 1)[1]
        asked.append(sym)
        return broken() if sym == bad_sym else _FakeResponse((AIV / f"{sym}.json").read_bytes())

    return lambda sym: mod.search_logo(sym, opener=opener, budget=budget)


@pytest.mark.parametrize("broken, logged", [
    (_incomplete_read, "IncompleteRead"),  # the connection is cut in the middle of the body
    (_tls_eof, "SSLEOFError"),             # the TLS session ends during the read
    (_nested_json, "RecursionError"),      # a body nested too deeply to parse
], ids=["incomplete-read", "tls-eof", "nested-json"])
def test_a_lookup_that_dies_below_http_error_keeps_null_and_the_file_is_still_written(tmp_path, capsys, broken,
                                                                                      logged):
    dest = tmp_path / "symbols.json"
    existing = {s: {**EXISTING[s], "domain": None} for s in ("AAPL", "DIS", "SPY")}
    dest.write_text(json.dumps(existing, indent=1) + "\n")
    fresh = {s: dict(FRESH[s]) for s in ("AAPL", "DIS", "SPY")}
    asked: list[str] = []
    out = mod.write_symbols(dest, fresh, _search_failing_for("DIS", broken, asked), sleep=lambda s: None)
    assert asked == ["AAPL", "DIS", "SPY"]  # one try for DIS, then the run carries on to SPY
    assert out["DIS"]["domain"] is None  # the failed lookup keeps null
    assert out["AAPL"]["domain"] == "apple.com" and out["SPY"]["domain"] == "ssga.com"
    written = json.loads(dest.read_text())
    assert written == out and dest.read_text() == mod.render(out)  # the file is written, in the usual layout
    assert written["DIS"]["logo"] == "https://new/DIS.png"  # with this run's Finnhub refresh, for the failed row too
    assert written["AAPL"]["name"] == "Apple Inc." and written["AAPL"]["industry"] == "Technology"
    err = capsys.readouterr().err
    assert f"DIS: search failed ({logged}" in err and "domain stays null" in err  # says what was caught


@pytest.mark.parametrize("exc", [KeyboardInterrupt(), SystemExit(1), TypeError("a bug in the script"),
                                 ValueError("refusing non-HTTPS URL")], ids=lambda e: type(e).__name__)
def test_a_stop_request_or_a_programming_error_is_not_swallowed(exc):
    """Only the failures of a lookup are kept as null: Ctrl-C, an exit and a bug still stop the run, loudly."""
    rows = {s: {"name": s, "logo": None, "industry": None, "domain": None} for s in ("AAPL", "DIS")}

    def search(sym: str) -> dict:
        raise exc

    with pytest.raises(type(exc)):
        mod.fill_domains(rows, search, sleep=lambda s: None)
