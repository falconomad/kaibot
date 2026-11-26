// The S&P 500 side of Home (stakeholder, 24 Nov 2026: "Ahead of / Behind the S&P 500 by $X" and the chart legend did not
// refresh during the session). The pot was live, SPY held its last close: the gap moved only with our stocks. Now the
// live point's SPY = the last close's SPY value x (SPY now / SPY's previous close), from the same /api/marks answer,
// when that price is fresh (the prices' 30 s rule) and its previous close is the session the line ends at. Otherwise
// SPY stays at the last close and the text says "at last close". The hero, the gap and the legend read one point.
// Pure maths: no network, no clock but the one passed in.
import { describe, expect, it } from "vitest";
import type { Curve } from "./model";
import { STALE_MS, type Fresh, type SpyMark, homeLine, parseMarks, parseSpy, spyFactor, vsSameText, vsSpyText } from "./marks";
import { applyStream } from "./stream";

// Thursday 19 Nov 2026, 12:40 in New York. The line's last close is Wednesday's.
const TODAY = "2026-11-19", CLOSE = "2026-11-18";
const NOW = Date.parse("2026-11-19T17:40:04Z");
const START = 95_000;
const CLOSES: Curve[] = [{ session: "2026-11-17", pot: 95_000, spy: 93_832.07 }, { session: CLOSE, pot: 94_997.26, spy: 92_917.28 }];
// SPY traded at 662.10 two seconds ago; Wednesday's close was 659.30
const SPY: SpyMark = { price: 662.1, at: NOW - 2_000, prevClose: 659.3, prevSession: CLOSE };
const K = 662.1 / 659.3;
const live = (pot = 94_958.46, at = NOW - 4_000): { pot: number; fresh: Fresh } => ({ pot, fresh: { kind: "live", at } });
const engine = (pot = 94_960): { pot: number; fresh: Fresh } => ({ pot, fresh: { kind: "engine", at: NOW - 40_000 } });

/** What Home shows from one homeLine answer, worked out as Live.tsx works it out. */
function home(c: Curve[], p: { pot: number; fresh: Fresh | null } | null, spy: SpyMark | null | undefined, now = NOW) {
  const { curve, end, spyAt } = homeLine(c, p, spy, TODAY, START, now);
  const hero = end?.pot ?? p?.pot ?? START;
  const vs = (hero - START) - (end ? end.spy - START : 0);
  const legend = { pot: curve.at(-1)!.pot, spy: curve.at(-1)!.spy, label: `SPY, same start${curve.at(-1)!.spyClose ? " (last close)" : ""}` };
  return { curve, end, spyAt, hero, vs, legend, text: vsSpyText(vs, spyAt) };
}

describe("the answer's spy", () => {
  it("is read with its trade time on the server's clock, as the prices' age is", () => {
    const body = { available: true, as_of: "2026-11-19T17:40:00.000Z", positions: [],
      spy: { price: 662.1, as_of: "2026-11-19T17:39:58.000Z", prev_close: 659.3, prev_session: CLOSE } };
    // the device's clock is 3 minutes fast: the age (6 s on the server) is counted back from receipt
    const here = NOW + 180_000;
    const m = parseMarks(body, here, "Thu, 19 Nov 2026 17:40:04 GMT")!;
    expect(m.at).toBe(here - 4_000);
    expect(m.spy).toEqual({ price: 662.1, at: here - 6_000, prevClose: 659.3, prevSession: CLOSE });
  });
  it("is absent or broken: no spy, the positions as they are", () => {
    for (const spy of [undefined, null, {}, { price: 0, as_of: "2026-11-19T17:39:58Z" }, { price: 662, as_of: "later" }, "662", { price: Number.NaN, as_of: "2026-11-19T17:39:58Z" }]) {
      const m = parseMarks({ available: true, as_of: "2026-11-19T17:40:00.000Z", positions: [], spy }, NOW);
      expect(m, JSON.stringify(spy)).not.toBeNull();
      expect(m!.spy).toBeUndefined();
    }
  });
  it("a previous close without its session (or a bad one) is no previous close", () => {
    const at = "2026-11-19T17:39:58Z";
    expect(parseSpy({ price: 662, as_of: at, prev_close: null, prev_session: null })).toMatchObject({ prevClose: null, prevSession: null });
    expect(parseSpy({ price: 662, as_of: at, prev_close: 659.3, prev_session: null })).toMatchObject({ prevClose: null, prevSession: null });
    expect(parseSpy({ price: 662, as_of: at, prev_close: 0, prev_session: CLOSE })).toMatchObject({ prevClose: null, prevSession: null });
    expect(parseSpy({ price: 662, as_of: at, prev_close: 659.3, prev_session: "Nov 18" })).toMatchObject({ prevClose: null, prevSession: null });
  });
  it("survives the stream: streamed stock prices never drop SPY", () => {
    const m = { at: NOW - 3_000, by: { XOM: { qty: 20, price: 158.68, prevClose: 160.58 } }, spy: SPY };
    expect(applyStream(m, { at: NOW - 200, asOf: "2026-11-19T17:40:03.8Z", prices: { XOM: 158.9 } }, NOW)!.spy).toEqual(SPY);
  });
});

describe("spyFactor: when SPY now may be used", () => {
  it("fresh and against the line's own last close: SPY now / SPY's previous close", () => {
    expect(spyFactor(SPY, CLOSE, NOW)).toBeCloseTo(K, 12);
    expect(spyFactor({ ...SPY, at: NOW - STALE_MS }, CLOSE, NOW)).toBeCloseTo(K, 12); // 30 s exactly: still fresh
  });
  it("stale (the same 30 s rule as the prices), or from the future: null", () => {
    expect(spyFactor({ ...SPY, at: NOW - STALE_MS - 1 }, CLOSE, NOW)).toBeNull();
    expect(spyFactor({ ...SPY, at: NOW + 5_000 }, CLOSE, NOW)).toBeNull();
  });
  it("prev close null, SPY missing, or a previous close of another session than the line's: null", () => {
    expect(spyFactor({ ...SPY, prevClose: null, prevSession: null }, CLOSE, NOW)).toBeNull();
    expect(spyFactor(null, CLOSE, NOW)).toBeNull();
    expect(spyFactor(undefined, CLOSE, NOW)).toBeNull();
    // the line's last close is Tuesday's (Wednesday's not booked yet): SPY's move since Wednesday is not its move since Tuesday
    expect(spyFactor(SPY, "2026-11-17", NOW)).toBeNull();
    expect(spyFactor(SPY, undefined, NOW)).toBeNull();
  });
});

describe("homeLine: the hero, the gap and the legend read one point", () => {
  it("live pot and SPY now: the live point's SPY moves, and all three agree", () => {
    const h = home(CLOSES, live(), SPY);
    expect(h.spyAt).toBe("live");
    expect(h.end).toEqual({ session: TODAY, pot: 94_958.46, spy: 92_917.28 * K, live: true });
    expect(h.curve).toHaveLength(3);
    expect(CLOSES).toHaveLength(2); // the closes are not changed
    expect(h.hero).toBe(94_958.46);
    expect(h.legend.pot).toBe(h.hero);
    expect(h.legend.spy).toBe(h.end!.spy);
    expect(h.vs).toBeCloseTo((94_958.46 - START) - (92_917.28 * K - START), 9);
    expect(h.legend.label).toBe("SPY, same start");
    expect(h.text).toBe(`Ahead of the S&P 500 by $${(Math.round(Math.abs(h.vs) * 100) / 100).toLocaleString("en-US", { minimumFractionDigits: 2 })}`);
  });
  it("the gap now moves with SPY too: the same pot, SPY up 1 %, the gap 1 % of SPY's value smaller", () => {
    const a = home(CLOSES, live(), SPY), b = home(CLOSES, live(), { ...SPY, price: 659.3 * 1.01 });
    expect(a.hero).toBe(b.hero);
    expect(a.vs - b.vs).toBeCloseTo(92_917.28 * 1.01 - 92_917.28 * K, 6);
    // and SPY flat on its close: the old figure exactly
    expect(home(CLOSES, live(), { ...SPY, price: 659.3 }).end!.spy).toBe(92_917.28);
  });
  it("the engine's minute row is live too: SPY now applies the same way", () => {
    const h = home(CLOSES, engine(), SPY);
    expect(h.spyAt).toBe("live");
    expect(h.end!.spy).toBeCloseTo(92_917.28 * K, 9);
    expect(h.legend.pot).toBe(94_960);
  });
  it("no fresh SPY (stale, missing, prev close null, another session): the last close, as before, and it says so", () => {
    for (const spy of [{ ...SPY, at: NOW - 31_000 }, null, undefined, { ...SPY, prevClose: null, prevSession: null }, { ...SPY, prevSession: "2026-11-17" }]) {
      const h = home(CLOSES, live(), spy);
      expect(h.spyAt, JSON.stringify(spy)).toBe("close");
      expect(h.end).toEqual({ session: TODAY, pot: 94_958.46, spy: 92_917.28, live: true, spyClose: true });
      expect(h.legend.label).toBe("SPY, same start (last close)");
      expect(h.text).toMatch(/^(Ahead of|Behind) the S&P 500 at last close by \$/);
      expect(h.legend.pot).toBe(h.hero);
    }
  });
  it("the market closed (a weekend, after the close, an early close: no polls, the pot is a booked close): closes on both sides, no note", () => {
    const sat = Date.parse("2026-11-21T16:00:00Z");
    const h = home(CLOSES, { pot: 94_997.26, fresh: { kind: "close", session: CLOSE } }, { ...SPY, at: sat - 1_000 }, sat);
    expect(h.spyAt).toBe("none");
    expect(h.curve).toEqual(CLOSES);
    expect(h.end).toEqual(CLOSES[1]);
    expect(h.legend.label).toBe("SPY, same start");
    expect(h.vs).toBeCloseTo((94_997.26 - START) - (92_917.28 - START), 9); // the closes' own gap: +$2,079.98
    expect(h.text).toBe("Ahead of the S&P 500 by $2,079.98");
    expect(h.text).not.toContain("last close");
  });
  it("a booked close the line's last point does not carry yet: the point takes the book's figure, so the hero and the legend agree", () => {
    const h = home(CLOSES, { pot: 94_990, fresh: { kind: "close", session: CLOSE } }, null);
    expect(h.end).toEqual({ session: CLOSE, pot: 94_990, spy: 92_917.28 });
    expect(h.legend.pot).toBe(h.hero);
  });
  it("today's point already on the line: it is replaced, and SPY is measured against the close before it", () => {
    const c = [...CLOSES, { session: TODAY, pot: 94_900, spy: 92_000 }];
    const h = home(c, live(), SPY);
    expect(h.curve).toHaveLength(3);
    expect(h.end).toEqual({ session: TODAY, pot: 94_958.46, spy: 92_917.28 * K, live: true });
    const f = home(c, live(), null);
    expect(f.end).toEqual({ session: TODAY, pot: 94_958.46, spy: 92_000, live: true, spyClose: true });
  });
  it("no closes yet (day one): the live point starts from the run's start; SPY now has no close to move from", () => {
    const h = home([], live(95_010), SPY);
    expect(h.end).toEqual({ session: TODAY, pot: 95_010, spy: START, live: true, spyClose: true });
    expect(h.spyAt).toBe("close");
  });
  it("a replay (no potLive): the line as it is", () => {
    const h = home(CLOSES, null, SPY);
    expect(h.curve).toEqual(CLOSES);
    expect(h.spyAt).toBe("none");
    expect(h.hero).toBe(94_997.26);
  });
});

describe("vsSpyText", () => {
  it("says ahead or behind, the amount, and 'at last close' only when a live pot meets SPY's last close", () => {
    expect(vsSpyText(1234.5, "live")).toBe("Ahead of the S&P 500 by $1,234.50");
    expect(vsSpyText(-1234.5, "none")).toBe("Behind the S&P 500 by $1,234.50");
    expect(vsSpyText(0, "live")).toBe("Ahead of the S&P 500 by $0.00");
    expect(vsSpyText(-10, "close")).toBe("Behind the S&P 500 at last close by $10.00");
    expect(vsSpyText(10, "close", false)).toBe("Ahead of the S&P 500 at last close");
  });
});

describe("#719: the same amount in SPY (SPY at the pot's own invested share)", () => {
  // the closes with the matched line, as mtmCurve gives them: 16% invested at Wednesday's close
  const M: Curve[] = [{ session: "2026-11-17", pot: 95_000, spy: 93_832.07, spyMatched: 95_000, exposure: 0.1 },
    { session: CLOSE, pot: 94_997.26, spy: 92_917.28, spyMatched: 94_907.33, exposure: 0.16 }];
  const lines = (c: Curve[], p: { pot: number; fresh: Fresh | null } | null, spy: SpyMark | null | undefined, now = NOW) => {
    const { end, spyAt } = homeLine(c, p, spy, TODAY, START, now);
    const hero = end?.pot ?? p?.pot ?? START;
    return [vsSpyText((hero - START) - (end!.spy - START), spyAt), vsSameText((hero - START) - (end!.spyMatched! - START), spyAt)];
  };

  it("live with SPY now: the matched point moves by the last close's invested share of SPY's move", () => {
    const { end } = homeLine(M, live(), SPY, TODAY, START, NOW);
    expect(end!.spyMatched).toBeCloseTo(94_907.33 * (1 + 0.16 * (K - 1)), 9);
    expect(lines(M, live(), SPY)).toMatchInlineSnapshot(`
      [
        "Ahead of the S&P 500 by $1,646.57",
        "Behind the same amount in SPY by $13.36",
      ]
    `);
  });
  it("live without a fresh SPY: both lines hold the last close and both say so", () => {
    const { end } = homeLine(M, live(), null, TODAY, START, NOW);
    expect(end!.spyMatched).toBe(94_907.33);
    expect(lines(M, live(), null)).toMatchInlineSnapshot(`
      [
        "Ahead of the S&P 500 at last close by $2,041.18",
        "Ahead of the same amount in SPY at last close by $51.13",
      ]
    `);
  });
  it("the market closed: the closes' own gaps, no note", () => {
    expect(lines(M, { pot: 94_997.26, fresh: { kind: "close", session: CLOSE } }, null)).toMatchInlineSnapshot(`
      [
        "Ahead of the S&P 500 by $2,079.98",
        "Ahead of the same amount in SPY by $89.93",
      ]
    `);
  });
  it("today's point already on the line: replaced, and measured from the close before it", () => {
    const c = [...M, { session: TODAY, pot: 94_900, spy: 92_000, spyMatched: 94_800, exposure: 0.2 }];
    expect(homeLine(c, live(), SPY, TODAY, START, NOW).end!.spyMatched).toBeCloseTo(94_907.33 * (1 + 0.16 * (K - 1)), 9);
    expect(homeLine(c, live(), null, TODAY, START, NOW).end!.spyMatched).toBe(94_800);
  });
  it("closes without it (a replay's curve, an older series): no matched point, and the S&P 500 side as before", () => {
    expect(homeLine(CLOSES, live(), SPY, TODAY, START, NOW).end).not.toHaveProperty("spyMatched");
    expect(homeLine([], live(95_010), SPY, TODAY, START, NOW).end).not.toHaveProperty("spyMatched");
  });
  it("vsSameText says ahead or behind, and 'at last close' under the same rule as vsSpyText", () => {
    expect([vsSameText(12.5, "live"), vsSameText(-12.5, "none"), vsSameText(-1, "close"), vsSameText(3, "close", false)]).toMatchInlineSnapshot(`
      [
        "Ahead of the same amount in SPY by $12.50",
        "Behind the same amount in SPY by $12.50",
        "Behind the same amount in SPY at last close by $1.00",
        "Ahead of the same amount in SPY at last close",
      ]
    `);
  });
});
