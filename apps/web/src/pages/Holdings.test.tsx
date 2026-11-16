// #130: the multi-day book on Holdings and Home (boards 11, 12, 15): states, numbers, phone-first structure.
import { describe, expect, it, vi } from "vitest";
import type { Heartbeat } from "../lib/api";
import { parseBook } from "../lib/book";
import { Holdings } from "./Holdings";
import { BookSection } from "../components/BookSection";
import liveSrc from "./Live.tsx?raw";
import { RAW_BOOK, RAW_EMPTY_BOOK } from "./testkit-book";
import { boom, ctxFor, emptyOverview, glitches, hasAlert, hasStatus, paperRun, render, replayRun, text, zeros } from "./testkit";

vi.mock("../lib/api", () => import("./testkit-api"));

const run = paperRun();
const EXP = { strategy: "trend_momentum", evidence: "failed_gate_b", experiment: true, label: "Experiment: failed the ten-year test",
  real_money: false, level: "balanced", max_exposure_pct: 60, retire_drawdown_pct: 21 };
const beat = (detail: Record<string, unknown>): Heartbeat[] =>
  [{ process: "engine", ts: new Date().toISOString(), detail: { state: "market_closed", next_open: "2027-03-12T09:30:00-05:00", ...detail } as Heartbeat["detail"] }];
const multi = beat({ experiments: [EXP], multi_day: { book: "open", rules: { trend_momentum: { last_decision: "2027-03-11" } } } });
const ctx = (hb: Heartbeat[]) => ctxFor(run, { overview: { ...emptyOverview(run), heartbeat: hb } });
const book = (raw: unknown) => ({ key: ["book", 1], data: parseBook(raw, 1) });
const home = (hb: Heartbeat[]) => <BookSection run={run} beats={hb} />;

describe("Holdings page (board 12)", () => {
  it("loading: a skeleton, never a zero", () => {
    const html = render(<Holdings />, { ctx: ctx(multi) });
    expect(hasStatus(html)).toBe(true);
    expect(zeros(html)).toEqual([]);
  });
  it("empty: no stocks yet, says why and when the next decision is", () => {
    const t = text(render(<Holdings />, { ctx: ctx(multi), seeds: [book(RAW_EMPTY_BOOK)] }));
    expect(t).toContain("No stocks yet. Kaibot buys only at a close");
    expect(t).toContain("The next decision is after the close on Mar 12, 2027.");
  });
  it("error: a plain sentence and a retry", () => {
    const html = render(<Holdings />, { ctx: ctx(multi), seeds: [{ key: ["book", 1], error: boom() }] });
    expect(hasAlert(html)).toBe(true);
    expect(text(html)).toContain("The holdings couldn't load.");
  });
  it("a replay: no book", () => expect(text(render(<Holdings />, { ctx: ctxFor(replayRun()) }))).toContain("Replays have no multi-day book"));
  it("held: grouped by rule, with share, days, sector, stop and gain; problems in words", () => {
    const html = render(<Holdings />, { ctx: ctx(multi), seeds: [book(RAW_BOOK)] });
    const t = text(html);
    expect(t).toContain("4 stocks · $3,051.70 in stocks, 68% of the pot");
    expect(t).toContain("Trend momentum 3 stocks · 47% of the pot");
    expect(t).toContain("Experiment — failed the ten-year test · paper only");
    expect(t).toContain("Limits: up to 60% invested, retires if it falls 21% from its peak.");
    expect(t).toContain("AAPL");
    expect(t).toContain("51 days since Jan 19, 2027");
    expect(t).toContain("21.2% 4 sh · $952.40");
    expect(t).toContain("Technology");
    expect(t).toContain("$196.70 in place · 17.4% below");
    expect(t).toContain("▲ +$27.80");
    expect(t).toContain("2 of 4 in place · ZZZQ missing"); // never silently: the strip names the unprotected stock
    expect(t).toContain("Match the broker");
    expect(html).toContain('role="alert"');
    expect(html.match(/aria-expanded="false"/g)?.length).toBeGreaterThanOrEqual(4); // details behind a tap
    expect(glitches(html)).toEqual([]);
  });
});

describe("Home: the book section (board 11)", () => {
  it("Home loads it as its own chunk, for paper runs", () => {
    expect(liveSrc).toMatch(/lazy\(\(\) => import\("\.\.\/components\/BookSection"\)/);
    expect(liveSrc).toContain('run.kind === "paper" && <Suspense fallback={null}><BookSection');
  });
  it("intraday-only run (no multi-day fields, empty book): nothing new", () => {
    const t = text(render(home(beat({})), { seeds: [book(RAW_EMPTY_BOOK)] }));
    expect(t).not.toContain("Stocks held for weeks");
  });
  it("multi-day on, nothing bought yet: the empty state with the next decision", () => {
    const t = text(render(home(multi), { seeds: [book(RAW_EMPTY_BOOK)] }));
    expect(t).toContain("Stocks held for weeks");
    expect(t).toContain("No stocks yet.");
    expect(t).toContain("The rules decide after the close on Mar 12, 2027");
    expect(t).toContain("Last decision: after the close on Mar 11, 2027.");
  });
  it("held: hero pot, that day's change, short list, invested against the limit", () => {
    const html = render(home(multi), { seeds: [book(RAW_BOOK)] });
    const t = text(html);
    expect(t).toContain("Pot, at the last close (Mar 11, 2027) $4,501.70");
    expect(t).toContain("▼ −$12.25 that day");
    expect(t).toContain("Invested 68%");
    expect(t).toContain("Limit 60%");
    expect(t).toContain("See all 4");
    expect(html).toContain('role="meter"');
  });
  it("loading the book on a multi-day run: a placeholder, no zeros", () => {
    const html = render(home(multi));
    expect(html).toContain("Loading the multi-day book");
  });
});
