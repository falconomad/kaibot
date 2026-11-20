"""Precision and recall of kaibot.news.tags on the hand-labelled sample (story S105, #379). Reads two files, no network.

    python -m kaibot.news.tagset.measure            # the table and the misses, as Markdown
    python -m kaibot.news.tagset.measure --json     # the same numbers as JSON
    python -m kaibot.news.tagset.measure --holdout  # the 70 held-out headlines instead (a check, not the bar)
    python -m kaibot.news.tagset.measure --v2       # sample v2 from the Mac's store (draw_store.py), once labelled

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


# ---------------------------------------------------------------- sample v2 (draw_store.py)
HEADLINES_V2 = HERE / "headlines_v2.jsonl"
LABELS_V2 = HERE / "labels_v2.jsonl"
DRAW_V2 = HERE / "draw_v2.jsonl"


def load_v2(here: Path = HERE) -> list[dict]:
    """[{n, headline, created_at, earnings_day, labels:set, stratum}]. Refuses a partly labelled sample."""
    heads, labels_p, draw_p = here / HEADLINES_V2.name, here / LABELS_V2.name, here / DRAW_V2.name
    for p in (heads, labels_p, draw_p):
        if not p.exists():
            raise SystemExit(f"{p.name} is missing: v2 is drawn on the Mac (draw_store.py draw) and labelled by "
                             "hand before it is measured")
    labels = {r["n"]: set(r["tags"]) for r in _jsonl(labels_p)}
    strata = {r["n"]: r["stratum"] for r in _jsonl(draw_p)}
    rows = _jsonl(heads)
    ns = sorted(r["n"] for r in rows)
    if ns != sorted(labels) or ns != sorted(strata):
        raise ValueError("headlines_v2, labels_v2 and draw_v2 do not cover the same headlines")
    return [{**r, "labels": labels[r["n"]], "stratum": strata[r["n"]]} for r in rows]


def _counts(rows: list[dict], tag: str, got: dict[int, set[str]]) -> dict:
    tp = sum(1 for r in rows if tag in r["labels"] and tag in got[r["n"]])
    fp = sum(1 for r in rows if tag not in r["labels"] and tag in got[r["n"]])
    fn = sum(1 for r in rows if tag in r["labels"] and tag not in got[r["n"]])
    return {"tagged": tp + fp, "tp": tp, "fp": fp, "fn": fn, "positives": tp + fn,
            "precision": tp / (tp + fp) if tp + fp else None, "recall": tp / (tp + fn) if tp + fn else None}


def measure_v2(rows: list[dict] | None = None) -> dict:
    """Per tag: precision on its fired stratum (a uniform sample of what the rule tags in the store) and on the
    random stratum; recall on the random stratum only (the fired strata are chosen by the rule, so they would
    flatter it). A tag ships with at least MIN_POSITIVES labelled positives in the whole sample and precision of at
    least MIN_PRECISION on at least MIN_POSITIVES tagged headlines of its fired stratum, or of the random stratum
    for a tag that has no fired stratum (analyst_up, shipped in v1). Guidance tags are also reported by earnings
    day (fired stratum and random stratum)."""
    rows = load_v2() if rows is None else rows
    got = {r["n"]: all_tags(r["headline"]) for r in rows}
    rand = [r for r in rows if r["stratum"] == "random"]
    out: dict = {"n": len(rows), "n_random": len(rand), "years": sorted({r["created_at"][:4] for r in rand}),
                 "tags": {}}
    for tag in ALL_TAGS:
        fired = [r for r in rows if r["stratum"].startswith(f"fired:{tag}")]
        whole = _counts(rows, tag, got)
        on_fired, on_rand = _counts(fired, tag, got), _counts(rand, tag, got)
        prec = on_fired["precision"] if fired else on_rand["precision"]
        established = (whole["positives"] >= MIN_POSITIVES
                       and (on_fired if fired else on_rand)["tagged"] >= MIN_POSITIVES)
        v = {"positives": whole["positives"], "fired": on_fired if fired else None, "random": on_rand,
             "precision": prec, "recall": on_rand["recall"], "established": established,
             "ships": bool(established and prec is not None and prec >= MIN_PRECISION)}
        if tag in ("guidance_up", "guidance_down"):
            v["by_earnings_day"] = {
                k: {"fired": _counts([r for r in fired if r.get("earnings_day") is want], tag, got),
                    "random": _counts([r for r in rand if r.get("earnings_day") is want], tag, got)}
                for k, want in (("earnings_day", True), ("other_day", False))}
        out["tags"][tag] = v
    out["shipped"] = sorted(t for t, v in out["tags"].items() if v["ships"])
    return out


def markdown_v2(res: dict) -> str:
    lines = [f"Sample v2: {res['n']} headlines, {res['n_random']} in the random stratum (years "
             f"{', '.join(res['years'])}).", "",
             "| Tag | Labelled positives | Precision (fired stratum) | Precision (random) | Recall (random) | Ships |",
             "|---|---|---|---|---|---|"]
    for tag, v in res["tags"].items():
        f = v["fired"]
        fp = f"{_pct(f['precision'])} of {f['tagged']}" if f else "-"
        rp = f"{_pct(v['random']['precision'])} of {v['random']['tagged']}"
        ships = "yes" if v["ships"] else ("no" if v["established"] else "no (precision not established)")
        lines.append(f"| `{tag}` | {v['positives']} | {fp} | {rp} | {_pct(v['recall'])} | {ships} |")
    lines += ["", "| Guidance tag | Day | Precision (fired) | Precision (random) | Recall (random) |",
              "|---|---|---|---|---|"]
    for tag in ("guidance_up", "guidance_down"):
        for day, c in res["tags"][tag].get("by_earnings_day", {}).items():
            lines.append(f"| `{tag}` | {day} | {_pct(c['fired']['precision'])} of {c['fired']['tagged']} | "
                         f"{_pct(c['random']['precision'])} of {c['random']['tagged']} | "
                         f"{_pct(c['random']['recall'])} |")
    return "\n".join(lines)


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--v2" in args:
        result = measure_v2()
        print(json.dumps(result, indent=1, ensure_ascii=False) if "--json" in args else markdown_v2(result))
    else:
        result = measure(load_holdout() if "--holdout" in args else None)
        print(json.dumps(result, indent=1, ensure_ascii=False) if "--json" in args else markdown(result))
