"""The two static checks of the test-writing agent (M12 3.8, #470). Defence in depth: the sandbox is the boundary.

`secret_findings(text)`: anything in a prompt that looks like a credential. The prompt is built from source files
only and never from the environment; a prompt with a finding is not sent (the run stops, nothing is spent).

`check_code(code, test_name, module)`: the model's proposed test, read as a syntax tree and never imported or run
here. A proposal is refused when it:
- does not parse, is too long, or defines anything at top level but imports, constants, `_helpers` and exactly one
  test function named `test_name`;
- imports outside the allow-list: the standard library's pure modules, `hypothesis`, `pytest`, and of Kaibot only
  the module under test and the Kaibot modules it imports itself (`allowed_kaibot`), never one that talks to the
  outside world (gateway, broker, store, settings, the engine's entry point). This is a check of NAMES; the
  sandbox then resolves every imported name and attribute chain to the module that really defines it
  (`import_spec`, QA on #470: a re-exported `Path` is pathlib's) before the test runs;
- calls or names `open`, `exec`, `eval`, `compile`, `__import__`, `getattr`/`setattr`/`delattr`, `globals` and the
  like, or reads any dunder attribute (`__class__`, `__globals__`, ...);
- assigns to an attribute of an imported module (a monkeypatch of the code under test: the agent may only test
  money-path code, never change it);
- takes a pytest fixture: the test's parameters must all come from its `@given(...)`.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
MAX_CODE_CHARS = 6000
MAX_CODE_LINES = 160

ALLOWED_IMPORT_ROOTS = frozenset({
    "hypothesis", "pytest", "kaibot", "math", "decimal", "fractions", "datetime", "itertools", "functools",
    "collections", "dataclasses", "typing", "enum", "json", "random", "string", "re", "copy", "operator",
    "statistics", "zoneinfo", "__future__",
})
# Kaibot modules that reach a network, a credential or the process environment: never imported by a proposal
DENIED_KAIBOT = ("kaibot.agents.gateway", "kaibot.agents.testwriter", "kaibot.broker", "kaibot.engine.alpaca",
                 "kaibot.engine.store", "kaibot.engine.config", "kaibot.engine.__main__", "kaibot.ingest",
                 "kaibot.engine.runner")
FORBIDDEN_NAMES = frozenset({
    "open", "exec", "eval", "compile", "__import__", "getattr", "setattr", "delattr", "globals", "locals", "vars",
    "breakpoint", "input", "exit", "quit", "help", "memoryview", "__builtins__", "__loader__", "__spec__",
})

# Credential shapes. Generic on purpose: a false alarm stops one run; a missed key would leave the machine.
SECRET_PATTERNS = (
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}")),
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9]{32,}")),
    ("aws_key_id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("alpaca_key_id", re.compile(r"\b[PA]K[0-9A-Z]{18}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghs|ghu|github_pat)_[A-Za-z0-9_]{20,}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("supabase_secret", re.compile(r"\bsb_secret_[A-Za-z0-9_\-]{10,}")),
    ("assigned_secret", re.compile(
        r"(?i)\b(?:api[_-]?key|secret[_-]?key|service[_-]?key|password|token)\b\s*[:=]\s*['\"][^'\"\s]{16,}['\"]")),
)


def secret_findings(text: str, env_values: tuple[str, ...] = ()) -> list[str]:
    """Names of the credential shapes found in `text` (never the matched text itself). `env_values`: values the
    caller knows are secret (for example the process's own key variables): any of them inside `text` is a finding."""
    out = [name for name, rx in SECRET_PATTERNS if rx.search(text)]
    if any(v and len(v) >= 12 and v in text for v in env_values):
        out.append("environment_value")
    return out


def secret_env_values(env) -> tuple[str, ...]:
    """The values of environment variables whose NAME says they are secret. Only compared, never logged or sent."""
    rx = re.compile(r"(?i)(KEY|SECRET|TOKEN|PASSWORD|PASS|CREDENTIAL)")
    return tuple(str(v) for k, v in env.items() if rx.search(str(k)) and v)


def _root(name: str) -> str:
    return name.split(".", 1)[0]


def _kaibot_denied(name: str) -> bool:
    return any(name == d or name.startswith(d + ".") for d in DENIED_KAIBOT)


def _allowed_top(node: ast.stmt) -> bool:
    """A `_helper` function, a constant (`NAME = ...`) or a docstring."""
    if isinstance(node, ast.FunctionDef):
        return node.name.startswith("_")
    if isinstance(node, ast.Assign):
        return all(isinstance(t, ast.Name) for t in node.targets)
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


def _given_params(fn: ast.FunctionDef) -> tuple[set[str], int] | None:
    for d in fn.decorator_list:
        call = d if isinstance(d, ast.Call) else None
        f = call.func if call is not None else d
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
        if name == "given" and call is not None:
            return {k.arg for k in call.keywords if k.arg}, len(call.args)
    return None


def _src_of(module: str, root: Path) -> Path:
    return root / "src" / Path(*module.split(".")).with_suffix(".py")


def allowed_kaibot(module: str | None, root: Path | None = None) -> list[str]:
    """The Kaibot modules a proposed test of `module` may import: the module itself and the Kaibot modules its own
    source imports (read from `root`'s copy), never a denied one. Empty without a module."""
    if not module or _root(module) != "kaibot" or _kaibot_denied(module):
        return []
    out = {module}
    src = _src_of(module, root or ROOT)
    try:
        tree = ast.parse(src.read_text())
    except (OSError, SyntaxError, ValueError):
        return sorted(out)
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and not node.level and (node.module or "").startswith("kaibot"):
            names = [node.module]
            names += [f"{node.module}.{a.name}" for a in node.names
                      if _src_of(f"{node.module}.{a.name}", root or ROOT).exists()]
        out |= {n for n in names if _root(n) == "kaibot" and n != "kaibot" and not _kaibot_denied(n)}
    return sorted(out)


def _kaibot_allowed(name: str, allowed: list[str]) -> bool:
    return any(name == k or name.startswith(k + ".") for k in allowed)


def _chains(tree: ast.AST, bound: set[str]) -> list[list[str]]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            parts, n = [], node
            while isinstance(n, ast.Attribute):
                parts.append(n.attr)
                n = n.value
            if isinstance(n, ast.Name) and n.id in bound:
                out.append([n.id, *reversed(parts)])
    return [list(c) for c in sorted({tuple(c) for c in out})]


def import_spec(code: str, module: str | None, root: Path | None = None) -> dict:
    """What the sandbox resolves before the test runs: each import (module, and the names it binds), each attribute
    chain read off an imported name, the allowed Kaibot modules and the allowed top-level packages. Raises
    SyntaxError when the code does not parse."""
    tree = ast.parse(code)
    imports, bound = [], set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                bind = a.asname or a.name.split(".")[0]
                imports.append({"module": a.name, "names": None, "bind": bind, "aliased": bool(a.asname)})
                bound.add(bind)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            names = [[a.name, a.asname or a.name] for a in node.names]
            imports.append({"module": node.module, "names": names})
            bound |= {x[1] for x in names}
    return {"imports": imports, "chains": _chains(tree, bound), "kaibot": allowed_kaibot(module, root),
            "roots": sorted((ALLOWED_IMPORT_ROOTS - {"kaibot"}) | {"_pytest"})}


def check_code(code: str, test_name: str, module: str | None = None, root: Path | None = None) -> list[str]:
    """Every reason to refuse the proposal (empty: it may go to the sandbox)."""
    allowed = allowed_kaibot(module, root)
    errs: list[str] = []
    if len(code) > MAX_CODE_CHARS or code.count("\n") + 1 > MAX_CODE_LINES:
        errs.append(f"too long (at most {MAX_CODE_CHARS} characters and {MAX_CODE_LINES} lines)")
    if not re.fullmatch(r"test_[a-z0-9_]{3,60}", test_name or ""):
        errs.append("test name must match test_[a-z0-9_]{3,60}")
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError) as e:
        return [*errs, f"does not parse: {e}"[:200]]
    imported: set[str] = set()
    tests = []
    for node in tree.body:
        if isinstance(node, ast.Import | ast.ImportFrom):
            continue
        if isinstance(node, ast.FunctionDef) and node.name == test_name:
            tests.append(node)
        elif not _allowed_top(node):
            errs.append(f"line {getattr(node, 'lineno', '?')}: only imports, constants, _helpers and {test_name}")
    if len(tests) != 1:
        errs.append(f"exactly one test function named {test_name} is required, found {len(tests)}")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imported.add((a.asname or a.name).split(".")[0])
                errs += _import_errors(a.name, allowed)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                errs.append("relative imports are not allowed")
                continue
            base = node.module or ""
            for a in node.names:
                imported.add(a.asname or a.name)
                if a.name == "*":
                    errs.append("star imports are not allowed")
                errs += _import_errors(f"{base}.{a.name}" if base.startswith("kaibot") else base, allowed)
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            errs.append(f"line {node.lineno}: {node.id} is not allowed")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__") and node.attr.endswith("__"):
            errs.append(f"line {node.lineno}: dunder attribute {node.attr} is not allowed")
        elif isinstance(node, ast.Global | ast.Nonlocal):
            errs.append(f"line {node.lineno}: global/nonlocal is not allowed")
    for node in ast.walk(tree):  # no assignment to an imported module's attribute: test, never patch
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AugAssign | ast.AnnAssign):
            targets = [node.target]
        elif isinstance(node, ast.Delete):
            targets = node.targets
        for t in targets:
            base = t
            while isinstance(base, ast.Attribute | ast.Subscript):
                base = base.value
            if t is not base and isinstance(base, ast.Name) and base.id in imported:
                errs.append(f"line {node.lineno}: changes {base.id}, an imported module or name (no patches)")
    for fn in tests:
        params = [a.arg for a in fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs]
        if fn.args.vararg or fn.args.kwarg:
            errs.append("the test may not take *args or **kwargs")
        g = _given_params(fn)
        if params and g is None:
            errs.append("the test takes parameters but has no @given: fixtures are not allowed")
        elif params and g is not None:
            kw, npos = g
            if npos:
                if len(params) != npos:
                    errs.append("every parameter must come from @given")
            elif not set(params) <= kw:
                errs.append(f"parameters {sorted(set(params) - kw)} are not drawn by @given (fixtures are not allowed)")
    return list(dict.fromkeys(errs))


def _import_errors(name: str, allowed: list[str]) -> list[str]:
    if _root(name) not in ALLOWED_IMPORT_ROOTS:
        return [f"import of {name} is not allowed"]
    if _root(name) == "kaibot" and (_kaibot_denied(name) or not _kaibot_allowed(name, allowed)):
        return [f"import of {name} is not allowed (only the module under test and the Kaibot modules it imports)"]
    return []
