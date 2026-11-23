"""M10 1.20 (#445), step 1: the engine reaches the venue's order calls through one module, `engine/order_path.py`.

A search of the package for a broker write (submit, the bracket/OTO/MOC submits, close_position, replace, cancel)
finds it only in `order_path.py`; the exceptions are named below with their reason. A new direct call fails here.
"""

from __future__ import annotations

import ast
from pathlib import Path

from kaibot.engine import order_path

SRC = Path(__file__).resolve().parents[2] / "src" / "kaibot"
WRITES = {"submit", "submit_bracket", "submit_oto_stop", "submit_moc_sell", "close_position", "replace",
          "replace_stop", "replace_limit", "cancel", "cancel_order"}
EXEMPT = {
    "engine/order_path.py": "the path itself",
    "engine/fence.py": "the lease fence wraps the broker: it IS a broker to the engine and calls the inner one",
    "engine/watchdog.py": "a separate process with its own fail-safe orders (its key is #585's)",
}


def _broker_writes(path: Path) -> list[tuple[int, str]]:
    out = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in WRITES:
            owner = ast.unparse(node.func.value)
            if owner == "broker" or owner.endswith(".broker") or owner.endswith("._broker"):
                out.append((node.lineno, f"{owner}.{node.func.attr}"))
    return out


def test_one_call_site_per_broker_write():
    found = {}
    for p in sorted(SRC.rglob("*.py")):
        rel = p.relative_to(SRC).as_posix()
        if rel.startswith("broker/") or rel in EXEMPT:
            continue
        if calls := _broker_writes(p):
            found[rel] = calls
    assert found == {}, f"send through kaibot.engine.order_path, not straight to the broker: {found}"
    sites = _broker_writes(SRC / "engine" / "order_path.py")
    assert sorted(name.split(".")[-1] for _, name in sites) == sorted(WRITES)  # one site each


def test_the_path_passes_every_call_through_unchanged():
    calls = []

    class Spy:
        def __getattr__(self, name):
            return lambda *a, **kw: calls.append((name, a, kw)) or {"id": name}

    b = Spy()
    assert order_path.submit(b, "req") == {"id": "submit"}
    order_path.submit_bracket(b, "c", "A", 3, 9.0, 11.0)
    order_path.submit_oto_stop(b, "c", "A", 3, 9.0)
    order_path.submit_moc_sell(b, "c", "A", 3)
    order_path.close_position(b, "A")
    order_path.replace(b, "o1", stop_price=9.5, client_order_id="c.r1")
    order_path.replace_stop(b, "o1", 9.5)
    order_path.replace_limit(b, "o1", 10.5)
    order_path.cancel(b, "o1")
    order_path.cancel_order(b, "o1")
    assert calls == [("submit", ("req",), {}), ("submit_bracket", ("c", "A", 3, 9.0, 11.0), {}),
                     ("submit_oto_stop", ("c", "A", 3, 9.0), {}), ("submit_moc_sell", ("c", "A", 3), {}),
                     ("close_position", ("A",), {}),
                     ("replace", ("o1",), {"stop_price": 9.5, "client_order_id": "c.r1"}),
                     ("replace_stop", ("o1", 9.5), {}), ("replace_limit", ("o1", 10.5), {}), ("cancel", ("o1",), {}),
                     ("cancel_order", ("o1",), {})]
