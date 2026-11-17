// The size budget of the first load (#297). Runs at the end of `npm run build`, so the `web` CI job fails when the
// first screen grows past the budget in size-budget.json. No dependencies: Node only.
//
//   node scripts/size_budget.mjs [--dist dist] [--budget size-budget.json]
//
// What counts as the first load is read from the built index.html, as the browser reads it:
// - JavaScript: the entry script, every <link rel="modulepreload">, and whatever those import statically. Pages that
//   load on demand (`import()`) are not in it.
// - CSS: the stylesheets of this site (another host's stylesheet is not ours to weigh).
// - Fonts: the files index.html preloads.
// JavaScript and CSS are weighed gzipped (level 9), fonts as they are (woff2 is already compressed).
//
// On Vercel the check prints and never fails: CI is the gate, and a deploy is not held back by it.
import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { gzipSync } from "node:zlib";
import { fileURLToPath } from "node:url";

const attr = (tag, name) => new RegExp(`\\s${name}="([^"]*)"`).exec(tag)?.[1] ?? null;
const local = (href) => !!href && href.startsWith("/") && !href.startsWith("//");
const clean = (href) => href.split("?")[0].split("#")[0].replace(/^\//, "");

/** The files of the first load, by kind, from the built index.html (paths relative to the dist folder). */
export function firstLoad(dist) {
  const html = readFileSync(join(dist, "index.html"), "utf8");
  const tags = html.match(/<(?:script|link)\b[^>]*>/g) ?? [];
  const js = new Set(), css = new Set(), fonts = new Set();
  for (const tag of tags) {
    const rel = attr(tag, "rel"), href = attr(tag, tag.startsWith("<script") ? "src" : "href");
    if (!local(href)) continue;
    if (tag.startsWith("<script") && attr(tag, "type") === "module") js.add(clean(href));
    else if (rel === "modulepreload") js.add(clean(href));
    else if (rel === "stylesheet") css.add(clean(href));
    else if (rel === "preload" && attr(tag, "as") === "font") fonts.add(clean(href));
  }
  // Static imports of those chunks (`from"./x.js"`, `import"./x.js"`); `import("./x.js")` is on demand and not followed.
  const todo = [...js];
  while (todo.length) {
    const file = todo.pop();
    const code = readFileSync(join(dist, file), "utf8");
    for (const m of code.matchAll(/(?:from|import)\s*["'](\.{1,2}\/[^"']+\.js)["']/g)) {
      const dep = join(dirname(file), m[1]).replace(/\\/g, "/");
      if (!js.has(dep)) { js.add(dep); todo.push(dep); }
    }
  }
  return { js: [...js].sort(), css: [...css].sort(), fonts: [...fonts].sort() };
}

const gz = (path) => gzipSync(readFileSync(path), { level: 9 }).length;
const raw = (path) => readFileSync(path).length;

/** Bytes per file and in total: { first_load_js_gzip, first_load_css_gzip, preloaded_fonts }, each { total, files }. */
export function measure(dist) {
  const files = firstLoad(dist);
  const sum = (list, weigh) => {
    const rows = list.map((f) => [f, weigh(join(dist, f))]);
    return { total: rows.reduce((n, r) => n + r[1], 0), files: Object.fromEntries(rows) };
  };
  return { first_load_js_gzip: sum(files.js, gz), first_load_css_gzip: sum(files.css, gz), preloaded_fonts: sum(files.fonts, raw) };
}

/** One line per budget that is exceeded; an empty list passes. A budget with nothing measured against it also fails,
 *  so a build that stops listing its files cannot pass by weighing nothing. */
export function check(measured, budget) {
  const over = [];
  for (const [key, limit] of Object.entries(budget)) {
    const got = measured[key]?.total;
    if (!got) over.push(`${key}: nothing was measured (is index.html still listing these files?)`);
    else if (got > limit) over.push(`${key}: ${got} bytes is ${got - limit} bytes over the budget of ${limit}`);
  }
  return over;
}

const kb = (n) => `${(n / 1000).toFixed(1)} kB`;

export function main(argv = process.argv.slice(2), env = process.env) {
  const arg = (name, fallback) => (argv.includes(name) ? argv[argv.indexOf(name) + 1] : fallback);
  const here = resolve(dirname(fileURLToPath(import.meta.url)), "..");
  const dist = resolve(arg("--dist", join(here, "dist")));
  const budgetFile = resolve(arg("--budget", join(here, "size-budget.json")));
  if (!existsSync(join(dist, "index.html"))) { console.error(`size budget: no build at ${dist}`); return 1; }
  const { budget, measured: basis } = JSON.parse(readFileSync(budgetFile, "utf8"));
  const measured = measure(dist);
  console.log("\nFirst-load size budget (size-budget.json):");
  for (const [key, m] of Object.entries(measured)) {
    for (const [f, n] of Object.entries(m.files)) console.log(`  ${kb(n).padStart(9)}  ${f}`);
    const limit = budget[key];
    console.log(`  ${kb(m.total).padStart(9)}  ${key}${limit ? `: budget ${kb(limit)}, set from ${kb(basis[key])} measured on ${basis.on}` : ""}`);
  }
  const over = check(measured, budget);
  if (!over.length) { console.log("size budget: within budget\n"); return 0; }
  for (const line of over) console.error(`size budget EXCEEDED: ${line}`);
  console.error("Make the first load smaller (load the new code on demand), or raise the budget in size-budget.json with the new measured number and the reason in the PR.\n");
  if (env.VERCEL) { console.error("size budget: on Vercel this only warns; CI is the gate."); return 0; }
  return 1;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) process.exit(main());
