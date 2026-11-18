// Brand polish (#265) and the corporate-action rows (#266, PR #271).
import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import css from "./app.css?raw";
import tokens from "./tokens.css?raw";
import indexHtml from "../index.html?raw";
import { PotChart, resetChartDraw } from "./components/PotChart";
import { HomeStatus } from "./components/Home";
import { Tape } from "./pages/Tape";
import { caNotes, caSkips } from "./lib/corpactions";
import { ctxFor, emptyOverview, paperRun, render, text } from "./pages/testkit";

vi.mock("./lib/api", () => import("./pages/testkit-api"));

const data = [{ session: "2026-11-12", pot: 10000, spy: 10000 }, { session: "2026-11-13", pot: 10050, spy: 10020 }] as never;

describe("type and the font swap", () => {
  it("all four Outfit weights load first, and the fallback is Arial set to Outfit's measures", () => {
    for (const w of [400, 500, 600, 700]) expect(indexHtml).toContain(`<link rel="preload" href="/fonts/outfit-latin-${w}-normal.woff2" as="font" type="font/woff2" crossorigin />`);
    expect(tokens).toMatch(/@font-face \{ font-family: "Outfit Fallback"; src: local\("Arial"\)[^}]*size-adjust: 101\.43%; ascent-override: 98\.59%; descent-override: 25\.63%; line-gap-override: 0%; \}/);
    expect(tokens).toContain('--font-ui: "Outfit", "Outfit Fallback", "Inter",');
  });
  it("no other host holds up the first paint: Inter's stylesheet never blocks, and the data API and the logo host are connected early (#297)", () => {
    expect(indexHtml.match(/<link[^>]*rel="stylesheet"[^>]*>/g)).toEqual([
      `<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet" media="print" onload="this.media='all'" />`]);
    expect(indexHtml.match(/<link rel="preconnect"[^>]*>/g)).toEqual([
      '<link rel="preconnect" href="%VITE_SUPABASE_URL%" crossorigin />', '<link rel="preconnect" href="https://cdn.tickerlogos.com" crossorigin />']);
  });
  it("letter spacing per type step: hero -0.035em, title -0.03em, heading -0.01em, caps +0.08em", () => {
    expect(css).toMatch(/\.home-hero \.hero \{[^}]*letter-spacing: -0\.035em/);
    expect(css).toMatch(/\.page-title \{[^}]*letter-spacing: -0\.03em/);
    expect(css).toMatch(/\.sheet-title \{[^}]*letter-spacing: -0\.01em/);
    expect(css).toMatch(/\.cap \{[^}]*letter-spacing: 0\.08em/);
    expect(css).toMatch(/\.section-h \{[^}]*letter-spacing: \.08em/);
  });
});

describe("the chart", () => {
  it("draws its line once per app load, with a clip that grows by transform", () => {
    resetChartDraw();
    expect(renderToStaticMarkup(<PotChart data={data} start={10000} />)).toContain('<clipPath id="kb-potdraw"><rect class="chart-draw"');
    expect(css).toMatch(/@keyframes kb-draw-x \{ from \{ transform: scaleX\(0\); \} to \{ transform: none; \} \}/);
    expect(css).toMatch(/\.chart-draw \{[^}]*animation: kb-draw-x var\(--d-draw\)/);
  });
  it("the live point pops on a new value only (transform and opacity), never on the first render", () => {
    expect(renderToStaticMarkup(<PotChart data={data} start={10000} />)).not.toMatch(/point-pop|point-pulse/);
    expect(css).toMatch(/@keyframes kb-point-pop \{ from \{ transform: scale\(0\); \} to \{ transform: none; \} \}/);
    expect(css).toMatch(/@keyframes kb-point-pulse \{ from \{ opacity: \.6; transform: none; \} to \{ opacity: 0; transform: scale\(2\.4\); \} \}/);
  });
});

describe("corporate actions (#266)", () => {
  const md = { book: "open", rules: {}, corporate_actions: [
    "NVDA: split on 2026-11-20 — the engine does not handle this yet, check before then",
    "trend_momentum: no new buys this session: corporate actions read failed (HTTP 503)"] };
  it("reads the heartbeat's notes, and nothing when the field is missing or malformed", () => {
    expect(caNotes(md)).toEqual({ held: [{ symbol: "NVDA", action: "split", on: "2026-11-20", line: md.corporate_actions[0] }],
      unread: ["no new buys this session: corporate actions read failed (HTTP 503)"] });
    for (const bad of [undefined, null, {}, { corporate_actions: "x" }, { corporate_actions: [1, null] }]) expect(caNotes(bad)).toEqual({ held: [], unread: [] });
  });
  it("a held stock's note is an amber row on Home (and Engine, which shares the rows)", () => {
    const run = paperRun();
    const o = { ...emptyOverview(run), heartbeat: [{ process: "engine", ts: new Date().toISOString(), detail: { multi_day: md } }] } as never;
    const html = renderToStaticMarkup(<MemoryRouter><HomeStatus run={run} overview={o} risk={undefined} engineLive book={undefined} /></MemoryRouter>);
    expect(html).toMatch(/class="status-row warn"[^>]*><span class="status-text">NVDA: split on Nov 20, 2026<\/span>/);
    expect(text(html)).toContain("No new buys this session: corporate actions couldn't be read");
    const none = renderToStaticMarkup(<MemoryRouter><HomeStatus run={run} overview={{ ...emptyOverview(run), heartbeat: [] } as never} risk={undefined} engineLive book={undefined} /></MemoryRouter>);
    expect(none).not.toContain("split on");
  });
  it("Activity shows a skipped row with the plain reason in its sheet, and nothing until the events arrive", () => {
    const ev = [{ type: "multi_day_ca_skip", ts: "2026-11-16T21:05:00Z", run_id: 1,
      payload: { strategy: "trend_momentum", session: "2026-11-17", symbol: "NVDA", action: "split", on: "2026-11-20", text: "skipped: corporate action on 2026-11-20" } },
      { type: "multi_day_ca_skip", ts: "x", run_id: 1, payload: { symbol: "AMD" } }];
    expect(caSkips(ev as never)).toEqual([{ ts: "2026-11-16T21:05:00Z", symbol: "NVDA", action: "split", on: "2026-11-20", strategy: "trend_momentum" }]);
    expect(caSkips(undefined)).toEqual([]);
    const run = paperRun();
    const seeds = [{ key: ["tape", 1], data: [], infinite: true }, { key: ["health"], data: { now: "", heartbeat: [], halts: [], events: ev, deadman: { armed: true, jobs: [], runs: [] }, pending_commands: [] } }];
    const html = render(<Tape />, { ctx: ctxFor(run), seeds });
    expect(text(html)).toContain("Skipped NVDA: split on Nov 20, 2026");
    const without = render(<Tape />, { ctx: ctxFor(run), seeds: [{ key: ["tape", 1], data: [], infinite: true }] });
    expect(text(without)).not.toContain("Skipped NVDA");
  });
});
