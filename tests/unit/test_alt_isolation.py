"""S19 acceptance: the engine never reads alt data. No trading package may import kaibot.ingest.alt."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "kaibot"
TRADING = ["engine", "features", "strategies", "risk", "broker", "agents", "replay"]


def _imports(path: Path) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            out |= {f"{node.module}.{a.name}" for a in node.names}
    return out


def test_trading_code_never_imports_alt_data():
    offenders = []
    for pkg in TRADING:
        for f in (SRC / pkg).rglob("*.py"):
            text = f.read_text()
            if any(m.startswith("kaibot.ingest.alt") for m in _imports(f)) or "data/alt" in text or '"alt"' in text:
                offenders.append(str(f.relative_to(SRC)))
    assert offenders == []
