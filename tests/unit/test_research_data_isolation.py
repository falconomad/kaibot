"""The point-in-time research stores (kaibot.data, kaibot.ingest.sec_fundamentals) are not wired into trading yet:
no trading package may import them. Wiring one in is a separate, reviewed story."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "kaibot"
TRADING = ["engine", "features", "strategies", "risk", "broker", "agents", "replay", "news"]
FORBIDDEN = ("kaibot.data", "kaibot.ingest.sec_fundamentals")


def _imports(path: Path) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            out |= {f"{node.module}.{a.name}" for a in node.names}
    return out


def test_trading_code_never_imports_the_research_stores():
    offenders = []
    for pkg in TRADING:
        for f in (SRC / pkg).rglob("*.py"):
            imps = _imports(f)
            if any(m == x or m.startswith(x + ".") for m in imps for x in FORBIDDEN):
                offenders.append(str(f.relative_to(SRC)))
    assert offenders == []
