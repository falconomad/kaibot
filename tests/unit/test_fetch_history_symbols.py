"""scripts/fetch_history.py --symbols / --test-group (stories S66 #94, S03 #3): explicit symbol lists for replay
history. Offline: the HTTP layer (urllib, under kaibot.common.http) is faked, no request leaves the machine."""

from __future__ import annotations

import importlib.util
import io
import json
import urllib.error
import urllib.parse
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pyarrow.parquet as pq
import pytest
import yaml

from kaibot.common import ratelimit
from kaibot.common.ratelimit import Budget, BudgetSpec
from kaibot.ingest import alpaca_history as ah
from kaibot.ingest import test_group as tg

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "config" / "universe_seed.yaml"
NOW = datetime(2026, 11, 11, 3, 0, tzinfo=UTC)
MONTHS = ("2026-09", "2026-10", "2026-11")  # --start 2026-09-01 at NOW; 2026-11 is the running month


def load_script(name: str = "fetch_history"):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s


class _Resp(io.BytesIO):
    status = 200
    headers: dict = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeHttp:
    """Stands in for urllib.request.urlopen: answers Alpaca's /v2/stocks/<sym>/bars, records every request."""

    def __init__(self, no_bars=(), status=None):
        self.no_bars, self.status = set(no_bars), status or {}
        self.requests: list[tuple[str, dict, dict]] = []

    def __call__(self, req, timeout):
        u = urllib.parse.urlsplit(req.full_url)
        q = dict(urllib.parse.parse_qsl(u.query))
        self.requests.append((f"{u.scheme}://{u.netloc}{u.path}", q, dict(req.header_items())))
        sym = u.path.split("/")[3]
        if sym in self.status:
            raise urllib.error.HTTPError(req.full_url, self.status[sym], "err", {}, io.BytesIO(b"{}"))
        day = datetime.fromisoformat(q["start"]).strftime("%Y-%m-%d")
        bars = [] if sym in self.no_bars else [
            {"t": f"{day}T{hm}:00Z", "o": 10, "h": 11, "l": 9, "c": 10.5, "v": 100, "n": 3, "vw": 10.2}
            for hm in ("12:00", "15:00", "15:01")]  # 12:00 UTC is before the open: dropped by --rth-only
        return _Resp(json.dumps({"bars": bars, "next_page_token": None}).encode())

    def symbols(self) -> list[str]:
        return list(dict.fromkeys(u.split("/")[5] for u, _, _ in self.requests))


@pytest.fixture
def http(monkeypatch):
    fake = FakeHttp()
    monkeypatch.setattr("urllib.request.urlopen", fake)
    return fake


@pytest.fixture
def seed(tmp_path):
    p = tmp_path / "config" / "universe_seed.yaml"
    p.parent.mkdir()
    p.write_text(SEED.read_text())
    return p


def api():
    return ah.Alpaca("k", "s", budget=Budget("alpaca", BudgetSpec(), FakeClock()))


def run(tmp_path, *args, seed=SEED, start="2026-09-01", **kw):
    return load_script().main(["--universe", str(seed), "--data", str(tmp_path / "data"), "--start", start,
                               "--rth-only", *args], api=kw.pop("api", None) or api(), now=NOW, **kw)


# ---------- symbol validation ----------
def test_parse_symbols_normalises_and_dedupes():
    assert ah.parse_symbols(" nbis, CRWV ,BRK.B,NBIS,") == ["NBIS", "CRWV", "BRK.B"]
    assert ah.parse_symbols(["A", "fsly"]) == ["A", "FSLY"]


@pytest.mark.parametrize("bad", ["", " , ", "A B", "../ETC", "NBIS;rm", "TOOLONG", "1ABC", "X/Y", "A.", ".A", "A..B",
                                 "NBIS,CR WV", "symbol=X", "A-B", "ÄB"])
def test_parse_symbols_rejects_anything_that_is_not_a_ticker(bad):
    with pytest.raises(ValueError, match="symbol"):
        ah.parse_symbols(bad)


def test_parse_symbols_names_every_bad_entry():
    with pytest.raises(ValueError, match=r"'CR WV'.*'\.\./X'"):
        ah.parse_symbols("NBIS,CR WV,../X")


# ---------- which symbols get downloaded ----------
def test_explicit_symbols_and_group_are_added_to_spy_only(seed):
    fh = load_script()
    cfg = yaml.safe_load(seed.read_text())
    default = fh.universe_symbols(cfg)
    assert fh.select_symbols(cfg) == default and len(default) == 1 + 11 + 59
    assert fh.select_symbols(cfg, symbols="nbis,CRWV") == ["SPY", "NBIS", "CRWV"]
    assert fh.select_symbols(cfg, symbols="SPY,NVDA,NBIS,NVDA") == ["SPY", "NVDA", "NBIS"]
    tg.write_test_group(seed, "high_beta_growth", ["NBIS", "CRWV"], "t", date(2026, 11, 12), tg.Filters())
    cfg = yaml.safe_load(seed.read_text())
    assert fh.select_symbols(cfg) == default, "a written test group never joins the default download"
    assert fh.select_symbols(cfg, test_group="high_beta_growth") == ["SPY", "NBIS", "CRWV"]
    assert fh.select_symbols(cfg, symbols="FSLY,CRWV", test_group="high_beta_growth") == ["SPY", "NBIS", "CRWV", "FSLY"]


def test_unknown_group_fails_clearly(seed):
    fh = load_script()
    with pytest.raises(SystemExit, match=r"no test group 'high_beta_growth'.*screen_test_group\.py.*--write"):
        fh.select_symbols(yaml.safe_load(seed.read_text()), test_group="high_beta_growth")
    tg.write_test_group(seed, "high_beta_growth", ["NBIS"], "t", date(2026, 11, 12), tg.Filters())
    with pytest.raises(SystemExit, match=r"no test group 'nope'.*known groups: high_beta_growth"):
        fh.select_symbols(yaml.safe_load(seed.read_text()), test_group="nope")


def test_a_hand_edited_group_with_a_bad_symbol_is_refused(seed):
    seed.write_text(seed.read_text() + "\ntest_groups:\n  g:\n    symbols: [NBIS, '../../etc']\n")
    with pytest.raises(SystemExit, match="symbol"):
        load_script().select_symbols(yaml.safe_load(seed.read_text()), test_group="g")


# ---------- the download ----------
def test_symbols_download_writes_replay_layout_with_old_sip_data_only(tmp_path, http, capsys):
    before = SEED.read_text()
    assert run(tmp_path, "--symbols", "NBIS,crwv") == 0
    assert http.symbols() == ["SPY", "NBIS", "CRWV"], "only SPY and the named symbols are requested"
    assert len(http.requests) == 9
    for url, q, _ in http.requests:
        assert url.startswith("https://data.alpaca.markets/v2/stocks/") and url.endswith("/bars")
        assert q["feed"] == "sip" and q["timeframe"] == "1Min"
        assert datetime.fromisoformat(q["end"]) <= NOW - timedelta(minutes=15), "SIP only when >= 15 min old"
    data = tmp_path / "data" / "bars_1m"
    assert sorted(p.name for p in data.iterdir()) == ["symbol=CRWV", "symbol=NBIS", "symbol=SPY"]
    for sym in ("SPY", "NBIS", "CRWV"):
        assert sorted(p.name for p in (data / f"symbol={sym}").iterdir()) == [f"{m}.parquet" for m in MONTHS]
    t = pq.read_table(data / "symbol=NBIS" / "2026-10.parquet").to_pylist()
    assert len(t) == 2 and all(r["available_ts"] - r["ts"] == timedelta(minutes=1) for r in t)  # point-in-time
    assert SEED.read_text() == before, "the download never edits the universe the engine reads"
    out = capsys.readouterr().out
    assert "3 symbols" in out and "NBIS" in out


def test_rerun_is_idempotent_and_only_refreshes_the_running_month(tmp_path, http):
    assert run(tmp_path, "--symbols", "NBIS") == 0
    old = tmp_path / "data" / "bars_1m" / "symbol=NBIS" / "2026-09.parquet"
    stamp, n = old.stat().st_mtime_ns, len(http.requests)
    assert n == 6
    assert run(tmp_path, "--symbols", "NBIS") == 0
    again = http.requests[n:]
    assert len(again) == 2 and all(q["start"].startswith("2026-11-01") for _, q, _ in again)
    assert old.stat().st_mtime_ns == stamp
    assert sorted(p.name for p in old.parent.iterdir()) == [f"{m}.parquet" for m in MONTHS]


def test_months_before_a_listing_leave_markers_and_are_not_asked_again(tmp_path, monkeypatch, capsys):
    fake = FakeHttp(no_bars={"CRWV"})
    monkeypatch.setattr("urllib.request.urlopen", fake)
    assert run(tmp_path, "--symbols", "CRWV") == 1  # a symbol without a single bar is reported, not hidden
    d = tmp_path / "data" / "bars_1m" / "symbol=CRWV"
    assert sorted(p.name for p in d.iterdir()) == [f"{m}.empty" for m in MONTHS]
    assert "CRWV: no bars" in capsys.readouterr().out
    n = len(fake.requests)
    run(tmp_path, "--symbols", "CRWV")
    assert [q["start"][:7] for u, q, _ in fake.requests[n:] if "/CRWV/" in u] == ["2026-11"]


def test_bad_input_stops_before_any_request_or_file(tmp_path, http):
    fh = load_script()
    for args in (["--symbols", "NBIS,../x"], ["--symbols", ","], ["--test-group", "missing"],
                 ["--symbols", "NBIS", "--shard", "3/2"], ["--symbols", "NBIS", "--start", "2031-01-01"]):
        with pytest.raises(SystemExit) as e:
            fh.main(["--env", str(tmp_path / "missing.env"), "--data", str(tmp_path / "data"), *args], now=NOW)
        assert e.value.code not in (0, None)
    assert http.requests == [] and not (tmp_path / "data").exists()


def test_an_unknown_ticker_is_skipped_and_reported_without_stopping_the_run(tmp_path, monkeypatch, capsys):
    fake = FakeHttp(status={"NOPE": 404})
    monkeypatch.setattr("urllib.request.urlopen", fake)
    assert run(tmp_path, "--symbols", "NOPE,NBIS") == 1
    out = capsys.readouterr().out
    assert "NOPE: skipped (HTTP 404" in out
    assert (tmp_path / "data" / "bars_1m" / "symbol=NBIS" / "2026-09.parquet").exists()
    assert not (tmp_path / "data" / "bars_1m" / "symbol=NOPE").exists()


def test_a_rejected_key_stops_the_run_without_printing_it(tmp_path, monkeypatch, capsys):
    fake = FakeHttp(status={"SPY": 401})
    monkeypatch.setattr("urllib.request.urlopen", fake)
    assert run(tmp_path, "--symbols", "NBIS", api=ah.Alpaca("key-abc123", "secret-xyz789",
                                                           budget=Budget("alpaca", BudgetSpec(), FakeClock()))) == 1
    out = capsys.readouterr().out
    assert "HTTP 401" in out and "abc123" not in out and "xyz789" not in out
    assert len(fake.requests) == 1, "an auth failure is not retried symbol after symbol"


# ---------- keys and budget ----------
def test_env_file_is_required_and_defaults_to_kaibot_env_file(tmp_path, http, capsys, monkeypatch):
    fh = load_script()
    monkeypatch.setenv("KAIBOT_ENV_FILE", str(tmp_path / "from-env-var.env"))
    assert fh.main(["--symbols", "NBIS", "--data", str(tmp_path / "data")], now=NOW) == 2
    assert "from-env-var.env" in capsys.readouterr().out
    env = tmp_path / ".env"
    env.write_text("ALPACA_API_KEY=abc123secret\n")
    assert fh.main(["--env", str(env), "--symbols", "NBIS", "--data", str(tmp_path / "data")], now=NOW) == 2
    assert "abc123secret" not in capsys.readouterr().out and http.requests == []


def test_requests_run_under_the_shared_alpaca_budget_plus_per_min(tmp_path, http, monkeypatch):
    reg = ratelimit.Registry(clock=FakeClock())
    monkeypatch.setattr(ah, "REGISTRY", reg)
    monkeypatch.setenv("SSL_CERT_FILE", "unchanged")
    env = tmp_path / ".env"
    env.write_text("ALPACA_API_KEY=abc\nALPACA_SECRET=def\n")
    fh = load_script()
    assert fh.main(["--env", str(env), "--symbols", "NBIS", "--data", str(tmp_path / "data"), "--start",
                    "2026-09-01", "--per-min", "45"], now=NOW) == 0
    shared = ratelimit.load_specs()["alpaca"].windows
    assert shared == ((150, 60.0),), "config/sources.yaml: alpaca budget"
    assert reg.get("alpaca").spec.windows == (*shared, (45, 60.0))
    assert len(http.requests) == 6 and {h["Apca-api-key-id"] for _, _, h in http.requests} == {"abc"}


def test_per_min_caps_the_request_rate(tmp_path, http):
    clock = FakeClock()
    slow = ah.Alpaca("k", "s", budget=Budget("alpaca", BudgetSpec(((150, 60.0), (2, 60.0))), clock))
    assert run(tmp_path, "--symbols", "NBIS", api=slow) == 0
    assert len(http.requests) == 6 and clock.t - 1000.0 >= 120.0  # 6 requests at 2/min: at least two full waits
