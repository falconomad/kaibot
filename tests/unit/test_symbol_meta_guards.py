"""Two guards on scripts/fetch_symbol_meta.py that its own tests leave open (#383, from QA-Ops's key on #364).

1. A plain run (no flag) never asks AllInvestView. tests/test_fetch_symbol_meta.py only checks that the flag parses
   to False; nothing ran `main()`, so a `main()` that searched on every run passed.
2. `search_logo` draws from the `allinvestview_logo_search` budget. The existing test hands it a budget, so the
   source name it gives to `get_json` was never looked at: another source's name passed.

No network: `main()` runs against a temp tree with a fake `urlopen`, and the budget registry is a recording one.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from kaibot.common import http, ratelimit

ROOT = Path(__file__).resolve().parents[2]
AIV = ROOT / "tests" / "fixtures" / "allinvestview"
SEARCH_SOURCE = "allinvestview_logo_search"  # spelled out: a renamed constant must not carry the test with it
SEARCH_HOST = "www.allinvestview.com"


@pytest.fixture
def mod():
    """A fresh copy of the script per test, so nothing patched on it can leak."""
    spec = importlib.util.spec_from_file_location("fetch_symbol_meta_guards", ROOT / "scripts" / "fetch_symbol_meta.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Body:
    def __init__(self, body: bytes):
        self.status, self.headers, self._body = 200, {"Content-Type": "application/json"}, body

    def read(self, n: int = -1) -> bytes:
        body, self._body = self._body, b""
        return body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# --- 1. a plain run does not search ---------------------------------------------------------------------------------


class _Run:
    """What one `main()` did: every URL opened, every search asked for, and the file it wrote."""

    def __init__(self) -> None:
        self.urls: list[str] = []
        self.searched: list[str] = []
        self.json_gets: list[str] = []
        self.dest: Path | None = None

    def hosts(self) -> set[str]:
        return {urllib.parse.urlsplit(u).hostname for u in self.urls}


def _run_main(mod, monkeypatch, tmp_path: Path, argv: list[str]) -> _Run:
    """`main()` on a one-stock universe in a temp tree. Finnhub is a fake; any search is recorded at three levels:
    `search_logo`, `get_json` and `urlopen`, so a search that goes around one of them still shows."""
    run = _Run()
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "universe_seed.yaml").write_text("sectors:\n  media: [DIS]\n")
    run.dest = tmp_path / "apps" / "web" / "src" / "data" / "symbols.json"
    run.dest.parent.mkdir(parents=True)
    # every ETF already has a domain, DIS has none: a run that searches asks exactly once (and so never sleeps)
    rows = {e: {"name": n, "logo": None, "industry": "ETF", "domain": "ssga.com"} for e, n in mod.ETF_NAMES.items()}
    rows["DIS"] = {"name": "Walt Disney Co", "logo": None, "industry": "Media", "domain": None}
    run.dest.write_text(json.dumps(rows, indent=1) + "\n")

    def urlopen(req, timeout=None, **kw):
        url = req if isinstance(req, str) else req.full_url
        run.urls.append(url)
        if urllib.parse.urlsplit(url).hostname == "finnhub.io":
            return _Body(json.dumps({"name": "Walt Disney Co", "logo": "https://img/DIS.png",
                                     "finnhubIndustry": "Media"}).encode())
        return _Body((AIV / "DIS.json").read_bytes())

    def search_logo(symbol, **kw):
        run.searched.append(symbol)
        return json.loads((AIV / f"{symbol}.json").read_text())

    def get_json(url, **kw):
        run.json_gets.append(url)
        return json.loads((AIV / "DIS.json").read_text())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(mod, "search_logo", search_logo)
    monkeypatch.setattr(mod, "get_json", get_json)
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "time", types.SimpleNamespace(sleep=lambda s: None))  # Finnhub's 1.1 s pause
    monkeypatch.setenv("FINNHUB_API_KEY", "not-a-key")
    monkeypatch.setattr(sys, "argv", ["fetch_symbol_meta.py", *argv])
    mod.main()
    return run


def test_a_plain_run_never_asks_allinvestview(mod, monkeypatch, tmp_path, capsys):
    run = _run_main(mod, monkeypatch, tmp_path, [])
    assert run.searched == [] and run.json_gets == []
    assert run.hosts() == {"finnhub.io"}  # one Finnhub call for DIS and nothing else leaves the machine
    out = json.loads(run.dest.read_text())
    assert out["DIS"] == {"name": "Walt Disney Co", "logo": "https://img/DIS.png", "industry": "Media", "domain": None}
    assert "13 symbols, 1 with logos, 12 with a domain" in capsys.readouterr().out  # the 12 ETFs, not DIS


def test_the_same_run_with_the_flag_does_ask(mod, monkeypatch, tmp_path):
    """The control for the test above: with --fill-domains the same harness sees the search, so its silence on a
    plain run means something."""
    run = _run_main(mod, monkeypatch, tmp_path, ["--fill-domains"])
    assert run.searched == ["DIS"]
    assert json.loads(run.dest.read_text())["DIS"]["domain"] == "thewaltdisneycompany.com"


# --- 2. search_logo uses its own budget -----------------------------------------------------------------------------


class _Clock:
    def monotonic(self) -> float:
        return 0.0

    def sleep(self, seconds: float) -> None:
        pass


class _SpyBudget(ratelimit.Budget):
    def __init__(self, name: str, spec: ratelimit.BudgetSpec, log: list[str]):
        super().__init__(name, spec, _Clock())
        self._log = log

    def acquire(self) -> float:
        self._log.append(self.name)
        return super().acquire()


class _SpyRegistry:
    """Stands in for ratelimit.REGISTRY: the real specs of config/sources.yaml, and a note of every budget asked
    for and every request drawn from it. An unknown source is a KeyError, as in the real one."""

    def __init__(self) -> None:
        self.specs = ratelimit.load_specs(ROOT / "config" / "sources.yaml")
        self.asked: list[str] = []
        self.drawn: list[str] = []
        self._budgets: dict[str, _SpyBudget] = {}

    def get(self, source: str) -> _SpyBudget:
        self.asked.append(source)
        if source not in self._budgets:
            self._budgets[source] = _SpyBudget(source, self.specs[source], self.drawn)
        return self._budgets[source]


def test_search_logo_draws_from_the_allinvestview_budget_and_no_other(mod, monkeypatch):
    reg = _SpyRegistry()
    monkeypatch.setattr(ratelimit, "REGISTRY", reg)  # every get_budget() goes here, whoever imported it
    assert http.get_budget is ratelimit.get_budget
    seen: list[str] = []

    def opener(req, timeout):
        seen.append(req.full_url)
        return _Body((AIV / "AAPL.json").read_bytes())

    got = mod.search_logo("AAPL", opener=opener)  # no budget handed in: the script's own choice of source decides
    assert got == json.loads((AIV / "AAPL.json").read_text())
    assert seen == [f"https://{SEARCH_HOST}/api/logo-search/?q=AAPL"]
    assert reg.drawn == [SEARCH_SOURCE]  # one request, one slot, from this budget
    assert set(reg.asked) == {SEARCH_SOURCE}  # no other source's budget was even looked up
    assert not reg.specs[SEARCH_SOURCE].unlimited  # and it is a real limit in config/sources.yaml


def test_a_search_made_by_fill_domains_by_default_is_under_that_budget_too(mod, monkeypatch):
    """`fill_domains` without a `search` uses `search_logo`: the path `--fill-domains` takes in a real run."""
    reg = _SpyRegistry()
    monkeypatch.setattr(ratelimit, "REGISTRY", reg)
    seen: list[str] = []

    def urlopen(req, timeout=None, **kw):
        seen.append(req.full_url)
        return _Body((AIV / "SPY.json").read_bytes())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    rows = {"SPY": {"name": "S&P 500 ETF", "logo": None, "industry": "ETF", "domain": None}}
    assert mod.fill_domains(rows, sleep=lambda s: None) == {"SPY": "ssga.com"}
    assert [urllib.parse.urlsplit(u).hostname for u in seen] == [SEARCH_HOST]
    assert reg.drawn == [SEARCH_SOURCE] and set(reg.asked) == {SEARCH_SOURCE}
