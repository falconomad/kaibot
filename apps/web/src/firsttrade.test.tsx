// Before the first real holdings (18 Nov 2026): the phone chart's live point, the sign-in landing, a sold-out row.
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import css from "./app.css?raw";
import { PotChart } from "./components/PotChart";
import { parseBook } from "./lib/book";
import { FROM_SIGNIN, primeGlowFromUrl } from "./lib/glow";

const data = [{ session: "2026-11-12", pot: 10000, spy: 10000 }, { session: "2026-11-13", pot: 10050, spy: 10020 }] as never;

describe("the phone chart's live point", () => {
  it("is a dot over the stretched chart, at the line's end in percent of the chart, only in the compact chart", () => {
    const html = renderToStaticMarkup(<PotChart data={data} start={10000} compact />);
    expect(html).toMatch(/<span class="chart-point" aria-hidden="true" style="left:87\.89\d*%;top:10\.86\d*%"><\/span>/);
    expect(renderToStaticMarkup(<PotChart data={data} start={10000} />)).not.toContain("chart-point");
    expect(css).toMatch(/\.chart-point \{ display: none; position: absolute; width: 10px; height: 10px; margin: -5px 0 0 -5px; border-radius: 999px;/);
    expect(css).toMatch(/@media \(max-width: 760px\) \{ \.potchart\.compact \.chart-point \{ display: block; \} \}/);
  });
});

describe("landing from the emailed sign-in link", () => {
  const setup = (search: string) => {
    const style = new Map<string, string>();
    const doc = { body: { dataset: {} as Record<string, string>, style: { setProperty: (k: string, v: string) => style.set(k, v) } } } as unknown as Document;
    const calls: string[] = [];
    (globalThis as { history?: unknown }).history = { state: null, replaceState: (_s: unknown, _t: string, url: string) => calls.push(url) };
    return { doc, calls, primed: primeGlowFromUrl({ search, pathname: "/", hash: "#access_token=x" }, doc) };
  };
  it("starts the glow at sign-in's place and takes the marker out of the address bar, keeping the rest", () => {
    const { doc, calls, primed } = setup(`?${FROM_SIGNIN}&tab=1`);
    expect(primed).toBe(true);
    expect(doc.body.dataset.glow).toBe("signin");
    expect(calls).toEqual(["/?tab=1#access_token=x"]);
  });
  it("does nothing on an ordinary load", () => {
    for (const q of ["", "?from=other", "?tab=1"]) expect(setup(q).primed).toBe(false);
  });
});

describe("a stock with 0 shares left after a fill", () => {
  it("is not a holding", () => {
    const pos = (symbol: string, qty: number) => ({ rule: "trend_momentum", symbol, qty, avg_cost: 10, mark: 11, last: 11 });
    const b = parseBook({ session: "2026-11-17", positions: [pos("ORCL", 0), pos("NVDA", 3), pos("X", -1)] });
    expect(b.positions.map((p) => p.symbol)).toEqual(["NVDA"]);
  });
});
