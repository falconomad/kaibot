// Audit 2 ticket 7.3 (#713): the Content-Security-Policy in vercel.json, sent Report-Only first (violations show in
// the browser console; nothing is blocked). It must name every origin the browser really uses, so that switching it
// to enforcing later breaks nothing:
// - scripts: our own files; the one inline handler in index.html (the Inter sheet's onload) by its hash;
// - images: the logo sources of components/StockBadge.tsx (tickerlogos, our list's Finnhub URLs, FMP), data: and blob:;
// - connect: our /api/* functions ('self') and the Supabase project, over https and wss (the price stream);
// - Alpaca is not there on purpose: only the server functions in api/ call it, never the browser.
import { describe, expect, it } from "vitest";
import VERCEL from "../vercel.json";
import META from "./data/symbols.json";
import html from "../index.html?raw";
import py from "../../../src/kaibot/engine/config.py?raw";
import { TICKERLOGOS } from "./lib/logos";
import { fmpLogo } from "./components/StockBadge";

type Header = { key: string; value: string };
const all = (VERCEL as { headers: { source: string; headers: Header[] }[] }).headers.find((h) => h.source === "/(.*)")!;
const csp = all.headers.find((h) => h.key === "Content-Security-Policy-Report-Only")?.value ?? "";
const directive = (name: string) =>
  (csp.split(";").map((d) => d.trim().split(/\s+/)).find(([n]) => n === name) ?? []).slice(1);
const origin = (u: string) => new URL(u).origin;

describe("Content-Security-Policy (report-only)", () => {
  it("is sent on every path, report-only, with a safe default", () => {
    expect(csp).not.toBe("");
    expect(all.headers.some((h) => h.key === "Content-Security-Policy")).toBe(false); // not enforcing yet
    expect(directive("default-src")).toEqual(["'self'"]);
    expect(directive("object-src")).toEqual(["'none'"]);
    expect(directive("base-uri")).toEqual(["'self'"]);
    expect(csp).not.toMatch(/'unsafe-inline'|'unsafe-eval'|\*/);
  });

  it("allows index.html's inline handlers by hash and nothing inline else", async () => {
    const handlers = [...html.matchAll(/\son[a-z]+="([^"]*)"/g)].map((m) => m[1]);
    expect(handlers.length).toBeGreaterThan(0);
    const sha256 = async (s: string) =>
      btoa(String.fromCharCode(...new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s)))));
    const hashes = await Promise.all(handlers.map(async (h) => `'sha256-${await sha256(h)}'`));
    expect(directive("script-src")).toEqual(["'self'", "'unsafe-hashes'", ...hashes]);
    expect(html).not.toMatch(/<script(?![^>]*\ssrc=)[^>]*>/); // no inline <script> block
  });

  it("allows every logo source the badges use", () => {
    const img = directive("img-src");
    const used = new Set([origin(TICKERLOGOS), origin(fmpLogo("AAPL"))]);
    for (const m of Object.values(META as Record<string, { logo?: string | null }>)) if (m.logo) used.add(origin(m.logo));
    for (const o of used) expect(img).toContain(o);
    expect(img).toEqual(expect.arrayContaining(["'self'", "data:", "blob:"]));
  });

  it("allows the fonts and the Inter stylesheet of index.html", () => {
    const sheets = [...html.matchAll(/<link[^>]+rel="stylesheet"[^>]*>/g)].map((m) => /href="([^"]+)"/.exec(m[0])![1]);
    for (const s of sheets.filter((s) => s.startsWith("https://"))) expect(directive("style-src")).toContain(origin(s));
    expect(directive("font-src")).toEqual(["'self'", "https://fonts.gstatic.com"]);
  });

  it("allows the Supabase project the engine and the scripts name, over https and wss, and no broker", () => {
    const url = /DEFAULT_SUPABASE_URL = "([^"]+)"/.exec(py)![1];
    const host = new URL(url).host;
    expect(directive("connect-src")).toEqual(["'self'", `https://${host}`, `wss://${host}`]);
    expect(csp).not.toMatch(/alpaca/);
  });
});
