// S69 (#97): the 'My lists' card in the Strategy page's Focus card: states, the database-update notes and the rules.
import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { Route } from "react-router-dom";
import { levelDefaults, normalizeBounds, type ConfigBody, type ConfigResp } from "../lib/config";
import { estimateTrades } from "../lib/estimator";
import type { Item } from "../lib/tickers";
import { MyListsCard, ThisListCard, type ListEditor } from "./MyLists";
import { Strategy, StrategyHome } from "./Strategy";
import { EST } from "./StrategyShared";
import { glitches, hasAlert, render, text, type Seed } from "./testkit";

vi.mock("../lib/api", () => import("./testkit-api"));

const B = normalizeBounds(null, "balanced");
const ok = (symbol: string, etf = "XLK", inCatalogue = false): Item => ({ symbol, state: "accepted", etf, inCatalogue });
const custom = { name: "AI infrastructure", tickers: ["NBIS", "CRWV", "FSLY", "PLTR", "SNOW"].map((symbol) => ({ symbol, etf: "XLK" })) };
const draftBody = () => { const d = levelDefaults(B); return { ...d, focus: { ...d.focus, mode: "only" as const, custom } }; };
const resp = (body: ConfigBody = draftBody()) => ({ level: "balanced", bounds: {}, themes: {}, universe: [], applied: null, pending: null,
  draft: { body, updated_at: "2026-11-12T20:00:00Z" }, versions: [] } as unknown as ConfigResp);
const missing = Object.assign(new Error("Could not find the function public.kb_ticker_lists without parameters in the schema cache"), { code: "PGRST202" });

const page = (seeds: Seed[]) => render(<Strategy />, {
  path: "/strategy", route: "/strategy", seeds, children: <Route index element={<StrategyHome />} />,
});

/** A list editor with fixed state, for rendering the card on its own. */
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
const card = (ed: ListEditor, mode: "all" | "prefer" | "only" = "only") => renderToStaticMarkup(<MyListsCard ed={ed} mode={mode} />);

describe("My lists on the Strategy page", () => {
  it("a draft with a custom list opens on My lists with its tickers accepted and trades a month not measured yet", () => {
    const html = page([{ key: ["config", 1], data: resp() }]);
    const t = text(html);
    expect(t).toContain("My lists");
    expect(t).toContain("5 of 40");
    expect(html).toContain('aria-label="NBIS, accepted"');
    expect(t).toContain("This list");
    expect(t).toContain("not measured yet");
    expect(t).toMatch(/new to Kaibot, so there is no measured trade rate/);
    expect(t).toContain("trades/mo not measured yet");
    expect(html).toContain('aria-label="Loading your lists"'); // the saved lists are still loading: skeleton cards
    expect(glitches(html)).toEqual([]);
  });

  it("before the database update: saved lists say so in plain words, the editor still works", () => {
    const html = page([{ key: ["config", 1], data: resp() }, { key: ["tickerLists"], error: missing }]);
    const t = text(html);
    expect(t).toContain("Saved lists need a database update that isn't applied yet.");
    expect(t).not.toContain("schema cache");
    expect(html).toContain('id="ml-add"');
  });

  it("another error loading the lists is an alert with the message", () => {
    const html = page([{ key: ["config", 1], data: resp() }, { key: ["tickerLists"], error: Object.assign(new Error("permission denied"), { code: "42501" }) }]);
    expect(hasAlert(html)).toBe(true);
    expect(text(html)).toContain("Couldn't load your saved lists");
  });

  it("a draft without a custom list opens on Themes", () => {
    const html = page([{ key: ["config", 1], data: resp(levelDefaults(B)) }]);
    expect(html).toContain('aria-label="Themes"');
    expect(html).not.toContain('id="ml-add"');
  });
});

describe("the My lists card", () => {
  it("shows checking, accepted and refused tickers with the reason", () => {
    const html = card(fakeEd([ok("NBIS"), { symbol: "FSLY", state: "checking" }, { symbol: "ZZZT", state: "refused", reason: "We could not find ZZZT at the broker. Check the spelling." }]));
    const t = text(html);
    expect(html).toContain('aria-label="FSLY, checking"');
    expect(html).toContain('aria-label="ZZZT, refused: We could not find ZZZT at the broker. Check the spelling."');
    expect(t).toContain("1 accepted · 1 refused · 1 checking");
    expect(t).toContain("Checking 1 ticker…");
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Save list<\/button>/);
  });

  it("Only these with fewer than 5 accepted warns how many to add", () => {
    const t = text(card(fakeEd([ok("NBIS"), ok("CRWV")])));
    expect(t).toContain("“Only these” needs at least 5 accepted tickers. This list has 2. Add 3 more.");
  });

  it("Prefer mode says new tickers are left out", () => {
    const t = text(card(fakeEd([ok("NBIS"), ok("MSFT", "XLK", true)]), "prefer"));
    expect(t).toMatch(/“Prefer these” keeps all 59 catalogue stocks, so NBIS is left out/);
  });

  it("a full list disables the input and says why", () => {
    const items = Array.from({ length: 40 }, (_, i) => ok(`Q${String.fromCharCode(65 + (i % 26))}${String.fromCharCode(65 + Math.floor(i / 26))}`));
    const html = card(fakeEd(items));
    expect(html).toMatch(/<input[^>]*id="ml-add"[^>]*disabled=""/);
    expect(text(html)).toContain("This list is full: 40 tickers is the most Kaibot can scan in time.");
  });

  it("before the database update, delete is hidden with a plain note", () => {
    const cur = { name: "Cloud", updated_at: null, symbols: [] };
    const withDel = card(fakeEd([ok("NBIS")], { cur, sel: "Cloud", isNew: false } as Partial<ListEditor>));
    expect(text(withDel)).toContain("Delete list");
    const noDel = card(fakeEd([ok("NBIS")], { cur, sel: "Cloud", isNew: false, deleteMissing: () => true } as Partial<ListEditor>));
    expect(text(noDel)).not.toContain("Delete list");
    expect(text(noDel)).toContain("Deleting a list needs a database update that isn't applied yet. The list stays saved.");
  });

  it("a ticker still pending after 30 s offers a retry; an errored check too", () => {
    const html = card(fakeEd([{ symbol: "PLTR", state: "slow" }, { symbol: "SNOW", state: "error", reason: "We could not check SNOW right now. Try again later." }]));
    expect(html).toContain('aria-label="Check PLTR again"');
    expect(html).toContain('aria-label="Check SNOW again"');
    expect(text(html)).toContain("Not checked · We could not check SNOW right now.");
  });

  it("the side card counts and keeps the estimate honest", () => {
    const ed = fakeEd([ok("NBIS"), ok("MSFT", "XLK", true), { symbol: "ZZZT", state: "refused", reason: "x" }]);
    const d = levelDefaults(B);
    const est = estimateTrades({ ...d, focus: { ...d.focus, mode: "only", symbols: ["MSFT"], custom: { name: "x", tickers: [{ symbol: "NBIS", etf: "XLK" }] } } }, EST, {});
    const t = text(renderToStaticMarkup(<ThisListCard ed={ed} est={est} nextOpen="Mon 16 Nov 09:30 ET" />));
    expect(t).toContain("Trades a month not measured yet");
    expect(t).toContain("NBIS is new to Kaibot");
    expect(t).toMatch(/Accepted 2 Refused, never traded 1 New to Kaibot 1 Room left 37 of 40/);
  });
});
