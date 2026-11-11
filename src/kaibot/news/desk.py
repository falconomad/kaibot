"""News ingest, echo dedup, triage and point-in-time reads (story S33).

Flow per poll: fetch recent Alpaca/Benzinga articles for the symbols (`AlpacaData.news`, paced by the shared
`alpaca` budget in config/sources.yaml) -> `normalise` -> skip (headline_sha1, source) already stored -> assign a
cluster -> insert news_item -> triage every cluster created in this poll exactly once.

Clustering (tech design §2): an item joins a cluster when they share a ticker, the item's published_at is within
24 h of the cluster's first story, and either the canonical-headline SHA-1 matches exactly or the MinHash estimate
of word-3-shingle Jaccard similarity is >= 0.8. Candidates come only from the in-memory window bucketed by ticker,
so each new item costs O(clusters sharing its tickers in the last 24 h), not a scan of the table.
n_outlets = distinct sources in the cluster. A cluster is triaged once, when it is created: later echoes only bump
n_outlets and last_seen ("echoes count once").

Point-in-time: news_item.available_ts and news_cluster.first_seen are Kaibot's ingest time (clock()), never the
provider's timestamp. `clusters_for` / `evidence_pack` rebuild every field from the items with
available_ts <= as_of (tickers, n_outlets) and hide a triage whose triage_ts is after as_of.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from kaibot.news import text
from kaibot.news.triage import triage as run_triage

log = logging.getLogger("kaibot.news")
WINDOW = timedelta(hours=24)
MAX_PACK_ITEMS = 20
MAX_TICKERS = 20
_TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_SOURCE = re.compile(r"[^a-z0-9_]+")
# Press-release wires Benzinga republishes; detected from the article's author/source so a wire copy counts as its
# own outlet (and so pump_risk can tell promotional wires from reporting).
WIRES = {
    "globenewswire": ("globenewswire", "globe newswire"),
    "prnewswire": ("prnewswire", "pr newswire"),
    "accesswire": ("accesswire", "access wire"),
    "businesswire": ("businesswire", "business wire"),
    "newsfile": ("newsfile",),
}


def iso(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def parse_ts(v) -> datetime | None:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=UTC)
    if not isinstance(v, str) or not v:
        return None
    try:
        t = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def outlet(raw: dict) -> str:
    who = f"{raw.get('author') or ''} {raw.get('source') or ''}".lower()
    for name, keys in WIRES.items():
        if any(k in who for k in keys):
            return name
    src = _SOURCE.sub("", str(raw.get("source") or "").lower())[:40]
    return src or "alpaca"


def normalise(raw: dict, now: datetime) -> dict | None:
    """One untrusted provider article -> a clean item dict, or None if unusable."""
    if not isinstance(raw, dict):
        return None
    headline = text.clean(raw.get("headline"), 500)
    canon = text.canonical(headline)
    published = parse_ts(raw.get("created_at") or raw.get("published_at"))
    if not canon or published is None:
        return None
    published = min(published, now)  # a provider clock ahead of ours must not create a future story
    syms = raw.get("symbols") if isinstance(raw.get("symbols"), list) else []
    tickers = sorted({s.strip().upper() for s in syms if isinstance(s, str) and _TICKER.match(s.strip().upper())})
    url = raw.get("url") if isinstance(raw.get("url"), str) else ""
    url = text.clean(url, 1000) if re.match(r"^https?://", url or "") else None
    return {"source": outlet(raw), "published_at": published, "headline": headline, "canonical": canon,
            "headline_sha1": text.headline_sha1(headline), "tickers": tickers[:MAX_TICKERS], "url": url,
            "summary": text.clean(raw.get("summary"), 400)}


@dataclass
class _Cluster:
    id: int
    tickers: set[str]
    first_published: datetime
    sha1s: set[str]
    minhash: list[int]
    sources: set[str]
    triaged: bool = False
    items: list[dict] = field(default_factory=list)  # only for clusters created by this process (triage input)


class NewsDesk:
    """Owns the 24 h dedup window. One per engine process (the engine is the only writer)."""

    def __init__(self, store, source=None, gateway=None, clock: Callable[[], datetime] | None = None,
                 window: timedelta = WINDOW):
        self.store, self.source, self.gateway = store, source, gateway
        self.clock = clock or (lambda: datetime.now(UTC))
        self.window = window
        self._clusters: dict[int, _Cluster] = {}
        self._by_ticker: dict[str, set[int]] = {}
        self._seen: dict[tuple[str, str], datetime] = {}
        self._pending: list[int] = []
        self.triage_calls = 0

    # ---------------- ingest ----------------
    def poll(self, symbols: list[str], lookback: timedelta = WINDOW) -> dict:
        """Fetch, store, dedup and triage the last `lookback` of news for `symbols`. Never raises."""
        now = self.clock()
        raw: list[dict] = []
        try:
            for i in range(0, len(symbols), 50):
                raw.extend(self.source.news(symbols[i : i + 50], now - lookback, now))
        except Exception as e:
            log.warning("news fetch failed: %s", type(e).__name__)
        return self.ingest(raw)

    def ingest(self, raw_items: Iterable[dict]) -> dict:
        now = self.clock()
        items = [x for x in (normalise(r, now) for r in raw_items) if x is not None]
        stored = self._stored_keys({x["headline_sha1"] for x in items})
        added = created = 0
        for it in sorted(items, key=lambda x: x["published_at"]):
            key = (it["headline_sha1"], it["source"])
            if key in self._seen or key in stored:
                continue
            res = self._add(it, now)
            if res is None:
                continue
            added += 1
            created += res
        tri = self._triage_pending()
        self._prune(now)
        return {"fetched": len(items), "added": added, "clusters_created": created, "triaged": tri}

    def _stored_keys(self, shas: set[str]) -> set[tuple[str, str]]:
        shas = {s for s in shas if s}
        if not shas:
            return set()
        try:
            rows = self.store.select("news_item", {"select": "headline_sha1,source",
                                                   "headline_sha1": f"in.({','.join(sorted(shas))})"})
        except Exception as e:
            log.warning("news dedup lookup failed: %s", type(e).__name__)
            return set()
        return {(r["headline_sha1"], r["source"]) for r in rows if r.get("headline_sha1") in shas}

    def _match(self, it: dict, sig: list[int]) -> int | None:
        buckets = it["tickers"] or [""]
        cands: set[int] = set()
        for t in buckets:
            cands |= self._by_ticker.get(t, set())
        best, best_sim = None, -1.0
        for cid in sorted(cands):
            c = self._clusters[cid]
            if abs(it["published_at"] - c.first_published) > self.window:
                continue
            sim = 2.0 if it["headline_sha1"] in c.sha1s else text.similarity(sig, c.minhash)
            if sim >= text.JACCARD_MIN and sim > best_sim:
                best, best_sim = cid, sim
        return best

    def _add(self, it: dict, now: datetime) -> int | None:
        """Store one new item. Returns 1 if it created a cluster, 0 if it joined one, None on a store error."""
        sig = text.minhash(it["canonical"])
        cid = self._match(it, sig)
        created = cid is None
        try:
            if created:
                row = self.store.insert("news_cluster", {
                    "tickers": it["tickers"], "first_seen": iso(now), "last_seen": iso(now), "n_outlets": 1,
                    "canonical_headline": it["canonical"][:500], "minhash": sig})
                cid = int(row["id"])
                c = _Cluster(cid, set(it["tickers"]), it["published_at"], set(), sig, set())
                self._clusters[cid] = c
                for t in it["tickers"] or [""]:
                    self._by_ticker.setdefault(t, set()).add(cid)
                self._pending.append(cid)
            c = self._clusters[cid]
            self.store.insert("news_item", {
                "cluster_id": cid, "source": it["source"], "published_at": iso(it["published_at"]),
                "available_ts": iso(now), "headline": it["headline"], "url": it["url"], "tickers": it["tickers"],
                "headline_sha1": it["headline_sha1"]}, returning=False)
        except Exception as e:  # e.g. the unique (headline_sha1, source) index after a concurrent insert
            log.warning("news insert skipped: %s", type(e).__name__)
            if created and cid is not None and cid in self._clusters:  # an empty cluster: forget it, never triage
                self._forget(cid)
            return None
        self._seen[(it["headline_sha1"], it["source"])] = it["published_at"]
        c.sha1s.add(it["headline_sha1"])
        new_src = it["source"] not in c.sources
        c.sources.add(it["source"])
        new_t = set(it["tickers"]) - c.tickers
        for t in new_t:
            self._by_ticker.setdefault(t, set()).add(cid)
        c.tickers |= new_t
        if len(c.items) < 5 and not c.triaged:
            c.items.append(it)
        if not created:
            vals: dict = {"last_seen": iso(now)}
            if new_src:
                vals["n_outlets"] = len(c.sources)
            if new_t:
                vals["tickers"] = sorted(c.tickers)
            try:
                self.store.update("news_cluster", {"id": cid}, vals)
            except Exception as e:
                log.warning("news cluster update failed: %s", type(e).__name__)
        return 1 if created else 0

    def _triage_pending(self) -> int:
        n = 0
        pending, self._pending = self._pending, []
        for cid in pending:
            c = self._clusters.get(cid)
            if c is None or c.triaged:
                continue
            c.triaged = True  # set before the call: one attempt per cluster, even if it fails
            self.triage_calls += 1
            score = run_triage(self.gateway, sorted(c.tickers), c.items)
            c.items = []
            if score is None:
                continue
            try:
                self.store.update("news_cluster", {"id": cid},
                                  {"triage": score.model_dump(mode="json"), "triage_ts": iso(self.clock())})
                n += 1
            except Exception as e:
                log.warning("triage store failed: %s", type(e).__name__)
        return n

    def _forget(self, cid: int) -> None:
        c = self._clusters.pop(cid)
        if cid in self._pending:
            self._pending.remove(cid)
        for t in c.tickers or {""}:
            b = self._by_ticker.get(t)
            if b:
                b.discard(cid)
                if not b:
                    del self._by_ticker[t]

    def _prune(self, now: datetime) -> None:
        horizon = now - 2 * self.window
        for cid in [cid for cid, c in self._clusters.items() if c.first_published < horizon]:
            self._forget(cid)
        self._seen = {k: t for k, t in self._seen.items() if t >= horizon}

    def load_window(self) -> int:
        """Rebuild the dedup window from the store after a restart. Loaded clusters are never re-triaged."""
        now = self.clock()
        try:
            items = self.store.select("news_item", {
                "select": "cluster_id,source,published_at,headline_sha1,tickers",
                "published_at": f"gte.{iso(now - 2 * self.window)}", "order": "id.asc", "limit": "20000"})
            ids = sorted({int(r["cluster_id"]) for r in items if r.get("cluster_id") is not None})
            rows = []
            for i in range(0, len(ids), 200):
                rows += self.store.select("news_cluster", {
                    "select": "id,tickers,minhash", "id": f"in.({','.join(map(str, ids[i:i + 200]))})"})
        except Exception as e:
            log.warning("news window load failed: %s", type(e).__name__)
            return 0
        sigs = {int(r["id"]): r for r in rows}
        for r in items:
            pub = parse_ts(r.get("published_at"))
            cid = r.get("cluster_id")
            if pub is None or cid is None or int(cid) not in sigs:
                continue
            cid = int(cid)
            c = self._clusters.get(cid)
            if c is None:
                sig = [int(x) for x in (sigs[cid].get("minhash") or [])]
                c = self._clusters[cid] = _Cluster(cid, set(), pub, set(), sig, set(), triaged=True)
            c.first_published = min(c.first_published, pub)
            c.sha1s.add(r["headline_sha1"])
            c.sources.add(r["source"])
            c.tickers |= set(r.get("tickers") or [])
            self._seen[(r["headline_sha1"], r["source"])] = pub
        for cid, c in self._clusters.items():
            for t in c.tickers or {""}:
                self._by_ticker.setdefault(t, set()).add(cid)
        return len(self._clusters)

    # ---------------- point-in-time reads ----------------
    def clusters_for(self, symbol: str, as_of: datetime, hours: float = 24.0) -> list[dict]:
        """Clusters about `symbol` known at `as_of`: first_seen <= as_of, built only from items with
        available_ts <= as_of, with at least one such item published in the last `hours`. Newest first.
        Each: {id, tickers, first_seen, canonical_headline, n_outlets, triage, items:[...oldest first]}."""
        return self._query(symbol, as_of, hours)[0]

    def _query(self, symbol: str, as_of: datetime, hours: float) -> tuple[list[dict], bool]:
        as_of = parse_ts(as_of) or as_of
        sym = (symbol or "").strip().upper()
        if not _TICKER.match(sym):
            return [], True
        since = as_of - timedelta(hours=hours)
        try:
            cl = self.store.select("news_cluster", {
                "select": "id,tickers,first_seen,canonical_headline,triage,triage_ts",
                "tickers": f"cs.{{{sym}}}", "first_seen": f"lte.{iso(as_of)}", "last_seen": f"gte.{iso(since)}",
                "order": "first_seen.desc", "limit": "500"})
            cl = [c for c in cl if (parse_ts(c.get("first_seen")) or as_of + timedelta(1)) <= as_of]
            if not cl:
                return [], True
            ids = ",".join(str(int(c["id"])) for c in cl)
            items = self.store.select("news_item", {
                "select": "id,cluster_id,source,published_at,available_ts,headline,url,tickers",
                "cluster_id": f"in.({ids})", "available_ts": f"lte.{iso(as_of)}", "order": "id.asc",
                "limit": "5000"})
        except Exception as e:
            log.warning("clusters_for %s failed: %s", sym, type(e).__name__)
            return [], False
        by: dict[int, list[dict]] = {}
        for r in items:
            av, pub = parse_ts(r.get("available_ts")), parse_ts(r.get("published_at"))
            if av is None or pub is None or av > as_of or r.get("cluster_id") is None:
                continue
            by.setdefault(int(r["cluster_id"]), []).append({**r, "available_ts": av, "published_at": pub})
        out = []
        for c in cl:
            its = sorted(by.get(int(c["id"]), []), key=lambda r: (r["published_at"], r["id"]))
            tickers = sorted({t for r in its for t in (r.get("tickers") or [])})
            if not its or sym not in tickers or max(r["published_at"] for r in its) < since:
                continue
            tts = parse_ts(c.get("triage_ts"))
            out.append({"id": int(c["id"]), "tickers": tickers, "first_seen": parse_ts(c["first_seen"]),
                        "canonical_headline": c.get("canonical_headline"),
                        "n_outlets": len({r["source"] for r in its}),
                        "triage": c.get("triage") if c.get("triage") and tts and tts <= as_of else None,
                        "items": its})
        out.sort(key=lambda c: (c["items"][-1]["published_at"], c["id"]), reverse=True)
        return out, True

    def evidence_pack(self, symbol: str, sector_etf: str | None, as_of: datetime, hours: float = 24) -> dict:
        """Deduplicated news evidence for the agent room (tech design §3, input 2). See docs/ops/news.md.

        Returns {"symbol", "sector_etf", "as_of", "hours", "items", "n_clusters", "truncated", "complete"}
        where items is at most 20 dicts {"id": "n<cluster_id>", "kind": "news", "scope": "ticker"|"sector", "source",
        "published_at", "headline", "url", "n_outlets", "triage"}, newest first. source/headline/url are the
        cluster's first story; published_at its time (ISO UTC); n_outlets and triage are as known at as_of.
        complete=False means a store read failed (the pack may be missing stories: treat as no evidence).
        Headlines are cleaned and capped but still untrusted: keep them inside a data block in any prompt."""
        as_of = parse_ts(as_of) or as_of
        seen: set[int] = set()
        complete = True
        rows = []
        scopes = [(symbol, "ticker")]
        if sector_etf and sector_etf.upper() != (symbol or "").upper():
            scopes.append((sector_etf, "sector"))
        for sym, scope in scopes:
            found, ok = self._query(sym, as_of, hours)
            complete = complete and ok
            for c in found:
                if c["id"] in seen:
                    continue
                seen.add(c["id"])
                first = c["items"][0]
                rows.append({"id": f"n{c['id']}", "kind": "news", "scope": scope,
                             "source": text.clean(first.get("source"), 40),
                             "published_at": first["published_at"].astimezone(UTC).isoformat(),
                             "headline": text.clean(first.get("headline"), 300),
                             "url": text.clean(first.get("url"), 500) or None,
                             "n_outlets": c["n_outlets"], "triage": c["triage"],
                             "_latest": c["items"][-1]["published_at"]})
        rows.sort(key=lambda r: (r["_latest"], r["id"]), reverse=True)
        for r in rows:
            del r["_latest"]
        return {"symbol": symbol, "sector_etf": sector_etf, "as_of": as_of.astimezone(UTC).isoformat(),
                "hours": hours, "items": rows[:MAX_PACK_ITEMS], "n_clusters": len(rows),
                "truncated": len(rows) > MAX_PACK_ITEMS, "complete": complete}
