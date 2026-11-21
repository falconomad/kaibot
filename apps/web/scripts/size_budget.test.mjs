// The size budget's own rules (#297): what counts as the first load, and that going over fails.
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { check, firstLoad, main, measure } from "./size_budget.mjs";

/** A small built site: an entry that imports one chunk statically and one on demand, a preloaded chunk, a stylesheet of
 *  its own and one from another host, and one preloaded font. */
function site() {
  const dist = mkdtempSync(join(tmpdir(), "kb-size-"));
  mkdirSync(join(dist, "assets"));
  mkdirSync(join(dist, "fonts"));
  const noise = (n) => Array.from({ length: n }, (_, i) => `x${(i * 7919) % 104729}`).join(";");
  writeFileSync(join(dist, "index.html"), `<!doctype html><head>
    <link rel="preload" href="/fonts/a.woff2" as="font" type="font/woff2" crossorigin />
    <link href="https://fonts.example/css2?family=Inter" rel="stylesheet" media="print" />
    <link rel="icon" href="/favicon.ico?v=k1" sizes="32x32" />
    <script type="module" crossorigin src="/assets/index.js"></script>
    <link rel="modulepreload" crossorigin href="/assets/vendor.js">
    <link rel="stylesheet" crossorigin href="/assets/index.css"></head>`);
  writeFileSync(join(dist, "assets/index.js"), `import{a}from"./vendor.js";import"./shared.js";const p=()=>import("./Page.js");${noise(400)}`);
  writeFileSync(join(dist, "assets/vendor.js"), `export const a=1;${noise(900)}`);
  writeFileSync(join(dist, "assets/shared.js"), `export const s=2;${noise(100)}`);
  writeFileSync(join(dist, "assets/Page.js"), `export const page=3;${noise(5000)}`);
  writeFileSync(join(dist, "assets/index.css"), `.a{color:red}${noise(200)}`);
  writeFileSync(join(dist, "fonts/a.woff2"), Buffer.alloc(1234, 7));
  return dist;
}
const budgetFile = (dist, budget) => {
  const f = join(dist, "budget.json");
  writeFileSync(f, JSON.stringify({ measured: { on: "2026-11-17", first_load_js_gzip: 1, first_load_css_gzip: 1, preloaded_fonts: 1 }, budget }));
  return f;
};
const quiet = (run) => {
  const log = vi.spyOn(console, "log").mockImplementation(() => {}), err = vi.spyOn(console, "error").mockImplementation(() => {});
  try { return [run(), err.mock.calls.flat().join("\n")]; } finally { log.mockRestore(); err.mockRestore(); }
};

describe("the first-load size budget", () => {
  it("the first load is the entry, the preloaded chunks and their static imports; on-demand pages and other hosts are not in it", () => {
    expect(firstLoad(site())).toEqual({ js: ["assets/index.js", "assets/shared.js", "assets/vendor.js"], css: ["assets/index.css"], fonts: ["fonts/a.woff2"] });
  });
  it("JavaScript and CSS are weighed gzipped, fonts as they are", () => {
    const dist = site(), m = measure(dist);
    expect(m.preloaded_fonts.total).toBe(1234);
    expect(m.first_load_js_gzip.total).toBeLessThan(readFileSync(join(dist, "assets/index.js")).length + readFileSync(join(dist, "assets/vendor.js")).length);
    expect(m.first_load_js_gzip.total).toBe(Object.values(m.first_load_js_gzip.files).reduce((a, b) => a + b, 0));
    expect(Object.keys(m.first_load_js_gzip.files)).not.toContain("assets/Page.js");
  });
  it("one byte over a budget fails; at the budget passes; a budget that weighed nothing fails", () => {
    const m = { first_load_js_gzip: { total: 1000 }, first_load_css_gzip: { total: 0 } };
    expect(check(m, { first_load_js_gzip: 1000 })).toEqual([]);
    expect(check(m, { first_load_js_gzip: 999 })).toEqual(["first_load_js_gzip: 1000 bytes is 1 bytes over the budget of 999"]);
    expect(check(m, { first_load_css_gzip: 5000 })[0]).toContain("nothing was measured");
  });
  it("the command exits 1 when the first-load JavaScript is over its budget and 0 when it is within; on Vercel it only warns", () => {
    const dist = site(), js = measure(dist).first_load_js_gzip.total;
    expect(quiet(() => main(["--dist", dist, "--budget", budgetFile(dist, { first_load_js_gzip: js })], {}))[0]).toBe(0);
    const [code, said] = quiet(() => main(["--dist", dist, "--budget", budgetFile(dist, { first_load_js_gzip: js - 1 })], {}));
    expect(code).toBe(1);
    expect(said).toContain("size budget EXCEEDED: first_load_js_gzip");
    expect(quiet(() => main(["--dist", dist, "--budget", budgetFile(dist, { first_load_js_gzip: js - 1 })], { VERCEL: "1" }))[0]).toBe(0);
    expect(quiet(() => main(["--dist", join(dist, "nowhere")], {}))[0]).toBe(1);
  });
  it("the build runs it, and the budget file says which measured numbers it came from", () => {
    const pkg = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));
    // #481: the installability check runs after it (scripts/pwa_check.mjs)
    expect(pkg.scripts.build).toBe("tsc --noEmit && vite build && node scripts/size_budget.mjs && node scripts/pwa_check.mjs");
    const { measured, budget } = JSON.parse(readFileSync(new URL("../size-budget.json", import.meta.url), "utf8"));
    expect(Object.keys(budget)).toEqual(["first_load_js_gzip", "first_load_css_gzip", "preloaded_fonts"]);
    for (const key of ["first_load_js_gzip", "first_load_css_gzip"]) {
      expect(budget[key]).toBe(Math.ceil((measured[key] * 1.03) / 1000) * 1000);
    }
    expect(budget.preloaded_fonts).toBeGreaterThanOrEqual(measured.preloaded_fonts);
    expect(measured.on).toMatch(/^\d{4}-\d\d-\d\d$/);
  });
});
