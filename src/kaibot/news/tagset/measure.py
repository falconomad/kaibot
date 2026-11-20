"""Precision and recall of kaibot.news.tags on the hand-labelled sample (story S105, #379). Reads two files, no network.

    python -m kaibot.news.tagset.measure            # the table and the misses, as Markdown
    python -m kaibot.news.tagset.measure --json     # the same numbers as JSON
    python -m kaibot.news.tagset.measure --holdout  # the 70 held-out headlines instead (a check, not the bar)

A tag passes with at least MIN_POSITIVES labelled positives and precision of at least MIN_PRECISION. With fewer
positives its precision is "not established", whatever the figure. The labels are never changed here.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from kaibot.news.tags import ALL_TAGS, all_tags

HERE = Path(__file__).resolve().parent
HEADLINES = HERE / "headlines_v1.jsonl"
LABELS = HERE / "labels_v1.jsonl"
HOLDOUT = HERE / "holdout_v1.jsonl"
MIN_PRECISION = 0.95
MIN_POSITIVES = 20
WORST = 20


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def load() -> list[dict]:
    """The sample: [{n, headline, created_at, symbols, source, source_id, labels:set}]."""
    labels = {r["n"]: set(r["tags"]) for r in _jsonl(LABELS)}
    rows = _jsonl(HEADLINES)
    if [r["n"] for r in rows] != sorted(labels):
        raise ValueError("headlines_v1.jsonl and labels_v1.jsonl do not cover the same headlines")
    return [{**r, "labels": labels[r["n"]]} for r in rows]


def load_holdout() -> list[dict]:
    """The 70 headlines drawn and labelled after the rules were written (README.md, "Held-out check")."""
    return [{**r, "labels": set(r["tags"])} for r in _jsonl(HOLDOUT)]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def measure(rows: list[dict] | None = None) -> dict:
    rows = load() if rows is None else rows
    got = {r["n"]: all_tags(r["headline"]) for r in rows}
    out: dict = {"n": len(rows), "tags": {}}
    for tag in ALL_TAGS:
        tp = [r for r in rows if tag in r["labels"] and tag in got[r["n"]]]
        fp = [r for r in rows if tag not in r["labels"] and tag in got[r["n"]]]
        fn = [r for r in rows if tag in r["labels"] and tag not in got[r["n"]]]
        pos, hits = len(tp) + len(fn), len(tp) + len(fp)
        precision = len(tp) / hits if hits else None
        established = pos >= MIN_POSITIVES
        out["tags"][tag] = {
            "positives": pos, "tagged": hits, "tp": len(tp), "fp": len(fp), "fn": len(fn),
            "precision": precision, "recall": len(tp) / pos if pos else None, "established": established,
            "ships": bool(established and precision is not None and precision >= MIN_PRECISION),
            # false positives first (they are what the 95% bar is about), then the misses; sample order within each
            "misses": [{"n": r["n"], "kind": k, "headline": r["headline"]}
                       for k, rs in (("false positive", fp), ("missed", fn)) for r in rs][:WORST],
        }
    out["shipped"] = sorted(t for t, v in out["tags"].items() if v["ships"])
    return out


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.0f}%"


def markdown(res: dict) -> str:
    lines = ["| Tag | Labelled positives | Tagged | Right | False positives | Missed | Precision | Recall | Ships |",
             "|---|---|---|---|---|---|---|---|---|"]
    for tag, v in res["tags"].items():
        ships = "yes" if v["ships"] else ("no" if v["established"] else "no (precision not established)")
        lines.append(f"| `{tag}` | {v['positives']} | {v['tagged']} | {v['tp']} | {v['fp']} | {v['fn']} | "
                     f"{_pct(v['precision'])} | {_pct(v['recall'])} | {ships} |")
    for tag, v in res["tags"].items():
        lines += ["", f"`{tag}`: {len(v['misses'])} wrong" + ("" if v["misses"] else " (none)")]
        lines += [f"- {m['kind']}, n={m['n']}: {m['headline']}" for m in v["misses"]]
    return "\n".join(lines)


if __name__ == "__main__":
    result = measure(load_holdout() if "--holdout" in sys.argv[1:] else None)
    print(json.dumps(result, indent=1, ensure_ascii=False) if "--json" in sys.argv[1:] else markdown(result))
