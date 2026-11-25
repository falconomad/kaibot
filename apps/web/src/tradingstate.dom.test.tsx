// N1-N3 (docs/design/ui_after_engine_v3.md) in the DOM: the top bar's trading-state line, the red damaged-journal
// stop (never "asleep"), and the refused broker answer's words with a link to the books.
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { Shell } from "./components/Shell";
import { HomeStatus } from "./components/Home";
import { Health } from "./pages/Health";
import { engineChip, loadTradingState } from "./components/Shell";
import type { Health as HealthResp, Heartbeat, LlmBudget, Overview, Run } from "./lib/api";
import { ctxFor, emptyOverview, hasAlert, paperRun, render, text } from "./pages/testkit";

vi.mock("./lib/api", () => import("./pages/testkit-api"));
vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });
afterEach(() => { vi.useRealTimers(); });
// the line is a lazy chunk: the shell shows it once loaded (before that, the engine chip)
beforeAll(async () => { await loadTradingState(); });

const NIGHT = new Date("2026-11-23T22:30:00Z"); // Mon 23:30 CET: the Mac's night
const OPEN = new Date("2026-11-23T16:00:00Z"); // Mon 11:00 ET
const at = (d: Date, s: number) => new Date(d.getTime() - s * 1000).toISOString();
const topChip = (html: string) => { const i = html.indexOf("<a", html.lastIndexOf("<a", html.indexOf("engine-chip"))); return html.slice(i, html.indexOf("</a>", i)); };
const shell = (run: Run, beat: Heartbeat, health?: Partial<HealthResp>, over: Partial<Overview> = {}) => render(<Shell />, { seeds: [
  { key: ["runs"], data: [run] },
  { key: ["overview", run.id], data: { run, daily: [], pending_commands: [], ledger: null, today: null, segments: [], halts_today: [], heartbeat: [beat], ...over } },
  ...(health ? [{ key: ["health"], data: { now: "", heartbeat: [beat], halts: [], events: [], pending_commands: [], deadman: { armed: true, jobs: [], runs: [] }, ...health } }] : []),
] });
const JOURNAL = [{ type: "engine_error", ts: "", run_id: 1, payload: { error: "JournalCorrupt: journal entry 4711 fails its checksum" } },
  { type: "engine_stop", ts: "", run_id: 1, payload: {} }];
const journalAt = (d: Date) => JOURNAL.map((e, i) => ({ ...e, ts: at(d, 600 - i) }));

describe("N1: the top bar says what kaibo does, with the reason in words", () => {
  it("the line is not in the first load: a lazy chunk, the engine chip until it is here", async () => {
    const src = (await import("./components/Shell.tsx?raw")).default;
    expect(src).toMatch(/import\("\.\/TradingState"\)/);
    expect(src).not.toMatch(/from "\.\.\/lib\/tradingstate"|from "\.\/TradingState"/);
  });
  it("Buying, Holding and Selling only read as words, not only a colour", () => {
    vi.useFakeTimers({ now: OPEN, toFake: ["Date"] });
    const b = (d: Record<string, unknown>) => ({ process: "engine", ts: at(OPEN, 5), detail: { lease: "ok", account_clean: true, ...d } }) as Heartbeat;
    expect(text(topChip(shell(paperRun(), b({ state: "trading" }))))).toBe("Trading: Buying — nothing blocks new buys");
    expect(text(topChip(shell(paperRun(), b({ state: "managing" }))))).toBe("Trading: Holding — reason not reported");
    const paused = topChip(shell(paperRun({ status: "paused" }), b({ state: "managing" })));
    expect(text(paused)).toBe("Trading: Selling only — paused by you");
    expect(paused).toContain('data-kind="selling_only"');
    expect(text(topChip(shell(paperRun(), b({ state: "managing", lease: "degraded" }))))).toBe("Trading: Selling only — database unreachable at start (needs you)");
  });
  it("a replay keeps its own chip", () => {
    const run = paperRun({ kind: "replay", status: "ended" });
    expect(text(topChip(shell(run, { process: "engine", ts: at(OPEN, 5), detail: { state: "trading" } })))).toBe("Engine: Replay past data");
  });
});

describe("N2: a damaged-journal stop is never shown as asleep", () => {
  const stopped = (d: Date): Heartbeat => ({ process: "engine", ts: at(d, 590), detail: { state: "stopped", host: "mac" } });
  it("at night: red in the top bar, the phone bar and the Engine tab, not 'asleep'", () => {
    vi.useFakeTimers({ now: NIGHT, toFake: ["Date"] });
    const html = shell(paperRun(), stopped(NIGHT), { events: journalAt(NIGHT) });
    const chip = topChip(html);
    expect(text(chip)).toBe("Trading: Stopped — journal damaged · needs you at the Mac (needs you)");
    expect(chip).toContain("ts-chip red");
    expect(text(html)).not.toMatch(/asleep/i);
    expect(html).toContain('class="haltbar"');
    expect(html).toContain('class="tab-dot breach"');
    // without the events the same night is calm (the engine's night): the events decide
    expect(text(topChip(shell(paperRun(), stopped(NIGHT))))).toMatch(/^Trading: Not running — asleep · starts /);
  });
  it("in market hours too", () => {
    vi.useFakeTimers({ now: OPEN, toFake: ["Date"] });
    expect(text(topChip(shell(paperRun(), stopped(OPEN), { events: journalAt(OPEN) })))).toMatch(/^Trading: Stopped — journal damaged · needs you at the Mac/);
  });
  it("Home's engine row is the red journal row with the steps, not the calm asleep row", () => {
    vi.useFakeTimers({ now: NIGHT, toFake: ["Date"] });
    const run = paperRun();
    const o = { ...emptyOverview(run), heartbeat: [stopped(NIGHT)] };
    const chip = engineChip(o, run, journalAt(NIGHT));
    expect(chip.journal).toBe(true);
    const html = render(<HomeStatus run={run} overview={o} risk={undefined} engineLive={false} engine={chip} book={undefined} />);
    expect(text(html)).toContain("Stopped — journal damaged · needs you at the Mac");
    expect(text(html)).not.toMatch(/asleep/i);
    expect(html).toContain("status-row breach");
    expect(hasAlert(html)).toBe(true);
  });
  it("the Engine screen: red line, the journal row, the check marked as a problem", () => {
    vi.useFakeTimers({ now: NIGHT, toFake: ["Date"] });
    const run = paperRun();
    const beat = stopped(NIGHT);
    const health: HealthResp = { now: NIGHT.toISOString(), heartbeat: [beat], halts: [], events: journalAt(NIGHT), pending_commands: [],
      deadman: { armed: true, jobs: [], runs: [] } };
    const html = render(<Health />, { ctx: ctxFor(run, { overview: { ...emptyOverview(run), heartbeat: [beat] } }),
      seeds: [{ key: ["health"], data: health }, { key: ["llm_budget"], data: budget }] });
    const t = text(html);
    expect(t).toMatch(/^Engine Stopped — journal damaged Trading: Stopped — journal damaged · needs you at the Mac/);
    expect(t).toContain("kaibo stopped because its journal is damaged · needs you at the Mac");
    expect(t).not.toMatch(/asleep/i);
    expect(hasAlert(html)).toBe(true);
  });
});

const budget: LlmBudget = { day: "2026-11-23", day_usd: 0, month_usd: 0, state: "ok", alert: false,
  caps: { daily_soft: 1, monthly_alert: 15, monthly_hard: 20 }, calls_today: 0, open_reserved_usd: 0, by_purpose: {} };

describe("N3: a refused broker answer says what happened and links to the books", () => {
  it("never 'no orders sent'; a link opens the books sheet", () => {
    const now = new Date().toISOString();
    const run = paperRun();
    const beat: Heartbeat = { process: "engine", ts: now, detail: { state: "managing", lease: "ok", reconcile: { ok: true } } as Heartbeat["detail"] };
    const health: HealthResp = { now, heartbeat: [beat], halts: [], pending_commands: [], deadman: { armed: true, jobs: [], runs: [] },
      events: [{ type: "multi_day_error", ts: now, run_id: 1, payload: { step: "broker_payload", error: "fill XOM: qty not a number", refused: 1,
        action: "not booked; buys in the symbol wait for the reconciliation" } },
      { type: "multi_day_error", ts: now, run_id: 1, payload: { step: "decide", error: "daily store stale" } }] };
    const html = render(<Health />, { ctx: ctxFor(run, { overview: { ...emptyOverview(run), heartbeat: [beat] } }),
      seeds: [{ key: ["health"], data: health }, { key: ["llm_budget"], data: budget }] });
    const t = text(html);
    expect(t).toContain("A broker answer was refused and not booked — orders may have gone out; check the books Check the books");
    expect(html).toContain('<a href="#books">Check the books</a>');
    expect(html).toContain('<li id="books">');
    // another failed multi-day step keeps the registry's label
    expect(t).toContain("Multi-day step failed (no orders sent)");
    expect(t.match(/no orders sent/g)).toHaveLength(1);
  });
});
