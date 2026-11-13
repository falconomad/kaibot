// S69 (#97): the custom ticker list rules and the RPC error handling (lib/tickers.ts, config.ts custom support).
import { describe, expect, it } from "vitest";
import estimatorJson from "../data/estimator.json";
import themesJson from "../data/themes.json";
import { clampBody, diffConfig, focusSet, levelDefaults, normalizeBounds, validateBody, type ConfigBody, type Themes } from "./config";
import { estShort, estimateTrades, type EstimatorData } from "./estimator";
import {
  DB_UPDATE, MAX_TICKERS, NOT_A_TICKER, addable, applyBlockers, dropCustomUnlessOnly, isMissingFunction, itemFromCheck, itemFromSaved, itemsFromBody,
  listWords, looksLikeTicker, needsCheck, onlyShort, parseTickers, rpcErrorText, saveBlocker, tally, withList, withoutCustom, type Item,
} from "./tickers";

const EST = estimatorJson as unknown as EstimatorData;
const THEMES = themesJson as Themes;
const UNI = Object.keys(EST.dip_per_month);
const B = normalizeBounds(null, "balanced");
const D = () => levelDefaults(B);
const ok = (symbol: string, etf: string | null = "XLK", inCatalogue = false): Item => ({ symbol, state: "accepted", etf, inCatalogue });
const no = (symbol: string, reason = "too thin"): Item => ({ symbol, state: "refused", reason });
const FRESH = ["NBIS", "CRWV", "FSLY", "PLTR", "SNOW"];

describe("typing and pasting tickers", () => {
  it("splits on commas, spaces, semicolons and new lines, upper-cases, strips $ and de-duplicates", () => {
    expect(parseTickers("$nbis, crwv;FSLY\n  nbis | pltr ")).toEqual(["NBIS", "CRWV", "FSLY", "PLTR"]);
    expect(parseTickers("   ")).toEqual([]);
  });
  it("refuses obvious non-tickers without a server call", () => {
    expect(looksLikeTicker("PLTR")).toBe(true);
    expect(looksLikeTicker("BRK.B")).toBe(true);
    for (const s of ["123", "PL TR", "", "$$", "TOOLONGSYMBOL"]) expect(looksLikeTicker(s)).toBe(false);
    expect(NOT_A_TICKER).toMatch(/1 to 5 letters/);
  });
});

describe("at most 40 tickers", () => {
  it("adds only up to 40 and says how many were left out; duplicates are skipped", () => {
    const have = Array.from({ length: 38 }, (_, i) => `T${String.fromCharCode(65 + (i % 26))}${i}`);
    const r = addable(have, ["NBIS", "CRWV", "FSLY", "NBIS", have[0]]);
    expect(r.add).toEqual(["NBIS", "CRWV"]);
    expect(r.overflow).toBe(1);
    expect(have.length + r.add.length).toBe(MAX_TICKERS);
  });
  it("a full list reports full, and refused tickers count towards the 40", () => {
    const items = [...Array.from({ length: 39 }, (_, i) => ok(`A${i}`)), no("ZZZT")];
    expect(tally(items).full).toBe(true);
    expect(addable(items.map((i) => i.symbol), ["NBIS"]).add).toEqual([]);
  });
  it("the cleaned body never carries more than 40 custom tickers", () => {
    const d = D();
    const tickers = Array.from({ length: 45 }, (_, i) => ({ symbol: `Q${String.fromCharCode(65 + (i % 26))}${String.fromCharCode(65 + Math.floor(i / 26))}`, etf: "XLK" }));
    const x = clampBody({ ...d, focus: { ...d.focus, mode: "only", custom: { name: "big", tickers } } }, B, THEMES, UNI);
    expect(x.focus.custom?.tickers.length).toBe(40);
  });
});

describe("at least 5 accepted in Only mode", () => {
  it("warns with how many are missing, only in Only mode and only when something was added", () => {
    const four = FRESH.slice(0, 4).map((s) => ok(s));
    expect(onlyShort(four, "only")).toBe(1);
    expect(onlyShort([...four, no("ZZZT")], "only")).toBe(1); // a refused one does not count
    expect(onlyShort(four, "prefer")).toBe(0);
    expect(onlyShort([], "only")).toBe(0);
    expect(onlyShort(FRESH.map((s) => ok(s)), "only")).toBe(0);
  });
  it("the config validator counts custom tickers towards the 5 and blocks fewer", () => {
    const four = withList(D(), FRESH.slice(0, 4).map((s) => ok(s)), "Mine", UNI);
    expect(validateBody(four, B, THEMES).join(" ")).toMatch(/at least 5 stocks/);
    const five = withList(D(), FRESH.map((s) => ok(s)), "Mine", UNI);
    expect(focusSet(five, THEMES)).toEqual(FRESH);
    expect(validateBody(five, B, THEMES)).toEqual([]);
  });
});

describe("only accepted tickers reach the draft", () => {
  const items: Item[] = [ok("NBIS"), ok("CRWV", "XLK"), ok("MSFT", null, true), no("ZZZT"), { symbol: "FSLY", state: "checking" },
    { symbol: "PLTR", state: "slow" }, { symbol: "SNOW", state: "error", reason: "x" }, ok("AAPL", null, true), ok("AMD", null, true)];
  const body = withList(D(), items, "  AI infra  ", UNI);
  it("catalogue stocks go to symbols, others to custom with their sector fund, mode becomes Only, themes cleared", () => {
    expect(body.focus.mode).toBe("only");
    expect(body.focus.themes).toEqual([]);
    expect(body.focus.symbols).toEqual(["MSFT", "AAPL", "AMD"]);
    expect(body.focus.custom).toEqual({ name: "AI infra", tickers: [{ symbol: "NBIS", etf: "XLK" }, { symbol: "CRWV", etf: "XLK" }] });
  });
  it("refused, checking, slow and errored tickers are never written", () => {
    const all = [...body.focus.symbols, ...(body.focus.custom?.tickers.map((t) => t.symbol) ?? [])];
    for (const s of ["ZZZT", "FSLY", "PLTR", "SNOW"]) expect(all).not.toContain(s);
  });
  it("an accepted ticker without a sector fund is not written as custom", () => {
    expect(withList(D(), [ok("NBIS", null)], "x", UNI).focus.custom).toBeUndefined();
  });
  it("a catalogue stock is never put in custom, even if the check missed in_catalogue", () => {
    const x = withList(D(), [ok("MSFT", "XLK", false)], "x", UNI);
    expect(x.focus.symbols).toEqual(["MSFT"]);
    expect(x.focus.custom).toBeUndefined();
  });
  it("the cleaned body keeps the list and the diff names the tickers", () => {
    const x = clampBody(body, B, THEMES, UNI);
    expect(x.focus.custom?.tickers.map((t) => t.symbol)).toEqual(["NBIS", "CRWV"]);
    expect(diffConfig(D(), x, THEMES).find((r) => r.key === "custom")?.to).toBe("CRWV, NBIS");
  });
  it("a saved draft comes back as accepted rows", () => {
    expect(itemsFromBody(body).map((i) => `${i.symbol}:${i.state}`)).toEqual(["MSFT:accepted", "AAPL:accepted", "AMD:accepted", "NBIS:accepted", "CRWV:accepted"]);
  });
});

describe("drop focus.custom when the mode isn't Only", () => {
  const only = withList(D(), FRESH.map((s) => ok(s)), "Mine", UNI);
  it("switching to Prefer or All removes the list from the draft", () => {
    for (const mode of ["prefer", "all"] as const) {
      const x = dropCustomUnlessOnly({ ...only, focus: { ...only.focus, mode } });
      expect(x.focus.custom).toBeUndefined();
      expect("custom" in x.focus).toBe(false);
    }
    expect(dropCustomUnlessOnly(only)).toBe(only);
  });
  it("clampBody drops it too, so no path can send a custom list outside Only", () => {
    for (const mode of ["prefer", "all"] as const)
      expect(clampBody({ ...only, focus: { ...only.focus, mode } }, B, THEMES, UNI).focus.custom).toBeUndefined();
  });
  it("in Prefer mode a list writes only its catalogue stocks", () => {
    const pre: ConfigBody = { ...D(), focus: { ...D().focus, mode: "prefer", themes: ["x"] } };
    const x = withList(pre, [ok("NBIS"), ok("MSFT", null, true)], "Mine", UNI);
    expect(x.focus.mode).toBe("prefer");
    expect(x.focus.symbols).toEqual(["MSFT"]);
    expect(x.focus.custom).toBeUndefined();
  });
  it("the validator rejects a hand-made custom list outside Only with the server's sentence", () => {
    const bad = { ...only, focus: { ...only.focus, mode: "prefer" as const } };
    expect(validateBody(bad, B, THEMES)).toContain("A custom list can only be used with 'Only these'. Choose 'Only these' or remove the list.");
  });
});

describe("trades a month: not measured yet for new tickers", () => {
  it("a custom ticker has no rate, so the estimate says not measured yet", () => {
    const x = withList(D(), [...FRESH.slice(0, 2).map((s) => ok(s)), ok("MSFT", null, true), ok("AAPL", null, true), ok("AMD", null, true)], "Mine", UNI);
    const e = estimateTrades(x, EST, THEMES);
    expect(e.unmeasured).toEqual(["NBIS", "CRWV"]);
    expect(estShort(e)).toBe("not measured yet");
  });
  it("a list of catalogue stocks only keeps its measured estimate", () => {
    const x = withList(D(), ["MSFT", "AAPL", "AMD", "NVDA", "AMZN"].map((s) => ok(s, null, true)), "Big", UNI);
    const e = estimateTrades(x, EST, THEMES);
    expect(e.unmeasured).toEqual([]);
    expect(estShort(e)).toMatch(/^≈ \d+$/);
  });
});

describe("saving a named list", () => {
  it("waits for checks, needs an accepted ticker and a name of at most 40 characters", () => {
    expect(saveBlocker("x", [ok("NBIS"), { symbol: "FSLY", state: "checking" }])).toMatch(/Checking 1 ticker/);
    expect(saveBlocker("x", [no("ZZZT")])).toMatch(/accepted ticker/);
    expect(saveBlocker("  ", [ok("NBIS")])).toMatch(/Name the list/);
    expect(saveBlocker("x".repeat(41), [ok("NBIS")])).toMatch(/40 characters/);
    expect(saveBlocker("Mine", [ok("NBIS"), no("ZZZT")])).toBeNull();
  });
});

describe("reading kb_check_ticker and kb_ticker_lists answers", () => {
  it("maps each status; refused always carries a reason", () => {
    expect(itemFromCheck({ symbol: "nbis", status: "accepted", reason: null, etf: "XLK", in_catalogue: false })).toMatchObject({ symbol: "NBIS", state: "accepted", etf: "XLK" });
    expect(itemFromCheck({ symbol: "ZZZT", status: "refused", reason: "We could not find ZZZT at the broker. Check the spelling." }).reason).toMatch(/broker/);
    expect(itemFromCheck({ symbol: "ZZZT", status: "refused", reason: null }).reason).toBeTruthy();
    expect(itemFromCheck({ symbol: "X", status: "error", reason: null }).reason).toMatch(/could not check X/);
    expect(itemFromCheck({ symbol: "X", status: "pending", reason: null }).state).toBe("checking");
  });
  it("a saved entry counts as accepted only with a recent accepted check; others are checked again", () => {
    expect(itemFromSaved({ symbol: "NBIS", status: "accepted", etf: "XLK" }).state).toBe("accepted");
    expect(itemFromSaved({ symbol: "NBIS", status: "accepted" }).state).toBe("checking"); // no sector fund: check again
    expect(itemFromSaved({ symbol: "MSFT", status: "accepted", in_catalogue: true }).state).toBe("accepted");
    expect(itemFromSaved({ symbol: "NBIS", status: "unchecked" }).state).toBe("checking");
    expect(itemFromSaved({ symbol: "ZZZT", status: "refused", reason: "too thin" }).state).toBe("refused");
    expect(itemFromSaved("pltr").state).toBe("checking");
    expect(needsCheck([itemFromSaved("PLTR"), ok("NBIS")])).toEqual(["PLTR"]);
  });
});

describe("never apply refused tickers: the re-check before apply", () => {
  const tickers = [{ symbol: "NBIS", etf: "XLK" }, { symbol: "CRWV", etf: "XLK" }, { symbol: "FSLY", etf: "XLK" }, { symbol: "PLTR", etf: "XLK" }, { symbol: "SNOW", etf: "XLK" }];
  it("blocks a refused, pending, missing or sector-changed ticker and lets the accepted ones through", () => {
    const bad = applyBlockers(tickers, [
      { symbol: "NBIS", status: "accepted", reason: null, etf: "XLK" },
      { symbol: "CRWV", status: "refused", reason: "CRWV closed at $3.10; Kaibot only trades stocks above $5." },
      { symbol: "FSLY", status: "pending", reason: null },
      null,
      { symbol: "SNOW", status: "accepted", reason: null, etf: "SPY" },
    ]);
    expect(bad.map((b) => b.symbol)).toEqual(["CRWV", "FSLY", "PLTR", "SNOW"]);
    expect(bad[0].why).toMatch(/above \$5/);
  });
  it("all accepted with the same fund: nothing blocks", () => {
    expect(applyBlockers(tickers, tickers.map((t) => ({ ...t, status: "accepted" as const, reason: null })))).toEqual([]);
  });
  it("blocked tickers are taken out of the draft; the list goes when none is left", () => {
    const body = withList(D(), FRESH.map((s) => ok(s)), "Mine", UNI);
    expect(withoutCustom(body, ["NBIS"]).focus.custom?.tickers.map((t) => t.symbol)).toEqual(["CRWV", "FSLY", "PLTR", "SNOW"]);
    expect(withoutCustom(body, FRESH).focus.custom).toBeUndefined();
  });
});

describe("RPC errors", () => {
  it("knows a function that isn't in the database yet (before the migration step)", () => {
    expect(isMissingFunction({ code: "PGRST202", message: "Could not find the function public.kb_delete_ticker_list(p_name) in the schema cache" })).toBe(true);
    expect(isMissingFunction({ code: "42883", message: "function public.kb_delete_ticker_list(text) does not exist" })).toBe(true);
    expect(isMissingFunction({ message: "function kb_apply_config(bigint, uuid) does not exist" })).toBe(true);
    expect(isMissingFunction({ code: "22023", message: "A list can hold at most 40 tickers." })).toBe(false);
    expect(isMissingFunction({ code: "42501", message: "permission denied" })).toBe(false);
    expect(isMissingFunction(null)).toBe(false);
  });
  it("shows server rule messages as they are, and plain words otherwise", () => {
    expect(rpcErrorText({ code: "22023", message: "You can keep at most 20 lists. Delete one first." })).toBe("You can keep at most 20 lists. Delete one first.");
    expect(rpcErrorText({ code: "PGRST202", message: "Could not find the function" })).toBe(DB_UPDATE);
    expect(rpcErrorText(new Error("Failed to fetch"))).toBe("Couldn't reach the server: Failed to fetch");
  });
  it("names lists of tickers in plain words", () => {
    expect(listWords(["NBIS"])).toBe("NBIS");
    expect(listWords(["NBIS", "CRWV"])).toBe("NBIS and CRWV");
    expect(listWords(["NBIS", "CRWV", "FSLY"])).toBe("NBIS, CRWV and FSLY");
    expect(listWords(FRESH)).toBe("NBIS, CRWV, FSLY and 2 more");
  });
});
