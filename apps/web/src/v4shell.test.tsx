// v4 PR 2 (docs/design/mobile_first_v4.md): tokens, one-row phone header, five-tab bar with the Engine dot, More, the
// bottom sheet. Interaction (focus, Escape, swipe) is checked in the browser by scripts/mobile_check.py --interact.
import { describe, expect, it, vi } from "vitest";
import css from "./app.css?raw";
import tokens from "./v4tokens.css?raw";
import mainSrc from "./main.tsx?raw";
import sheetSrc from "./components/Sheet.tsx?raw";
import { Shell } from "./components/Shell";
import { More } from "./pages/More";
import type { Overview, Run } from "./lib/api";
import { ctxFor, paperRun, render, text } from "./pages/testkit";

vi.mock("./lib/api", () => import("./pages/testkit-api"));
vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });

const fresh = () => new Date().toISOString();
const shell = (run: Run, o: Partial<Overview>, path = "/") => render(<Shell />, { path, seeds: [
  { key: ["runs"], data: [run] },
  { key: ["overview", run.id], data: { run, daily: [], pending_commands: [], ledger: null, today: null, segments: [], halts_today: [],
    heartbeat: [{ process: "engine", ts: fresh(), detail: { state: "trading" } }], ...o } },
] });
const tabbar = (html: string) => html.slice(html.indexOf('<nav class="tabbar"'), html.indexOf("</nav>", html.indexOf('<nav class="tabbar"')));

describe("v4 shell", () => {
  it("five tabs: Home, Positions, Activity, Engine, More", () => {
    const t = text(tabbar(shell(paperRun(), {})));
    expect(t).toBe("Home Positions Activity Engine More");
  });
  it("the Engine tab carries a red dot while a halt is in force, amber while the engine is offline, none otherwise", () => {
    const halt = tabbar(shell(paperRun(), { halts_today: [{ kind: "drift", reason: "x", ts: fresh() }] }));
    expect(halt).toContain('class="tab-dot breach"');
    expect(halt).toContain('aria-label="Engine: a halt is in force"');
    const off = tabbar(shell(paperRun(), { heartbeat: [{ process: "engine", ts: "2026-01-01T00:00:00Z", detail: { state: "trading" } }] }));
    expect(off).toContain('class="tab-dot offline"');
    expect(tabbar(shell(paperRun(), {}))).not.toContain("tab-dot");
  });
  it("Paper is always in the header; the level and the run picker are desktop-only there", () => {
    const html = shell(paperRun(), {});
    expect(html).toMatch(/<span class="pill"[^>]*>PAPER<span class="hide-sm"> · BALANCED<\/span><\/span>/);
    const two = render(<Shell />, { seeds: [{ key: ["runs"], data: [paperRun(), paperRun({ id: 9, label: "Replay" })] }] });
    expect(two).toMatch(/<select aria-label="Run" class="chip hide-sm"/);
  });
  it("More lists every page that is not a tab, the run picker and sign out", () => {
    const run = paperRun();
    const t = text(render(<More />, { ctx: { ...ctxFor(run, { runs: [run, paperRun({ id: 9, label: "Replay A" })] }),
      meters: { dayPct: -0.4, dd: 1.2, replay: false }, pickRun: () => {} } }));
    for (const w of ["Report", "Go / no-go", "Strategy", "Run & settings", "New run", "Sign out", "Day vs stop", "Run drawdown", "Replay A"])
      expect(t, w).toContain(w);
  });
  it("old routes redirect: /trades -> /positions (with the id), /tape -> /activity", () => {
    expect(mainSrc).toContain('<Route path="trades" element={<Navigate to="/positions" replace />} />');
    expect(mainSrc).toContain('<Route path="tape" element={<Navigate to="/activity" replace />} />');
    expect(mainSrc).toMatch(/<Navigate to=\{`\/positions\/\$\{id\}\$\{search\}`\} replace \/>/);
  });
  it("tokens: role names exist as aliases; nothing below 12 px; only transform and opacity animate", () => {
    for (const t of ["--bg", "--text", "--accent", "--up", "--down", "--amber", "--t-cap: 12px", "--tap: 44px", "--d-base: 200ms", "--d-sheet: 320ms"])
      expect(tokens, t).toContain(t);
    const keyframes = [...css.matchAll(/@keyframes [\w-]+ \{([^@]*?)\}\s*\}?/g)].map((m) => m[1]).join(" ");
    expect(keyframes).not.toMatch(/box-shadow|background-position|width:|height:/);
    expect(css).toContain("main > *, .enter { animation: kb-in var(--d-base)");
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\) \{\s*\*, \*::before, \*::after \{ animation: none !important; transition: none !important; \}/);
  });
  it("the sheet is a labelled modal dialog that closes on Escape, the scrim, the X and a swipe, and returns focus", () => {
    expect(sheetSrc).toContain('role="dialog" aria-modal="true" aria-labelledby={id}');
    for (const k of ['e.key === "Escape"', "onKeyDown={onKey}", 'className="sheet-scrim" onClick={onClose}', 'aria-label="Close" onClick={onClose}', "d.dy > 80", "opener.current", 'e.key !== "Tab"'])
      expect(sheetSrc, k).toContain(k);
  });
});
