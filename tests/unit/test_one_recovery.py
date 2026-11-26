"""M10 1.20 (#445), step 3: one start-up recovery routine, `Engine.recover`.

The engine had three start-up recoveries, called one by one from `startup`: the flatten sales (`_rebuild_md_exits`),
the intraday path's orphaned entries (`_recover_intraday`) and the daily path's order manager (`LiveDaily.startup`,
which recovers the manager's orders through `ProtectiveStops.startup` and `OrderManager.recover`), with the waiting
flatten command (`_restore_cmd_waiting`) between them. They now run behind `Engine.recover`, in the same order, and
a search of the package finds each called from one place only. The step is behaviour-neutral: the DST seeds' traces
are identical to main (the PR says how that was checked)."""

from __future__ import annotations

import ast
from pathlib import Path

from kaibot.engine.core import Engine

SRC = Path(__file__).resolve().parents[2] / "src" / "kaibot"

# the (file, enclosing function) of every call of each recovery part
PARTS = {
    "_rebuild_md_exits": {("engine/core.py", "recover")},
    "_restore_cmd_waiting": {("engine/core.py", "recover")},
    "_recover_intraday": {("engine/core.py", "recover")},
    "_adopt_orphans": {("engine/intraday_legacy.py", "_recover_intraday")},
    "recover": {("engine/core.py", "startup"), ("engine/protect.py", "startup")},  # the engine's; the manager's
}


def _calls(name: str) -> set[tuple[str, str]]:
    out = set()
    for p in sorted(SRC.rglob("*.py")):
        rel = p.relative_to(SRC).as_posix()
        tree = ast.parse(p.read_text())
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == name:
                    out.add((rel, fn.name))
    return out


def test_each_recovery_part_has_one_caller():
    found = {name: _calls(name) for name in PARTS}
    assert found == PARTS


def test_the_daily_paths_start_up_runs_only_from_recover():
    """`self.daily.startup()` (inside the `_daily_step` lambda) is called from `Engine.recover` and nowhere else."""
    sites = set()
    for p in sorted(SRC.rglob("*.py")):
        rel = p.relative_to(SRC).as_posix()
        for fn in ast.walk(ast.parse(p.read_text())):
            if isinstance(fn, ast.FunctionDef):
                for node in ast.walk(fn):
                    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                            and node.func.attr == "startup" and ast.unparse(node.func.value).endswith("daily")):
                        sites.add((rel, fn.name))
    assert sites == {("engine/core.py", "recover")}


def test_recover_runs_its_parts_in_order_and_startup_runs_it_once(monkeypatch):
    calls = []
    for name in ("_rebuild_md_exits", "_restore_cmd_waiting", "_recover_intraday"):
        # (#694: the book's engine has no `_recover_intraday`; only engine/intraday_legacy.IntradayEngine defines it)
        monkeypatch.setattr(Engine, name, lambda self, n=name: calls.append(n), raising=False)
    monkeypatch.setattr(Engine, "_daily_step", lambda self, step, call: calls.append(f"daily:{step}"))

    class Bare:
        pass

    eng = Bare()
    for name in ("_rebuild_md_exits", "_restore_cmd_waiting", "_recover_intraday", "_daily_step", "recover"):
        setattr(eng, name, getattr(Engine, name).__get__(eng))
    eng.intraday = True  # the intraday engine: its orphaned entries are recovered third
    eng.recover()
    assert calls == ["_rebuild_md_exits", "_restore_cmd_waiting", "_recover_intraday", "daily:startup"]
    calls.clear()
    eng.intraday = False  # the book's engine (the live process): the intraday part is not called
    eng.recover()
    assert calls == ["_rebuild_md_exits", "_restore_cmd_waiting", "daily:startup"]

    # in `startup`: after the ledger and the daily-path check, before the account guard and the reconciliation
    order = []
    for name in ("_restore_positions", "_attach_ledger", "_require_daily_path", "recover", "check_account"):
        setattr(eng, name, lambda n=name: order.append(n))
    eng._startup_reconcile = lambda why: order.append("reconcile")
    eng.store = type("S", (), {"active_run": lambda self: None, "event": lambda self, *a: order.append("event")})()
    eng.code = eng.fingerprint = None
    eng.pos = {}
    eng._rid = lambda: None
    Engine.startup(eng)
    assert order == ["_restore_positions", "_attach_ledger", "_require_daily_path", "recover", "check_account",
                     "reconcile", "event"]
