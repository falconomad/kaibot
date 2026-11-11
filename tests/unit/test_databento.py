"""S54: Databento validation source. Every network call is faked; nothing here can spend the credit."""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
import sys
import types
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from kaibot.common import http
from kaibot.common.ratelimit import Budget, BudgetSpec, load_specs
from kaibot.ingest import databento as dbn

ROOT = Path(__file__).resolve().parents[2]
KEY = "db-TESTKEY-not-real"
NOW = datetime(2026, 11, 11, 22, 0, tzinfo=UTC)
CSV = (b"ts_event,rtype,publisher_id,instrument_id,open,high,low,close,volume,symbol\n"
       b"2026-10-01T13:30:00.000000000Z,33,2,38,227.1,227.5,226.9,227.3,120345,AAPL\n"
       b"2026-10-01T13:31:00.000000000Z,33,2,38,227.3,227.8,227.2,227.7,98012,AAPL\n")


class _Resp(io.BytesIO):
    def __init__(self, body: bytes):
        super().__init__(body)
        self.status = 200
        self.headers = {"Content-Type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeDatabento:
    """urlopen stand-in: records (method, url, form, headers) and answers by endpoint."""

    def __init__(self, cost: float = 0.42, csv: bytes = CSV, status: int | None = None):
        self.cost, self.csv, self.status = cost, csv, status
        self.calls: list[tuple[str, str, dict, dict]] = []

    def __call__(self, req, timeout):
        assert timeout and timeout <= 120
        form = dict(urllib.parse.parse_qsl(req.data.decode())) if req.data else {}
        self.calls.append((req.get_method(), req.full_url, form, dict(req.header_items())))
        assert req.full_url.startswith("https://hist.databento.com/v0/")
        assert KEY not in req.full_url and KEY not in json.dumps(form)
        if self.status:
            raise urllib.error.HTTPError(req.full_url, self.status, "x", {}, io.BytesIO(b""))
        if "metadata.list_datasets" in req.full_url:
            return _Resp(b'["XNAS.ITCH","GLBX.MDP3"]')
        if "metadata.get_cost" in req.full_url:
            return _Resp(json.dumps(self.cost).encode())
        if "timeseries.get_range" in req.full_url:
            assert req.get_method() == "POST"
            return _Resp(self.csv)
        raise AssertionError(req.full_url)

    def methods(self, endpoint: str) -> list[str]:
        return [m for m, u, _, _ in self.calls if endpoint in u]


@pytest.fixture
def net(monkeypatch):
    fake = FakeDatabento()
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return fake


def _client():
    return dbn.Databento(KEY, budget=Budget("t", BudgetSpec()))


def _q(**kw):
    d = {"dataset": "XNAS.ITCH", "schema": "ohlcv-1m", "symbols": ("AAPL",), "start": "2026-10-01",
         "end": "2026-10-02"}
    return dbn.Query(**{**d, **kw})


POLICY = dbn.Policy(max_cost_usd=10.0, credit_usd=125.0)


def test_sources_yaml_entry_is_validation_only_with_default_cap():
    p = dbn.Policy.load()
    assert p.max_cost_usd == 10.0 and p.credit_usd == 125.0
    assert not load_specs()["databento"].unlimited


def test_policy_refuses_non_validation_use(tmp_path):
    f = tmp_path / "s.yaml"
    f.write_text("market_data:\n  databento: {use: primary, max_cost_usd_per_request: 10}\n")
    with pytest.raises(ValueError, match="validation_only"):
        dbn.Policy.load(f)


def test_auth_header_and_zero_cost_list_datasets(net):
    assert _client().list_datasets() == ["XNAS.ITCH", "GLBX.MDP3"]
    method, url, _, headers = net.calls[0]
    assert method == "GET" and "metadata.list_datasets" in url
    assert headers["Authorization"] == "Basic " + base64.b64encode(f"{KEY}:".encode()).decode()
    assert KEY not in repr(_client())


def test_dry_run_prints_cost_and_downloads_nothing(net, tmp_path):
    lines: list[str] = []
    r = dbn.fetch(_client(), _q(), root=tmp_path, policy=POLICY, now=NOW, out=lines.append)
    assert r.status == "dry_run" and r.cost_usd == pytest.approx(0.42)
    assert "estimated cost: $0.4200" in lines[0]
    assert net.methods("timeseries.get_range") == []
    assert not (tmp_path / "databento").exists()
    cost_call = next(u for _, u, _, _ in net.calls if "get_cost" in u)
    qs = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(cost_call).query))
    assert qs["schema"] == "ohlcv-1m" and qs["symbols"] == "AAPL" and qs["dataset"] == "XNAS.ITCH"


def test_confirm_checks_cost_first_then_downloads_and_caches(net, tmp_path):
    lines: list[str] = []
    r = dbn.fetch(_client(), _q(), root=tmp_path, policy=POLICY, confirm=True, now=NOW, out=lines.append)
    assert r.status == "downloaded" and r.rows == 2
    endpoints = [u.split("/v0/")[1].split("?")[0] for _, u, _, _ in net.calls]
    assert endpoints == ["metadata.get_cost", "timeseries.get_range"]  # estimate always before the download
    form = net.calls[1][2]
    assert form["encoding"] == "csv" and form["schema"] == "ohlcv-1m" and form["symbols"] == "AAPL"
    t = pq.read_table(r.path)
    assert t.num_rows == 2 and "close" in t.column_names
    assert dbn.spent(tmp_path) == pytest.approx(0.42)
    assert json.loads(r.path.with_suffix(".json").read_text())["cost_usd"] == pytest.approx(0.42)
    assert not list(r.path.parent.glob("*.tmp"))

    # second identical request: served from cache, no network at all, nothing paid twice
    n = len(net.calls)
    r2 = dbn.fetch(_client(), _q(), root=tmp_path, policy=POLICY, confirm=True, now=NOW, out=lines.append)
    assert r2.status == "cached" and r2.cost_usd == 0 and len(net.calls) == n
    assert dbn.spent(tmp_path) == pytest.approx(0.42)


def test_symbol_order_does_not_change_cache_key():
    assert _q(symbols=("AAPL", "MSFT")).key == _q(symbols=("MSFT", "AAPL")).key
    assert _q().key != _q(schema="bbo-1m").key


def test_refuses_above_cap_even_with_confirm(net, tmp_path):
    net.cost = 10.01
    with pytest.raises(dbn.CostRefused, match="above the \\$10.00 cap"):
        dbn.fetch(_client(), _q(), root=tmp_path, policy=POLICY, confirm=True, now=NOW, out=lambda s: None)
    assert net.methods("timeseries.get_range") == []


def test_cli_max_cost_only_lowers_the_cap(net, tmp_path):
    net.cost = 3.0
    with pytest.raises(dbn.CostRefused):
        dbn.fetch(_client(), _q(), root=tmp_path, policy=POLICY, confirm=True, max_cost_usd=2.0, now=NOW,
                  out=lambda s: None)
    net.cost = 11.0
    with pytest.raises(dbn.CostRefused):  # a higher --max-cost cannot raise the configured $10
        dbn.fetch(_client(), _q(), root=tmp_path, policy=POLICY, confirm=True, max_cost_usd=50.0, now=NOW,
                  out=lambda s: None)
    assert net.methods("timeseries.get_range") == []


def test_refuses_past_remaining_credit(net, tmp_path):
    led = dbn.ledger_path(tmp_path)
    led.parent.mkdir(parents=True)
    led.write_text(json.dumps({"key": "x", "cost_usd": 124.8, "at": "t"}) + "\n")
    with pytest.raises(dbn.CostRefused, match="credit"):
        dbn.fetch(_client(), _q(), root=tmp_path, policy=POLICY, confirm=True, now=NOW, out=lambda s: None)
    assert net.methods("timeseries.get_range") == []


def test_refuses_recent_window_and_bad_inputs(net, tmp_path):
    with pytest.raises(ValueError, match="15 minutes"):
        dbn.fetch(_client(), _q(start="2026-11-11T21:00", end="2026-11-11T21:50"), root=tmp_path, policy=POLICY,
                  now=NOW, out=lambda s: None)
    assert net.calls == []
    for bad in ({"schema": "mbo"}, {"symbols": ()}, {"end": "2026-09-30"}, {"dataset": "../x"}):
        with pytest.raises(ValueError):
            _q(**bad)


def test_bad_cost_response_and_http_errors(monkeypatch, tmp_path):
    fake = FakeDatabento(cost=-1)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    with pytest.raises(http.HttpError):
        _client().get_cost(_q())
    monkeypatch.setattr(urllib.request, "urlopen", FakeDatabento(status=401))
    with pytest.raises(http.HttpError) as e:
        _client().list_datasets()
    assert KEY not in str(e.value)


def test_empty_download_still_caches(monkeypatch, tmp_path):
    monkeypatch.setattr(urllib.request, "urlopen", FakeDatabento(csv=b""))
    r = dbn.fetch(_client(), _q(), root=tmp_path, policy=POLICY, confirm=True, now=NOW, out=lambda s: None)
    assert r.rows == 0 and r.path.exists()


def test_engine_never_imports_databento():
    for p in (ROOT / "src" / "kaibot" / "engine").rglob("*.py"):
        assert "databento" not in p.read_text(), p


# --- scripts/fetch_databento.py ---------------------------------------------------------------------------

def _cli():
    spec = importlib.util.spec_from_file_location("fetch_databento", ROOT / "scripts" / "fetch_databento.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CERT_BUNDLE = "/fake/certifi/cacert.pem"


@pytest.fixture(autouse=True)
def cert_env(monkeypatch):
    """The CLI may set SSL_CERT_FILE: start each test without it and restore the real value afterwards."""
    monkeypatch.setenv("SSL_CERT_FILE", "x")
    monkeypatch.delenv("SSL_CERT_FILE")
    monkeypatch.setitem(sys.modules, "certifi", types.SimpleNamespace(where=lambda: CERT_BUNDLE))


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    from kaibot.common import ratelimit
    monkeypatch.setattr(ratelimit, "REGISTRY", ratelimit.Registry(loader=lambda: {"databento": BudgetSpec()}))
    monkeypatch.delenv("DATABENTO_API_KEY", raising=False)
    env = tmp_path / "secrets.env"
    env.write_text(f"DATABENTO_API_KEY={KEY}  # one-time credit\n")
    return env


def test_cli_default_is_dry_run(net, cli_env, tmp_path, capsys):
    rc = _cli().main(["--env", str(cli_env), "--symbols", "aapl", "--start", "2026-10-01", "--end", "2026-10-02",
                      "--data", str(tmp_path / "d")])
    out = capsys.readouterr()
    assert rc == 0 and "estimated cost" in out.out and "dry run" in out.out
    assert KEY not in out.out + out.err
    assert net.methods("timeseries.get_range") == []


def test_cli_confirm_downloads_bbo(net, cli_env, tmp_path, capsys):
    rc = _cli().main(["--env", str(cli_env), "--schema", "bbo-1m", "--symbols", "SPY", "--start", "2026-10-01",
                      "--end", "2026-10-02", "--data", str(tmp_path / "d"), "--confirm"])
    assert rc == 0 and net.methods("timeseries.get_range") == ["POST"]
    assert list((tmp_path / "d" / "databento" / "XNAS.ITCH" / "bbo-1m").glob("*.parquet"))
    assert KEY not in capsys.readouterr().out


def test_cli_refused_exit_2(net, cli_env, tmp_path, capsys):
    net.cost = 99.0
    rc = _cli().main(["--env", str(cli_env), "--symbols", "SPY", "--start", "2026-01-01", "--end", "2026-10-01",
                      "--data", str(tmp_path / "d"), "--confirm"])
    assert rc == 2 and "REFUSED" in capsys.readouterr().err
    assert net.methods("timeseries.get_range") == []


def test_cli_missing_key(net, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("DATABENTO_API_KEY", raising=False)
    rc = _cli().main(["--env", str(tmp_path / "none.env"), "--symbols", "SPY", "--start", "2026-10-01",
                      "--end", "2026-10-02"])
    assert rc == 1 and "DATABENTO_API_KEY" in capsys.readouterr().err and net.calls == []


def test_cli_sets_certifi_bundle_before_any_request(cli_env, tmp_path, monkeypatch):
    m = _cli()
    seen = []

    def fake_fetch(*a, **kw):
        seen.append(os.environ.get("SSL_CERT_FILE"))

    monkeypatch.setattr(m.dbn, "fetch", fake_fetch)
    args = ["--env", str(cli_env), "--symbols", "SPY", "--start", "2026-10-01", "--end", "2026-10-02"]
    assert m.main(args) == 0 and seen == [CERT_BUNDLE]
    monkeypatch.setenv("SSL_CERT_FILE", "/my/own/roots.pem")
    assert m.main(args) == 0 and seen[-1] == "/my/own/roots.pem"


def test_cli_status_0_prints_the_certificate_hint(cli_env, tmp_path, monkeypatch, capsys):
    m = _cli()

    def no_roots(*a, **kw):
        raise http.HttpError(0, "https://hist.databento.com/v0/metadata.get_cost", "giving up (URLError)")

    monkeypatch.setattr(m.dbn, "fetch", no_roots)
    args = ["--env", str(cli_env), "--symbols", "SPY", "--start", "2026-10-01", "--end", "2026-10-02"]
    assert m.main(args) == 1
    err = capsys.readouterr().err
    assert err.count(m.CERT_HINT) == 1 and "pip install certifi" in err and KEY not in err

    def forbidden(*a, **kw):
        raise http.HttpError(403, "https://hist.databento.com/v0/metadata.get_cost", "Forbidden")

    monkeypatch.setattr(m.dbn, "fetch", forbidden)
    assert m.main(args) == 1 and m.CERT_HINT not in capsys.readouterr().err
