// @vitest-environment jsdom
// #327: axe-core on every page and on the stock summary card, in the loading, error, empty and loaded states, and on
// the new parts on their own (My lists chips in every state, an opened focus group, the shell).
// No serious or critical finding is allowed; what is left out is listed in ./axe.ts (EXCLUDED) with the reason.
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { Shell } from "../components/Shell";
import { StockSummary } from "../components/StockSummary";
import { MyListsCard, type ListEditor } from "../pages/MyLists";
import { ThemeSheet } from "../pages/Strategy";
import type { Item } from "../lib/tickers";
import { boom, ctxFor, emptyOverview, render, type Seed } from "../pages/testkit";
import type { RunCtx } from "../components/Shell";
import { EXCLUDED, audit, auditNode, lines } from "./axe";
import { mount, unmount } from "./dom";
import { PAGES, loadedCtx, run, summary, type Page } from "./fixtures";

vi.mock("../lib/api", () => import("../pages/testkit-api"));
vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });
afterEach(unmount);

const at = (p: Page, seeds: Seed[] = [], ctx?: RunCtx) => render(p.el, { path: p.path, route: p.route, children: p.children, seeds, ctx });
const clean = async (html: string) => expect(lines(await audit(html))).toEqual([]);

describe("the check itself", () => {
  it("finds a control with no name, and one whose name does not contain the words on it", async () => {
    const found = await audit('<button type="button"><svg width="10" height="10"></svg></button><button type="button" aria-label="About it">NBIS Nebius</button><ul><div role="status">x</div></ul>');
    expect(found.map((f) => f.id).sort()).toEqual(["button-name", "label-content-name-mismatch", "list"]);
  });
  it("leaves out only the rules listed with a reason", () => {
    expect(Object.keys(EXCLUDED).sort()).toEqual(["color-contrast", "region"]);
    for (const why of Object.values(EXCLUDED)) expect(why.length).toBeGreaterThan(20);
  });
});

describe("axe: every page while it loads", () => {
  for (const p of PAGES) it(p.name, async () => {
    await clean(at(p));                                               // the page's own queries still on their way
    await clean(at(p, [], ctxFor(run, { overview: undefined })));     // and the shell's overview too
  });
});

describe("axe: every page when its data could not be read", () => {
  for (const p of PAGES) it(p.name, async () => {
    await clean(at(p, p.keys.map((key) => ({ key, error: boom() }))));
  });
});

describe("axe: every page with nothing to show yet", () => {
  for (const p of PAGES) it(p.name, async () => {
    await clean(at(p, p.empty, ctxFor(run, { overview: emptyOverview(run) })));
  });
});

describe("axe: every page with data", () => {
  for (const p of PAGES) it(p.name, async () => {
    await clean(at(p, p.loaded, loadedCtx()));
  });
});

describe("Go / no-go: a rule's row that opens", () => {
  it("carries the experiment label as words, not as a button inside the row's own control; the label wraps on a phone", async () => {
    const p = PAGES.find((x) => x.name === "Go / no-go")!;
    document.body.innerHTML = at(p, p.loaded, loadedCtx());
    const rows = [...document.querySelectorAll("details.acc > summary")];
    expect(rows.length).toBeGreaterThan(0);
    for (const s of rows) expect(s.querySelectorAll("button, a, input").length).toBe(0);
    expect(rows.some((s) => s.querySelector(".exp-tag")?.textContent?.startsWith("Experiment"))).toBe(true);
    // The badge that opens the experiment's sheet is still there, inside the opened row, under the rule's heading.
    expect(document.querySelector("details.acc .acc-body h2 button.exp-btn")).not.toBeNull();
    const css = (await import("../app.css?raw")).default;
    expect(css).toContain(".acc-title .exp-tag { white-space: normal; overflow-wrap: anywhere; line-height: 1.3; }");
  });
});

describe("axe: the shell (header, tab bar, landmarks) around a page", () => {
  const seeds = (o: unknown): Seed[] => [{ key: ["runs"], data: [run] }, { key: ["overview", 1], data: o }];
  it("loaded, and with a halt in force", async () => {
    await clean(render(<Shell />, { seeds: seeds(loadedCtx().overview) }));
  });
  it("while the runs load, and when they could not be read", async () => {
    await clean(render(<Shell />));
    await clean(render(<Shell />, { seeds: [{ key: ["runs"], error: boom() }] }));
  });
});

describe("axe: the stock summary card", () => {
  const card = (seed: Seed | null) => render(<StockSummary symbol="AAPL" />, { seeds: seed ? [seed] : [] });
  const key = ["stockSummary", "AAPL"];
  it("loaded", async () => { await clean(card({ key, data: summary })); });
  it("loaded, with no news that week", async () => { await clean(card({ key, data: { ...summary!, news: [], newsStatus: "none" } })); });
  it("loading", async () => { await clean(card(null)); });
  it("empty: no summary yet", async () => { await clean(card({ key, data: null })); });
  it("error", async () => { await clean(card({ key, error: boom() })); });
});

/** A list editor with fixed state (as in pages/MyLists.test.tsx). */
function fakeEd(items: Item[], over: Partial<ListEditor> = {}): ListEditor {
  const n = (s: Item["state"][]) => items.filter((i) => s.includes(i.state));
  const accepted = n(["accepted"]);
  return {
    lists: { isPending: false, isError: false, data: { lists: [] } }, saved: [], sel: "\u0000new", cur: null, name: "AI infrastructure", items, draft: "",
    msg: null, save: { kind: "idle" }, del: { kind: "idle" }, same: false, isNew: true, deleteMissing: () => false,
    t: { total: items.length, accepted, refused: n(["refused", "error"]), checking: n(["checking", "slow"]), fresh: accepted.filter((i) => !i.inCatalogue), full: items.length >= 40 },
    pick() {}, setName() {}, setDraft() {}, commit() {}, drop() {}, retry() {}, saveList: async () => {}, askDelete() {}, cancelDelete() {}, deleteList: async () => {},
    ...over,
  } as unknown as ListEditor;
}
const ok = (symbol: string): Item => ({ symbol, state: "accepted", etf: "XLK", inCatalogue: false });
const MIXED: Item[] = [ok("NBIS"), { symbol: "FSLY", state: "checking" }, { symbol: "ZZZT", state: "refused", reason: "We could not find ZZZT at the broker. Check the spelling." },
  { symbol: "PLTR", state: "slow" }, { symbol: "SNOW", state: "error", reason: "We could not check SNOW right now. Try again later." }];

describe("axe: the My lists card", () => {
  const card = (ed: ListEditor, mode: "all" | "prefer" | "only" = "only") => renderToStaticMarkup(<MyListsCard ed={ed} mode={mode} />);
  it("tickers being checked, accepted, refused, waiting and not checked", async () => { await clean(card(fakeEd(MIXED))); });
  it("empty", async () => { await clean(card(fakeEd([]))); });
  it("the saved lists still loading, and not readable", async () => {
    await clean(card(fakeEd([ok("NBIS")], { lists: { isPending: true, isError: false } } as unknown as Partial<ListEditor>)));
    await clean(card(fakeEd([ok("NBIS")], { lists: { isPending: false, isError: true, error: boom() } } as unknown as Partial<ListEditor>)));
  });
  it("a saved list being deleted, and the Prefer note", async () => {
    const cur = { name: "Cloud", updated_at: null, symbols: [] };
    await clean(card(fakeEd([ok("NBIS")], { cur, sel: "Cloud", isNew: false, del: { kind: "confirm" } } as Partial<ListEditor>), "prefer"));
  });
});

describe("axe: an opened focus group (the sheet is a portal, so it is mounted)", () => {
  it("its stocks, one of them never traded, and the add button", async () => {
    await mount(<ThemeSheet card={{ key: "chips", name: "Chips", syms: ["NVDA", "AMD", "AVGO"], on: false }} exclude={["AMD"]} onClose={() => {}} onToggle={() => {}} />);
    expect(document.querySelector('[role="dialog"]')).not.toBeNull();
    expect(lines(await auditNode(document.body))).toEqual([]);
  });
});
