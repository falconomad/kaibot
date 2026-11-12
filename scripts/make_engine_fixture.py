"""Write the committed bar fixture for the engine-spine goldens and the engine/replay parity test (issue #115).

    python scripts/make_engine_fixture.py            # rewrites tests/fixtures/engine_spine/
    python scripts/make_engine_fixture.py --check    # exit 1 if the committed files differ from this script

The real 1-minute history (12 GB) lives only on the stakeholder's Mac, so the fixture is synthetic and seeded:
five stocks in two sectors, the two sector ETFs and SPY, 64 sessions ending 17 Sep 2026.

- Sessions 1-61 are warm-up for the daily statistics (20-day volatility and volume, 60-day beta, the 20-day
  cumulative-volume profile). They carry ONE bar every 30 minutes, which is all those statistics read, and keeps
  the file small. The engine and the replay both treat a minute without a bar as "no trades" (last close
  carried forward, zero volume), so both see exactly the same history.
- Sessions 62-64 (15, 16 and 17 Sep 2026) carry every minute. Sector-wide sell-offs with different endings make
  the dip rule trade: rebounds (target, trailing stop), further falls (stop), late dips (time exit).
- 16 Sep 2026 is an FOMC day (14:00 statement), 17 Sep has a VIX above 25 (half size), one stock has a
  headline before a dip (news veto) and EEE is thin (minutes without a bar).

Prices are stored in cents and volume-weighted prices in 1/100 of a cent (integers, delta-encoded) so that the
file is small and identical on every machine. `numpy.random.RandomState` is used on purpose: its stream is
frozen across NumPy versions, the newer `Generator` is not guaranteed to be.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "fixtures" / "engine_spine"
ET = "America/New_York"
N_MIN = 390
SEED = 7
N_WARMUP, WARMUP_STEP = 61, 30
TEST_DAYS = ("2026-09-15", "2026-09-16", "2026-09-17")
HOLIDAYS = ("2026-06-19", "2026-07-03", "2026-09-07")  # NYSE closures inside the fixture's range
SECTOR_OF = {"AAA": "XLK", "BBB": "XLK", "CCC": "XLK", "DDD": "XLF", "EEE": "XLF"}
MARKET = "SPY"
FOMC = ("2026-09-16",)
VIX_PREV_CLOSE = {"2026-09-15": 17.6, "2026-09-16": 19.2, "2026-09-17": 26.4}  # other sessions: 18.0
VIX_DEFAULT = 18.0
THIN = {"EEE": 0.25}  # share of test-session minutes without a bar (never the first minute)
# symbol: (start price, beta to its sector / the market, idiosyncratic 1-minute vol, shares per minute)
STOCKS = {"AAA": (184.0, 1.20, 0.00050, 6_000), "BBB": (62.0, 0.90, 0.00040, 14_000),
          "CCC": (311.0, 1.40, 0.00060, 3_000), "DDD": (47.0, 1.00, 0.00045, 16_000),
          "EEE": (128.0, 1.10, 0.00055, 2_400)}
ETFS = {"XLK": (228.0, 1.10, 0.00020, 20_000), "XLF": (52.0, 0.90, 0.00020, 60_000)}
SPY = (655.0, 0.00030, 150_000)  # start price, 1-minute vol of the market factor, shares per minute
# Sell-offs on the test sessions: (session, sector, first minute, minutes, fall of the sector, then per stock the
# path after the short bounce off the low). The bounce gives every stock in the sector a higher low; what
# follows decides the trade: "target" rallies, "trail" rallies then fades, "stop" rolls over, "flat" drifts.
DIPS = (
    ("2026-09-15", "XLK", 36, 24, -0.017, {"AAA": "target", "BBB": "trail", "CCC": "stop"}),
    ("2026-09-15", "XLF", 150, 26, -0.021, {"DDD": "target", "EEE": "trail"}),
    ("2026-09-15", "XLK", 318, 22, -0.026, {"AAA": "flat", "BBB": "flat", "CCC": "flat"}),
    ("2026-09-16", "XLF", 62, 24, -0.018, {"DDD": "stop", "EEE": "target"}),
    ("2026-09-16", "XLK", 120, 24, -0.021, {"AAA": "trail", "BBB": "target", "CCC": "stop"}),
    ("2026-09-16", "XLK", 236, 24, -0.024, {"AAA": "target", "BBB": "target", "CCC": "target"}),  # in the blackout
    ("2026-09-16", "XLF", 328, 22, -0.026, {"DDD": "flat", "EEE": "flat"}),
    ("2026-09-17", "XLK", 44, 24, -0.018, {"AAA": "stop", "BBB": "stop", "CCC": "stop"}),
    ("2026-09-17", "XLF", 96, 22, -0.020, {"DDD": "target", "EEE": "target"}),
    ("2026-09-17", "XLK", 205, 24, -0.026, {"AAA": "target", "BBB": "trail", "CCC": "target"}),
)
BOUNCE_MIN, BOUNCE = 8, 0.004
AFTER_MIN = 40
# total log move over (first 14 minutes, last 26 minutes) of the 40 minutes after the bounce
PATHS = {"target": (0.010, 0.016), "trail": (0.009, -0.012), "stop": (-0.006, -0.009), "flat": (0.001, 0.002)}
NEWS = {"EEE": ("2026-09-16T11:05:00Z",)}  # a headline before EEE's dip on 16 Sep: vetoed, live and in replay
SPY_GAP = {"2026-09-15": 0.006}  # SPY opens strong on 15 Sep, so SPY late momentum has a signal that day


def sessions() -> list[str]:
    days = np.busdaycalendar(holidays=list(HOLIDAYS))
    first = np.busday_offset(TEST_DAYS[0], -N_WARMUP, roll="forward", busdaycal=days)
    out = np.busday_offset(first, np.arange(N_WARMUP + len(TEST_DAYS)), busdaycal=days)
    assert [str(d) for d in out[-len(TEST_DAYS):]] == list(TEST_DAYS)
    return [str(d) for d in out]


def _returns(rng) -> dict[str, np.ndarray]:
    """Per-minute log returns (sessions x 390) and overnight gaps for every symbol, from a factor model."""
    days = sessions()
    nd = len(days)
    row = {d: i for i, d in enumerate(days)}
    mkt = rng.normal(0.0, SPY[1], (nd, N_MIN))
    gap_mkt = rng.normal(0.0, 0.004, nd)
    for d, g in SPY_GAP.items():
        gap_mkt[row[d]] = g
    out = {MARKET: (mkt, gap_mkt)}
    sector = {}
    for e, (_, beta, vol, _) in ETFS.items():
        r = beta * mkt + rng.normal(0.0, vol, (nd, N_MIN))
        for day, sec, m0, n, fall, _ in DIPS:
            if sec == e:
                r[row[day], m0:m0 + n] += fall / n
                r[row[day], m0 + n:m0 + n + BOUNCE_MIN] += BOUNCE / BOUNCE_MIN
        sector[e] = r
        out[e] = (r, beta * gap_mkt + rng.normal(0.0, 0.002, nd))
    for s, (_, beta, vol, _) in STOCKS.items():
        e = SECTOR_OF[s]
        r = beta * sector[e] + rng.normal(0.0, vol, (nd, N_MIN))
        for day, sec, m0, n, _, after in DIPS:
            if sec == e:
                lo = m0 + n + BOUNCE_MIN
                first, rest = PATHS[after[s]]
                r[row[day], lo:lo + 14] += first / 14
                r[row[day], lo + 14:lo + AFTER_MIN] += rest / (AFTER_MIN - 14)
        out[s] = (r, beta * out[e][1] + rng.normal(0.0, 0.004, nd))
    return out


def _bars(rng, price0: float, r: np.ndarray, gap: np.ndarray, shares: int) -> dict[str, np.ndarray]:
    """OHLCV arrays (sessions x 390). Prices in whole cents; the open of a minute sits within a cent of the
    previous close, highs and lows up to three cents outside the open/close range."""
    nd = len(r)
    c = np.empty((nd, N_MIN))
    o = np.empty((nd, N_MIN))
    prev = price0
    for d in range(nd):
        first = prev * np.exp(gap[d])
        c[d] = first * np.exp(np.cumsum(r[d]))
        o[d, 0] = first
        o[d, 1:] = c[d, :-1]
        prev = c[d, -1]
    c = np.round(c * 100)
    o = np.round(o * 100)
    o[:, 1:] += rng.randint(-1, 2, (nd, N_MIN - 1))
    h = np.maximum(o, c) + rng.randint(0, 4, (nd, N_MIN))
    lo = np.minimum(o, c) - rng.randint(0, 4, (nd, N_MIN))
    m = np.arange(N_MIN)
    shape = 0.7 + 1.6 * np.exp(-m / 25.0) + 1.1 * np.exp(-(N_MIN - 1 - m) / 30.0)  # busy open and close
    v = np.round(shares * shape[None, :] * np.exp(rng.normal(0.0, 0.35, (nd, N_MIN))))
    vw = np.round((h + lo + c) / 3 * 100)
    return {"o": o, "h": h, "l": lo, "c": c, "v": v, "vw": vw}


def _thin_warmup(a: dict[str, np.ndarray]) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """30-minute bars for one warm-up session: (minute of each bar, columns)."""
    k = N_MIN // WARMUP_STEP

    def blocks(x):
        return x.reshape(k, WARMUP_STEP)

    v = blocks(a["v"]).sum(axis=1)
    vw = np.round((blocks(a["vw"]) * blocks(a["v"])).sum(axis=1) / np.maximum(v, 1))
    return np.arange(0, N_MIN, WARMUP_STEP), {"o": blocks(a["o"])[:, 0], "h": blocks(a["h"]).max(axis=1),
                                               "l": blocks(a["l"]).min(axis=1), "c": blocks(a["c"])[:, -1],
                                               "v": v, "vw": vw}


def build() -> pd.DataFrame:
    """One long frame: symbol, ts (UTC), o, h, l, c in cents, v in shares, vw in 1/100 cent (all integers)."""
    rng = np.random.RandomState(SEED)
    days = sessions()
    rets = _returns(rng)
    spec = {MARKET: (SPY[0], SPY[2]), **{e: (p, n) for e, (p, _, _, n) in ETFS.items()},
            **{s: (p, n) for s, (p, _, _, n) in STOCKS.items()}}
    parts = []
    for sym in sorted(spec):
        price0, shares = spec[sym]
        r, gap = rets[sym]
        arr = _bars(rng, price0, r, gap, shares)
        for d, day in enumerate(days):
            one = {k: x[d] for k, x in arr.items()}
            if d < N_WARMUP:
                mins, cols = _thin_warmup(one)
            else:
                keep = np.ones(N_MIN, dtype=bool)
                if sym in THIN:
                    keep = rng.uniform(size=N_MIN) >= THIN[sym]
                    keep[0] = True
                mins, cols = np.flatnonzero(keep), {k: x[keep] for k, x in one.items()}
            open_utc = pd.Timestamp(f"{day} 09:30", tz=ET).tz_convert("UTC")
            f = pd.DataFrame({k: cols[k].astype(np.int64) for k in ("o", "h", "l", "c", "v", "vw")})
            f.insert(0, "ts", open_utc + pd.to_timedelta(mins, unit="m"))
            f.insert(0, "symbol", sym)
            parts.append(f)
    return pd.concat(parts, ignore_index=True)


def meta() -> dict:
    days = sessions()
    return {
        "issue": 115,
        "seed": SEED,
        "sessions": days,
        "test_days": list(TEST_DAYS),
        "sector_of": SECTOR_OF,
        "market": MARKET,
        "fomc": list(FOMC),
        "vix_prev_close": {d: VIX_PREV_CLOSE.get(d, VIX_DEFAULT) for d in days},
        "news": {s: list(ts) for s, ts in NEWS.items()},
        "units": {"o,h,l,c": "cents", "vw": "1/100 cent", "v": "shares", "ts": "bar start, UTC"},
    }


def write(out: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    out.mkdir(parents=True, exist_ok=True)
    df = build()
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, out / "bars.parquet", compression="zstd", compression_level=19, use_dictionary=["symbol"],
                   column_encoding={k: "DELTA_BINARY_PACKED" for k in ("ts", "o", "h", "l", "c", "v", "vw")},
                   write_statistics=False)
    (out / "meta.json").write_text(json.dumps(meta(), indent=1, sort_keys=True) + "\n")


def check(out: Path) -> bool:
    have = pd.read_parquet(out / "bars.parquet")
    want = build()
    same_bars = have.reset_index(drop=True).equals(want.astype(have.dtypes.to_dict()))
    same_meta = json.loads((out / "meta.json").read_text()) == json.loads(json.dumps(meta()))
    return bool(same_bars and same_meta)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--check", action="store_true", help="compare the committed files with this script's output")
    a = ap.parse_args()
    out = Path(a.out)
    if a.check:
        ok = check(out)
        print("fixture matches the script" if ok else "fixture DIFFERS from the script")
        sys.exit(0 if ok else 1)
    write(out)
    size = sum(f.stat().st_size for f in out.iterdir())
    print(f"wrote {out} ({size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
