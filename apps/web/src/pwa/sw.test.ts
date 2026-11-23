// M13 4.7 (#481): the service worker keeps the app shell and nothing else. The worker's text (as the build writes it)
// is run here against a stand-in for the browser's worker scope, so each rule can be seen: what is stored at install,
// what an old build's store becomes, and above all what is NEVER answered from or put into the store: the data API,
// sign-in, prices, /api/*, anything that is not a GET.
import { describe, expect, it } from "vitest";
import indexHtml from "../../index.html?raw";
import { CACHE_PREFIX, SHELL_FONTS, SW_PATH, shellFiles, shellVersion, swSource } from "./sw";

const ORIGIN = "https://kaibo.example";
const OUTPUTS = ["index.html", "manifest.webmanifest", "assets/index-AbC12345.js", "assets/index-ZyX98765.css", "assets/Health-Qq11Ww22.js",
  "assets/react-CozFcNv-.js", "assets/outfit-latin-400-normal-AAAA1111.woff2", "sw.js"];
const FILES = shellFiles(OUTPUTS);

type Res = { ok: boolean; type: string; status: number; body: string; headers: { get(k: string): string | null }; clone(): Res };
const res = (body: string, type = "text/javascript", status = 200): Res => {
  const r: Res = { ok: status < 400, type: "basic", status, body, headers: { get: (k) => (k.toLowerCase() === "content-type" ? type : null) }, clone: () => r };
  return r;
};

/** A worker scope with a network the test controls. `net(url)` answers a request, or throws when offline. */
function boot(src: string, net: (url: string) => Res, stores = new Map<string, Map<string, Res>>()) {
  const handlers: Record<string, (e: unknown) => void> = {};
  const asked: string[] = [];
  const flags = { skipped: false, claimed: false };
  const path = (k: unknown) => { const u = typeof k === "string" ? k : (k as { url: string }).url; return u.startsWith("/") ? u : new URL(u).pathname; };
  const fresh: string[] = [];
  const fetch = async (req: unknown) => {
    const u = typeof req === "string" ? req : (req as { url: string }).url;
    asked.push(u);
    if (typeof req !== "string" && (req as { cache?: string }).cache === "reload") fresh.push(u);
    return net(u);
  };
  const open = async (name: string) => {
    if (!stores.has(name)) stores.set(name, new Map());
    const m = stores.get(name)!;
    return {
      match: async (k: unknown) => m.get(path(k)),
      put: async (k: unknown, v: Res) => { m.set(path(k), v); },
      addAll: async (reqs: unknown[]) => { for (const r of reqs) { const got = await fetch(r); if (!got.ok) throw new Error(`precache ${path(r)}`); m.set(path(r), got); } },
    };
  };
  const caches = { open, keys: async () => [...stores.keys()], delete: async (k: string) => stores.delete(k) };
  const self = { location: { origin: ORIGIN }, addEventListener: (t: string, f: (e: unknown) => void) => { handlers[t] = f; },
    skipWaiting: async () => { flags.skipped = true; }, clients: { claim: async () => { flags.claimed = true; } } };
  class Req { url: string; cache?: string; constructor(u: string, o?: { cache?: string }) { this.url = u; this.cache = o?.cache; } }
  new Function("self", "caches", "fetch", "Request", "Response", "URL", src)(self, caches, fetch, Req, { error: () => res("", "", 0) }, URL);
  const lifecycle = async (t: "install" | "activate") => { let p: Promise<unknown> = Promise.resolve(); handlers[t]({ waitUntil: (x: Promise<unknown>) => { p = x; } }); await p; };
  /** Null: the worker left the request alone (the browser goes to the network as if there were no worker). */
  const get = async (url: string, o: { method?: string; mode?: string } = {}) => {
    let p: Promise<Res> | null = null;
    handlers.fetch({ request: { url: url.startsWith("/") ? ORIGIN + url : url, method: o.method ?? "GET", mode: o.mode ?? "cors" }, respondWith: (x: Promise<Res>) => { p = x; } });
    return p === null ? null : await (p as Promise<Res>);
  };
  return { lifecycle, get, asked, stores, flags, fresh };
}

const online = (url: string) => (new URL(url, ORIGIN).pathname === "/" ? res("<html>shell</html>", "text/html") : res(`code of ${new URL(url, ORIGIN).pathname}`));
const offline = () => { throw new TypeError("Failed to fetch"); };
const NAME = CACHE_PREFIX + shellVersion(FILES);

describe("the app shell's file list", () => {
  it("is the page, every script and stylesheet the build made, and the fonts index.html preloads; nothing else", () => {
    expect(FILES).toEqual(["/", "/assets/Health-Qq11Ww22.js", "/assets/index-AbC12345.js", "/assets/index-ZyX98765.css", "/assets/react-CozFcNv-.js", ...SHELL_FONTS]);
    const preloaded = [...indexHtml.matchAll(/<link rel="preload" href="([^"]+)" as="font"/g)].map((m) => m[1]);
    expect(SHELL_FONTS).toEqual(preloaded);
    expect(FILES.some((f) => /api|manifest|sw\.js|supabase/.test(f))).toBe(false);
    expect(SW_PATH).toBe("sw.js"); // at the site's root, so it may serve the whole site
  });

  it("a new build has a new store name; the same build the same one", () => {
    expect(shellVersion(FILES)).toMatch(/^[0-9a-f]{8}$/);
    expect(shellVersion(FILES)).toBe(shellVersion([...FILES]));
    expect(shellVersion(shellFiles([...OUTPUTS, "assets/More-Nn33Mm44.js"]))).not.toBe(shellVersion(FILES));
  });
});

describe("the service worker", () => {
  it("install stores the shell, the page fetched fresh; activate deletes an older build's store and nothing that is not the app's", async () => {
    const stores = new Map([[`${CACHE_PREFIX}old00000`, new Map([["/", res("old")]])], ["someone-elses", new Map<string, Res>()]]);
    const w = boot(swSource(FILES), online, stores);
    await w.lifecycle("install");
    expect([...w.stores.get(NAME)!.keys()]).toEqual(FILES);
    expect(w.fresh).toEqual(["/"]); // the page past the browser's own cache; hashed files may come from it
    expect(w.flags.skipped).toBe(true);
    await w.lifecycle("activate");
    expect([...w.stores.keys()].sort()).toEqual([NAME, "someone-elses"].sort());
    expect(w.flags.claimed).toBe(true);
  });

  it("a file of the shell that cannot be fetched fails the install (the old worker stays)", async () => {
    const w = boot(swSource(FILES), (u) => (u.includes("Health") ? res("", "text/html", 404) : online(u)));
    await expect(w.lifecycle("install")).rejects.toThrow(/precache/);
    expect(w.flags.skipped).toBe(false);
  });

  it("NEVER touches the data: another host, /api/*, and anything that is not a GET pass straight through", async () => {
    const w = boot(swSource(FILES), online);
    await w.lifecycle("install");
    const before = w.asked.length;
    for (const [url, o] of [
      ["https://abc.supabase.co/rest/v1/rpc/kb_overview", { method: "POST" }],
      ["https://abc.supabase.co/rest/v1/rpc/kb_health", {}],
      ["https://abc.supabase.co/auth/v1/token?grant_type=refresh_token", { method: "POST" }],
      ["https://abc.supabase.co/auth/v1/user", {}],
      ["https://cdn.tickerlogos.com/apple.com", {}],
      ["/api/marks", {}],
      ["/api/summary?symbol=AAPL", {}],
      ["/api/check-ticker", { method: "POST" }],
      ["/api/logo-domain?symbol=AAPL", {}],
      ["/assets/index-AbC12345.js", { method: "POST" }],
      ["/manifest.webmanifest?v=k2", {}],
      ["/icons/icon-192.png", {}],
      ["/favicon.ico?v=k1", {}],
    ] as [string, { method?: string }][]) expect(await w.get(url, o), url).toBeNull();
    expect(w.asked.length).toBe(before); // the worker made no request of its own for them
    expect([...w.stores.get(NAME)!.keys()]).toEqual(FILES); // and stored nothing new
  });

  it("a page opens from the network; offline, the stored page answers for any route, and the data still cannot", async () => {
    let net: (u: string) => Res = online;
    const w = boot(swSource(FILES), (u) => net(u));
    await w.lifecycle("install");
    net = (u) => res(`<html>fresh ${new URL(u).pathname}</html>`, "text/html");
    expect((await w.get("/positions", { mode: "navigate" }))!.body).toBe("<html>fresh /positions</html>");
    expect(w.stores.get(NAME)!.get("/")!.body).toBe("<html>shell</html>"); // a navigation is never stored
    expect(w.stores.get(NAME)!.has("/positions")).toBe(false);
    net = offline;
    expect((await w.get("/positions", { mode: "navigate" }))!.body).toBe("<html>shell</html>");
    expect((await w.get("/health", { mode: "navigate" }))!.body).toBe("<html>shell</html>");
    expect(await w.get("/api/marks")).toBeNull(); // the page's own request then fails: "You seem to be offline"
  });

  it("the app's files answer from the store; a page chunk of an older build is stored on first use", async () => {
    let net: (u: string) => Res = online;
    const w = boot(swSource(FILES), (u) => net(u));
    await w.lifecycle("install");
    net = offline;
    expect((await w.get("/assets/index-AbC12345.js"))!.body).toBe("code of /assets/index-AbC12345.js");
    expect((await w.get("/fonts/outfit-core-400-normal.woff2"))!.ok).toBe(true);
    net = online;
    expect((await w.get("/assets/Tape-Old00001.js"))!.body).toBe("code of /assets/Tape-Old00001.js");
    net = offline;
    expect((await w.get("/assets/Tape-Old00001.js"))!.body).toBe("code of /assets/Tape-Old00001.js");
  });

  it("the host's page in place of a missing script, or an error, is passed on and never stored", async () => {
    const w = boot(swSource(FILES), online);
    await w.lifecycle("install");
    const w2 = boot(swSource(FILES), () => res("<html>shell</html>", "text/html; charset=utf-8"), w.stores);
    expect((await w2.get("/assets/Gone-Xx00Yy11.js"))!.body).toBe("<html>shell</html>");
    expect(w.stores.get(NAME)!.has("/assets/Gone-Xx00Yy11.js")).toBe(false);
    const w3 = boot(swSource(FILES), () => res("nope", "text/javascript", 500), w.stores);
    expect((await w3.get("/assets/Err-Xx00Yy11.js"))!.status).toBe(500);
    expect(w.stores.get(NAME)!.has("/assets/Err-Xx00Yy11.js")).toBe(false);
  });

  it("the host never lets a browser keep an old worker: /sw.js is served with no-cache (vercel.json)", async () => {
    const vercel = (await import("../../vercel.json")).default as { headers: { source: string; headers: { key: string; value: string }[] }[] };
    expect(vercel.headers.find((h) => h.source === `/${SW_PATH}`)?.headers).toContainEqual({ key: "Cache-Control", value: "no-cache" });
  });

  it("the worker's text names no host and no key: it knows only this site's paths", () => {
    const src = swSource(FILES);
    expect(src).not.toMatch(/supabase|https?:\/\/|apikey|token/i);
    expect(src).toContain('url.pathname.startsWith("/api/")');
  });
});
