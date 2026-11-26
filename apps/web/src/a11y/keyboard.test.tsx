// @vitest-environment jsdom
// #327: focus groups, the stock summary card and the My lists "checking" state by keyboard alone, and what a screen
// reader is told. jsdom has no Tab key and draws nothing, so this file checks what can be read from the DOM: the
// controls are native buttons in the Tab order, a sheet takes focus, keeps Tab inside, closes on Escape and gives focus
// back, the opener says open or closed, and changes are written to a polite live region. The real Tab key, the focus
// ring as drawn and the 44 px targets are measured in Chromium by scripts/mobile_check.py --interact.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { Route } from "react-router-dom";
import appCss from "../app.css?raw";
import strategyCss from "../pages/strategy.css?raw";
// #723: the Strategy pages' rules come with their chunk (pages/strategy.css); together they are what those pages get
const css = `${appCss}\n${strategyCss}`;
import { StockSummary } from "../components/StockSummary";
import { Sheet } from "../components/Sheet";
import { MyListsCard, type ListEditor } from "../pages/MyLists";
import { Strategy, StrategyHome } from "../pages/Strategy";
import type { Item } from "../lib/tickers";
import { boom } from "../pages/testkit";
import { activate, all, client, doing, mount, press, q, reducedMotionOn, said, settle, unmount } from "./dom";
import { configThemes, summary } from "./fixtures";

vi.mock("../lib/api", () => import("../pages/testkit-api"));
vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });
beforeEach(reducedMotionOn);
afterEach(unmount);

const dialog = () => q('[role="dialog"]');
const focused = () => document.activeElement as HTMLElement;

describe("focus groups by keyboard", () => {
  const page = () => mount(<Strategy />, { path: "/strategy", route: "/strategy", children: <Route index element={<StrategyHome />} />,
    seeds: [{ key: ["config", 1], data: configThemes() }, { key: ["chapters", 1], data: [] }] });
  const opener = () => all<HTMLButtonElement>(".kb-tcard-open").find((b) => /Chips/.test(b.textContent ?? ""))!;

  it("a group's card says it opens a dialog and whether it is open", async () => {
    await page();
    expect(opener().getAttribute("aria-haspopup")).toBe("dialog");
    expect(opener().getAttribute("aria-expanded")).toBe("false");
    await activate(opener());
    expect(opener().getAttribute("aria-expanded")).toBe("true");
    expect(all(".kb-tcard-open").filter((b) => b.getAttribute("aria-expanded") === "true")).toHaveLength(1);
  });

  it("Enter opens the sheet with focus inside; Tab stays inside; Escape closes it and focus returns to the card", async () => {
    await page();
    const from = opener();
    await activate(from);
    const d = dialog()!;
    expect(d.getAttribute("aria-modal")).toBe("true");
    expect(document.getElementById(d.getAttribute("aria-labelledby")!)!.textContent).toBe("Chips");
    expect(d.contains(focused())).toBe(true);
    // Tab from the last control goes to the first, Shift+Tab from the first to the last.
    const stops = [...d.querySelectorAll<HTMLElement>("button:not([disabled])")];
    expect(stops.map((b) => b.getAttribute("aria-label") ?? b.textContent)).toEqual(["Close", "In your focus · remove"]);
    stops[stops.length - 1].focus();
    await press("Tab");
    expect(focused()).toBe(stops[0]);
    await doing(() => { focused().dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", shiftKey: true, bubbles: true, cancelable: true })); });
    expect(focused()).toBe(stops[stops.length - 1]);
    await press("Escape");
    await settle();
    expect(dialog()).toBeNull();
    expect(focused()).toBe(from);
    expect(from.getAttribute("aria-expanded")).toBe("false");
  });

  it("the round button adds or removes the group and says which (aria-pressed)", async () => {
    await page();
    const round = () => all<HTMLButtonElement>(".kb-tcheck").find((b) => /Energy/.test(b.getAttribute("aria-label") ?? ""))!;
    expect([round().getAttribute("aria-pressed"), round().getAttribute("aria-label")]).toEqual(["false", "Add Energy to focus"]);
    await activate(round());
    expect([round().getAttribute("aria-pressed"), round().getAttribute("aria-label")]).toEqual(["true", "Remove Energy from focus"]);
  });

  it("the button in the sheet adds the group without closing it", async () => {
    await page();
    await activate(all<HTMLButtonElement>(".kb-tcard-open").find((b) => /Energy/.test(b.textContent ?? ""))!);
    const btn = () => dialog()!.querySelector<HTMLButtonElement>(".kb-peek-btn")!;
    expect(btn().textContent).toBe("Add to focus");
    await activate(btn());
    expect(btn().textContent).toBe("In your focus · remove");
  });
});

/** A list editor with fixed state (as in pages/MyLists.test.tsx), around items the test can change. */
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
let setItems: (xs: Item[]) => void = () => {};
function Lists({ start, over }: { start: Item[]; over?: Partial<ListEditor> }) {
  const [items, set] = useState(start);
  setItems = set;
  return <MyListsCard ed={fakeEd(items, over)} mode="only" />;
}
const ok = (symbol: string): Item => ({ symbol, state: "accepted", etf: "XLK", inCatalogue: false });

describe("My lists: the checking state", () => {
  it("the chips stay a list of items, and a ticker being checked is named as such", async () => {
    await mount(<Lists start={[{ symbol: "FSLY", state: "checking" }, ok("NBIS")]} />);
    const chips = all(".kb-chips > *");
    expect(chips.map((c) => c.tagName)).toEqual(["LI", "LI"]);
    expect(chips.every((c) => !c.hasAttribute("role"))).toBe(true);
    expect(chips[0].getAttribute("aria-label")).toBe("FSLY, checking");
  });

  it("checking, then the answer, are said in one polite live region: accepted, refused with the reason, waiting, not checked", async () => {
    await mount(<Lists start={[]} />);
    const region = () => q("#ml-said")!;
    expect(region().getAttribute("role")).toBe("status");   // role="status" is a polite live region
    expect(region().textContent).toBe("");
    await doing(() => setItems([{ symbol: "FSLY", state: "checking" }, { symbol: "ZZZT", state: "checking" }]));
    expect(region().textContent).toBe("Checking FSLY and ZZZT.");
    await doing(() => setItems([ok("FSLY"), { symbol: "ZZZT", state: "checking" }]));
    expect(region().textContent).toBe("FSLY accepted.");
    await doing(() => setItems([ok("FSLY"), { symbol: "ZZZT", state: "refused", reason: "We could not find ZZZT at the broker. Check the spelling." }]));
    expect(region().textContent).toBe("ZZZT refused: We could not find ZZZT at the broker. Check the spelling.");
    await doing(() => setItems([ok("FSLY"), { symbol: "PLTR", state: "checking" }]));
    await doing(() => setItems([ok("FSLY"), { symbol: "PLTR", state: "slow" }]));
    expect(region().textContent).toBe("PLTR: no checker has answered yet. Try again in a minute.");
    await doing(() => setItems([ok("FSLY"), { symbol: "PLTR", state: "error", reason: "We could not check PLTR right now. Try again later." }]));
    expect(region().textContent).toBe("PLTR not checked: We could not check PLTR right now. Try again later.");
    // A removed ticker and an unchanged list say nothing new.
    await doing(() => setItems([ok("FSLY")]));
    expect(region().textContent).toBe("PLTR not checked: We could not check PLTR right now. Try again later.");
  });

  it("a list that is already there when the card opens is not read out", async () => {
    await mount(<Lists start={[ok("NBIS"), { symbol: "ZZZT", state: "refused", reason: "x" }]} />);
    expect(q("#ml-said")!.textContent).toBe("");
  });

  it("the visible tally is not a second live region for the same change", async () => {
    await mount(<Lists start={[ok("NBIS")]} />);
    const tally = all(".kb-ml span").find((s) => /1 accepted/.test(s.textContent ?? "") && s.children.length === 0)!;
    expect(tally.hasAttribute("aria-live")).toBe(false);
  });

  it("Retry and Remove are buttons in the Tab order, named with the words on them and the ticker", async () => {
    const retry = vi.fn(), drop = vi.fn();
    await mount(<Lists start={[{ symbol: "PLTR", state: "slow" }]} over={{ retry, drop } as Partial<ListEditor>} />);
    const [again, remove] = all<HTMLButtonElement>(".kb-chip button");
    expect(again.textContent).toBe("Retry");
    expect(again.getAttribute("aria-label")).toBe("Retry the check of PLTR");
    expect(remove.getAttribute("aria-label")).toBe("Remove PLTR");
    await activate(again);
    await activate(remove);
    expect([retry.mock.calls[0][0], drop.mock.calls[0][0]]).toEqual(["PLTR", "PLTR"]);
  });
});

describe("the stock summary card by keyboard", () => {
  it("an accepted ticker opens its summary in a sheet; Escape closes it and focus returns to the ticker", async () => {
    await mount(<Lists start={[ok("MSFT"), { symbol: "ZZZT", state: "refused", reason: "x" }]} />, { seeds: [{ key: ["stockSummary", "MSFT"], data: null }] });
    const info = q<HTMLButtonElement>(".kb-chip-info")!;
    expect(all(".kb-chip-info")).toHaveLength(1);                        // a refused ticker offers no summary
    expect([info.getAttribute("aria-haspopup"), info.getAttribute("aria-expanded")]).toEqual(["dialog", "false"]);
    // Its name is the words on it (ticker and company), then what it does.
    expect(info.hasAttribute("aria-label")).toBe(false);
    expect(info.textContent).toBe("MSFTMicrosoft Corporation, about this stock");
    await activate(info);
    expect(info.getAttribute("aria-expanded")).toBe("true");
    const d = dialog()!;
    expect(d.contains(focused())).toBe(true);
    expect(d.querySelector("h3")!.textContent).toBe("In plain words");
    await press("Escape");
    await settle();
    expect(dialog()).toBeNull();
    expect(focused()).toBe(info);
    expect(info.getAttribute("aria-expanded")).toBe("false");
  });

  const inSheet = (seeds = [] as { key: unknown[]; data?: unknown; error?: unknown }[]) =>
    mount(<Sheet open onClose={() => {}} title="AAPL"><StockSummary symbol="AAPL" /></Sheet>, { seeds });
  const key = ["stockSummary", "AAPL"];

  it("loading is said once, politely, and the answer when it arrives", async () => {
    await inSheet();
    const card = q(".ss")!;
    expect(card.getAttribute("aria-busy")).toBe("true");
    expect(said()).toEqual(["Loading the summary for AAPL"]);
    await doing(() => { client.setQueryData(key, summary); });
    await settle();
    expect(card.getAttribute("aria-busy")).toBe("false");
    expect(said()).toEqual(["The summary for AAPL is shown."]);
    expect(card.textContent).toContain("Apple Inc is a US technology company listed on NASDAQ.");
  });

  it("no summary yet, and a summary that could not be read, are said too", async () => {
    await inSheet([{ key, data: null }]);
    expect(said()).toEqual(["No summary for AAPL yet."]);
    await inSheet([{ key, error: boom() }]);
    expect(said()).toEqual(["The summary for AAPL couldn't load."]);
    expect(q(".ss")!.textContent).toContain("Couldn't load the summary. The rest of this page is unaffected.");
  });

  it("the card has nothing to operate: its text is read in order, under its own heading", async () => {
    await inSheet([{ key, data: summary }]);
    const card = q(".ss")!;
    expect(card.querySelectorAll("button, a, input, [tabindex]")).toHaveLength(0);
    expect(document.getElementById(card.getAttribute("aria-labelledby")!)!.textContent).toBe("In plain words");
  });
});

describe("the focus ring", () => {
  it("every button and link shows the 2 px ring in the focus colour, and no new control switches it off", () => {
    expect(css).toContain("button:focus-visible, a:focus-visible, input:focus-visible, select:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }");
    const off = [...css.matchAll(/([^{}]+)\{[^}]*outline:\s*(?:none|0)\b[^}]*\}/g)].map((m) => m[1].replace(/\/\*[\s\S]*?\*\//g, "").trim());
    // Not controls: a heading and the page area that take focus after a navigation, the sheet's panel (focus moves to
    // it so the next Tab is inside), and the search box, whose ring is drawn on its wrapper (.kb-search:focus-within).
    // (sorted: since #723 the Strategy rules, .kb-search among them, come after app.css's)
    expect(off.sort()).toEqual([".kb-search input", ".sheet", 'h1[tabindex="-1"]:focus', "main:focus"]);
    expect(css).toMatch(/\.kb-search:focus-within \{[^}]*(?:outline|border-color|box-shadow)/);
  });
  it("cards in a sideways scroller (theme cards, saved lists on a phone) draw the ring inside, where it is not cut off", () => {
    expect(css).toMatch(/\.kb-tgrid \{ display: flex; overflow-x: auto;/);
    expect(css).toContain(".kb-tcard-open:focus-visible, .kb-tcheck:focus-visible, .kb-lcard:focus-visible { outline-offset: -2px; }");
  });
});
