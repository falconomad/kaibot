// v4 PR 1 (docs/design/audit_2026-11-16.md): regressions the audit found, guarded in code. The page-level checks
// (overflow, tap targets, layout shift at 375/390/430 and 1280/1440) are in scripts/mobile_check.py.
import { describe, expect, it, vi } from "vitest";
import css from "./app.css?raw";
import { Shell } from "./components/Shell";
import { ExperimentBadge } from "./components/Experiment";
import { paperRun, render, text } from "./pages/testkit";
import { renderToStaticMarkup } from "react-dom/server";

vi.mock("./lib/api", () => import("./pages/testkit-api"));

describe("v4 PR 1 fixes", () => {
  it("no text under 12 px in the stylesheet (audit #10)", () => {
    const sizes = [...css.matchAll(/font-size:\s*(\d+(?:\.\d+)?)px/g)].map((m) => Number(m[1]));
    expect(sizes.filter((s) => s < 12)).toEqual([]);
  });
  it("carousel cards contain their absolutely positioned text, so the page never widens (audit #1)", () =>
    expect(css).toMatch(/\.kb-presets > \*, \.kb-tgrid > \*, \.kb-lgrid > \* \{ position: relative; \}/));
  it("shell and Strategy controls are at least 44 px tall (audit #7)", () => {
    for (const sel of [".brand", ".chip", ".range button", ".kb-pill", ".kb-seg-opt", ".kb-ghost", ".kb-tog", ".kb-x", ".kb-chip-x"])
      expect(css, sel).toMatch(new RegExp(`${sel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")} \\{[^}]*min-height: 44px`));
    expect(css).toMatch(/nav\.topnav a \{[^}]*min-height: 44px/);
  });
  it("the experiment label is a 44 px button whose tag wraps, never clipped (audit #5)", () => {
    const html = renderToStaticMarkup(<ExperimentBadge e={{ strategy: "trend_momentum", label: "Experiment: failed the ten-year test", evidence: "failed_gate_b",
      experiment: true, realMoney: false, level: null, capPct: null, retirePct: null }} />);
    expect(html).toContain('class="exp-btn"');
    expect(text(html)).toBe("Experiment — failed the ten-year test · paper only");
    expect(css).toMatch(/button\.exp-btn \{[^}]*min-height: 44px/);
    expect(css).toMatch(/\.exp-btn > \.tag \{[^}]*white-space: normal/);
  });
  it("the page waits for the overview before it renders, so the halt strip never pushes it down (audit #9)", () => {
    const run = paperRun();
    vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });
    const html = render(<Shell />, { seeds: [{ key: ["runs"], data: [run] }] });
    expect(html).toContain('aria-busy="true"');
    expect(html).toContain('class="body body-pending"');
  });
});
