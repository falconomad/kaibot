"""A counted proposer (M12 ticket 3.10, issue #472; docs/research/proposer/hurdle.md). Research only.

A model (or a person) may propose a new signal, but only as a FORMULA in a small restricted language, at most three
machine proposals an ISO week, and every proposal is counted as a trial whether or not it is new. Deterministic code
then does everything else: it parses and checks the formula, gives it a canonical form (so a duplicate is
recognised, and still counted), computes it point in time, and hands it to the tier-one screen (ticket 2.11) as a
candidate. Nothing a model writes is executed: the formula is parsed into a tree of the functions below, never
evaluated as Python.

The language (whitespace ignored; integers are session counts):

    expr  := term (("+" | "-") term)*
    term  := factor ("*" factor)*
    factor:= "-" factor | number | call | "(" expr ")"
    call  := ret(n[, skip]) | vol(n) | sma_gap(n) | high_gap(n) | rank(expr) | zscore(expr) | sign(expr)

- `ret(n, skip)`: the close skip sessions ago over the close n sessions ago, minus 1 (1 <= skip < n <= 504)
- `vol(n)`: the standard deviation of the last n daily returns (5 <= n <= 252)
- `sma_gap(n)`: the close over its n-session average, minus 1 (5 <= n <= 252)
- `high_gap(n)`: the close over the highest close of the last n sessions, minus 1 (5 <= n <= 504)
- `rank`, `zscore`: across the stocks of the day (point in time); `sign`: -1, 0 or 1
- numbers: constants between -10 and 10; at most 12 nodes in a formula.

Every function reads closes at or before the decision row only. A formula that reads further back than 504 sessions,
uses another name, or does not parse is refused with the reason.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

MAX_LOOKBACK = 504
MAX_NODES = 12
WEEKLY_CAP = 3  # machine proposals per ISO week
FUNCS = {"ret": (1, 2), "vol": (1, 1), "sma_gap": (1, 1), "high_gap": (1, 1), "rank": (1, 1), "zscore": (1, 1),
         "sign": (1, 1)}
_TOKEN = re.compile(r"\s*(?:(?P<num>\d+(?:\.\d+)?)|(?P<name>[a-z_]+)|(?P<op>[-+*(),]))")


class FormulaError(ValueError):
    """A proposal the deterministic intake refuses."""


@dataclass(frozen=True)
class Node:
    op: str  # num | add | sub | mul | neg | <function name>
    args: tuple = ()
    value: float = 0.0

    def canon(self) -> str:
        """A canonical text: sums and products sorted, so `a + b` and `b + a` are the same proposal."""
        if self.op == "num":
            return f"{self.value:g}"
        if self.op in ("add", "mul"):
            parts = sorted(a.canon() for a in _flatten(self, self.op))
            return f"{self.op}({','.join(parts)})"
        if self.op in ("ret", "vol", "sma_gap", "high_gap"):
            return f"{self.op}({','.join(str(int(a)) for a in self.args)})"
        return f"{self.op}({','.join(a.canon() for a in self.args)})"

    def size(self) -> int:
        return 1 + sum(a.size() for a in self.args if isinstance(a, Node))

    def lookback(self) -> int:
        if self.op == "ret":
            return int(self.args[0])
        if self.op in ("vol", "sma_gap", "high_gap"):
            return int(self.args[0])
        return max([a.lookback() for a in self.args if isinstance(a, Node)] or [0])


def _flatten(n: Node, op: str) -> list[Node]:
    out = []
    for a in n.args:
        out.extend(_flatten(a, op) if isinstance(a, Node) and a.op == op else [a])
    return out


def _tokens(text: str) -> list[tuple[str, str]]:
    out, pos = [], 0
    text = text.strip()
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise FormulaError(f"cannot read the formula at {text[pos:pos + 12]!r}")
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
        pos = m.end()
    return out


def parse(text: str) -> Node:
    """The formula as a checked tree, or FormulaError with the reason."""
    if not isinstance(text, str) or not text.strip() or len(text) > 300:
        raise FormulaError("a formula is a non-empty text of at most 300 characters")
    toks = _tokens(text)
    i = 0

    def peek():
        return toks[i] if i < len(toks) else (None, None)

    def take(want=None):
        nonlocal i
        t = peek()
        if want is not None and t[1] != want:
            raise FormulaError(f"expected {want!r}, found {t[1]!r}")
        i += 1
        return t

    def expr() -> Node:
        n = term()
        while peek()[1] in ("+", "-"):
            op = take()[1]
            n = Node("add" if op == "+" else "sub", (n, term()))
        return n

    def term() -> Node:
        n = factor()
        while peek()[1] == "*":
            take()
            n = Node("mul", (n, factor()))
        return n

    def integer() -> int:
        kind, v = take()
        if kind != "num" or "." in v:
            raise FormulaError(f"a session count must be a whole number, found {v!r}")
        return int(v)

    def factor() -> Node:
        kind, v = peek()
        if v == "-":
            take()
            return Node("neg", (factor(),))
        if v == "(":
            take()
            n = expr()
            take(")")
            return n
        if kind == "num":
            take()
            x = float(v)
            if not -10 <= x <= 10:
                raise FormulaError(f"a constant must lie between -10 and 10, found {x:g}")
            return Node("num", value=x)
        if kind == "name":
            take()
            if v not in FUNCS:
                raise FormulaError(f"unknown function {v!r}; allowed: {', '.join(sorted(FUNCS))}")
            take("(")
            lo, hi = FUNCS[v]
            if v in ("ret", "vol", "sma_gap", "high_gap"):
                args = [integer()]
                while peek()[1] == ",":
                    take()
                    args.append(integer())
            else:
                args = [expr()]
            take(")")
            if not lo <= len(args) <= hi:
                raise FormulaError(f"{v} takes {lo} to {hi} arguments")
            _check_args(v, args)
            return Node(v, tuple(args))
        raise FormulaError(f"unexpected {v!r}")

    tree = expr()
    if i != len(toks):
        raise FormulaError(f"unexpected {toks[i][1]!r} after the formula")
    if tree.size() > MAX_NODES:
        raise FormulaError(f"{tree.size()} nodes; at most {MAX_NODES}")
    if tree.lookback() > MAX_LOOKBACK:
        raise FormulaError(f"reads {tree.lookback()} sessions back; at most {MAX_LOOKBACK}")
    if not _reads_prices(tree):
        raise FormulaError("the formula reads no price: a constant ranks nothing")
    return tree


def _check_args(name: str, args: list[int]) -> None:
    if name == "ret":
        n, skip = args[0], (args[1] if len(args) > 1 else 0)
        if not (1 <= n <= MAX_LOOKBACK and 0 <= skip < n):
            raise FormulaError(f"ret({n}, {skip}): need 0 <= skip < n <= {MAX_LOOKBACK}")
    elif name in ("vol", "sma_gap"):
        if not 5 <= args[0] <= 252:
            raise FormulaError(f"{name}({args[0]}): n between 5 and 252")
    elif name == "high_gap" and not 5 <= args[0] <= MAX_LOOKBACK:
        raise FormulaError(f"high_gap({args[0]}): n between 5 and {MAX_LOOKBACK}")


def _reads_prices(n: Node) -> bool:
    return n.op in ("ret", "vol", "sma_gap", "high_gap") or any(isinstance(a, Node) and _reads_prices(a)
                                                                for a in n.args)


def canonical(text: str) -> tuple[str, str]:
    """(canonical text, its sha256[:16]): the identity a duplicate is recognised by."""
    c = parse(text).canon()
    return c, hashlib.sha256(c.encode()).hexdigest()[:16]


# ---------------------------------------------------------------- evaluation, point in time


def evaluate(tree: Node, c: np.ndarray, k: int) -> np.ndarray:
    """The formula's score of every stock on decision row k, from closes at rows <= k only (`c`: sessions x
    stocks). NaN where a stock lacks the history."""
    def go(n: Node) -> np.ndarray:
        with np.errstate(invalid="ignore", divide="ignore"):
            if n.op == "num":
                return np.full(c.shape[1], n.value)
            if n.op == "ret":
                back, skip = int(n.args[0]), int(n.args[1]) if len(n.args) > 1 else 0
                return c[k - skip] / c[k - back] - 1 if k - back >= 0 else np.full(c.shape[1], np.nan)
            if n.op == "vol":
                w = int(n.args[0])
                if k - w < 0:
                    return np.full(c.shape[1], np.nan)
                r = c[k - w + 1:k + 1] / c[k - w:k] - 1
                return np.std(r, axis=0, ddof=1)
            if n.op == "sma_gap":
                w = int(n.args[0])
                return c[k] / c[k - w + 1:k + 1].mean(axis=0) - 1 if k - w + 1 >= 0 else np.full(c.shape[1], np.nan)
            if n.op == "high_gap":
                w = int(n.args[0])
                return c[k] / c[k - w + 1:k + 1].max(axis=0) - 1 if k - w + 1 >= 0 else np.full(c.shape[1], np.nan)
            if n.op == "add":
                return go(n.args[0]) + go(n.args[1])
            if n.op == "sub":
                return go(n.args[0]) - go(n.args[1])
            if n.op == "mul":
                return go(n.args[0]) * go(n.args[1])
            if n.op == "neg":
                return -go(n.args[0])
            x = go(n.args[0])
            if n.op == "sign":
                return np.sign(x)
            s = pd.Series(x)
            if n.op == "rank":
                return s.rank(pct=True).to_numpy()
            sd = float(s.std(ddof=1))
            return ((s - s.mean()) / sd).to_numpy() if sd > 0 else np.full(c.shape[1], np.nan)
        raise AssertionError(n.op)  # pragma: no cover - parse() admits nothing else

    return go(tree)


def as_signal(text: str) -> Callable[[np.ndarray, int], np.ndarray]:
    """The formula as a screen signal `(closes, row) -> scores` (ticket 2.11's `candidate_scores` shape)."""
    tree = parse(text)
    return lambda c, k: evaluate(tree, c, int(k))


# ---------------------------------------------------------------- the proposals ledger


def read_proposals(path: Path | str) -> list[dict]:
    p = Path(path)
    if not p.is_file():
        return []
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def intake(path: Path | str, formula: str, *, proposer: str, family: str, on: date, why: str = "") -> dict:
    """Check one proposal and append it to the proposals ledger (append-only). Machine proposals (`proposer`
    starting with "model:") are capped at three an ISO week; a refused formula or a fourth machine proposal raises
    FormulaError and writes nothing. A duplicate of an earlier proposal is accepted, marked, and still counted (a
    model that repeats itself spends its tries)."""
    canon, cid = canonical(formula)
    rows = read_proposals(path)
    week = on.isocalendar()[:2]
    machine = proposer.startswith("model:")
    if machine and sum(1 for r in rows if r["proposer"].startswith("model:")
                       and tuple(date.fromisoformat(r["date"]).isocalendar()[:2]) == tuple(week)) >= WEEKLY_CAP:
        raise FormulaError(f"{WEEKLY_CAP} machine proposals already in ISO week {week[0]}-W{week[1]:02d}")
    first = next((r for r in rows if r["canonical_id"] == cid), None)
    row = {"id": f"p{len(rows) + 1:04d}", "date": on.isoformat(), "proposer": proposer, "family": family,
           "formula": formula, "canonical": canon, "canonical_id": cid, "duplicate_of": first["id"] if first else None,
           "why": why, "n_trials": 1}
    with Path(path).open("a") as fh:
        fh.write(json.dumps(row) + "\n")
    return row


def counted(rows: list[dict]) -> int:
    """Configurations the proposals add: every proposal, duplicates included."""
    return sum(int(r.get("n_trials", 1)) for r in rows)


__all__ = ("FormulaError", "Node", "parse", "canonical", "evaluate", "as_signal", "intake", "read_proposals",
           "counted", "WEEKLY_CAP", "MAX_LOOKBACK", "MAX_NODES")
