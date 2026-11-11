"""S54: Databento validation source (cost gate, cap, cache, budget) and the smoke-test probes. No network."""

import importlib.util
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
