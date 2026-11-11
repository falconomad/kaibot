"""S19 acceptance: the engine never reads alt data. No trading package may import kaibot.ingest.alt, except the
one audited door kaibot/features/alt.py (tech design §1: "unreachable from features, except pump_risk"), and no
trading module may import that door (the live pump_risk veto lives in features/pump.py and reads no alt data)."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "kaibot"
TRADING = ["engine", "features", "strategies", "risk", "broker", "agents", "replay"]
DOOR = Path("features") / "alt.py"


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
            if f.relative_to(SRC) == DOOR:
                continue
            text = f.read_text()
            imps = _imports(f)
            if (any(m.startswith("kaibot.ingest.alt") or m.startswith("kaibot.features.alt") for m in imps)
                    or "data/alt" in text or '"alt"' in text):
                offenders.append(str(f.relative_to(SRC)))
    assert offenders == []


def test_the_door_exists_and_the_veto_does_not_need_it():
    assert any(m.startswith("kaibot.ingest.alt") for m in _imports(SRC / DOOR))
    pump = _imports(SRC / "features" / "pump.py")
    assert not any("alt" in m.split(".") for m in pump)
