"""#360: the news desk never leaves a cluster row without an item (QA-Ops on #355).

An item insert can fail after its new cluster row was written (the unique (headline_sha1, source) index after a
concurrent insert, a timeout). The desk then deletes the row it just wrote; if that delete fails too, it keeps the
id and deletes it at the next ingest. The in-memory window forgets the cluster either way.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fakes import Clock, FakeStore  # noqa: E402

from kaibot.news import NewsDesk  # noqa: E402

T0 = datetime(2026, 11, 11, 14, 0, tzinfo=UTC)


def art(headline, at=T0, symbols=("ACME",), source="benzinga"):
    return {"headline": headline, "source": source, "author": "Benzinga Newsdesk", "symbols": list(symbols),
            "created_at": at.isoformat().replace("+00:00", "Z"), "url": "https://x.test/a"}


class ItemFails(FakeStore):
    """news_item inserts fail while `fail_items`; deletes fail while `fail_deletes`."""

    def __init__(self):
        super().__init__()
        self.fail_items, self.fail_deletes = True, False

    def insert(self, table, row, returning=True):
        if table == "news_item" and self.fail_items:
            raise RuntimeError("duplicate key value violates unique constraint")
        return super().insert(table, row, returning)

    def delete(self, table, match):
        if self.fail_deletes:
            raise RuntimeError("timeout")
        return super().delete(table, match)


def test_a_failed_item_insert_leaves_no_cluster_row():
    st = ItemFails()
    d = NewsDesk(st, clock=Clock(T0))
    res = d.ingest([art("ACME cuts full-year guidance")])
    assert res["added"] == 0 and d.orphans_removed == 1
    assert st.tables.get("news_cluster", []) == []
    assert d._clusters == {}


def test_the_next_ingest_deletes_a_row_the_first_delete_could_not():
    st = ItemFails()
    st.fail_deletes = True
    d = NewsDesk(st, clock=Clock(T0))
    d.ingest([art("ACME cuts full-year guidance")])
    assert len(st.tables["news_cluster"]) == 1  # the delete failed: the row is still there, the desk knows it
    st.fail_items = st.fail_deletes = False
    d.ingest([art("ACME wins a contract")])
    clusters, items = st.tables["news_cluster"], st.tables["news_item"]
    assert len(clusters) == 1 and len(items) == 1 and items[0]["cluster_id"] == clusters[0]["id"]
    assert d.orphans_removed == 1 and d._orphans == set()


def test_every_cluster_has_an_item_when_some_inserts_fail():
    st = ItemFails()
    d = NewsDesk(st, clock=Clock(T0))
    calls = {"n": 0}
    real = FakeStore.insert

    def flaky(table, row, returning=True):
        if table == "news_item":
            calls["n"] += 1
            if calls["n"] % 2 == 0:
                raise RuntimeError("timeout")
        return real(st, table, row, returning)

    st.insert = flaky
    d.ingest([art(f"ACME story number {i} about a different thing", symbols=(f"S{i}",)) for i in range(10)])
    ids = {c["id"] for c in st.tables.get("news_cluster", [])}
    used = {i["cluster_id"] for i in st.tables.get("news_item", [])}
    assert ids == used and len(ids) == 5
