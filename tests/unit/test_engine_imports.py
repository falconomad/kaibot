"""The engine must start with nothing but requirements-engine.txt (the Dockerfile installs only that file).

Found while working on issue #126: `evaluation/metrics.py` imported scipy, the engine imports it through
`gate_report`, and scipy is not in requirements-engine.txt. A clean image therefore could not start the engine
at all, let alone read the trial registry. This test blocks every package the image does not install and
imports the engine's entry points in a fresh interpreter."""

from __future__ import annotations

import re
import subprocess
import sys

from kaibot.engine.config import ROOT

# Packages of pyproject.toml (and of the research and dev lists) that requirements-engine.txt does not install.
NOT_IN_THE_IMAGE = ["scipy", "datasketch", "duckdb", "sqlalchemy", "psycopg", "alembic", "apscheduler", "structlog",
                    "tenacity", "feedparser", "fastapi", "uvicorn", "pydantic_settings", "websockets", "hypothesis",
                    "pytest", "respx", "freezegun"]
MODULES = ["kaibot.engine.__main__", "kaibot.engine.watchdog", "kaibot.evaluation.gate_report",
           "kaibot.evaluation.trials", "kaibot.evaluation.fingerprint", "kaibot.evaluation.metrics"]
PROGRAM = """
import importlib, importlib.abc, sys
BLOCKED = {blocked!r}
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ModuleNotFoundError(name + " is not installed in the engine image", name=name)
        return None
sys.meta_path.insert(0, Block())
for m in {modules!r}:
    importlib.import_module(m)
from kaibot.engine import core
assert ";core=" in core._fingerprint(), core._fingerprint()
print("ok")
"""


def test_engine_starts_with_engine_requirements_only():
    code = PROGRAM.format(blocked=set(NOT_IN_THE_IMAGE), modules=MODULES)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
                       env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0 and r.stdout.strip() == "ok", r.stderr[-2000:]


def test_the_block_list_is_what_the_image_lacks():
    """If a package moves into requirements-engine.txt it must leave the list above (and the reverse)."""
    names = {re.split(r"[<>=\[ ]", x.strip())[0].lower().replace("-", "_")
             for x in (ROOT / "requirements-engine.txt").read_text().splitlines()
             if x.strip() and not x.lstrip().startswith("#")}
    assert not names & set(NOT_IN_THE_IMAGE)
    assert {"numpy", "pandas", "pyyaml"} <= names and "scipy" not in names


def test_evaluation_code_does_not_import_scipy():
    for f in sorted((ROOT / "src" / "kaibot" / "evaluation").glob("*.py")):
        assert not re.search(r"^\s*(from|import)\s+scipy", f.read_text(), re.M), f.name
