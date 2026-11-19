// Stakeholder, 19 Nov 2026 09:25 CET: Home's chart showed a flat blue line pinned to the top and a dotted line falling,
// with no labels. The pot had barely moved (95,000 → 95,000 → 94,997.26; 16% invested) while SPY fell about 3.5%.
// The chart now names its two lines, says where each ended and by how much, has a minimal scale on phones too, and a
// short caption while there are fewer than 5 sessions. Dataviz rules: one axis, a legend for two series, text in text
// tokens (never the series colour).
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { PotChart, changePct, chartCaption, rangesFor } from "./components/PotChart";
import { text } from "./pages/testkit";

// the three sessions of the real run (kb_equity_series) and SPY from the same start (daily_result spy_ret)
const DATA = [
  { session: "2026-11-16", pot: 95000, spy: 95000 * (1 - 0.013967) },
  { session: "2026-11-17", pot: 95000, spy: 95000 * (1 - 0.013967) * (1 - 0.012294) },
  { session: "2026-11-18", pot: 94997.26, spy: 95000 * (1 - 0.013967) * (1 - 0.012294) * (1 - 0.009749) },
];
const { readFileSync } = (await import(/* @vite-ignore */ ["node", "fs"].join(":"))) as { readFileSync(path: URL, enc: "utf8"): string };
const css = readFileSync(new URL("./app.css", import.meta.url), "utf8");

describe("the Home chart is readable with three flat sessions", () => {
  it("change in plain percent, precise enough for a tiny move", () => {
    expect(changePct(94997.26, 95000)).toBe("−0.003%");
    expect(changePct(91648, 95000)).toBe("−3.5%");
    expect(changePct(95000, 95000)).toBe("0.0%");
    expect(changePct(95950, 95000)).toBe("+1.0%");
    expect(changePct(95400, 95000)).toBe("+0.42%");
  });
  it("a legend names both lines, with the end value and the change of each", () => {
    const t = text(renderToStaticMarkup(<PotChart data={DATA} start={95000} compact />));
    expect(t).toContain("kaibo $94,997 −0.003%");
    expect(t).toContain("SPY, same start $91,620 −3.6%"); // 95,000 × (1 − 1.40%) × (1 − 1.23%) × (1 − 0.97%)
  });
  it("the legend is shown on phones (it was hidden), its text in text tokens", () => {
    expect(css).not.toMatch(/\.potchart\.compact \.legend[^{]*\{ display: none/);
    const html = renderToStaticMarkup(<PotChart data={DATA} start={95000} compact />);
    expect(html).toMatch(/class="sw pot"/);
    expect(html).toMatch(/class="sw spy"/);
    expect(css).toMatch(/\.chart-legend \{[^}]*color: var\(--text-2\)/);
  });
  it("a minimal scale on phones: the top and bottom values and the first and last day", () => {
    const html = renderToStaticMarkup(<PotChart data={DATA} start={95000} compact />);
    const t = text(html);
    expect(html).toContain('class="chart-scale"');
    expect(t).toMatch(/\$9\dk.*\$9\dk/);
    expect(t).toContain("Nov 16, 2026");
    expect(t).toContain("Nov 18, 2026");
  });
  it("the caption while there are fewer than 5 sessions", () => {
    expect(chartCaption(3, 0.1613)).toBe("3 days so far · 16% invested, the rest in cash");
    expect(chartCaption(1, null)).toBe("1 day so far");
    expect(chartCaption(5, 0.16)).toBeNull();
    const t = text(renderToStaticMarkup(<PotChart data={DATA} start={95000} compact caption={chartCaption(3, 0.1613)} />));
    expect(t).toContain("3 days so far · 16% invested, the rest in cash");
  });
  it("the fill is a short fade under the line, never a block down to the baseline", () => {
    const html = renderToStaticMarkup(<PotChart data={DATA} start={95000} compact />);
    expect(html).toMatch(/<linearGradient id="kb-potfill"[^>]*gradientUnits="userSpaceOnUse"/);
    const m = /<linearGradient id="kb-potfill"[^>]*y1="([\d.]+)"[^>]*y2="([\d.]+)"/.exec(html)!;
    expect(Number(m[2]) - Number(m[1])).toBeLessThanOrEqual(60);
  });
  it("one axis: a single set of y ticks for both lines", () => {
    const html = renderToStaticMarkup(<PotChart data={DATA} start={95000} />);
    expect((html.match(/text-anchor="end" font-size="12"/g) ?? []).length).toBeGreaterThan(0);
    expect(html).not.toContain("y2-axis");
  });
  it("ranges: only those that show something different from All", () => {
    expect(rangesFor(3)).toEqual(["ALL"]);
    expect(rangesFor(7)).toEqual(["1W", "ALL"]);
    expect(rangesFor(30)).toEqual(["1W", "1M", "ALL"]);
    expect(rangesFor(100)).toEqual(["1W", "1M", "3M", "ALL"]);
  });
});
