"""S54: Databento validation source (cost gate, cap, cache, budget) and the smoke-test probes. No network."""

import importlib.util
import json
import os
import sys
import types
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from kaibot.common import ratelimit
from kaibot.ingest import databento_hist as dbh

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
NOW = datetime(2026, 11, 11, 18, 0, tzinfo=UTC)


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeStore:
    def __init__(self, df):
        self.df = df

    def to_df(self):
        return self.df


class FakeClient:
    def __init__(self, cost=0.5, df=None):
        self.calls = []
        client = self

        class Meta:
            def get_cost(self, **kw):
                client.calls.append(("get_cost", kw))
                return cost

        class TS:
            def get_range(self, **kw):
                client.calls.append(("get_range", kw))
                syms = kw["symbols"]
                idx = pd.DatetimeIndex([pd.Timestamp("2026-10-01T13:30Z")] * len(syms), name="ts_event")
                return FakeStore(df if df is not None else pd.DataFrame(
                    {"symbol": syms, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10}, index=idx))

        self.metadata, self.timeseries = Meta(), TS()

    def names(self):
        return [c[0] for c in self.calls]


@pytest.fixture(autouse=True)
def _registry(monkeypatch):
    monkeypatch.setattr(ratelimit, "REGISTRY", ratelimit.Registry())


@pytest.fixture(autouse=True)
def _cert_env(monkeypatch):
    """The CLI's main() may set SSL_CERT_FILE: start without it and restore the real value afterwards."""
    monkeypatch.setenv("SSL_CERT_FILE", "x")
    monkeypatch.delenv("SSL_CERT_FILE")


def _req(syms=("SPY", "AAPL"), schema="ohlcv-1m"):
    return dbh.Request.make(syms, "2026-10-01", "2026-10-02", schema=schema, now=NOW)


def test_sources_yaml_entry_is_validation_only_with_10_usd_cap_and_budget():
    cfg = dbh.load_config()
    assert cfg["use"] == "validation_only"
    assert dbh.max_cost_from_config(cfg) == 10.0
    assert not ratelimit.load_specs()["databento"].unlimited


def test_env_example_has_databento_key():
    text = (SCRIPTS.parent / ".env.example").read_text()
    line = next(x for x in text.splitlines() if x.startswith("DATABENTO_API_KEY="))
    assert "never in the live path" in line.lower()


def test_dry_run_estimates_and_prints_but_downloads_nothing(tmp_path):
    c, out = FakeClient(cost=0.42), []
    res = dbh.fetch(c, _req(), tmp_path, 10.0, out=out.append)
    assert c.names() == ["get_cost"] and res.dry_run and res.estimated_usd == 0.42
    assert any("estimated cost: $0.4200" in line for line in out)
    assert c.calls[0][1]["dataset"] == "EQUS.MINI" and c.calls[0][1]["schema"] == "ohlcv-1m"
    assert not (tmp_path / "databento").exists()


def test_confirm_estimates_first_then_downloads_parquet_with_available_ts(tmp_path):
    c = FakeClient()
    res = dbh.fetch(c, _req(), tmp_path, 10.0, confirm=True, out=lambda _: None)
    assert c.names() == ["get_cost", "get_range"]
    assert res.downloaded == {"AAPL": 1, "SPY": 1}
    df = pd.read_parquet(dbh.cache_path(tmp_path, _req(), "SPY"))
    assert (df["available_ts"] - df["ts_event"]).iloc[0] == pd.Timedelta(minutes=1)


def test_cost_above_cap_is_refused_and_never_downloads(tmp_path):
    c = FakeClient(cost=10.01)
    with pytest.raises(dbh.CostCapExceeded):
        dbh.fetch(c, _req(), tmp_path, 10.0, confirm=True, out=lambda _: None)
    assert c.names() == ["get_cost"]


def test_cached_symbols_are_not_paid_twice(tmp_path):
    dbh.fetch(FakeClient(), _req(("SPY",)), tmp_path, 10.0, confirm=True, out=lambda _: None)
    c = FakeClient()
    dbh.fetch(c, _req(), tmp_path, 10.0, confirm=True, out=lambda _: None)
    assert [k["symbols"] for _, k in c.calls] == [["AAPL"], ["AAPL"]]  # estimate + download only the new one
    c2 = FakeClient()
    res = dbh.fetch(c2, _req(), tmp_path, 10.0, confirm=True, out=lambda _: None)
    assert c2.calls == [] and res.estimated_usd == 0.0


def test_empty_result_leaves_marker(tmp_path):
    dbh.fetch(FakeClient(df=pd.DataFrame()), _req(("SPY",)), tmp_path, 10.0, confirm=True, out=lambda _: None)
    assert dbh.cache_path(tmp_path, _req(("SPY",)), "SPY").with_suffix(".empty").exists()
    assert dbh.is_cached(tmp_path, _req(("SPY",)), "SPY")


def test_every_call_acquires_the_databento_budget(tmp_path):
    class Spy(ratelimit.Budget):
        n = 0

        def acquire(self):
            Spy.n += 1
            return 0.0

    b = Spy("databento", ratelimit.BudgetSpec())
    dbh.fetch(FakeClient(), _req(), tmp_path, 10.0, confirm=True, out=lambda _: None, budget=b)
    assert Spy.n == 2


@pytest.mark.parametrize("kw", [
    {"symbols": []},
    {"schema": "mbo"},
    {"start": "2026-10-02", "end": "2026-10-01"},
    {"end": "2026-11-11T17:50:00Z"},  # only 10 min old
    {"symbols": ["../etc"]},
])
def test_request_validation(kw):
    args = {"symbols": ["SPY"], "start": "2026-10-01", "end": "2026-10-02", "schema": "ohlcv-1m"} | kw
    with pytest.raises(ValueError):
        dbh.Request.make(args["symbols"], args["start"], args["end"], schema=args["schema"], now=NOW)


def test_bbo_1m_schema_allowed():
    assert _req(schema="bbo-1m").schema == "bbo-1m"


def test_cli_is_dry_run_by_default_and_refuses_over_cap(tmp_path, capsys):
    cli = _load("fetch_databento")
    env = tmp_path / ".env"
    env.write_text("DATABENTO_API_KEY=db-test\n")
    base = ["--env", str(env), "--symbols", "SPY", "--start", "2026-10-01", "--end", "2026-10-02",
            "--data", str(tmp_path)]
    c = FakeClient(cost=1.0)
    assert cli.main(base, client=c) == 0 and c.names() == ["get_cost"]
    assert "dry run" in capsys.readouterr().out
    c = FakeClient(cost=1.0)
    assert cli.main([*base, "--confirm", "--max-cost", "0.5"], client=c) == 3 and c.names() == ["get_cost"]
    c = FakeClient(cost=1.0)
    assert cli.main([*base, "--confirm", "--max-cost", "999"], client=c) == 0  # cap stays $10, 1.0 is fine
    c = FakeClient(cost=11.0)
    fresh = [*base[:-1], str(tmp_path / "fresh")]
    assert cli.main([*fresh, "--confirm", "--max-cost", "999"], client=c) == 3  # --max-cost cannot raise it
    assert c.names() == ["get_cost"]


def _ledger(root):
    return [json.loads(x) for x in dbh.ledger_path(root).read_text().splitlines()]


def test_sources_yaml_credit_is_125_and_validated():
    assert dbh.credit_from_config(dbh.load_config()) == 125.0
    assert dbh.credit_from_config({}) == 125.0
    for bad in (-1, True, "125", float("nan")):
        with pytest.raises(ValueError):
            dbh.credit_from_config({"credit_usd": bad})


def test_confirmed_download_is_recorded_in_the_ledger_and_dry_run_is_not(tmp_path):
    dbh.fetch(FakeClient(cost=0.42), _req(), tmp_path, 10.0, out=lambda _: None)
    assert not dbh.ledger_path(tmp_path).exists() and dbh.spent(tmp_path) == 0.0
    dbh.fetch(FakeClient(cost=0.42), _req(("SPY",)), tmp_path, 10.0, confirm=True, out=lambda _: None)
    dbh.fetch(FakeClient(cost=1.5), _req(), tmp_path, 10.0, confirm=True, out=lambda _: None)
    rows = _ledger(tmp_path)
    assert [r["cost_usd"] for r in rows] == [0.42, 1.5] and rows[1]["symbols"] == ["AAPL"]
    assert rows[0]["dataset"] == "EQUS.MINI" and dbh.spent(tmp_path) == pytest.approx(1.92)
    assert dbh.ledger_path(tmp_path) == tmp_path / "databento" / "ledger.jsonl"


def test_request_past_the_total_credit_is_refused_even_with_confirm(tmp_path):
    out = []
    dbh.fetch(FakeClient(cost=4.0), _req(("SPY",)), tmp_path, 10.0, confirm=True, out=out.append, credit_usd=5.0)
    assert "recorded spend $0.00 of $5.00 credit" in out[0]
    c = FakeClient(cost=1.01)
    with pytest.raises(dbh.CreditExceeded, match="past the \\$5.00 credit"):
        dbh.fetch(c, _req(("AAPL",)), tmp_path, 10.0, confirm=True, out=lambda _: None, credit_usd=5.0)
    assert c.names() == ["get_cost"] and len(_ledger(tmp_path)) == 1
    assert not dbh.is_cached(tmp_path, _req(("AAPL",)), "AAPL")
    with pytest.raises(dbh.CreditExceeded):  # a dry run is refused too: it would not be allowed anyway
        dbh.fetch(FakeClient(cost=1.01), _req(("AAPL",)), tmp_path, 10.0, out=lambda _: None, credit_usd=5.0)
    c = FakeClient(cost=1.0)  # exactly up to the credit is allowed
    dbh.fetch(c, _req(("AAPL",)), tmp_path, 10.0, confirm=True, out=lambda _: None, credit_usd=5.0)
    assert c.names() == ["get_cost", "get_range"] and dbh.spent(tmp_path) == 5.0


def test_fetch_guards_the_125_usd_credit_by_default(tmp_path):
    dbh.ledger_path(tmp_path).parent.mkdir(parents=True)
    dbh.ledger_path(tmp_path).write_text(json.dumps({"cost_usd": 124.5}) + "\n")
    c = FakeClient(cost=0.6)
    with pytest.raises(dbh.CreditExceeded):
        dbh.fetch(c, _req(), tmp_path, 10.0, confirm=True, out=lambda _: None)
    assert c.names() == ["get_cost"]


def test_spend_is_recorded_even_if_the_local_write_fails(tmp_path, monkeypatch):
    def boom(self, *a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", boom)
    with pytest.raises(OSError):
        dbh.fetch(FakeClient(cost=2.0), _req(("SPY",)), tmp_path, 10.0, confirm=True, out=lambda _: None)
    assert dbh.spent(tmp_path) == 2.0


@pytest.mark.parametrize("line", ["not json", '{"cost": 1}', '{"cost_usd": -3}', '{"cost_usd": "NaN"}'])
def test_unreadable_ledger_refuses_instead_of_counting_zero(tmp_path, line):
    dbh.ledger_path(tmp_path).parent.mkdir(parents=True)
    dbh.ledger_path(tmp_path).write_text(line + "\n")
    c = FakeClient()
    with pytest.raises(ValueError, match="ledger"):
        dbh.fetch(c, _req(), tmp_path, 10.0, confirm=True, out=lambda _: None)
    assert "get_range" not in c.names()


@pytest.mark.parametrize("cost", [-1.0, float("nan"), float("inf")])
def test_unusable_estimate_is_refused(tmp_path, cost):
    c = FakeClient(cost=cost)
    with pytest.raises(dbh.CostCapExceeded):
        dbh.fetch(c, _req(), tmp_path, 10.0, confirm=True, out=lambda _: None)
    assert c.names() == ["get_cost"] and not dbh.ledger_path(tmp_path).exists()


def test_cli_refuses_past_the_credit_from_sources_yaml(tmp_path, capsys):
    cli = _load("fetch_databento")
    env = tmp_path / ".env"
    env.write_text("DATABENTO_API_KEY=db-test\n")
    data = tmp_path / "data"
    dbh.ledger_path(data).parent.mkdir(parents=True)
    dbh.ledger_path(data).write_text(json.dumps({"cost_usd": 124.0}) + "\n")
    args = ["--env", str(env), "--symbols", "SPY", "--start", "2026-10-01", "--end", "2026-10-02",
            "--data", str(data), "--confirm"]
    c = FakeClient(cost=1.5)
    assert cli.main(args, client=c) == 3 and c.names() == ["get_cost"]
    assert "past the $125.00 credit" in capsys.readouterr().err
    c = FakeClient(cost=0.5)
    assert cli.main(args, client=c) == 0 and c.names() == ["get_cost", "get_range"]
    assert dbh.spent(data) == 124.5
    dbh.ledger_path(data).write_text("garbage\n")
    c = FakeClient(cost=0.5)
    assert cli.main([*args[:3], "AAPL", *args[4:]], client=c) == 3 and "get_range" not in c.names()
    assert "ledger" in capsys.readouterr().err


def test_cli_sets_certifi_bundle_only_when_ssl_cert_file_is_unset(tmp_path, monkeypatch):
    cli = _load("fetch_databento")
    monkeypatch.setitem(sys.modules, "certifi", types.SimpleNamespace(where=lambda: "/fake/cacert.pem"))
    env = tmp_path / ".env"
    env.write_text("DATABENTO_API_KEY=db-test\n")
    args = ["--env", str(env), "--symbols", "SPY", "--start", "2026-10-01", "--end", "2026-10-02",
            "--data", str(tmp_path)]
    seen = []

    c = FakeClient()
    orig = c.metadata.get_cost
    c.metadata.get_cost = lambda **kw: (seen.append(os.environ.get("SSL_CERT_FILE")), orig(**kw))[1]
    assert cli.main(args, client=c) == 0 and seen == ["/fake/cacert.pem"]
    monkeypatch.setenv("SSL_CERT_FILE", "/my/own/roots.pem")
    assert cli.main(args, client=FakeClient()) == 0 and os.environ["SSL_CERT_FILE"] == "/my/own/roots.pem"


def test_smoke_sip_probe_pass_and_fail(monkeypatch):
    smoke = _load("smoke_test_keys")
    seen = []

    def fake_get(url, headers=None, data=None):
        seen.append(url)
        return 200, {"bars": [{"t": "2026-11-10T14:30:00Z"}]}

    monkeypatch.setattr(smoke, "get", fake_get)
    ok, detail = smoke.check_alpaca_sip_history({}, now=NOW)
    assert ok and "serves SIP history" in detail
    assert "feed=sip" in seen[0] and "end=2026-11-11T17%3A40%3A00Z" in seen[0]  # 20 min before now
    monkeypatch.setattr(smoke, "get", lambda *a, **k: (403, None))
    ok, detail = smoke.check_alpaca_sip_history({}, now=NOW)
    assert not ok and "does NOT" in detail


def test_smoke_databento_auth_is_zero_cost_metadata_call(monkeypatch):
    smoke = _load("smoke_test_keys")
    seen = []

    def fake_get(url, headers=None, data=None):
        seen.append((url, headers))
        return 200, ["EQUS.MINI", "XNAS.ITCH"]

    monkeypatch.setattr(smoke, "get", fake_get)
    ok, detail = smoke.check_databento("db-secret")
    assert ok and "EQUS.MINI=yes" in detail and "db-secret" not in detail
    assert seen[0][0].endswith("metadata.list_datasets") and seen[0][1]["Authorization"].startswith("Basic ")
    monkeypatch.setattr(smoke, "get", lambda *a, **k: (401, None))
    assert smoke.check_databento("bad") == (False, "http 401 (key rejected)")
