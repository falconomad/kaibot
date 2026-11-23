// The first load (#297, size-budget.json) carries only what the first screen needs. lib/api.ts is in it, so a value it
// imports pulls that whole module in: the stock summary's and the ticker checker's parsers are fetched on use instead.
import { describe, expect, it } from "vitest";
import API from "./api.ts?raw";

describe("lib/api.ts keeps lazy-only modules out of the first load", () => {
  it("imports nothing but types from ./summary and ./tickers, and fetches their parsers on use", () => {
    const imports = [...API.matchAll(/^import (type )?\{[^}]*\} from "\.\/(summary|tickers)";$/gm)];
    for (const m of imports) expect(m[1]).toBe("type ");
    expect(API).toContain('await import("./summary")');
    expect(API).toContain('await import("./tickers")');
  });
});
