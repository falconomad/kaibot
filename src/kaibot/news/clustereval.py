"""Offline quality of the news desk's clustering rule on the hand-labelled sample (#360). No network, no LLM.

    python -m kaibot.news.clustereval            # Markdown: the current rule, and the alternatives beside it
    python -m kaibot.news.clustereval --json

The sample (tests/fixtures/news_clusters/: 91 real headlines, labelled by hand into stories before the rule ever ran
on them) goes through the real `NewsDesk.ingest` on an in-memory store with no triage. Then, per run:

- **split**: a story with two or more headlines that ends up in more than one cluster (one story, several
  clusters: the room and the reviewer would see it twice);
- **merged**: a cluster that holds headlines of more than one story (different news shown as one);
- pairwise precision and recall over headline pairs (same cluster vs same story).

Alternatives are shown beside the current rule (`text.JACCARD_MIN`), on the same sample. Nothing is adopted here:
a change to what the room or the reviewer sees is its own story (#360).
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from itertools import combinations
from pathlib import Path

from kaibot.news import text
from kaibot.news.desk import NewsDesk

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "tests" / "fixtures" / "news_clusters"
ALTERNATIVES = (0.8, 0.5, 0.3, 0.1)  # MinHash Jaccard thresholds through the real desk; 0.8 is the current rule
TOKEN_ALTERNATIVES = (0.3, 0.45, 0.6)  # an offline-only alternative: content-word Jaccard, single link
STOPWORDS = frozenset(["a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with", "by", "at", "from", "as",
                       "is", "are", "be", "its", "it", "this", "that", "s", "new", "over", "after", "says", "inc", "co",
                       "corp", "plc"])


class _MemoryStore:
    """Just what NewsDesk.ingest writes and reads; no triage, no network."""

    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = defaultdict(list)
        self._id = 0

    def insert(self, table, row, returning=True):
        self._id += 1
        r = {**row, "id": self._id}
        self.tables[table].append(r)
        return r

    def update(self, table, match, values):
        for r in self.tables[table]:
            if all(r.get(k) == v for k, v in match.items()):
                r.update(values)

    def delete(self, table, match):
        self.tables[table] = [r for r in self.tables[table] if not all(r.get(k) == v for k, v in match.items())]

    def select(self, table, params):
        return []


def _jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def fixture_sha256(fixture: Path = FIXTURE) -> str:
    import hashlib

    return hashlib.sha256((fixture / "sample_v1.jsonl").read_bytes()
                          + (fixture / "labels_v1.jsonl").read_bytes()).hexdigest()


def load(fixture: Path = FIXTURE) -> list[dict]:
    rows = _jsonl(fixture / "sample_v1.jsonl")
    story = {r["n"]: r["story"] for r in _jsonl(fixture / "labels_v1.jsonl")}
    if sorted(story) != [r["n"] for r in rows]:
        raise ValueError("sample_v1.jsonl and labels_v1.jsonl do not cover the same headlines")
    return [{**r, "story": story[r["n"]]} for r in rows]


@contextmanager
def _threshold(j: float):
    old = text.JACCARD_MIN
    text.JACCARD_MIN = j
    try:
        yield
    finally:
        text.JACCARD_MIN = old


def clusters_of(rows: list[dict], jaccard: float = text.JACCARD_MIN) -> dict[int, int]:
    """n -> the cluster id the real desk gives each headline at this threshold."""
    st = _MemoryStore()
    last = max(datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")) for r in rows)
    desk = NewsDesk(st, clock=lambda: last + timedelta(hours=1))
    raw = [{"headline": r["headline"], "created_at": r["created_at"], "symbols": r["symbols"],
            "source": r.get("source") or "benzinga", "author": "", "url": f"https://x.test/{r['n']}"} for r in rows]
    with _threshold(jaccard):
        desk.ingest(raw)
    by_head = {(i["headline"], i["published_at"][:19]): i["cluster_id"] for i in st.tables["news_item"]}
    by_text = {i["headline"]: i["cluster_id"] for i in st.tables["news_item"]}
    out = {}
    for r in rows:
        t = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")).astimezone(UTC)
        h = text.clean(r["headline"], 500)
        # a headline the desk dropped as a repeat of an earlier one (same text and outlet) counts as merged into it
        out[r["n"]] = by_head.get((h, t.strftime("%Y-%m-%dT%H:%M:%S")), by_text.get(h))
    return out


def score(rows: list[dict], cluster: dict[int, int | None]) -> dict:
    stories: dict[int, set] = defaultdict(set)
    members: dict[int, set] = defaultdict(set)
    for r in rows:
        stories[r["story"]].add(cluster[r["n"]])
        members[cluster[r["n"]]].add(r["story"])
    multi = {s: cs for s, cs in stories.items() if sum(1 for r in rows if r["story"] == s) > 1}
    split = sorted(s for s, cs in multi.items() if len(cs) > 1)
    merged = sorted(c for c, ss in members.items() if len(ss) > 1)
    tp = fp = fn = 0
    for a, b in combinations(rows, 2):
        same_c, same_s = cluster[a["n"]] == cluster[b["n"]], a["story"] == b["story"]
        tp += same_c and same_s
        fp += same_c and not same_s
        fn += same_s and not same_c
    head = {r["n"]: r["headline"] for r in rows}
    return {"headlines": len(rows), "stories": len(stories), "clusters": len(members),
            "multi_headline_stories": len(multi), "split_stories": len(split), "merged_clusters": len(merged),
            "pair_precision": tp / (tp + fp) if tp + fp else None, "pair_recall": tp / (tp + fn) if tp + fn else None,
            "split": [[head[r["n"]] for r in rows if r["story"] == s] for s in split],
            "merged": [[head[r["n"]] for r in rows if cluster[r["n"]] == c] for c in merged]}


def _words(headline: str) -> set[str]:
    return {w for w in text.canonical(headline).split() if w not in STOPWORDS and len(w) > 1}


def token_clusters(rows: list[dict], threshold: float, window: timedelta = timedelta(hours=24)) -> dict[int, int]:
    """Offline only, not the desk: single-link groups of headlines that share a ticker, lie within `window` of
    each other and have a content-word Jaccard of at least `threshold`. A candidate rule to compare, never used."""
    parent = {r["n"]: r["n"] for r in rows}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    ts = {r["n"]: datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")) for r in rows}
    words = {r["n"]: _words(r["headline"]) for r in rows}
    for a, b in combinations(rows, 2):
        if not set(a["symbols"]) & set(b["symbols"]) or abs(ts[a["n"]] - ts[b["n"]]) > window:
            continue
        x, y = words[a["n"]], words[b["n"]]
        if x | y and len(x & y) / len(x | y) >= threshold:
            parent[find(a["n"])] = find(b["n"])
    return {r["n"]: find(r["n"]) for r in rows}


def evaluate(rows: list[dict] | None = None, alternatives=ALTERNATIVES, tokens=TOKEN_ALTERNATIVES) -> dict:
    rows = load() if rows is None else rows
    runs = {f"desk minhash >= {j}": score(rows, clusters_of(rows, j)) for j in alternatives}
    runs |= {f"offline content words >= {t}": score(rows, token_clusters(rows, t)) for t in tokens}
    return {"current": f"desk minhash >= {text.JACCARD_MIN}", "runs": runs}


def _pct(x) -> str:
    return "-" if x is None else f"{100 * x:.0f}%"


def markdown(res: dict) -> str:
    L = ["| Rule | Clusters | Stories split (of those with 2+ headlines) | Clusters merging stories | "
         "Pair precision | Pair recall |", "|---|---|---|---|---|---|"]
    for j, v in res["runs"].items():
        tag = " (current)" if j == res["current"] else ""
        L.append(f"| {j}{tag} | {v['clusters']} | {v['split_stories']} of {v['multi_headline_stories']} | "
                 f"{v['merged_clusters']} | {_pct(v['pair_precision'])} | {_pct(v['pair_recall'])} |")
    for j, v in res["runs"].items():
        L += ["", f"{j}: split stories"]
        L += [f"- {' / '.join(h)}" for h in v["split"]] or ["- none"]
        L += [f"{j}: merged clusters"]
        L += [f"- {' / '.join(h)}" for h in v["merged"]] or ["- none"]
    return "\n".join(L)


if __name__ == "__main__":
    out = evaluate()
    print(json.dumps(out, indent=1, ensure_ascii=False) if "--json" in sys.argv[1:] else markdown(out))
