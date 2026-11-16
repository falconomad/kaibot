"""scripts/check_ca_feed.py (#266, QA-Ops): asks Alpaca's corporate-actions feed for known past cases the way the
#271 buy guard does and says whether the guard would see them. A fake feed only: no network, no keys."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load():
    spec = importlib.util.spec_from_file_location("check_ca_feed", ROOT / "scripts" / "check_ca_feed.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_ca_feed"] = mod  # dataclasses look their module up
    spec.loader.exec_module(mod)
    return mod


class Feed:
    """GET /v1/corporate-actions: `rows` maps the symbol asked for to that answer's lists."""

    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def __call__(self, path, params):
        self.calls.append((path, dict(params)))
        assert path == "/v1/corporate-actions"
        return {"corporate_actions": self.rows.get(params["symbols"], {}), "next_page_token": None}


def by_parent():
    """The feed as the guard hopes: each action under the guard's symbol and field."""
    return {
        "HON": {"spin_offs": [{"source_symbol": "HON", "new_symbol": "SOLS", "ex_date": "2025-10-30"},
                              {"source_symbol": "HON", "new_symbol": "HONA", "ex_date": "2026-06-29"}],
                "reverse_splits": [{"symbol": "HON", "ex_date": "2026-06-29"}]},
        "DD": {"spin_offs": [{"source_symbol": "DD", "new_symbol": "Q", "ex_date": "2025-11-03"}]},
        "CTVA": {"spin_offs": [{"source_symbol": "CTVA", "new_symbol": "XYZ", "ex_date": "2026-10-01"}]},
        "JNPR": {"cash_mergers": [{"acquirer_symbol": "HPE", "acquiree_symbol": "JNPR",
                                   "effective_date": "2025-07-02", "rate": 40.0}]},
        "CHK": {"name_changes": [{"old_symbol": "CHK", "new_symbol": "EXE", "process_date": "2024-10-02"}]},
    }


def test_every_case_seen_by_parent_says_yes_and_exits_0(capsys):
    mod = load()
    feed = Feed(by_parent())
    assert mod.main([], get=feed) == 0
    out = capsys.readouterr().out
    assert "guard sees spin-offs by parent: yes (4 of 4 cases)" in out
    assert "guard sees cash mergers by the acquired company: yes" in out
    assert "guard sees symbol changes by the old symbol: yes" in out
    assert "guard sees it: yes, as spin_offs.source_symbol" in out
    asked = [p["symbols"] for _, p in feed.calls]
    assert asked[:2] == ["HON", "SOLS"] and {"JNPR", "HPE", "CHK", "EXE"} <= set(asked)
    for _, p in feed.calls:  # the guard's request shape: every guarded type, a short window around the event
        assert "spin_off" in p["types"] and "cash_merger" in p["types"] and "name_change" in p["types"]


def test_a_spin_off_filed_under_the_new_company_says_no_and_where(capsys):
    mod = load()
    rows = by_parent()
    rows["SOLS"] = {"spin_offs": [{"source_symbol": "HON", "new_symbol": "SOLS", "ex_date": "2025-10-30"}]}
    rows["HON"] = {"spin_offs": []}  # the filter by HON returns nothing for either HON case
    assert mod.main([], get=Feed(rows)) == 3
    out = capsys.readouterr().out
    assert "guard sees spin-offs by parent: no (2 of 4 cases)" in out
    assert "asked by SOLS: 1 row(s); found as spin_offs.source_symbol, spin_offs.new_symbol (SOLS)" in out
    assert "guard sees it: NO" in out


def test_a_parent_named_only_in_another_field_is_not_what_the_guard_reads(capsys):
    mod = load()
    rows = by_parent()
    rows["DD"] = {"spin_offs": [{"parent": "DD", "new_symbol": "Q", "ex_date": "2025-11-03"}]}  # not source_symbol
    assert mod.main([], get=Feed(rows)) == 3
    out = capsys.readouterr().out
    assert "DD appears as spin_offs.parent" in out and "guard sees spin-offs by parent: no (3 of 4 cases)" in out


def test_an_unreadable_feed_exits_1(capsys):
    mod = load()

    def broken(path, params):
        raise RuntimeError("HTTP 403 for https://data.alpaca.markets/v1/corporate-actions: forbidden")

    assert mod.main([], get=broken) == 1
    assert "could not be read" in capsys.readouterr().out


def test_the_keys_come_from_the_env_file_and_are_never_printed(tmp_path, monkeypatch, capsys):
    mod = load()
    for k in ("ALPACA_API_KEY", "ALPACA_SECRET", "ALPACA_API_SECRET", "ALPACA_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    env = tmp_path / ".env"
    env.write_text("ALPACA_API_KEY=PKSENTINELKEY\nALPACA_SECRET=SENTINELSECRET\n")
    seen = {}

    class Data:
        def __init__(self, key, secret):
            seen["keys"] = (key, secret)

        def _d(self, path, params):
            return Feed(by_parent())(path, params)

    monkeypatch.setattr("kaibot.engine.alpaca.AlpacaData", Data)
    assert mod.main(["--env", str(env)]) == 0
    out = capsys.readouterr().out
    assert seen["keys"] == ("PKSENTINELKEY", "SENTINELSECRET")
    assert "SENTINEL" not in out


@pytest.mark.parametrize("env,code", [("", 2), ("ALPACA_API_KEY=k\nALPACA_SECRET=s\n"
                                               "ALPACA_BASE_URL=https://api.alpaca.markets\n", 2)])
def test_missing_keys_or_a_live_url_refuse(tmp_path, monkeypatch, capsys, env, code):
    mod = load()
    for k in ("ALPACA_API_KEY", "ALPACA_SECRET", "ALPACA_API_SECRET", "ALPACA_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    f = tmp_path / ".env"
    f.write_text(env)
    assert mod.main(["--env", str(f)]) == code


def test_the_data_client_reads_only_the_market_data_host():
    from kaibot.engine import alpaca

    assert alpaca.DATA_URL == "https://data.alpaca.markets"
