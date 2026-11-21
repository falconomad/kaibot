"""#360: the offline clustering measurement is deterministic, uses the committed sample and matches docs/ops/news.md."""

from __future__ import annotations

import hashlib
from pathlib import Path

from kaibot.news import clustereval as ce
from kaibot.news import text

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures" / "news_clusters"
SHA256 = "c85583a216cd72ce3128996f60366cf60a412d0e904a375b5104176915d4edff"  # sample_v1 + labels_v1


def test_the_sample_and_labels_cover_the_same_91_headlines_and_82_stories():
    rows = ce.load()
    assert len(rows) == 91 and len({r["story"] for r in rows}) == 82
    assert sum(1 for s in {r["story"] for r in rows} if sum(r["story"] == s for r in rows) > 1) == 8


def test_the_measurement_is_deterministic_and_leaves_the_threshold_as_it_was():
    a, b = ce.evaluate(), ce.evaluate()
    assert a == b and text.JACCARD_MIN == 0.8


def test_the_numbers_in_docs_ops_news_md():
    runs = ce.evaluate()["runs"]
    cur = runs["desk minhash >= 0.8"]
    assert (cur["clusters"], cur["split_stories"], cur["merged_clusters"]) == (90, 8, 1)
    assert (runs["offline content words >= 0.45"]["split_stories"],
            runs["offline content words >= 0.45"]["merged_clusters"]) == (5, 2)
    doc = (ROOT / "docs" / "ops" / "news.md").read_text()
    assert "| **desk, MinHash >= 0.8 (current)** | 90 | **8 of 8** | 1 | 0% | 0% |" in doc


def test_a_perfect_clustering_scores_perfectly_and_one_big_cluster_merges_everything():
    rows = ce.load()
    perfect = ce.score(rows, {r["n"]: r["story"] for r in rows})
    assert perfect["split_stories"] == perfect["merged_clusters"] == 0 and perfect["pair_precision"] == 1.0
    one = ce.score(rows, {r["n"]: 1 for r in rows})
    assert one["merged_clusters"] == 1 and one["pair_recall"] == 1.0


def test_fixture_files_do_not_change_silently():
    digest = hashlib.sha256((FIX / "sample_v1.jsonl").read_bytes() + (FIX / "labels_v1.jsonl").read_bytes())
    assert digest.hexdigest() == SHA256 == ce.fixture_sha256()
