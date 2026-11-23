// lib/tick.ts: the direction of a move, the characters that changed, and the coalescing of bursts.
import { describe, expect, it } from "vitest";
import { TICK_GAP_MS, TICK_IN_MS, TICK_OUT_MS, TINT_MS, changedPart, tickDelay, tickDir } from "./tick";

describe("the direction of a move", () => {
  it("up, down; equal, unknown and non-finite values do not move", () => {
    expect(tickDir(100, 100.01)).toBe("up");
    expect(tickDir(100, 99.99)).toBe("down");
    expect(tickDir(-5, -4)).toBe("up");
    expect(tickDir(100, 100)).toBeNull();
    expect(tickDir(null, 1)).toBeNull();
    expect(tickDir(1, undefined)).toBeNull();
    expect(tickDir(Infinity, 1)).toBeNull();
    expect(tickDir(1, NaN)).toBeNull();
  });
  it("the timings: 120 ms in, about 600 ms out, a 250 ms window", () => {
    expect([TICK_IN_MS, TICK_OUT_MS, TINT_MS, TICK_GAP_MS]).toEqual([120, 560, 680, 250]);
  });
});

describe("the characters that changed", () => {
  it("only the digits that moved; the same start and, at the same length, the same end are kept", () => {
    expect(changedPart("$3,173.60", "$3,173.65")).toEqual(["$3,173.6", "5", ""]);
    expect(changedPart("$3,173.60", "$3,193.60")).toEqual(["$3,1", "9", "3.60"]);
    expect(changedPart("+$38.20 · 1.2%", "+$39.20 · 1.2%")).toEqual(["+$3", "9", ".20 · 1.2%"]);
  });
  it("a figure that grew or shrank changes from the first different character to its end", () => {
    expect(changedPart("$999.99", "$1,000.01")).toEqual(["$", "1,000.01", ""]);
    expect(changedPart("−$1.00", "+$1.00")).toEqual(["", "+", "$1.00"]);
  });
  it("the same text: nothing changed", () => expect(changedPart("$5.00", "$5.00")).toEqual(["$5.00", "", ""]));
});

describe("coalescing", () => {
  it("the wait: none at first or after the window, the rest of the window inside it", () => {
    expect(tickDelay(null, 1_000)).toBe(0);
    expect(tickDelay(1_000, 1_000)).toBe(250);
    expect(tickDelay(1_000, 1_100)).toBe(150);
    expect(tickDelay(1_000, 1_250)).toBe(0);
    expect(tickDelay(1_000, 900)).toBe(0); // the clock went back
  });
  it("a stock that prices every 10 ms ticks at most once a window, and the last value of a burst is the one shown", () => {
    // the loop components/Tick.tsx runs: a value that has to wait is held, and the latest one is shown at the window's end
    let shown = 100, at: number | null = null, due: number | null = null, latest = shown;
    const ticks: [number, number][] = [];
    const commit = (t: number) => { if (tickDir(shown, latest)) { ticks.push([t, latest]); at = t; } shown = latest; due = null; };
    for (let t = 0; t <= 2_000; t += 10) {
      if (due !== null && t >= due) commit(t);
      latest = Math.round((100 + Math.sin(t / 7) * 5) * 100) / 100;
      if (due !== null) continue;                   // a value waits already: the latest wins at its end
      const wait = tickDir(shown, latest) ? tickDelay(at, t) : 0;
      if (wait) due = t + wait; else commit(t);
    }
    for (let i = 1; i < ticks.length; i++) expect(ticks[i][0] - ticks[i - 1][0]).toBeGreaterThanOrEqual(TICK_GAP_MS);
    expect(ticks.length).toBeGreaterThanOrEqual(7);
    expect(ticks.length).toBeLessThanOrEqual(9);     // 2 s / 250 ms, plus the first
  });
});
