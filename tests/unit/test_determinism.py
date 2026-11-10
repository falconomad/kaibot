"""Story S05: the same inputs must give the same replay, byte for byte (events_sha256), on every run."""

import copy

import numpy as np
import pandas as pd

from kaibot.replay.data import N_MIN, SymbolDays
from kaibot.replay.events import events, events_digest, input_digest
from kaibot.replay.pipeline import prepare
from kaibot.replay.sim import random_entry_runs, run_portfolio
from kaibot.risk.profiles import load_profiles

START, END = np.datetime64("2025-04-01"), np.datetime64("2025-06-30")


def market(seed: int) -> tuple[dict, dict, dict, SymbolDays]:
    """Six stocks in two sectors with sharp intraday dips so the rules actually trade."""
    rng = np.random.default_rng(seed)
    dates = np.array([d for d in np.arange(np.datetime64("2025-01-02"), END + np.timedelta64(1, "D"))
                      if np.is_busday(d)], dtype="datetime64[D]")
    nd = len(dates)

    def sym(name, vol=0.0006, dips=True, price=100.0):
        r = rng.normal(0, vol, (nd, N_MIN))
        if dips:
            for d in range(60, nd, 3):  # a sell-off then a rebound on every third session
                m = int(rng.integers(40, 200))
                r[d, m:m + 25] -= 0.0018
                r[d, m + 30:m + 70] += 0.0012
        gap = np.cumsum(rng.normal(0, 0.01, nd))[:, None]
        c = price * np.exp(gap + np.cumsum(r, axis=1))
        o = np.c_[c[:, :1], c[:, :-1]]
        h = np.maximum(o, c) * (1 + 0.0002)
        lo = np.minimum(o, c) * (1 - 0.0002)
        v = rng.uniform(2e5, 6e5, (nd, N_MIN))
        return SymbolDays(name, dates, o, h, lo, c, v, (h + lo + c) / 3)

    etfs = {"XLK": sym("XLK", 0.0003, dips=False), "XLF": sym("XLF", 0.0003, dips=False)}
    sector_of = {"AAA": "XLK", "BBB": "XLK", "CCC": "XLK", "DDD": "XLF", "EEE": "XLF", "FFF": "XLF"}
    stocks = {s: sym(s, price=50 + 20 * i) for i, s in enumerate(sector_of)}
    return stocks, etfs, sector_of, sym("SPY", 0.0002, dips=False)


def replay(stocks, etfs, sector_of, spy):
    pol = load_profiles()["balanced"]
    prep = prepare(stocks, etfs, sector_of, {}, START, END)
    reg = {d: (np.zeros(N_MIN, dtype=bool), 18.0) for d in spy.dates if START <= d <= END}
    book = run_portfolio(prep.bars, prep.cands, reg, set(), pol, variant="A")
    tpd = pd.Series([t.date for t in book.trades]).value_counts().sort_index()
    rand = random_entry_runs(prep.bars, prep.atr5, prep.vwap, prep.sigma, tpd, pol, 100_000.0, n_runs=5,
                             variant="A")
    return book, rand


def test_same_inputs_same_hash():
    inputs = market(11)
    book1, rand1 = replay(*inputs)
    book2, rand2 = replay(*copy.deepcopy(inputs))
    assert len(book1.trades) >= 5, "fixture must actually trade"
    assert events_digest(book1) == events_digest(book2)
    assert np.array_equal(rand1, rand2)
    assert input_digest(inputs[0]) == input_digest(copy.deepcopy(inputs[0]))


def test_any_change_changes_the_hash():
    stocks, etfs, sector_of, spy = market(11)
    book1, _ = replay(stocks, etfs, sector_of, spy)
    t = book1.trades[len(book1.trades) // 2]
    sd = stocks[t.symbol]
    r = int(np.nonzero(sd.dates == t.date)[0][0])
    sd2 = copy.deepcopy(sd)
    sd2.o[r, t.entry_m] *= 1.001  # one price, one minute
    book2, _ = replay({**stocks, t.symbol: sd2}, etfs, sector_of, spy)
    assert events_digest(book1) != events_digest(book2)
    assert input_digest(stocks) != input_digest({**stocks, t.symbol: sd2})


def test_event_stream_is_ordered_and_complete():
    book, _ = replay(*market(11))
    evs = list(events(book))
    assert sum(e["type"] == "entry" for e in evs) == len(book.trades)
    assert sum(e["type"] == "day_close" for e in evs) == len(book.daily)
    keys = [(e["date"], e.get("m", 10**6)) for e in evs]
    assert keys == sorted(keys)
