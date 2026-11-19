// Follow-ups to the engine changes merged by 19 Nov 2026 (main 3c66c53, migrations 0023-0028):
// 1. the exit kind of a position from the source (kb_book `stop_state` / `exit`, heartbeat multi_day.rules[*].exit and
//    multi_day.exits), lib/exits.ts only for older rows;
// 2. orders the venue let expire at the close (`multi_day_unfilled`) on Activity and in Positions' "At the close";
// 3. the daily note in plain words on Home after the close (`daily_note`, multi_day.daily_note);
// 4. the shadow pre-open review on Engine (`multi_day_review_shadow`, multi_day.rules[*].review_shadow);
// 5. the lifetime AI spend on Engine → LLM budget (kb_llm_budget, kb_summary_spend).
import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { ReactElement } from "react";
import { BookChecks } from "./components/Book";
import { HomeStatus } from "./components/Home";
import { DailyNoteCard } from "./components/DailyNote";
import { bookChecks, parseBook } from "./lib/book";
import { exitOf } from "./lib/exits";
import { multiDayFrom } from "./lib/experiment";
import { dailyNote, exitKinds, noteLabel, shadowLine, shadowReviews, showNote, unfilledFrom, unfilledText } from "./lib/engineevents";
import type { Health as HealthResp, Heartbeat, LlmBudget, Overview } from "./lib/api";
import { Health } from "./pages/Health";
import { Tape } from "./pages/Tape";
import { Positions } from "./pages/Positions";
import { ctxFor, emptyOverview, glitches, paperRun, render, text } from "./pages/testkit";
import css from "./app.css?raw";

vi.mock("./lib/api", () => import("./pages/testkit-api"));
vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });

const inRouter = (el: ReactElement) => renderToStaticMarkup(<MemoryRouter>{el}</MemoryRouter>);
const run = paperRun({ id: 7, amount_usd: 95000 });
const SESSION = "2026-11-19";
const pos = (symbol: string, over: Record<string, unknown> = {}) =>
  ({ rule: "trend_momentum", symbol, qty: 4, avg_cost: 100, mark: 101, last: 101, opened_session: SESSION, dividends: 0, stop: null, ...over });
const RAW = (positions: unknown[]) => ({ run_id: 7, session: SESSION, mtm: { equity: 95000, equity_reported: 95000 }, positions,
  exposure: { gross: 0.1, overnight: 0.1, by_rule: [] }, reconciled: { ok: true, ts: "2026-11-19T21:06:00Z", positions: {}, cash_diff: 0 } });
const beat = (multi_day: unknown, ts = "2026-11-19T22:10:00Z", state = "market_closed"): Heartbeat =>
  ({ process: "engine", ts, detail: { state, next_open: "2026-11-20T09:30:00-05:00", multi_day } as never });
const ev = (type: string, payload: Record<string, unknown>, ts = "2026-11-19T21:01:00Z", run_id: number | null = 7) => ({ type, ts, run_id, payload });

// ---------------------------------------------------------------- 1. exit kinds from the source

describe("1. the exit kind comes from the source; lib/exits.ts only for older rows", () => {
  it("parses stop_state and exit from kb_book (0028)", () => {
    const b = parseBook(RAW([pos("ABBV", { stop_state: "none_by_design", exit: "target_list" })]), 7);
    expect(b.positions[0]).toMatchObject({ stopState: "none_by_design", exit: "target_list" });
  });
  it("stop_state none_by_design: no stop by design, whatever the rule is called", () => {
    expect(exitOf({ rule: "some_new_rule", exit: "target_list", stopState: "none_by_design" })).toBe("target_list");
  });
  it("the heartbeat's exits map (and rules[*].exit) decides for a rule, a neighbour counts as its rule", () => {
    const md = multiDayFrom({ multi_day: { rules: { orb_daily: { exit: "target_list" } }, exits: { trend_momentum: "broker_stop" } } });
    const kinds = exitKinds(md);
    expect(kinds).toEqual({ orb_daily: "target_list", trend_momentum: "broker_stop" });
    expect(exitOf({ rule: "trend_momentum~reg60", exit: "broker_stop", stopState: "missing" }, kinds)).toBe("broker_stop");
    expect(exitOf({ rule: "orb_daily", exit: "broker_stop", stopState: "missing" }, kinds)).toBe("target_list");
  });
  it("an older row (stop_state missing, no heartbeat word): the fallback list in lib/exits.ts", () => {
    // kb_book derives `exit` from stop_state, so on a 'missing' row it cannot tell an old momentum row from a real alarm
    expect(exitOf({ rule: "trend_momentum", exit: "broker_stop", stopState: "missing" })).toBe("target_list");
    expect(exitOf({ rule: "trend_momentum", exit: null, stopState: null })).toBe("target_list");
    expect(exitOf({ rule: "orb", exit: null, stopState: "missing" })).toBe("broker_stop");
  });
  it("a resting or re-arming stop says broker_stop from the row itself", () => {
    expect(exitOf({ rule: "trend_momentum", exit: "broker_stop", stopState: "resting" })).toBe("broker_stop");
  });
  it("bookChecks: none_by_design rows are never missing; the heartbeat's broker_stop makes an old missing row an alarm", () => {
    const b = parseBook(RAW([pos("ABBV", { stop_state: "none_by_design", exit: "target_list" }),
      pos("KO", { stop_state: "missing", exit: "broker_stop" }), pos("NVDA", { rule: "orb", stop_state: "missing", exit: "broker_stop" })]), 7);
    expect(bookChecks(b)).toMatchObject({ n: 3, needed: 1, missing: ["NVDA"], byDesign: ["ABBV", "KO"] });
    const kinds = exitKinds(multiDayFrom({ multi_day: { exits: { trend_momentum: "broker_stop" } } }));
    expect(bookChecks(b, kinds)).toMatchObject({ needed: 2, missing: ["KO", "NVDA"], byDesign: ["ABBV"] });
  });
  it("Home and the book strip use the heartbeat's exits", () => {
    const b = parseBook(RAW([pos("KO", { stop_state: "missing", exit: "broker_stop" })]), 7);
    const o: Overview = { ...emptyOverview(run), heartbeat: [beat({ rules: { trend_momentum: { exit: "broker_stop" } }, exits: { trend_momentum: "broker_stop" } })] };
    const html = inRouter(<HomeStatus run={run} overview={o} engineLive book={b} risk={{ severity: "ok", items: [] }} />);
    expect(text(html)).toContain("1 stock has no safety stop");
    const calm = inRouter(<HomeStatus run={run} overview={emptyOverview(run)} engineLive book={b} risk={{ severity: "ok", items: [] }} />);
    expect(text(calm)).not.toContain("no safety stop");
    expect(text(inRouter(<BookChecks b={b} exits={{ trend_momentum: "broker_stop" }} />))).toContain("0 of 1 in place · KO missing");
    expect(text(inRouter(<BookChecks b={b} />))).toContain("None by design");
  });
});

// ---------------------------------------------------------------- 2. unfilled orders

// #333's payload (engine/live_daily.LiveDaily._report_unfilled), run_id dropped from the body
const UNFILLED = ev("multi_day_unfilled", { strategy: "trend_momentum", symbol: "MPC", side: "buy", qty: 10, filled: 6, left: 4,
  coid: "md-tm-MPC-1", session: SESSION, status: "expired", purpose: "entry", type: "market", tif: "day" });

describe("2. orders not filled at the close", () => {
  it("parses #333's payload and words it", () => {
    const [u] = unfilledFrom([UNFILLED, ev("halt", {})], 7);
    expect(u).toMatchObject({ rule: "trend_momentum", symbol: "MPC", side: "buy", qty: 10, filled: 6, left: 4, session: SESSION });
    expect(unfilledText(u)).toBe("MPC · 4 shares not filled · expired at the close");
    expect(unfilledText({ ...u, left: 1 })).toBe("MPC · 1 share not filled · expired at the close");
  });
  it("another run's events and broken payloads are dropped", () => {
    expect(unfilledFrom([{ ...UNFILLED, run_id: 8 }], 7)).toEqual([]);
    expect(unfilledFrom([ev("multi_day_unfilled", { symbol: "MPC" })], 7)).toEqual([]);
    expect(unfilledFrom([ev("multi_day_unfilled", { ...UNFILLED.payload, left: "x" })], 7)).toEqual([]);
  });
  it("Activity lists it as not filled", () => {
    const health: HealthResp = { now: "2026-11-19T22:00:00Z", heartbeat: [], halts: [], events: [UNFILLED], pending_commands: [],
      deadman: { armed: true, jobs: [], runs: [] } };
    const html = render(<Tape />, { ctx: ctxFor(run), seeds: [{ key: ["tape", 7], data: [], infinite: true },
      { key: ["health"], data: health }] });
    expect(text(html)).toContain("MPC 4 shares not filled · expired at the close");
    expect(glitches(html)).toEqual([]);
  });
  it("Positions' At the close list marks the order as not filled", () => {
    const health: HealthResp = { now: "2026-11-19T22:00:00Z", heartbeat: [], halts: [], events: [UNFILLED], pending_commands: [],
      deadman: { armed: true, jobs: [], runs: [] } };
    const plan = { rules: [{ rule: "trend_momentum", decided_after_close_of: "2026-11-18", fills_at_close_of: SESSION, sell: [], buy_candidates: [], skipped: [], note: null,
      orders: [{ symbol: "MPC", shares_now: 0, shares_target: 10, action: "buy", why: "top_rank", state: "canceled" }] }] };
    vi.useFakeTimers({ now: new Date("2026-11-19T22:00:00Z"), toFake: ["Date"] });
    try {
      const html = render(<Positions />, { ctx: ctxFor(run), seeds: [{ key: ["health"], data: health }, { key: ["md-plan", 7], data: plan },
        { key: ["book", 7], data: parseBook(RAW([]), 7) }] });
      const t = text(html);
      expect(t).toContain("At the close of Nov 19, 2026");
      expect(t).toContain("4 shares not filled · expired at the close");
    } finally { vi.useRealTimers(); }
  });
  it("Positions: an unfilled order of today with no plan row still shows", () => {
    const health: HealthResp = { now: "2026-11-19T22:00:00Z", heartbeat: [], halts: [], events: [UNFILLED], pending_commands: [],
      deadman: { armed: true, jobs: [], runs: [] } };
    vi.useFakeTimers({ now: new Date("2026-11-19T22:00:00Z"), toFake: ["Date"] });
    try {
      const html = render(<Positions />, { ctx: ctxFor(run), seeds: [{ key: ["health"], data: health }, { key: ["md-plan", 7], data: { rules: [] } },
        { key: ["book", 7], data: parseBook(RAW([]), 7) }] });
      expect(text(html)).toContain("MPC 4 shares not filled · expired at the close not filled");
    } finally { vi.useRealTimers(); }
  });
});

// ---------------------------------------------------------------- 3. the daily note

const NOTE_MODEL = { session: SESSION, text: "kaibo bought 7 stocks at the close and sold none.", source: "model" };
const NOTE_TEMPLATE = { session: SESSION, text: "Bought 7. Sold 0. Holding 7.", source: "template" };

describe("3. today in plain words", () => {
  it("reads the heartbeat's multi_day.daily_note and the daily_note event, the latest session wins", () => {
    const md = multiDayFrom({ multi_day: { daily_note: NOTE_MODEL } });
    expect(dailyNote(md, [])).toMatchObject({ session: SESSION, source: "model" });
    const older = ev("daily_note", { ...NOTE_TEMPLATE, session: "2026-11-18", why: "no ANTHROPIC_API_KEY", cost_usd: 0 });
    expect(dailyNote(md, [older])?.source).toBe("model");
    const newer = ev("daily_note", { ...NOTE_TEMPLATE, session: "2026-11-20", why: "no ANTHROPIC_API_KEY" });
    expect(dailyNote(md, [newer])).toMatchObject({ session: "2026-11-20", source: "template", why: "no ANTHROPIC_API_KEY" });
    expect(dailyNote(null, [])).toBeNull();
    expect(dailyNote(multiDayFrom({ multi_day: { daily_note: { session: SESSION, text: "  " } } }), [])).toBeNull();
  });
  it("labels: AI-written, or plainly the template", () => {
    expect(noteLabel({ session: SESSION, text: "x", source: "model", why: null })).toBe("Written by AI from today's records");
    expect(noteLabel({ session: SESSION, text: "x", source: "template", why: "no key" })).toBe("Written from a fixed template, not by AI");
  });
  it("shows after the close and until the next open, never during market hours or for an old day", () => {
    const n = { session: SESSION, text: "x", source: "model" as const, why: null };
    expect(showNote(n, new Date("2026-11-19T22:30:00Z"))).toBe(true);   // Thu 17:30 ET
    expect(showNote(n, new Date("2026-11-20T12:00:00Z"))).toBe(true);   // Fri 07:00 ET, before the open
    expect(showNote(n, new Date("2026-11-20T15:00:00Z"))).toBe(false);  // Fri 10:00 ET, market open
    expect(showNote(n, new Date("2026-11-22T12:00:00Z"))).toBe(true);   // Sunday
    expect(showNote(n, new Date("2026-11-26T22:30:00Z"))).toBe(false);  // a week later
  });
  it("the card says who wrote it; the text sits behind a tap on phones", () => {
    const html = inRouter(<DailyNoteCard note={{ ...NOTE_MODEL, source: "model", why: null }} now={new Date("2026-11-19T22:30:00Z")} />);
    const t = text(html);
    expect(t).toContain("Today in plain words");
    expect(t).toContain("Written by AI from today's records");
    expect(t).toContain("kaibo bought 7 stocks");
    expect(html).toContain('aria-haspopup="dialog"');
    const tpl = text(inRouter(<DailyNoteCard note={{ ...NOTE_TEMPLATE, source: "template", why: "no ANTHROPIC_API_KEY" }} now={new Date("2026-11-19T22:30:00Z")} />));
    expect(tpl).toContain("Written from a fixed template, not by AI");
    expect(tpl).not.toContain("Written by AI");
  });
  it("the styles hold text with nowhere to break: three lines on the card, wrapped inside the sheet", () => {
    // measured in a browser by scripts/mobile_check.py --stress (a link and a ticker list with no spaces, at 375 to 430 px)
    expect(css).toMatch(/\.note-text \{[^}]*overflow-wrap: anywhere;[^}]*-webkit-line-clamp: 3;[^}]*overflow: hidden;/);
    expect(css).toMatch(/\.note-full \{[^}]*overflow-wrap: anywhere;/);
    // the reason under the note ("the AI note was not used: ...") is the engine's own text and may be one long link
    expect(css).toMatch(/\.sheet-body \{[^}]*overflow-wrap: break-word;/);
  });
  it("names the day when the note is not today's", () => {
    const t = text(inRouter(<DailyNoteCard note={{ ...NOTE_MODEL, source: "model", why: null }} now={new Date("2026-11-20T12:00:00Z")} />));
    expect(t).toContain("Thu, Nov 19 in plain words");
    expect(t).toContain("Written by AI from that day's records");
  });
});

// ---------------------------------------------------------------- 4. the shadow review

const SHADOW_HB = { mode: "shadow", session: SESSION, status: "done", would_veto: ["APA"], would_downgrade: ["BBY"],
  reasons: { APA: "veto: news: guidance cut", BBY: "downgrade: event: earnings tomorrow" }, cost_usd: 0.004,
  note: "shadow review (observer, no effect): would veto APA, halve BBY" };

describe("4. the shadow pre-open review: observer only", () => {
  it("reads the heartbeat block per rule and the events, the newest per rule", () => {
    const md = multiDayFrom({ multi_day: { rules: { trend_momentum: { exit: "target_list", review_shadow: SHADOW_HB } } } });
    const old = ev("multi_day_review_shadow", { strategy: "trend_momentum", session: "2026-11-18", status: "done", mode: "shadow", acted: false,
      would_veto: [], would_downgrade: [], allowed: ["XOM"], reasons: {} }, "2026-11-18T13:40:00Z");
    const r = shadowReviews(md, [old]);
    expect(r).toHaveLength(1);
    expect(r[0]).toMatchObject({ rule: "trend_momentum", session: SESSION, veto: ["APA"], downgrade: ["BBY"] });
    expect(r[0].reasons).toEqual([{ symbol: "APA", verdict: "veto", why: "news: guidance cut" }, { symbol: "BBY", verdict: "downgrade", why: "event: earnings tomorrow" }]);
    expect(shadowReviews(null, [old])[0]).toMatchObject({ session: "2026-11-18", allowed: ["XOM"], veto: [] });
    expect(shadowReviews(null, [])).toEqual([]);
  });
  it("words: allow, veto, downgrade, not run, skipped", () => {
    const base = { rule: "trend_momentum", session: SESSION, status: "done", veto: [], downgrade: [], allowed: [], reasons: [], costUsd: null, error: null };
    expect(shadowLine(base)).toBe("Would allow every buy");
    expect(shadowLine({ ...base, veto: ["APA"], downgrade: ["BBY", "PFE"] })).toBe("Would veto APA · would halve BBY, PFE");
    expect(shadowLine({ ...base, status: "not_yet", session: null })).toBe("Not run yet today");
    expect(shadowLine({ ...base, status: "skipped_budget" })).toBe("Skipped: the AI budget said no");
    expect(shadowLine({ ...base, status: "failed", error: "timeout" })).toBe("Did not finish (timeout)");
  });
  it("Engine shows it under Today, labelled observer · no effect on trades", () => {
    const health: HealthResp = { now: "2026-11-19T14:00:00Z", heartbeat: [beat({ rules: { trend_momentum: { review_shadow: SHADOW_HB } } }, "2026-11-19T14:00:00Z", "trading")],
      halts: [], events: [], pending_commands: [], deadman: { armed: true, jobs: [], runs: [] } };
    const t = text(render(<Health />, { ctx: ctxFor(run), seeds: [{ key: ["health"], data: health }] }));
    expect(t).toContain("Pre-open reviewer");
    expect(t).toContain("observer · no effect on trades");
    expect(t).toContain("Would veto APA · would halve BBY");
  });
  it("no shadow block and no event: no row", () => {
    const health: HealthResp = { now: "2026-11-19T14:00:00Z", heartbeat: [beat({ rules: { trend_momentum: {} } })], halts: [], events: [],
      pending_commands: [], deadman: { armed: true, jobs: [], runs: [] } };
    expect(text(render(<Health />, { ctx: ctxFor(run), seeds: [{ key: ["health"], data: health }] }))).not.toContain("Pre-open reviewer");
  });
});

// ---------------------------------------------------------------- 5. lifetime AI spend

describe("5. the lifetime AI spend on Engine → LLM budget", () => {
  const quiet: HealthResp = { now: "2026-11-19T22:00:00Z", heartbeat: [], halts: [], events: [], pending_commands: [], deadman: { armed: true, jobs: [], runs: [] } };
  const budget: LlmBudget = { day: SESSION, day_usd: 0.012, month_usd: 1.5, state: "ok", alert: false,
    caps: { daily_soft: 1, monthly_alert: 20, monthly_hard: 25, lifetime_hard: 150 }, calls_today: 3, open_reserved_usd: 0, by_purpose: {},
    lifetime_usd: 12.34, lifetime_summaries_usd: 0.56 };
  const spend = { day_usd: 0.01, month_usd: 0.2, day_cap: 0.3, month_cap: 5, cards_today: 2, lifetime_usd: 12.34, lifetime_cap: 150, over_reservation_calls: 2 };
  it("shows all time against the $150 cap, the summaries' part and calls above their reservation", () => {
    const t = text(render(<Health />, { ctx: ctxFor(run), seeds: [{ key: ["health"], data: quiet }, { key: ["llm_budget"], data: budget },
      { key: ["summary_spend"], data: spend }] }));
    expect(t).toContain("All time $12.34 / $150");
    expect(t).toContain("$0.560 of it stock summaries");
    expect(t).toContain("2 summary calls cost more than they reserved");
    expect(t).toContain("at $150 in all, no AI call at all");
  });
  it("older RPCs without the lifetime fields: no lifetime row, nothing broken", () => {
    const { lifetime_usd: _a, lifetime_summaries_usd: _b, ...old } = budget;
    const html = render(<Health />, { ctx: ctxFor(run), seeds: [{ key: ["health"], data: quiet },
      { key: ["llm_budget"], data: { ...old, caps: { daily_soft: 1, monthly_alert: 20, monthly_hard: 25 } } }] });
    expect(text(html)).not.toContain("All time");
    expect(glitches(html)).toEqual([]);
  });
});
