"""M3 wiring: daily bars are point-in-time, the pump veto blocks longs before the room, and the room's evidence pack
gets news clusters with a fallback to the default pack."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pandas as pd

from kaibot.engine.m3glue import LockedDesk, daily_bars, make_pack_fn, make_rules_extra

OPEN = datetime(2026, 11, 11, 14, 30, tzinfo=UTC)  # 09:30 ET


def minutes(day_open: datetime, n: int, price: float, vol: float) -> pd.DataFrame:
    ts = [day_open + timedelta(minutes=i) for i in range(n)]
    return pd.DataFrame({"ts": ts, "o": price, "h": price + 0.1, "l": price - 0.1, "c": price, "v": vol})


def market(prices_hist=(50.0,) * 10, today_price=50.0, today_vol=100.0, ratio=1.0):
    hist = pd.concat([minutes(OPEN - timedelta(days=i + 1), 390, p, 100.0)
                      for i, p in enumerate(reversed(prices_hist))], ignore_index=True)
    today = minutes(OPEN, 60, today_price, today_vol)
    return SimpleNamespace(history={"AAA": hist}, today_bars={"AAA": today}, vol_ratio={"AAA": ratio},
                           data=SimpleNamespace(news_count=lambda *a: 0))


def engine(mk, now):
    return SimpleNamespace(market=mk, now=lambda: now, events={}, day=None)


class FakeDesk:
    def __init__(self, items=(), fail=False):
        self.items, self.fail, self.polled = list(items), fail, []
        self.active = 0
        self.max_active = 0
        self.guard = threading.Lock()

    def poll(self, symbols):
        self._enter()
        self.polled.append(symbols)
        self._leave()

    def evidence_pack(self, symbol, etf, as_of, hours=24):
        self._enter()
        try:
            if self.fail:
                raise RuntimeError("boom")
            return {"items": self.items, "complete": True, "truncated": False, "n_clusters": len(self.items)}
        finally:
            self._leave()

    def _enter(self):
        with self.guard:
            self.active += 1
            self.max_active = max(self.max_active, self.active)

    def _leave(self):
        with self.guard:
            self.active -= 1


def test_daily_bars_point_in_time_and_scaled():
    mk = market(ratio=3.0)
    as_of = OPEN + timedelta(minutes=30)
    d = daily_bars(mk, "AAA", as_of)
    assert len(d) == 11
    last = d.iloc[-1]
    assert last["v"] == 30 * 100.0 * 3.0  # only the 30 minutes that ended before as_of, scaled to SIP
    assert last["ts"] < pd.Timestamp(as_of)
    assert d.iloc[0]["v"] == 390 * 100.0


def test_daily_bars_none_without_data():
    mk = SimpleNamespace(history={}, today_bars={}, vol_ratio={})
    assert daily_bars(mk, "AAA", OPEN) is None


def test_pump_veto_blocks_spiking_penny_stock():
    mk = market(prices_hist=(2.0,) * 10, today_price=3.0, today_vol=5000.0)
    veto = make_rules_extra(None)(engine(mk, OPEN + timedelta(minutes=45)), "AAA", 44)
    assert veto == "pump risk"


def test_pump_veto_passes_normal_stock():
    mk = market()
    assert make_rules_extra(FakeDesk())(engine(mk, OPEN + timedelta(minutes=45)), "AAA", 44) is None


def test_pump_veto_fails_closed_when_news_lookup_breaks():
    mk = market()
    assert make_rules_extra(FakeDesk(fail=True))(engine(mk, OPEN + timedelta(minutes=45)), "AAA", 44) == \
        "pump risk check failed"


def test_pack_merges_news_and_polls_symbol_and_etf():
    desk = FakeDesk(items=[{"id": "n7", "kind": "news", "headline": "x"}])
    pack = make_pack_fn(desk)(engine(market(), OPEN), "AAA", "XLK", OPEN, {"z": -2.0})
    ids = [i["id"] for i in pack["items"]]
    assert ids[0] == "feat" and "n7" in ids
    assert desk.polled == [["AAA", "XLK"]]
    assert pack["context"]["news_complete"] is True and pack["context"]["news_clusters"] == 1


def test_pack_falls_back_to_default_on_failure():
    pack = make_pack_fn(FakeDesk(fail=True))(engine(market(), OPEN), "AAA", None, OPEN, {})
    assert [i["id"] for i in pack["items"]][0] == "feat"
    assert all(i["kind"] != "news" for i in pack["items"])


def test_shared_lock_serialises_desk_access():
    desk = FakeDesk(items=[])
    locked = LockedDesk(desk)
    pack_fn, veto = make_pack_fn(locked), make_rules_extra(locked)
    mk = market()
    ts = [threading.Thread(target=pack_fn, args=(engine(mk, OPEN), "AAA", "XLK", OPEN, {})) for _ in range(4)]
    ts += [threading.Thread(target=veto, args=(engine(mk, OPEN + timedelta(minutes=45)), "AAA", 44))
           for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert desk.max_active == 1
