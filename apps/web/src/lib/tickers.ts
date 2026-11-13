// M5 custom ticker lists (S69, #97; contract docs/m5/contract.md "Custom ticker lists", backend PR #103).
// Pure helpers behind the 'My lists' card: parsing typed or pasted tickers, the 40 / 5 rules, how a list is written
// into the config draft, and how RPC errors are read. No React, no network: unit-tested in tickers.test.ts.
// The rule that matters most: only an ACCEPTED ticker ever reaches the config body. Refused, unchecked and
// still-checking tickers are shown, never written, never applied.
import type { ConfigBody, CustomTicker } from "./config";

/** Most tickers in one list (the 5-minute scan and the free data limits). The server rejects more. */
export const MAX_TICKERS = 40;
/** 'Only these' needs at least this many stocks (themes + symbols + custom tickers). */
export const MIN_ONLY = 5;
/** Longest list name the server takes. */
export const MAX_NAME = 40;
/** kb_check_ticker answers `pending` while the worker checks: ask again every 2 s, for at most 30 s. */
export const POLL_MS = 2_000;
export const POLL_LIMIT_MS = 30_000;

export type CheckStatus = "accepted" | "refused" | "pending" | "error";
/** One answer of kb_check_ticker. */
export interface TickerCheck {
  symbol: string; status: CheckStatus; reason: string | null; etf?: string | null; in_catalogue?: boolean;
  name?: string | null; industry?: string | null; checked_on?: string | null;
}
/** One ticker in the list editor. `checking` = waiting for the server; `slow` = still pending after 30 s. */
export type ItemState = "checking" | "slow" | "accepted" | "refused" | "error";
export interface Item { symbol: string; state: ItemState; reason?: string | null; etf?: string | null; inCatalogue?: boolean; name?: string | null }

export interface SavedSymbol { symbol: string; status: CheckStatus | "unchecked"; in_catalogue?: boolean; reason?: string | null; etf?: string | null; name?: string | null }
export interface SavedList { name: string; updated_at: string | null; symbols: SavedSymbol[] }
export interface TickerLists { max_tickers: number; max_lists: number; min_only: number; lists: SavedList[] }

const SYMBOL = /^[A-Z][A-Z.-]{0,9}$/;
export const NOT_A_TICKER = "That does not look like a US stock ticker (1 to 5 letters, for example PLTR).";

/** Split typed or pasted text into clean, de-duplicated symbols ("$nbis, crwv\nFSLY" -> NBIS CRWV FSLY). */
export function parseTickers(raw: string): string[] {
  const out: string[] = [];
  for (const part of raw.split(/[\s,;|]+/)) {
    const s = part.trim().replace(/^\$/, "").toUpperCase();
    if (s && !out.includes(s)) out.push(s);
  }
  return out;
}

/** Can this text even be a ticker? Anything else is refused here, without a server call. The server has the final word. */
export const looksLikeTicker = (s: string) => SYMBOL.test(s);

/** Which of `incoming` can be added: not already in the list, and only up to MAX_TICKERS in total (refused ones count:
 * they take a row until removed). Returns the symbols to add and how many were left out because the list is full. */
export function addable(have: string[], incoming: string[], max = MAX_TICKERS): { add: string[]; overflow: number } {
  const add: string[] = [];
  let overflow = 0;
  for (const s of incoming) {
    if (have.includes(s) || add.includes(s)) continue;
    if (have.length + add.length >= max) overflow++;
    else add.push(s);
  }
  return { add, overflow };
}

/** The editor row for a kb_check_ticker answer. */
export function itemFromCheck(c: TickerCheck): Item {
  const symbol = String(c.symbol ?? "").toUpperCase();
  const base = { symbol, inCatalogue: !!c.in_catalogue, name: c.name ?? null };
  switch (c.status) {
    case "accepted": return { ...base, state: "accepted", etf: c.etf ?? null };
    case "refused": return { ...base, state: "refused", reason: c.reason || "Kaibot's safety filters refused this ticker." };
    case "error": return { ...base, state: "error", reason: c.reason || `We could not check ${symbol} right now. Try again later.` };
    default: return { ...base, state: "checking" };
  }
}

/** An editor row from a saved list's entry (latest check of the last 7 days, or unchecked). Only an accepted entry with
 * a sector fund counts as accepted; anything else must be checked again before it can be used. */
export function itemFromSaved(s: SavedSymbol | string): Item {
  if (typeof s === "string") return { symbol: s.toUpperCase(), state: "checking" };
  if (s.status === "accepted" && (s.in_catalogue || s.etf)) return itemFromCheck({ ...s, status: "accepted", reason: null });
  if (s.status === "refused") return itemFromCheck({ ...s, status: "refused", reason: s.reason ?? null });
  return { symbol: String(s.symbol).toUpperCase(), state: "checking" };
}

/** Tickers that still need a kb_check_ticker call. */
export const needsCheck = (items: Item[]) => items.filter((i) => i.state === "checking").map((i) => i.symbol);

export function tally(items: Item[]) {
  const accepted = items.filter((i) => i.state === "accepted");
  return {
    total: items.length,
    accepted,
    refused: items.filter((i) => i.state === "refused" || i.state === "error"),
    checking: items.filter((i) => i.state === "checking" || i.state === "slow"),
    /** accepted tickers outside the catalogue: no measured trade rate yet */
    fresh: accepted.filter((i) => !i.inCatalogue),
    full: items.length >= MAX_TICKERS,
  };
}

/** Why the list can't be saved as a named list, or null if it can. */
export function saveBlocker(name: string, items: Item[]): string | null {
  const t = tally(items);
  if (t.checking.length) return `Checking ${t.checking.length} ticker${t.checking.length === 1 ? "" : "s"}…`;
  if (!t.accepted.length) return "Add an accepted ticker to save.";
  const n = name.trim();
  if (!n) return "Name the list to save it.";
  if (n.length > MAX_NAME) return `Keep the name to ${MAX_NAME} characters.`;
  if (t.accepted.length > MAX_TICKERS) return `A list can hold at most ${MAX_TICKERS} tickers.`;
  return null;
}

/** 'Only these' warning: fewer than 5 accepted tickers. */
export function onlyShort(items: Item[], mode: ConfigBody["focus"]["mode"]): number {
  const n = tally(items).accepted.length;
  return mode === "only" && items.length > 0 && n < MIN_ONLY ? MIN_ONLY - n : 0;
}

/** Write the list into the draft (contract "Using a list in a config"): accepted catalogue stocks go to focus.symbols,
 * every other accepted ticker to focus.custom.tickers with its sector fund; themes are cleared; mode becomes Only
 * unless the owner picked Prefer, where custom tickers are left out. Refused and unchecked tickers are never written. */
export function withList(body: ConfigBody, items: Item[], name: string, universe: string[]): ConfigBody {
  const ok = items.filter((i) => i.state === "accepted");
  const inCat = (i: Item) => i.inCatalogue || universe.includes(i.symbol);
  const symbols = ok.filter(inCat).map((i) => i.symbol);
  const tickers: CustomTicker[] = ok.filter((i) => !inCat(i) && i.etf).map((i) => ({ symbol: i.symbol, etf: String(i.etf) }));
  const mode = body.focus.mode === "prefer" ? "prefer" : "only";
  const focus: ConfigBody["focus"] = { themes: [], symbols, exclude: body.focus.exclude, mode };
  if (mode === "only" && tickers.length) focus.custom = { name: name.trim().slice(0, MAX_NAME), tickers: tickers.slice(0, MAX_TICKERS) };
  if (!symbols.length && !focus.custom) focus.mode = "all";
  return { ...body, preset: "custom", focus };
}

/** The contract's rule for the page: outside Only mode the draft never carries focus.custom. */
export function dropCustomUnlessOnly(body: ConfigBody): ConfigBody {
  if (body.focus.mode === "only" || !body.focus.custom) return body;
  const { custom: _drop, ...focus } = body.focus;
  return { ...body, focus };
}

/** Remove some custom tickers from the draft (used when a re-check before apply refuses them). */
export function withoutCustom(body: ConfigBody, symbols: string[]): ConfigBody {
  const c = body.focus.custom;
  if (!c) return body;
  const tickers = c.tickers.filter((t) => !symbols.includes(t.symbol));
  const { custom: _drop, ...rest } = body.focus;
  return { ...body, focus: tickers.length ? { ...rest, custom: { ...c, tickers } } : rest };
}

/** The editor rows for the list a draft already carries (it was accepted when it was saved). */
export function itemsFromBody(body: ConfigBody): Item[] {
  if (!body.focus.custom) return [];
  return [
    ...body.focus.symbols.map((s) => ({ symbol: s, state: "accepted" as const, inCatalogue: true })),
    ...body.focus.custom.tickers.map((t) => ({ symbol: t.symbol, state: "accepted" as const, etf: t.etf, inCatalogue: false })),
  ];
}

/** Last guard before apply: each custom ticker must be accepted again today, with the same sector fund. Returns the
 * tickers that can't be applied with the reason, so nothing refused is ever applied. A missing answer is a refusal. */
export function applyBlockers(tickers: CustomTicker[], checks: (TickerCheck | null)[]): { symbol: string; why: string }[] {
  const out: { symbol: string; why: string }[] = [];
  tickers.forEach((t, i) => {
    const c = checks[i];
    if (!c || c.status === "pending") out.push({ symbol: t.symbol, why: "still being checked" });
    else if (c.status !== "accepted") out.push({ symbol: t.symbol, why: c.reason || "refused by the safety filters" });
    else if ((c.etf ?? null) !== t.etf) out.push({ symbol: t.symbol, why: "its sector fund changed since it was checked" });
  });
  return out;
}

// ---------------------------------------------------------------- RPC errors
type RpcError = { code?: string; message?: string } | null | undefined;
/** The function is not in the database yet (Postgres 42883, or PostgREST's PGRST202 "Could not find the function"). */
export function isMissingFunction(e: unknown): boolean {
  const x = e as RpcError;
  if (!x) return false;
  if (x.code === "42883" || x.code === "PGRST202") return true;
  return /could not find the function|function .* does not exist/i.test(String(x.message ?? ""));
}
export const DB_UPDATE = "This needs a database update that isn't applied yet. Nothing was changed.";
/** Plain words for an RPC failure. Server rule messages (22023) are shown as they are. */
export function rpcErrorText(e: unknown): string {
  const x = e as RpcError;
  if (isMissingFunction(e)) return DB_UPDATE;
  if (x?.code === "22023" && x.message) return x.message;
  return `Couldn't reach the server: ${x?.message ?? String(e)}`;
}

/** "NBIS", "NBIS and CRWV", "NBIS, CRWV and FSLY", "NBIS, CRWV, FSLY and 2 more" */
export function listWords(symbols: string[]): string {
  if (symbols.length > 3) return `${symbols.slice(0, 3).join(", ")} and ${symbols.length - 3} more`;
  if (symbols.length <= 1) return symbols.join("");
  return `${symbols.slice(0, -1).join(", ")} and ${symbols[symbols.length - 1]}`;
}
