"""The newest `kaibot.config_check` across all migration files keeps both checks production relies on.

Production has 0014's config_check (custom tickers, `config_custom_check`). 0016 moves the strategy names into
`kaibot.config_check_strategies`. Any later migration that redefines config_check must start from the newest
version, or applying it silently drops one of the two (QA-Ops review of #156, point 5)."""

from __future__ import annotations

import re

from kaibot.engine.config import ROOT

DEF = re.compile(r"create or replace function kaibot\.config_check\s*\(", re.IGNORECASE)


def newest_config_check() -> tuple[str, str]:
    """(file name, body) of the last `create or replace function kaibot.config_check(` in migration file order."""
    found: tuple[str, str] | None = None
    for f in sorted((ROOT / "migrations").glob("*.sql")):
        text = f.read_text()
        for m in DEF.finditer(text):
            start = text.index("$$", m.end())
            end = text.index("$$", start + 2)
            found = (f.name, text[m.start() : end + 2])
    assert found is not None, "no migration defines kaibot.config_check"
    return found


def _code(body: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in body.splitlines())


def test_newest_config_check_keeps_custom_tickers_and_strategy_registry():
    name, body = newest_config_check()
    code = _code(body)
    assert "kaibot.config_check_strategies(" in code, f"{name}: config_check must call config_check_strategies"
    assert "focus.custom" in code and "'custom'" in code, f"{name}: config_check lost the focus.custom check (0014)"
    assert "spy_late_momentum" not in body, f"{name}: config_check hard-codes a strategy name"


def test_newest_config_check_keeps_custom_ticker_gate_in_rpcs():
    """config_custom_check is called next to config_check by the save/apply RPCs; their newest versions keep it."""
    for fn in ("public.kb_save_config", "public.kb_apply_config"):
        found = None
        for f in sorted((ROOT / "migrations").glob("*.sql")):
            text = f.read_text()
            for m in re.finditer(rf"create or replace function {re.escape(fn)}\s*\(", text):
                start = text.index("$$", m.end())
                found = (f.name, text[m.start() : text.index("$$", start + 2)])
        assert found is not None
        assert "kaibot.config_custom_check(" in _code(found[1]), f"{found[0]}: {fn} must call config_custom_check"


def test_config_check_strategies_is_defined_no_later_than_its_first_caller():
    files = sorted((ROOT / "migrations").glob("*.sql"))
    define = [f.name for f in files if "function kaibot.config_check_strategies(" in f.read_text()]
    callers = [f.name for f in files if re.search(r":= kaibot\.config_check_strategies\(", f.read_text())]
    assert define and callers and define[0] <= callers[0]
