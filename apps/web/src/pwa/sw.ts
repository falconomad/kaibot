// The service worker of the installable web app (M13 4.7, #481), written by the build (vite.config.ts `pwa`) as
// /sw.js. It keeps the APP SHELL only: the page, the app's own JavaScript and CSS, and the four font files. Nothing
// else is ever stored, and that is the point: a trading dashboard must never show a stored price, pot or position as
// if it were now. So:
// - only GET requests to this site are looked at; Supabase (the data API, sign-in, the price stream) and the logo CDN
//   are other hosts and pass straight through;
// - /api/* (live marks, stock summaries, the ticker check, the logo lookup) passes straight through;
// - a page navigation goes to the network first; the stored page answers only when the network does not (offline),
//   and then every data request fails and the pages say "You seem to be offline";
// - the app's hashed files under /assets/ and the shell list are answered from the store, and stored on first use
//   only when they really are JavaScript or CSS (the host answers a missing file with the page, which must not be
//   stored under a script's name).
// A new build has a new list, so a new store name; the old store is deleted when the new worker takes over.
// Used by vite.config.ts and the tests only: none of this ships in the app's JavaScript.

export const SW_PATH = "sw.js";
export const CACHE_PREFIX = "kb-shell-";
/** The fonts index.html preloads (public/fonts). */
export const SHELL_FONTS = [400, 500, 600, 700].map((w) => `/fonts/outfit-latin-${w}-normal.woff2`);

/** The shell's files from the build's output names: the page, every script and stylesheet of the app, the fonts. */
export function shellFiles(outputs: string[]): string[] {
  const assets = outputs.filter((f) => /^assets\/[^/]+\.(?:js|css)$/.test(f)).map((f) => `/${f}`).sort();
  return ["/", ...assets, ...SHELL_FONTS];
}

/** A short name for a list of files (their names carry their content's hash): a new build gets a new store. */
export function shellVersion(files: string[]): string {
  let h = 0x811c9dc5;
  for (const c of files.join("\n")) h = Math.imul(h ^ c.charCodeAt(0), 0x01000193) >>> 0;
  return h.toString(16).padStart(8, "0");
}

export function swSource(files: string[]): string {
  return `// The app shell only. Written by the build (apps/web/src/pwa/sw.ts); do not edit.
// Never stored: anything from another host (the data API, sign-in, prices), /api/*, anything that is not a GET.
const CACHE = ${JSON.stringify(CACHE_PREFIX + shellVersion(files))};
const SHELL = ${JSON.stringify(files)};
const KEEP = new Set(SHELL);
const ASSET = /^\\/assets\\/[^/]+\\.(?:js|css)$/;
const CODE = /javascript|text\\/css/;

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    // the page is fetched fresh; the other files carry their content's hash in their name (the fonts never change)
    await cache.addAll(SHELL.map((u) => (u === "/" ? new Request(u, { cache: "reload" }) : u)));
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    for (const k of await caches.keys()) if (k.startsWith(${JSON.stringify(CACHE_PREFIX)}) && k !== CACHE) await caches.delete(k);
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;
  if (req.mode === "navigate") {
    event.respondWith(fetch(req).catch(async () => (await (await caches.open(CACHE)).match("/")) || Response.error()));
    return;
  }
  if (!KEEP.has(url.pathname) && !ASSET.test(url.pathname)) return;
  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    const hit = await cache.match(url.pathname);
    if (hit) return hit;
    const res = await fetch(req);
    if (res.ok && res.type === "basic" && (KEEP.has(url.pathname) || CODE.test(res.headers.get("content-type") || ""))) {
      await cache.put(url.pathname, res.clone());
    }
    return res;
  })());
});
`;
}
