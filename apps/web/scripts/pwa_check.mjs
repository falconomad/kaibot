// The installable web app (M13 4.7, #481), checked on the build. Runs at the end of `npm run build`, so the `web` CI job
// fails when the build stops being installable or its service worker lists a file that is not there. Node only.
//
//   node scripts/pwa_check.mjs [--dist dist]
//
// What a phone's browser needs before it offers "add to home screen" as an app, and what must stay true of the worker:
// - index.html links the manifest and the iPhone's icon, and says the page can run in its own window;
// - the manifest has a name, start_url and scope at the root, display "standalone", and PNG icons of 192 and 512 px
//   that exist, one of them maskable;
// - sw.js is at the root, lists only files that exist in the build (the page, the app's scripts and styles, fonts),
//   and looks at no other host and never at /api/.
import { existsSync, readFileSync } from "node:fs";
import { join, resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

export function check(dist) {
  const bad = [];
  const need = (ok, msg) => { if (!ok) bad.push(msg); };
  const read = (f) => (existsSync(join(dist, f)) ? readFileSync(join(dist, f), "utf8") : null);

  const html = read("index.html");
  if (!html) return ["no build: index.html is missing"];
  const link = /<link rel="manifest" href="\/([^"?]+)[^"]*"/.exec(html)?.[1];
  need(link, "index.html does not link a manifest");
  need(/<link rel="apple-touch-icon" href="\/apple-touch-icon\.png/.test(html), "index.html does not link the apple-touch-icon");
  need(existsSync(join(dist, "apple-touch-icon.png")), "apple-touch-icon.png is missing");
  need(/<meta name="apple-mobile-web-app-capable" content="yes"/.test(html), "index.html lacks apple-mobile-web-app-capable");
  need(/<meta name="viewport" content="[^"]*width=device-width/.test(html), "index.html lacks a phone viewport");

  let m = null;
  try { m = link ? JSON.parse(read(link) ?? "") : null; } catch { m = null; }
  need(m, "the manifest is missing or is not JSON");
  if (m) {
    need(typeof m.name === "string" && m.name && typeof m.short_name === "string" && m.short_name, "manifest: name and short_name");
    need(m.display === "standalone", `manifest: display is ${JSON.stringify(m.display)}, not "standalone"`);
    need(m.start_url === "/" && m.scope === "/" && m.id === "/", "manifest: id, start_url and scope must be the site's root");
    need(/^#[0-9A-Fa-f]{6}$/.test(m.background_color ?? "") && /^#[0-9A-Fa-f]{6}$/.test(m.theme_color ?? ""), "manifest: background_color and theme_color");
    const icons = Array.isArray(m.icons) ? m.icons : [];
    for (const size of ["192x192", "512x512"]) {
      need(icons.some((i) => i.sizes === size && i.type === "image/png" && (i.purpose ?? "any") === "any"), `manifest: no ${size} PNG icon`);
    }
    need(icons.some((i) => i.purpose === "maskable" && i.sizes === "512x512"), "manifest: no maskable 512x512 icon");
    for (const i of icons) need(existsSync(join(dist, String(i.src).replace(/^\//, ""))), `manifest: icon ${i.src} is not in the build`);
  }

  const sw = read("sw.js");
  need(sw, "sw.js is missing from the build's root");
  if (sw) {
    let shell = [];
    try { shell = JSON.parse(/^const SHELL = (\[.*\]);$/m.exec(sw)?.[1] ?? "[]"); } catch { shell = []; }
    need(shell[0] === "/" && shell.length > 5, "sw.js: no shell list");
    for (const f of shell.slice(1)) {
      need(/^\/(assets\/[^/]+\.(js|css)|fonts\/[^/]+\.woff2)$/.test(f), `sw.js: ${f} is not a file of the app shell`);
      need(existsSync(join(dist, f.replace(/^\//, ""))), `sw.js: ${f} is not in the build`);
    }
    const entry = /<script type="module"[^>]* src="(\/assets\/[^"]+)"/.exec(html)?.[1];
    need(entry && shell.includes(entry), "sw.js: the page's entry script is not in the shell list");
    need(!/https?:\/\//.test(sw), "sw.js names another host");
    need(sw.includes('url.pathname.startsWith("/api/")') && sw.includes('req.method !== "GET"') && sw.includes("url.origin !== self.location.origin"),
      "sw.js: the rules that keep data out of the store are missing");
  }
  return bad;
}

export function main(argv = process.argv.slice(2)) {
  const here = resolve(dirname(fileURLToPath(import.meta.url)), "..");
  const dist = resolve(argv.includes("--dist") ? argv[argv.indexOf("--dist") + 1] : join(here, "dist"));
  const bad = check(dist);
  if (!bad.length) { console.log("installable web app: the manifest, the icons and the service worker's list agree with the build\n"); return 0; }
  for (const b of bad) console.error(`installable web app: ${b}`);
  return 1;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) process.exit(main());
