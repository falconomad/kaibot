// #724 (M19 ticket 8.6 of the second audit): the owner's notices of #604 that were still missing. N6 last seen on
// <host>, N7 the database lag, N9 the database out of reach, N10 "Since you last looked", N12 the price source, the
// static "How kaibo decides" page and the halt reason as a tappable chip.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { DB_DOWN, LAG_TEXT, PRICE_SOURCE, dbUnreachable, hostOf, lagFreshness, lastSeenText, replicationLag } from "./lib/ownernotes";
import { KEY, MAX_LINES, VISIT_MS, digest, nextLook, takeLook } from "./lib/sincelast";
import { ErrorState } from "./components/States";
import { engineChip } from "./components/Shell";
import { tradingState } from "./lib/tradingstate";
import { HaltChip, HaltSheetBody } from "./components/HaltChip";
import { HowItDecides } from "./pages/HowItDecides";
import { More } from "./pages/More";
import { Live } from "./pages/Live";
import { Tape } from "./pages/Tape";
import SinceLastLook from "./components/SinceLastLook";
import { ROUTE_LOADERS } from "./routes";
import { ctxFor, paperRun, render, text } from "./pages/testkit";
import type { Overview, TapeItem } from "./lib/api";
import shellSrc from "./components/Shell.tsx?raw";
import positionsSrc from "./pages/Positions.tsx?raw";
import mainSrc from "./main.tsx?raw";
import sinceCss from "./components/since.css?raw";
import chipCss from "./components/haltchip.css?raw";

vi.mock("./lib/api", () => import("./pages/testkit-api"));

const run = paperRun();
const OPEN = new Date("2026-11-19T17:40:00Z"); // a Thursday, 12:40 in New York
const beat = (detail: Record<string, unknown>, ageS = 600) => ({ process: "engine", ts: new Date(OPEN.getTime() - ageS * 1000).toISOString(), detail });
const overview = (detail: Record<string, unknown>, ageS = 600): Overview =>
  ({ run, daily: [], heartbeat: [beat(detail, ageS)], pending_commands: [], ledger: null, today: null, halts_today: [] });

describe("N6: last seen on <host>", () => {
  beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(OPEN); });
  afterEach(() => { vi.useRealTimers(); });
  it("the host is one short printable word, or nothing", () => {
    expect(hostOf({ host: "kai-macbook.local" })).toBe("kai-macbook.local");
    expect(hostOf({ host: "  bad‮host\n" })).toBe("badhost");
    expect(hostOf({ host: "x".repeat(90) })).toHaveLength(40);
    expect(hostOf({ host: "" })).toBeNull();
    expect(hostOf({})).toBeNull();
    expect(hostOf(null)).toBeNull();
    expect(lastSeenText("mac")).toBe("last seen on mac");
  });
  it("the offline chip in market hours says which machine it was last seen on; Home's row reads the same note", () => {
    const c = engineChip(overview({ state: "trading", host: "kai-macbook.local" }), run);
    expect(c.live).toBe(false);
    expect(c.text).toBe("Engine offline");
    expect(c.host).toBe("kai-macbook.local");
    expect(c.note).toBe("Engine offline · no new trades until it is back · last seen on kai-macbook.local");
  });
  it("no host field while live, asleep or never connected", () => {
    expect(engineChip(overview({ state: "trading", host: "mac" }, 5), run).host).toBeUndefined();
    vi.setSystemTime(new Date("2026-11-20T03:00:00Z")); // 22:00 in New York: asleep
    expect(engineChip(overview({ state: "trading", host: "mac" }, 4 * 3600), run).host).toBeUndefined();
    expect(engineChip({ ...overview({}), heartbeat: [] }, run).host).toBeUndefined();
  });
  it("the top bar renders it beside the chip (hidden on phones, where Home's row says it)", () => {
    expect(shellSrc).toContain('{chip.host && <span className="num hide-sm chip-host">· {lastSeenText(chip.host)}</span>}');
  });
});

describe("N7: the database lag", () => {
  it("only the heartbeat's backlog alarm counts, with the rows waiting when the engine says", () => {
    expect(replicationLag({ replication: { backlog_alarm: true, pending: 412 } })).toEqual({ pending: 412 });
    expect(replicationLag({ replication: { backlog_alarm: true } })).toEqual({ pending: null });
    expect(replicationLag({ replication: { backlog_alarm: false, pending: 9000 } })).toBeNull();
    expect(replicationLag({})).toBeNull();
    expect(lagFreshness({ replication: { backlog_alarm: true } })).toEqual({ state: "stale", text: LAG_TEXT });
    expect(LAG_TEXT).toBe("kaibo's record is ahead of this screen · figures may be behind");
  });
  it("Home says it amber under the pot, in the freshness component", () => {
    const o = overview({ state: "trading", host: "mac", replication: { backlog_alarm: true, pending: 12 } }, 5);
    const html = render(<Live />, { ctx: ctxFor(run, { overview: o }), seeds: [{ key: ["trades-all", 1], data: { total: 0, rows: [] } }] });
    expect(html).toMatch(/<p class="fresh stale" role="status"><span class="sr-only">Figures: <\/span>kaibo(&#x27;|')s record is ahead of this screen · figures may be behind<\/p>/);
  });
  it("not while the copy keeps up", () => {
    const o = overview({ state: "trading", replication: { backlog_alarm: false } }, 5);
    const html = render(<Live />, { ctx: ctxFor(run, { overview: o }), seeds: [{ key: ["trades-all", 1], data: { total: 0, rows: [] } }] });
    expect(text(html)).not.toContain("record is ahead");
  });
});

describe("N9: the database out of reach is its own state", () => {
  const net = new TypeError("Failed to fetch");
  it("connection errors are; answers from the database are not", () => {
    for (const e of [net, new Error("TypeError: Load failed"), { message: "fetch failed" }, { message: "", code: "PGRST002" },
      { message: "x", code: "08006" }, { message: "x", code: "57P01" }, new Error("HTTP 503"), new Error("Bad Gateway"), new Error("canceling statement due to statement timeout")])
      expect(dbUnreachable(e), JSON.stringify(e) + String(e)).toBe(true);
    for (const e of [Object.assign(new Error("permission denied for function"), { code: "42501" }), { message: "x", code: "PGRST202" },
      new Error("not authorised"), null, undefined, 42])
      expect(dbUnreachable(e)).toBe(false);
  });
  it("the shared error state says it is the connection, not the engine, and that stops stay at the broker", () => {
    const html = renderToStaticMarkup(<ErrorState what="The run overview" error={net} retry={() => {}} />);
    expect(html).toContain('data-state="db-unreachable"');
    const t = text(html);
    expect(t).toContain(`${DB_DOWN}.`);
    expect(t).toContain("The run overview couldn't load. This is the connection to the database, not the engine. The engine runs on its own, and stops stay at the broker.");
    expect(t).toContain("Try again");
  });
  it("another error keeps the old words", () => {
    const t = text(renderToStaticMarkup(<ErrorState what="Trades" error={new Error("permission denied")} />));
    expect(t).toContain("Trades couldn't load.");
    expect(t).not.toContain(DB_DOWN);
  });
  it("the shell shows a strip when the overview fails at the connection with older figures on screen", () => {
    expect(shellSrc).toContain("{o && overview.isError && dbUnreachable(overview.error) && (");
    expect(shellSrc).toContain('<div className="riskstrip warn db-down" role="status">');
  });
});

describe("N10: since you last looked", () => {
  const T0 = Date.parse("2026-11-19T12:00:00Z");
  it("the last visit: none, then the earlier one; a visit lasts VISIT_MS so a reload keeps its digest", () => {
    expect(nextLook(null, T0)).toEqual({ since: null, store: { at: T0, prev: null } });
    expect(nextLook({ at: T0 }, T0 + VISIT_MS + 1)).toEqual({ since: T0, store: { at: T0 + VISIT_MS + 1, prev: T0 } });
    expect(nextLook({ at: T0, prev: T0 - 5e6 }, T0 + 60_000)).toEqual({ since: T0 - 5e6, store: { at: T0, prev: T0 - 5e6 } });
    expect(nextLook({ at: T0 + 1e9 }, T0).since).toBeNull(); // a time in the future is no visit
    expect(nextLook({ at: "x" }, T0).since).toBeNull();
    expect(nextLook("garbage", T0).since).toBeNull();
  });
  it("storage that throws (private mode, full, blocked) means no card, never an error", () => {
    const bad = { getItem: () => { throw new Error("SecurityError"); }, setItem: () => { throw new Error("QuotaExceededError"); } };
    expect(takeLook(1, T0, bad)).toBeNull();
    const junk = { getItem: () => "{not json", setItem: () => {} };
    expect(takeLook(1, T0, junk)).toBeNull();
    const m = new Map<string, string>();
    const ok = { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => { m.set(k, v); } };
    expect(takeLook(1, T0, ok)).toBeNull();
    expect(JSON.parse(m.get(`${KEY}.1`)!)).toEqual({ at: T0, prev: null });
    expect(takeLook(1, T0 + VISIT_MS + 5, ok)).toBe(T0);
    expect(takeLook(2, T0, null)).toBeNull();
  });

  const at = (min: number) => new Date(T0 + min * 60_000).toISOString();
  const fill = (min: number, side: "buy" | "sell", symbol: string): TapeItem => ({ kind: "fill", ts: at(min), side, qty: 3, venue_px: 100, shadow_px: null, fees: null,
    tag: null, symbol, strategy: "trend_momentum", trade_id: null, exit_reason: null, pnl_usd: null, source: "multi_day" });
  const sig = (min: number, symbol: string, approved: boolean): TapeItem => ({ kind: "signal", ts: at(min), mode: "live", strategy: "trend_momentum", symbol,
    entry: 1, stop: 1, target: 1, reasons: [], veto: approved ? null : "x", approved, qty: 1, binding: null, trade_id: null, counterfactual: null });
  const ev = (min: number, type: string, payload: Record<string, unknown> = {}, run_id: number | null = 1) => ({ type, ts: at(min), run_id, payload });

  it("reds first, then warnings, buys and sells, decisions; one line per cause; only this run; only since the last look", () => {
    const events = [ev(-5, "halt", { kind: "daily_stop", reason: "x" }), ev(10, "halt", { kind: "daily_stop", reason: "day -1.6%" }), ev(20, "halt", { kind: "daily_stop", reason: "again" }),
      ev(30, "order_adopted"), ev(31, "command_rejected"), ev(32, "engine_error"), ev(33, "multi_day_carry", { carried: [["AAPL", 4, 0], ["MSFT", 2, 0]] }),
      ev(34, "reconcile_blocked", {}, 2), ev(35, "session_start"), ev(36, "clock_skew", { state: "ok" })];
    const tape = [fill(40, "sell", "NVDA"), fill(41, "sell", "AVGO"), fill(42, "buy", "LLY"), fill(-1, "buy", "OLD"), sig(43, "COST", true), sig(44, "CAT", false)];
    const d = digest(T0, events, tape, 1);
    expect(d.lines.map((l) => l.text)).toEqual([
      "Halt: Daily stop: flattened and halted for today (2 times)",
      "A command from the dashboard was refused by the engine",
      "Order left by a crash taken back", // worth knowing, not a warning: after the warnings
      "Bought LLY", // buys and sells newest first
      "Sold NVDA, AVGO (2 fills)",
    ]);
    expect(d.lines.length).toBe(MAX_LINES);
    expect(d.more).toBe(3); // decided, passed on, carried
    expect(d.lines[0]).toMatchObject({ tone: "red", to: `/activity?show=alerts&at=${encodeURIComponent(at(20))}` });
    expect(d.lines[4].to).toBe(`/activity?show=fills&at=${encodeURIComponent(at(41))}`);
    expect(d.lines[2].tone).toBe("calm");
    expect(d.lines[1].to).toBe("/health");
  });
  it("decisions: buys decided, setups passed on, carries", () => {
    const d = digest(T0, [ev(33, "multi_day_carry", { carried: [["AAPL", 4, 0]] })], [sig(43, "COST", true), sig(44, "CAT", false), sig(45, "AMD", false)], 1);
    expect(d.lines.map((l) => l.text)).toEqual(["Passed on 2 setups (CAT, AMD)", "Decided to buy COST", "kaibo carried 1 unfilled buy to the next session"]);
    expect(d.lines[0].to).toBe(`/activity?show=skipped&at=${encodeURIComponent(at(45))}`);
  });
  it("nothing since: no lines", () => {
    expect(digest(T0, [ev(-1, "halt", { kind: "daily_stop" })], [fill(-2, "buy", "X")], 1)).toEqual({ lines: [], more: 0 });
    expect(digest(T0, undefined, undefined, 1)).toEqual({ lines: [], more: 0 });
  });

  describe("the card", () => {
    const store = new Map<string, string>();
    beforeEach(() => {
      store.clear();
      vi.stubGlobal("localStorage", { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => { store.set(k, v); } });
    });
    afterEach(() => { vi.unstubAllGlobals(); });
    const seeds = [{ key: ["health"], data: { now: at(60), heartbeat: [], halts: [], events: [ev(10, "halt", { kind: "daily_stop", reason: "r" })], deadman: { armed: true, jobs: [], runs: [] }, pending_commands: [] } },
      { key: ["tape-since", 1], data: [fill(40, "sell", "NVDA")] }];
    it("with an earlier visit: the heading, the time and the lines, each a link", () => {
      store.set(`${KEY}.1`, JSON.stringify({ at: T0 - 2 * VISIT_MS }));
      const html = render(<SinceLastLook run={run} />, { seeds });
      const t = text(html);
      expect(t).toContain("Since you last looked");
      expect(t).toContain("Halt: Daily stop: flattened and halted for today");
      expect(t).toContain("Sold NVDA");
      expect(html).toContain('href="/activity?show=fills&amp;at=');
      expect(html).toContain('class="since-row red"');
    });
    it("the first visit ever records it and shows nothing", () => {
      expect(render(<SinceLastLook run={run} />, { seeds })).toBe("");
      expect(JSON.parse(store.get(`${KEY}.1`)!).prev).toBeNull();
    });
    it("Home loads it lazily, in a boundary of its own, for a live paper run", async () => {
      const src = (await import("./pages/Live.tsx?raw")).default;
      expect(src).toContain('const SinceLastLook = lazy(() => import("../components/SinceLastLook"));');
      expect(src).toContain('<ErrorBoundary name="Since you last looked" compact><Suspense fallback={null}><SinceLastLook run={run} /></Suspense></ErrorBoundary>');
      expect(sinceCss).toContain("min-height: 48px");
    });
  });

  it("Activity opens the row the card names: ?show picks the chip", () => {
    const html = render(<Tape />, { path: "/activity?show=fills", route: "/activity", seeds: [{ key: ["tape", 1], data: [], infinite: true }] });
    expect(html).toMatch(/aria-pressed="true"[^>]*>Buys &amp; sells</);
  });
});

describe("N12: where prices come from", () => {
  it("one line in Home's details and in the position sheet", () => {
    expect(PRICE_SOURCE).toBe("Live prices are from one exchange (IEX). The stop line and P&L use confirmed closing marks.");
    const o = overview({ state: "trading" }, 5);
    const t = text(render(<Live />, { ctx: ctxFor(run, { overview: o }), seeds: [{ key: ["trades-all", 1], data: { total: 0, rows: [] } }] }));
    expect(t).toContain(PRICE_SOURCE);
    expect(positionsSrc).toContain('<p className="muted price-source" style={{ fontSize: 14 }}>{PRICE_SOURCE}</p>');
  });
});

describe("How kaibo decides", () => {
  it("a static page in plain words, lazy, reached from More and Home's details", () => {
    const t = text(renderToStaticMarkup(<MemoryRouter><HowItDecides /></MemoryRouter>));
    expect(t).toContain("How kaibo decides");
    for (const s of ["A language model never picks a stock, a price, a size, a stop or the moment to sell.", "It may hold back a buy or halve it, and nothing else.",
      "The stop stays there when the engine, the Mac or this dashboard is down.", "Paper trading only"]) expect(t).toContain(s);
    expect(t).not.toMatch(/\byou should\b|\bgood time to\b|kaibo thinks/i);
    expect(ROUTE_LOADERS["/how"]).toBeTypeOf("function");
    expect(mainSrc).toContain('<Route path="how" element={page("How it decides", <HowItDecides />)} />');
    const more = render(<More />, { path: "/more", route: "/more" });
    expect(more).toContain('href="/how"');
    const home = render(<Live />, { ctx: ctxFor(run, { overview: overview({ state: "trading" }, 5) }), seeds: [{ key: ["trades-all", 1], data: { total: 0, rows: [] } }] });
    expect(home).toContain('href="/how"');
  });
});

describe("the halt reason as a tappable chip", () => {
  const h = { kind: "losses", reason: "3 consecutive losing trades; entries paused until 13:05 ET", ts: "2026-11-19T17:15:00Z", run_id: 1 };
  it("a button (44 px to tap) that opens a dialog, never a tooltip", () => {
    const html = renderToStaticMarkup(<MemoryRouter><HaltChip h={h} label="Why it halted" /></MemoryRouter>);
    expect(html).toMatch(/^<button type="button" class="halt-chip" aria-haspopup="dialog" data-kind="losses"><span class="tag veto">Why it halted<\/span><\/button>$/);
    expect(html).not.toContain("title=");
    expect(chipCss).toContain("min-height: 44px");
  });
  it("its sheet: the plain words, the engine's own reason, when (ET and CET), the state and the code", () => {
    const t = text(renderToStaticMarkup(<MemoryRouter><HaltSheetBody h={h} /></MemoryRouter>));
    expect(t).toContain("In the engine's words: 3 consecutive losing trades; entries paused until 13:05 ET");
    expect(t).toContain("12:15 ET (18:15 CET)");
    expect(t).toContain("in force");
    expect(t).toContain("losses");
    const over = text(renderToStaticMarkup(<MemoryRouter><HaltSheetBody h={h} cleared="2026-11-19T18:05:00Z" /></MemoryRouter>));
    expect(over).toContain("Over: the engine cleared it at 13:05 ET (19:05 CET).");
  });
  it("the trading state carries the halt in force, so Engine can put the chip beside the line", () => {
    const o = { ...overview({ state: "trading", lease: "ok" }, 5), halts_today: [h] };
    vi.useFakeTimers(); vi.setSystemTime(OPEN);
    try {
      const s = tradingState(run, o, [], OPEN.getTime());
      expect(s.kind).toBe("selling_only");
      expect(s.halt).toEqual(h);
    } finally { vi.useRealTimers(); }
  });
});
