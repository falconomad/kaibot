// v4 PR 6: the phone halt bar (Product, 16 Nov 2026), Run & settings, New run, and the stakeholder's iPhone fixes.
import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { Overview, Run } from "./lib/api";
import { riskStatus } from "./lib/derive";
import { Shell } from "./components/Shell";
import { ExperimentSheetBody } from "./components/Experiment";
import { NewRunRow } from "./components/Home";
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
  it("one slim line under the header on every page but Home and Engine (which have the red row); none without a red kind", () => {
    const halt = ov({ halts_today: [{ kind: "drift", reason: "x", ts: now }, { kind: "broker_rejects", reason: "y", ts: now }] });
    const html = shell(halt, "/positions");
    expect(html).toMatch(/<div class="haltbar-wrap" role="alert"><button type="button" class="haltbar" aria-haspopup="dialog">/);
    expect(text(html)).toContain("Positions differ from the broker: entries halted +1");
    expect(shell(halt, "/")).not.toContain("haltbar-wrap");
    expect(shell(ov({ halts_today: [{ kind: "multi_day_retired", reason: "rule=trend_momentum; x", ts: now }] }), "/positions")).not.toContain("haltbar-wrap");
    expect(css).toMatch(/\.haltbar \{[^}]*min-height: var\(--tap\)/);
    expect(css).toMatch(/\.haltbar-in \{[^}]*height: 36px/);
    expect(css).toMatch(/\.haltbar-text \{[^}]*text-overflow: ellipsis; white-space: nowrap;/);
  });
});

describe("Run & settings", () => {
  it("level cards, runs as rows, no table; Flatten and stop asks in a sheet", () => {
    const html = render(<Settings />, { ctx: ctxFor(run, { overview: ov() }) });
    expect(html).not.toContain("<table");
    expect(html.match(/<li class="level-card/g)).toHaveLength(3);
    expect(text(html)).toContain("Balanced current");
    expect(html).toMatch(/<button class="danger full-sm" aria-haspopup="dialog">Flatten and stop<\/button>/);
    expect(text(html)).toContain("No level uses shorts or leverage.");
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
  it("no history: a short empty state instead of an empty chart", () => {
    const t = text(renderToStaticMarkup(<PotChart data={[]} start={5000} compact />));
    expect(t).toBe("No history yet The line starts at the first close and runs next to the S&P 500 from the same start.");
  });
});
