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
