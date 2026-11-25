// Streamed prices (lib/stream.ts): the relay's broadcast over the 10 s poll, and the silent fallback after 5 s.
import { describe, expect, it } from "vitest";
import type { Marks } from "./marks";
import { STREAM_STALE_MS, applyStream, mergeStream, parseStream, streamLive } from "./stream";

const NOW = Date.parse("2026-11-19T17:40:00Z");
const POLL: Marks = { at: NOW - 4_000, by: { XOM: { qty: 20, price: 158.68, prevClose: 160.58 }, TMO: { qty: 4, price: 686.36, prevClose: 690 } } };
const msg = (prices: Record<string, unknown>, at = NOW - 500) => parseStream({ as_of: "2026-11-19T17:39:59.500Z", feed: "iex", prices }, at);

describe("parseStream", () => {
  it("reads what the relay sends and keeps only valid prices", () => {
    expect(msg({ XOM: 158.9, TMO: 0, bad: 1, BIIB: "246", APA: Number.NaN })).toEqual({ at: NOW - 500, asOf: "2026-11-19T17:39:59.500Z", prices: { XOM: 158.9 } });
  });
  it("refuses anything else", () => {
    for (const raw of [null, "x", [], { feed: "sip", as_of: "2026-11-19T17:39:59Z", prices: { XOM: 1 } }, { feed: "iex", as_of: "never", prices: { XOM: 1 } },
      { feed: "iex", as_of: "2026-11-19T17:39:59Z", prices: [] }, { feed: "iex", as_of: "2026-11-19T17:39:59Z", prices: { XOM: -1 } }]) {
      expect(parseStream(raw, NOW)).toBeNull();
    }
  });
});

describe("applyStream: the stream over the poll", () => {
  it("a live stream replaces the polled price of each stock it carries; qty and the previous close stay the poll's", () => {
    const m = applyStream(POLL, msg({ XOM: 158.9, NVDA: 140 }), NOW)!;
    expect(m.by.XOM).toEqual({ qty: 20, price: 158.9, prevClose: 160.58 });
    expect(m.by.TMO).toBe(POLL.by.TMO);                // beyond the stream's 30: the poll's price
    expect(m.by.NVDA).toBeUndefined();                 // a stock the poll does not list is never added
    expect(m.at).toBe(POLL.at);                        // liveBook still judges the poll's own age
    expect(m.streamAt).toBe(NOW - 500);
  });
  it("falls back to exactly the poll's marks when the stream is silent for more than 5 s", () => {
    const s = msg({ XOM: 158.9 }, NOW - STREAM_STALE_MS - 1);
    expect(streamLive(s, NOW)).toBe(false);
    expect(applyStream(POLL, s, NOW)).toBe(POLL);
    expect(applyStream(POLL, msg({ XOM: 158.9 }, NOW - STREAM_STALE_MS), NOW)?.streamAt).toBeDefined();
  });
  it("no poll, no stream, or a stream with none of the polled stocks: the poll as it is", () => {
    expect(applyStream(null, msg({ XOM: 1 }), NOW)).toBeNull();
    expect(applyStream(POLL, null, NOW)).toBe(POLL);
    expect(applyStream(POLL, msg({ NVDA: 1 }), NOW)).toBe(POLL);
  });
  it("a newer message wins; a stock only the older one carries keeps its price", () => {
    const m = mergeStream(msg({ XOM: 1, TMO: 2 }, NOW - 1_000), msg({ XOM: 3 }, NOW)!);
    expect(m).toMatchObject({ at: NOW, prices: { XOM: 3, TMO: 2 } });
  });
});
