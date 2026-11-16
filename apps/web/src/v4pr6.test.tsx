// v4 PR 6: the phone halt bar (Product, 16 Nov 2026), Run & settings, New run, and the stakeholder's iPhone fixes.
import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter, Route } from "react-router-dom";
import type { ConfigResp } from "./lib/config";
import { Strategy, StrategyHome } from "./pages/Strategy";
import type { Overview, Run } from "./lib/api";
import { defaultRun, riskStatus, runEndedRed } from "./lib/derive";
import { Shell } from "./components/Shell";
import { ExperimentSheetBody } from "./components/Experiment";
import { HomeStatus, NewRunRow, allRed } from "./components/Home";
import { PotChart } from "./components/PotChart";
import { Settings } from "./pages/Settings";
import { Live } from "./pages/Live";
import { experimentsFrom } from "./lib/experiment";
import css from "./app.css?raw";
import { ctxFor, emptyOverview, paperRun, render, text } from "./pages/testkit";

vi.mock("./lib/api", () => import("./pages/testkit-api"));
vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });

const run = paperRun();
const now = new Date().toISOString();
const ov = (o: Partial<Overview> = {}): Overview => ({ ...emptyOverview(run), segments: [], halts_today: [],
  heartbeat: [{ process: "engine", ts: now, detail: { state: "trading" } }], ...o });
const shell = (o: Overview, path: string) => render(<Shell />, { path, route: "*", seeds: [{ key: ["runs"], data: [run] }, { key: ["overview", 1], data: o }] });

describe("the phone halt bar", () => {
  it("red kinds only: a safety halt, the watchdog; not a pause or 'close to the stop'", () => {
    expect(riskStatus(run, ov({ halts_today: [{ kind: "drift", reason: "x", ts: now }] }), 0, 0).red).toEqual(["Positions differ from the broker: entries halted"]);
    expect(riskStatus(run, ov({ halts_today: [{ kind: "watchdog", reason: "x", ts: "2026-01-01T00:00:00Z" }],
      heartbeat: [{ process: "engine", ts: "2026-01-01T00:00:00Z", detail: {} }] }), 0, 0).red).toHaveLength(1);
    expect(riskStatus(paperRun({ status: "paused" }), ov(), -1.3, 0).red).toEqual([]);
    expect(riskStatus(run, ov(), -1.6, 0).red).toEqual(["Daily stop reached (−1.5%)"]);
  });
  it("one slim line under the header on every page, Home and Engine included; none without a red kind", () => {
    const halt = ov({ halts_today: [{ kind: "drift", reason: "x", ts: now }, { kind: "broker_rejects", reason: "y", ts: now }] });
    for (const path of ["/positions", "/", "/health", "/settings", "/more"]) {
      const html = shell(halt, path);
      expect(html, path).toMatch(/<div class="haltbar-wrap" role="alert"><button type="button" class="haltbar" aria-haspopup="dialog">/);
      expect(text(html), path).toContain("Positions differ from the broker: entries halted +1");
    }
    expect(shell(ov({ halts_today: [{ kind: "multi_day_retired", reason: "rule=trend_momentum; x", ts: now }] }), "/positions")).not.toContain("haltbar-wrap");
    expect(shell(ov(), "/")).not.toContain("haltbar-wrap");
    expect(css).toMatch(/\.haltbar \{[^}]*min-height: var\(--tap\)/);
    expect(css).toMatch(/\.haltbar-in \{[^}]*height: 36px/);
    expect(css).toMatch(/\.haltbar-text \{[^}]*text-overflow: ellipsis; white-space: nowrap;/);
  });
  it("a run that a limit ended is red; a run you stopped or replaced is not", () => {
    expect(runEndedRed({ status: "ended", end_reason: "drawdown 8% from peak" })).toEqual(["Run ended: drawdown 8% from peak"]);
    expect(runEndedRed({ status: "halted", end_reason: null })).toEqual(["Run ended by a limit"]);
    expect(runEndedRed({ status: "ended", end_reason: "replaced by a new run" })).toEqual([]);
    expect(runEndedRed({ status: "stopped", end_reason: "stopped by user" })).toEqual([]);
    const ended = paperRun({ status: "ended", end_reason: "drawdown 8% from peak" });
    const html = render(<Shell />, { path: "/positions", route: "*", seeds: [{ key: ["runs"], data: [ended] }, { key: ["overview", 1], data: { ...ov(), run: ended } }] });
    expect(text(html)).toContain("Run ended: drawdown 8% from peak");
  });
  it("on phones Home and Engine do not repeat the bar: their all-red row is marked and hidden by the phone CSS", () => {
    const halt = ov({ halts_today: [{ kind: "drift", reason: "x", ts: now }] });
    const risk = riskStatus(run, halt, 0, 0);
    const home = renderToStaticMarkup(<MemoryRouter><HomeStatus run={run} overview={halt} risk={risk} engineLive book={undefined} /></MemoryRouter>);
    expect(home).toContain('<div role="alert" class="dup-phone"><button type="button" class="status-row breach"');
    const mixed = riskStatus(paperRun({ status: "paused" }), halt, 0, 0);
    expect(renderToStaticMarkup(<MemoryRouter><HomeStatus run={run} overview={halt} risk={mixed} engineLive book={undefined} /></MemoryRouter>)).not.toContain("dup-phone");
    // a pause next to the red item: the row has an amber item the bar doesn't show, so it stays
    expect(allRed({ items: ["Entries paused", "Daily stop reached (−1.5%)"], red: ["Daily stop reached (−1.5%)"] })).toBe(false);
    expect(allRed({ items: ["Daily stop reached (−1.5%)"], red: ["Daily stop reached (−1.5%)"] })).toBe(true);
    expect(css).toMatch(/@media \(max-width: 760px\) \{[^@]*\.dup-phone \{ display: none; \}/);
  });
});

describe("Run & settings: the pre-v4 page (stakeholder, 16 Nov 2026: the old design was better)", () => {
  it("the old cards and tables, the inline Flatten-and-stop confirm, and the run picker in the Run line", () => {
    const other = paperRun({ id: 2, label: "Paper · Steady · $5,000" });
    const html = render(<Settings />, { ctx: { ...ctxFor(run, { overview: ov(), runs: [run, other] }), pickRun: () => {} } });
    expect(html.match(/<table class="list"/g)).toHaveLength(2);
    expect(html).not.toContain("level-card");
    expect(html).toMatch(/<button class="danger full-sm">Flatten and stop<\/button>/);
    expect(html).not.toContain('role="dialog"');
    expect(html).toMatch(/<select aria-label="Run shown"/);
    const t = text(html);
    for (const h of ["This run", "Controls", "The levels", "All runs", "Account"]) expect(t).toContain(h);
    expect(t).toContain("Pick a run in the Run menu above to look at it.");
  });
  it("one run: no picker, the run's name as before", () => {
    const html = render(<Settings />, { ctx: { ...ctxFor(run, { overview: ov() }), pickRun: () => {} } });
    expect(html).not.toContain("<select aria-label=\"Run shown\"");
  });
});

describe("the stakeholder's iPhone fixes", () => {
  it("the experiment sheet says it once: one pill, the explanation, the cap and the stop line", () => {
    const [e] = experimentsFrom({ experiments: [{ strategy: "trend_momentum", evidence: "failed_gate_b", experiment: true,
      label: "Experiment: failed the ten-year test", real_money: false, level: "balanced", max_exposure_pct: 60, retire_drawdown_pct: 21 }] });
    const t = text(renderToStaticMarkup(<ExperimentSheetBody e={e} />));
    expect(t.match(/Experiment · failed the ten-year test · paper only/g)).toHaveLength(1);
    expect(t).toContain("Most it may hold up to 60% invested Stop line retires if it falls 21% from its peak");
    expect(t).not.toMatch(/Paper experiment|Trend momentum/);
  });
  it("an ended run on screen while a new one is scheduled: a row on the first screen and 'Last run (ended)'", () => {
    const old = paperRun({ id: 3, status: "ended", created_at: "2026-11-10T00:00:00Z" });
    const next: Run = paperRun({ id: 4, status: "scheduled", amount_usd: 95000, level: "aggressive", created_at: "2026-11-16T20:00:00Z" });
    const o = { ...ov(), run: old, heartbeat: [{ process: "engine", ts: now, detail: { state: "market_closed", next_open: "2026-11-17T09:30:00-05:00" } }] };
    const row = text(renderToStaticMarkup(<MemoryRouter><NewRunRow next={next} overview={o} /></MemoryRouter>));
    expect(row).toBe("New run: $95,000 · Hunter · starts at the open, 09:30 ET (15:30 CET)");
    const html = render(<Live />, { ctx: { ...ctxFor(old, { runs: [next, old], overview: o }) }, seeds: [{ key: ["trades-all", 3], data: { total: 0, rows: [] } }] });
    expect(text(html)).toContain("New run: $95,000 · Hunter · starts at the open");
    expect(text(html)).toContain("Last run (ended)");
    expect(text(html)).not.toContain("Paper pot");
  });
  it("the default run: the ended run stays on screen while the newest is only scheduled, then the new run", () => {
    const old = paperRun({ id: 3, status: "ended", started_on: "2026-11-10" });
    const next = paperRun({ id: 4, status: "scheduled", started_on: null });
    expect(defaultRun([next, old])?.id).toBe(3);
    expect(defaultRun([{ ...next, status: "running", started_on: "2026-11-17" }, old])?.id).toBe(4);
    expect(defaultRun([next])?.id).toBe(4);
    expect(defaultRun([])).toBeUndefined();
  });
  it("after the new run starts, the ended run reads 'run ended', not 'Last run (ended)'", () => {
    const old = paperRun({ id: 3, status: "ended", created_at: "2026-11-10T00:00:00Z" });
    const next: Run = paperRun({ id: 4, status: "running", amount_usd: 95000, level: "aggressive", created_at: "2026-11-16T20:00:00Z" });
    const o = { ...ov(), run: old };
    const html = render(<Live />, { ctx: { ...ctxFor(old, { runs: [next, old], overview: o }) }, seeds: [{ key: ["trades-all", 3], data: { total: 0, rows: [] } }] });
    expect(text(html)).toContain("New run: $95,000 · Hunter · running now");
    expect(text(html)).toContain("Paper pot · run ended");
    expect(text(html)).not.toContain("Last run (ended)");
  });
  it("no history: a short empty state instead of an empty chart", () => {
    const t = text(renderToStaticMarkup(<PotChart data={[]} start={5000} compact />));
    expect(t).toBe("No history yet The line starts at the first close and runs next to the S&P 500 from the same start.");
  });
});

describe("Strategy on phones", () => {
  const fresh = { level: "balanced", bounds: {}, themes: {}, universe: [], applied: null, pending: null, draft: null, versions: [] } as unknown as ConfigResp;
  const at = () => render(<Strategy />, { path: "/strategy", route: "/strategy", seeds: [{ key: ["config", 1], data: fresh }],
    children: <Route index element={<StrategyHome />} /> });
  it("trading hours and extra caution sit behind a tap on a phone, and show as before on a wide screen", () => {
    const wide = at();
    expect(wide).not.toContain('class="kb-fold"');
    expect(text(wide)).toContain("Trading hours");
    vi.stubGlobal("window", { matchMedia: (q: string) => ({ matches: q === "(max-width: 760px)", addEventListener() {}, removeEventListener() {} }) });
    try {
      const phone = at();
      expect(phone.match(/<div class="kb-fold"><details class="acc">/g)).toHaveLength(2);
      expect(text(phone)).toMatch(/Trading hours New trades \d\d:\d\d–\d\d:\d\d ET/);
    } finally { vi.unstubAllGlobals(); vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} }); }
  });
});
