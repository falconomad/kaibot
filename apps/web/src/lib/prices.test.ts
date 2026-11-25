// api.prices (lib/api.ts): every page shares ONE Realtime channel for kb:prices. supabase-js hands out one channel
// per topic, so a page that left "its" channel used to take the next page's with it (Live -> Positions), and a join
// while the old channel was still leaving got that dead channel back. No network: the client is a fake.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const rt = vi.hoisted(() => {
  type Ch = { topic: string; config: unknown; handlers: ((m: unknown) => void)[]; status?: (s: string, e?: Error) => void; removed: boolean };
  const state = { channels: [] as Ch[], made: 0, removed: 0, offline: false };
  const client = {
    realtime: { setAuth: async () => {} },
    getChannels: () => state.channels.filter((c) => !c.removed),
    channel(topic: string, opts: { config: unknown }) {
      state.made++;
      // like realtime-js: on() and subscribe() return the channel itself
      const c = { topic: `realtime:${topic}`, config: opts.config, handlers: [], removed: false } as unknown as Ch & {
        on: (t: string, f: unknown, h: (m: unknown) => void) => unknown; subscribe: (cb: (s: string, e?: Error) => void) => unknown };
      c.on = (_t, _f, h) => { c.handlers.push(h); return c; };
      c.subscribe = (cb) => { c.status = cb; return c; };
      state.channels.push(c);
      return c;
    },
    removeChannel: async (c: Ch) => { c.removed = true; state.removed++; return "ok"; },
  };
  return { state, client };
});
// lib/supa.ts hands the realtime client out on demand (it is not in the first load); the fake is that client.
vi.mock("./supa", () => ({ createClient: () => ({ realtime: async () => {
  if (rt.state.offline) throw new TypeError("Failed to fetch dynamically imported module");
  return { ...rt.client, setAuth: rt.client.realtime.setAuth };
} }) }));

const { api } = await import("./api");
const flush = async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); };
const live = () => rt.state.channels.filter((c) => !c.removed);
const send = (prices: Record<string, number>) =>
  live().forEach((c) => c.handlers.forEach((h) => h({ payload: { as_of: new Date().toISOString(), feed: "iex", prices } })));

beforeEach(() => { vi.useFakeTimers(); rt.state.channels = []; rt.state.made = 0; rt.state.removed = 0; rt.state.offline = false; });
afterEach(async () => { await vi.runAllTimersAsync(); vi.useRealTimers(); });

describe("api.prices: one shared private channel", () => {
  it("joins kb:prices once, private, and hands every message to every page", async () => {
    const a = vi.fn(), b = vi.fn();
    const offA = api.prices(a), offB = api.prices(b);
    await flush();
    expect(rt.state.made).toBe(1);
    expect(live()[0]).toMatchObject({ topic: "realtime:kb:prices", config: { private: true } });
    send({ XOM: 158.6 });
    expect(a).toHaveBeenCalledTimes(1);
    expect(b).toHaveBeenCalledTimes(1);
    offA();
    send({ XOM: 158.7 });
    expect(a).toHaveBeenCalledTimes(1);   // a page that stopped gets nothing more
    expect(b).toHaveBeenCalledTimes(2);   // and the other page keeps its channel
    expect(rt.state.removed).toBe(0);
    offB();
  });

  it("a move between pages keeps the channel; it is left 2 s after the last page stops", async () => {
    const off1 = api.prices(vi.fn());
    await flush();
    off1();                                // Live unmounts ...
    const b = vi.fn();
    const off2 = api.prices(b);            // ... and Positions mounts in the same commit
    await flush();
    await vi.advanceTimersByTimeAsync(5_000);
    expect(rt.state.made).toBe(1);
    expect(rt.state.removed).toBe(0);
    send({ XOM: 1 });
    expect(b).toHaveBeenCalledTimes(1);
    off2();
    await vi.advanceTimersByTimeAsync(1_999);
    expect(rt.state.removed).toBe(0);
    await vi.advanceTimersByTimeAsync(1);
    expect(rt.state.removed).toBe(1);
    expect(live()).toEqual([]);
  });

  it("a refusal leaves the channel and nothing retries until a page asks again; a channel still there is removed first", async () => {
    const off = api.prices(vi.fn());
    await flush();
    const [ch] = live();
    ch.status?.("CHANNEL_ERROR", new Error("Unauthorized: You do not have permissions to read from this Channel topic: kb:prices"));
    expect(ch.removed).toBe(true);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(rt.state.made).toBe(1);
    off();
    // a leftover of this topic (e.g. one still leaving) is removed before the new join, never reused
    rt.client.channel("kb:prices", { config: { private: true } });
    const left = live()[0];
    const off2 = api.prices(vi.fn());
    await flush();
    expect(left.removed).toBe(true);
    expect(live()).toHaveLength(1);
    off2();
  });

  it("the realtime code could not be fetched: no channel and no error; the next page that asks tries again", async () => {
    rt.state.offline = true;
    const off1 = api.prices(vi.fn());
    await flush();
    expect(rt.state.made).toBe(0);
    off1();
    await vi.advanceTimersByTimeAsync(2_000);
    rt.state.offline = false;
    const b = vi.fn();
    const off2 = api.prices(b);
    await flush();
    expect(rt.state.made).toBe(1);
    send({ XOM: 2 });
    expect(b).toHaveBeenCalledTimes(1);
    off2();
  });
});

describe("api.prices: the stream's state (joined, then lost)", () => {
  it("a refusal by the owner policy is no stream here, not a drop: never down, and the channel is left", async () => {
    for (const msg of ["Unauthorized: You do not have permissions to read from this Channel topic: kb:prices", "new row violates row-level security policy"]) {
      rt.state.channels = [];
      const down = vi.fn();
      const off = api.prices(vi.fn(), down);
      await flush();
      const [ch] = live();
      ch.status?.("SUBSCRIBED");
      ch.status?.("CHANNEL_ERROR", new Error(msg));
      expect(ch.removed, msg).toBe(true);
      expect(down.mock.calls.map((c) => c[0]), msg).toEqual([false]); // told "not down" when asked, never "down"
      await vi.advanceTimersByTimeAsync(10_000);
      expect(down.mock.calls.some((c) => c[0] === true), msg).toBe(false);
      off();
    }
  });
  it("a channel joined and then lost is down until supabase-js joins it again; a channel never joined is not down", async () => {
    const down = vi.fn();
    const off = api.prices(vi.fn(), down);
    await flush();
    const [ch] = live();
    ch.status?.("TIMED_OUT");                       // never joined: no stream to have lost
    expect(down.mock.calls.map((c) => c[0])).toEqual([false]);
    ch.status?.("SUBSCRIBED");
    ch.status?.("CHANNEL_ERROR", new Error("socket closed"));
    expect(down.mock.calls.at(-1)?.[0]).toBe(true);
    expect(ch.removed).toBe(false);                 // kept: supabase-js joins it again by itself
    ch.status?.("SUBSCRIBED");
    expect(down.mock.calls.at(-1)?.[0]).toBe(false);
    off();
  });
});
