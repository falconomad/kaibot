// The installability check's own rules (#481): a build as the real one passes, and each thing a phone needs fails it.
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { check } from "./pwa_check.mjs";
import { MANIFEST_PATH, brandHtml, manifest } from "../src/brand/assets";
import { SW_PATH, shellFiles, swSource } from "../src/pwa/sw";

const web = join(dirname(fileURLToPath(import.meta.url)), "..");

/** A build as vite writes it: the real index.html (its entry rewritten to a hashed file), the real manifest and
 *  worker text, the real icons and fonts from public/. `change` edits it before the check. */
function site(change = () => {}) {
  const dist = mkdtempSync(join(tmpdir(), "kb-pwa-"));
  cpSync(join(web, "public"), dist, { recursive: true });
  mkdirSync(join(dist, "assets"));
  const outputs = ["assets/index-AbC12345.js", "assets/index-ZyX98765.css", "assets/Health-Qq11Ww22.js", "assets/react-CozFcNv-.js", "assets/query-DIBvs1zQ.js"];
  for (const f of outputs) writeFileSync(join(dist, f), "x");
  const html = brandHtml(readFileSync(join(web, "index.html"), "utf8")).replace('src="/src/main.tsx"', 'crossorigin src="/assets/index-AbC12345.js"');
  const files = { "index.html": html, [MANIFEST_PATH]: manifest(), [SW_PATH]: swSource(shellFiles([...outputs, "index.html", MANIFEST_PATH])) };
  change(files, dist);
  for (const [name, body] of Object.entries(files)) if (body !== null) writeFileSync(join(dist, name), typeof body === "string" ? body : JSON.stringify(body));
  return dist;
}

describe("the installable web app, checked on the build", () => {
  it("a build as the real one passes", () => {
    expect(check(site())).toEqual([]);
  });

  it("no build at all is said", () => {
    expect(check(join(tmpdir(), "kb-pwa-nothing-here"))).toEqual(["no build: index.html is missing"]);
  });

  it.each([
    ["display back to a browser tab", (f) => { f[MANIFEST_PATH].display = "browser"; }, /display is "browser"/],
    ["a start_url off the root", (f) => { f[MANIFEST_PATH].start_url = "/positions"; }, /site's root/],
    ["no 192 px icon", (f) => { f[MANIFEST_PATH].icons = f[MANIFEST_PATH].icons.filter((i) => i.sizes !== "192x192"); }, /no 192x192 PNG icon/],
    ["no maskable icon", (f) => { f[MANIFEST_PATH].icons = f[MANIFEST_PATH].icons.filter((i) => i.purpose !== "maskable"); }, /no maskable/],
    ["an icon that is not in the build", (_f, dist) => { rmSync(join(dist, "icons/icon-512.png")); }, /icon \/icons\/icon-512\.png is not in the build/],
    ["no colours", (f) => { delete f[MANIFEST_PATH].theme_color; }, /background_color and theme_color/],
    ["a manifest that is not JSON", (f) => { f[MANIFEST_PATH] = "{"; }, /not JSON/],
    ["index.html without the manifest link", (f) => { f["index.html"] = f["index.html"].replace(/<link rel="manifest"[^>]*>/, ""); }, /does not link a manifest/],
    ["index.html without the iPhone's icon", (f) => { f["index.html"] = f["index.html"].replace(/<link rel="apple-touch-icon"[^>]*>/, ""); }, /apple-touch-icon/],
    ["index.html without the own-window tag", (f) => { f["index.html"] = f["index.html"].replace(/<meta name="apple-mobile-web-app-capable"[^>]*>/, ""); }, /apple-mobile-web-app-capable/],
    ["no service worker", (f) => { f[SW_PATH] = null; }, /sw\.js is missing/],
    ["a worker that lists a file the build does not have", (f) => { f[SW_PATH] = f[SW_PATH].replace("Health-Qq11Ww22", "Health-Gone0000"); }, /Health-Gone0000\.js is not in the build/],
    ["a worker that lists the data API", (f) => { f[SW_PATH] = f[SW_PATH].replace('"/assets/Health-Qq11Ww22.js"', '"/api/marks"'); }, /\/api\/marks is not a file of the app shell/],
    ["a worker without the page's entry script", (f) => { f[SW_PATH] = f[SW_PATH].replace('"/assets/index-AbC12345.js",', ""); }, /entry script is not in the shell list/],
    ["a worker that names another host", (f) => { f[SW_PATH] += '\nfetch("https://abc.supabase.co/rest/v1/");'; }, /names another host/],
    ["a worker that lost the /api/ rule", (f) => { f[SW_PATH] = f[SW_PATH].replace(' || url.pathname.startsWith("/api/")', ""); }, /keep data out of the store/],
  ])("%s fails", (_name, change, message) => {
    expect(check(site(change)).join("\n")).toMatch(message);
  });
});
