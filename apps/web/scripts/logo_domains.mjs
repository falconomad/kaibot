#!/usr/bin/env node
// Fill in each symbol's company website (`domain`) in src/data/symbols.json, for the ticker-logo CDN
// https://cdn.tickerlogos.com/{domain} (stakeholder, 18 Nov 2026). Dev-only; needs network access:
//
//     cd apps/web && node scripts/logo_domains.mjs            # only symbols whose domain is still null
//     cd apps/web && node scripts/logo_domains.mjs --all      # look every symbol up again; prints what changed
//     cd apps/web && node scripts/logo_domains.mjs --dry-run  # print, write nothing
//
// It asks AllInvestView's search API (GET https://www.allinvestview.com/api/logo-search/?q=AAPL, at most 60 a minute
// per IP; this paces itself to one call every 1.2 s) and takes only the result whose symbol equals the ticker exactly,
// a US listing first, never a fuzzy match (the same pick as api/logo-domain.ts; apiLogoDomain.test.ts checks both).
// It then checks the CDN has a logo for that domain (a 404 is reported, the domain is still kept: the app falls back
// to the next source by itself). A symbol with no exact match keeps domain: null; the app then asks /api/logo-domain
// at runtime, and shows our Finnhub logo, FMP's, or the initials while there is none.
// Hand-entered domains are kept unless --all is given.
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export const SEARCH_URL = "https://www.allinvestview.com/api/logo-search/";
export const CDN = "https://cdn.tickerlogos.com/";
const US = new Set(["NASDAQ", "NASDAQGS", "NASDAQGM", "NASDAQCM", "NYSE", "NYSE ARCA", "NYSEARCA", "ARCA", "NYSE AMERICAN",
  "NYSE MKT", "NYSEAMERICAN", "AMEX", "BATS", "CBOE", "CBOE BZX", "BZX", "CBOE BZX EXCHANGE"]);
const LABEL = /^(?!-)[a-z0-9-]{1,63}(?<!-)$/;

export function cleanDomain(website) {
  if (typeof website !== "string") return null;
  let s = website.trim().toLowerCase();
  if (!s || /[\s@#\\]/.test(s)) return null;
  s = s.replace(/^https?:\/\//, "");
  s = s.split(/[/?]/)[0].replace(/:\d{1,5}$/, "").replace(/^www\./, "");
  if (s.length > 253 || s.includes(":")) return null;
  const labels = s.split(".");
  if (labels.length < 2 || !labels.every((l) => LABEL.test(l))) return null;
  if (!/^[a-z]{2,63}$/.test(labels[labels.length - 1])) return null;
  if (labels.some((l) => l.startsWith("xn--") && l.length < 6)) return null;
  return s;
}

export function pickWebsite(symbol, results) {
  if (!Array.isArray(results)) return null;
  const exact = results.filter((r) => r && typeof r === "object" && r.symbol === symbol);
  const us = (r) => typeof r.exchange === "string" && US.has(r.exchange.trim().toUpperCase());
  for (const r of [...exact.filter(us), ...exact.filter((r) => !us(r))]) {
    const d = cleanDomain(r.website);
    if (d) return d;
  }
  return null;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main(argv) {
  const all = argv.includes("--all"), dry = argv.includes("--dry-run");
  const file = join(dirname(fileURLToPath(import.meta.url)), "..", "src", "data", "symbols.json");
  const meta = JSON.parse(readFileSync(file, "utf8"));
  let asked = 0, found = 0;
  for (const sym of Object.keys(meta).sort()) {
    const had = meta[sym].domain ?? null;
    if (had && !all) continue;
    if (asked++) await sleep(1200);
    let domain = null;
    try {
      const r = await fetch(`${SEARCH_URL}?q=${encodeURIComponent(sym)}`, { headers: { accept: "application/json" }, signal: AbortSignal.timeout(15000) });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      domain = pickWebsite(sym, (await r.json())?.results);
    } catch (e) {
      console.error(`${sym}: search failed (${e.message}); kept ${had}`);
      continue;
    }
    if (!domain) { console.error(`${sym}: no exact match; kept ${had}`); continue; }
    found++;
    const logo = await fetch(CDN + domain, { method: "GET", signal: AbortSignal.timeout(15000) }).then((r) => r.status).catch(() => "error");
    console.log(`${sym}: ${domain}${had && had !== domain ? ` (was ${had})` : ""}${logo === 200 ? "" : `  [logo: ${logo}]`}`);
    meta[sym] = { ...meta[sym], domain };
  }
  for (const sym of Object.keys(meta)) if (!("domain" in meta[sym])) meta[sym].domain = null;
  if (!dry) writeFileSync(file, JSON.stringify(meta, null, 1) + "\n");
  console.log(`${dry ? "would write" : "wrote"} src/data/symbols.json: ${found} of ${asked} looked up found`);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) main(process.argv.slice(2)).catch((e) => { console.error(e); process.exit(1); });
