// #604: one freshness rule for every figure that can go stale (lib/freshness.ts).
import { describe, expect, it } from "vitest";
import { EVERY, LIVE, ageWords, bookFreshness, freshness, holdingsFreshness, liveFor, newsFreshness, nextChange } from "./freshness";

const T = Date.parse("2026-11-25T17:40:00Z"); // Wednesday 12:40 in New York
const MIN = 60_000;

describe("the rule: live, a few minutes old, stale with its age", () => {
  it("live while at most max(60 s, 2 x the interval) old: no words at all", () => {
    expect(liveFor(EVERY.prices)).toBe(60_000);
    expect(liveFor(EVERY.heartbeat)).toBe(90_000); // the engine chip's limit
    expect(liveFor(EVERY.minute)).toBe(120_000);
    expect(freshness(T - 60_000, EVERY.prices, T)).toEqual(LIVE);
    expect(freshness(T + 2_000, EVERY.prices, T)).toEqual(LIVE); // a clock a little ahead
    expect(LIVE.text).toBeNull();
  });
  it("then ten minutes of 'A few minutes old', then stale with whole minutes, hours or days; never seconds", () => {
    expect(freshness(T - 60_001, EVERY.prices, T)).toEqual({ state: "recent", text: "A few minutes old" });
    expect(freshness(T - 11 * MIN, EVERY.prices, T)).toEqual({ state: "recent", text: "A few minutes old" });
    expect(freshness(T - 11 * MIN - 1, EVERY.prices, T)).toEqual({ state: "stale", text: "Stale · 11 min old" });
    expect(freshness(T - 3 * 60 * MIN - 59_000, EVERY.heartbeat, T).text).toBe("Stale · 3 h old");
    expect(freshness(T - 3 * 24 * 60 * MIN, EVERY.heartbeat, T).text).toBe("Stale · 3 d old");
    for (const age of [61_000, 5 * MIN, 30 * MIN, 5 * 3_600_000]) expect(freshness(T - age, EVERY.prices, T).text).not.toMatch(/\d+ s\b|ago/);
  });
  it("no time at all is stale, and says it cannot tell its age", () => {
    expect(freshness(null, EVERY.prices, T)).toEqual({ state: "stale", text: "Stale · age unknown" });
    expect(freshness(NaN, EVERY.prices, T).state).toBe("stale");
  });
  it("age words round down", () => {
    expect(ageWords(119 * MIN + 59_000)).toBe("119 min");
    expect(ageWords(120 * MIN)).toBe("2 h");
    expect(ageWords(47 * 60 * MIN)).toBe("47 h");
    expect(ageWords(48 * 60 * MIN)).toBe("2 d");
  });
  it("the next change of state, for a page that draws again by itself", () => {
    expect(nextChange(T, EVERY.prices, T)).toBe(T + 60_001);
    expect(nextChange(T - 2 * MIN, EVERY.prices, T)).toBe(T - 2 * MIN + 11 * MIN + 1);
    expect(nextChange(T - 20 * MIN, EVERY.prices, T)).toBeNull();
    expect(nextChange(null, EVERY.prices, T)).toBeNull();
  });
});

describe("the pot and the holdings", () => {
  it("nothing while the page is not watching (market closed, hidden tab, the first answer on its way)", () => {
    expect(bookFreshness({ kind: "engine", at: T - 3_600_000 }, false, null, T)).toBeNull();
    expect(bookFreshness({ kind: "close", session: "2026-11-24" }, false, T - 3_600_000, T)).toBeNull();
  });
  it("live prices are as old as the held stock with the oldest price", () => {
    expect(bookFreshness({ kind: "live", at: T - 2_000 }, true, T - 2_000, T)).toEqual(LIVE);
    expect(bookFreshness({ kind: "live", at: T - 2_000 }, true, T - 90_000, T)?.text).toBe("A few minutes old");
  });
  it("the engine's minute row is as old as the row", () => {
    expect(bookFreshness({ kind: "engine", at: T - 100_000 }, true, null, T)).toEqual(LIVE);
    expect(bookFreshness({ kind: "engine", at: T - 3 * MIN }, true, null, T)?.state).toBe("recent");
    expect(bookFreshness({ kind: "engine", at: T - 40 * MIN }, true, null, T)?.text).toBe("Stale · 40 min old");
  });
  it("a booked close in market hours is the booked figure (no 'at the close' line): only the prices' own age is said", () => {
    expect(bookFreshness({ kind: "close", session: "2026-11-24" }, true, T - 5_000, T)).toEqual(LIVE);
    expect(bookFreshness({ kind: "close", session: "2026-11-24" }, true, T - 5 * MIN, T)?.state).toBe("recent");
    expect(bookFreshness({ kind: "close", session: "2026-11-24" }, true, null, T)).toBeNull(); // no prices: nothing to say
  });
  it("the holdings follow their own source: live prices, else the engine's marks, else the booked close", () => {
    const live = { kind: "live" as const, at: T - 1_000 };
    expect(holdingsFreshness({ fresh: live, live: {}, engine: null }, "2026-11-24", true, T - 70_000, T)?.text).toBe("A few minutes old");
    expect(holdingsFreshness({ fresh: { kind: "engine", at: T - 5_000 }, live: null, engine: { at: T - 30 * MIN } }, "2026-11-24", true, null, T)?.text)
      .toBe("Stale · 30 min old");
    // the pot is the engine's row, the rows are at the close marks: the rows say the prices' age
    expect(holdingsFreshness({ fresh: { kind: "engine", at: T - 5_000 }, live: null, engine: null }, "2026-11-24", true, T - 2 * MIN, T)?.state).toBe("recent");
    expect(holdingsFreshness(null, null, true, T - 20 * MIN, T)).toBeNull();
  });
});

describe("the headlines: the news feed's own freshness (heartbeat `news`)", () => {
  const news = (last_ok: string | null, every_s?: number) => ({ news: { state: "ok", last_ok, every_s } });
  it("by the poller's own interval", () => {
    expect(newsFreshness(news(new Date(T - 20 * MIN).toISOString(), 1800), true, T)).toEqual(LIVE);
    expect(newsFreshness(news(new Date(T - 65 * MIN).toISOString(), 1800), true, T)?.text).toBe("A few minutes old");
    expect(newsFreshness(news(new Date(T - 3 * 60 * MIN).toISOString(), 300), true, T)?.text).toBe("Stale · 3 h old");
    expect(newsFreshness(news(new Date(T - 9 * MIN).toISOString()), true, T)).toEqual(LIVE); // 5 min when it does not say
  });
  it("nothing without the block, or while the engine is asleep on purpose; never polled is stale", () => {
    expect(newsFreshness({ state: "trading" }, true, T)).toBeNull();
    expect(newsFreshness(news(new Date(T - 9 * 3_600_000).toISOString()), false, T)).toBeNull();
    expect(newsFreshness(news(null), true, T)).toEqual({ state: "stale", text: "Stale · age unknown" });
  });
});
