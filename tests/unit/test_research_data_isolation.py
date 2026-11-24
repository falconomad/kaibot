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


def _resolved_imports(path: Path) -> set[str]:
    """Like _imports, but relative imports (`from ..data import x`) are resolved against the file's package."""
    pkg = ["kaibot", *path.relative_to(SRC).parent.parts]
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.level:
            base = pkg[: len(pkg) - (node.level - 1)] if node.level > 1 else pkg
            mod = ".".join([*base, *([node.module] if node.module else [])])
            out.add(mod)
            out |= {f"{mod}.{a.name}" for a in node.names}
    return out


def test_no_relative_import_of_the_research_stores():  # QA-Ops (#622): `from ..data import fundamentals`
    offenders = []
    for pkg in TRADING:
        for f in (SRC / pkg).rglob("*.py"):
            imps = _resolved_imports(f)
            if any(m == x or m.startswith(x + ".") for m in imps for x in FORBIDDEN):
                offenders.append(str(f.relative_to(SRC)))
    assert offenders == []


def test_importing_every_trading_module_loads_no_research_store():  # QA-Ops (#622): transitive imports too
    import subprocess
    import sys

    code = (
        "import importlib, pkgutil, sys\n"
        f"for p in {TRADING!r}:\n"
        "    m = importlib.import_module('kaibot.' + p)\n"
        "    for i in pkgutil.walk_packages(m.__path__, m.__name__ + '.'):\n"
        "        if not i.name.endswith('__main__'):\n"
        "            importlib.import_module(i.name)\n"
        f"bad = sorted(n for n in sys.modules if any(n == x or n.startswith(x + '.') for x in {FORBIDDEN!r}))\n"
        "print(','.join(bad))\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300,
                       env={**__import__("os").environ, "PYTHONPATH": str(SRC.parent)})
    assert r.returncode == 0, r.stderr[-2000:]
    assert r.stdout.strip() == ""
