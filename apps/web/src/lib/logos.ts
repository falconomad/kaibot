// Company logos from AllInvestView's free ticker-logo CDN (stakeholder, 18 Nov 2026): https://cdn.tickerlogos.com/{domain},
// a PNG with transparency, CORS open, 404 when there is none. The domain is the company's website: from symbols.json for
// our own list, else from /api/logo-domain (api/logo-domain.ts), kept in memory and in localStorage.
// Attribution is required: a visible plain link to CREDIT_URL in the app shell (components/Shell.tsx, LogoCredit).
//
// Tile colour for any ticker: the tickerlogos image is drawn on a small canvas after it has loaded and read back with
// the rule of scripts/logo_colors.py, kept per domain in localStorage. Until then, or when it cannot be read, the soft
// white tile. Every storage call is wrapped: private mode, a full quota or storage turned off only cost the cache.
import meta from "../data/symbols.json";

export const TICKERLOGOS = "https://cdn.tickerlogos.com/";
export const CREDIT_URL = "https://www.allinvestview.com/tools/ticker-logos/";
export const TICKER = /^[A-Z]{1,5}([.][A-Z])?$/;
/** At most this many /api/logo-domain calls in flight from one page; the rest wait their turn. */
export const MAX_LOOKUPS = 6;
const DOMAINS_KEY = "kb.logo-domains.v1", TILES_KEY = "kb.logo-tiles.v1";
const DAY = 864e5, FOUND_TTL = 30 * DAY, MISS_TTL = DAY;
const HOST = /^(?=.{4,253}$)(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))*\.[a-z]{2,63}$/;

const KNOWN = meta as Record<string, { domain?: string | null }>;
export const isDomain = (d: unknown): d is string => typeof d === "string" && HOST.test(d);
export const tickerLogo = (domain: string) => TICKERLOGOS + domain;

// ------------------------------------------------------------------ storage (never throws)
function readStore<T>(key: string): Record<string, T> {
  try {
    const v = JSON.parse(globalThis.localStorage?.getItem(key) ?? "null");
    return v && typeof v === "object" && !Array.isArray(v) ? v : {};
  } catch { return {}; }
}
function writeStore(key: string, map: Record<string, unknown>) {
  try { globalThis.localStorage?.setItem(key, JSON.stringify(map)); } catch { /* the memory copy still works */ }
}

// ------------------------------------------------------------------ domains
type Saved = [string | null, number];
let saved: Record<string, Saved> | null = null;         // localStorage's copy, read once per page
const session = new Map<string, string | null>();         // answers of this page (errors included, not saved)
const inflight = new Map<string, Promise<string | null>>();
const queue: (() => void)[] = [];
let active = 0;

const savedMap = () => (saved ??= readStore<Saved>(DOMAINS_KEY));

/** What we already know about a symbol's domain, without asking: `undefined` means it must be looked up. */
export function peekDomain(symbol: string, now = Date.now()): { domain: string | null } | undefined {
  if (!TICKER.test(symbol)) return { domain: null };
  const own = KNOWN[symbol]?.domain;
  if (isDomain(own)) return { domain: own };
  if (session.has(symbol)) return { domain: session.get(symbol)! };
  const s = savedMap()[symbol];
  if (Array.isArray(s) && typeof s[1] === "number") {
    const [d, at] = s;
    if (d === null && now - at < MISS_TTL) return { domain: null };
    if (isDomain(d) && now - at < FOUND_TTL) return { domain: d };
  }
  return undefined;
}

function remember(symbol: string, domain: string | null, persist: boolean) {
  session.set(symbol, domain);
  if (!persist) return;
  const map = savedMap();
  map[symbol] = [domain, Date.now()];
  writeStore(DOMAINS_KEY, map);
}

function slot<T>(job: () => Promise<T>): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const run = () => {
      active++;
      job().then(resolve, reject).finally(() => { active--; queue.shift()?.(); });
    };
    if (active < MAX_LOOKUPS) run(); else queue.push(run);
  });
}

type Fetch = (url: string, init?: RequestInit) => Promise<Response>;

/** The symbol's company domain, asking /api/logo-domain when it is not known yet (one request per symbol per page). */
export function lookupDomain(symbol: string, fetchFn: Fetch = (u, i) => fetch(u, i)): Promise<string | null> {
  const hit = peekDomain(symbol);
  if (hit) return Promise.resolve(hit.domain);
  const running = inflight.get(symbol);
  if (running) return running;
  const p = slot(async () => {
    try {
      const timeout = typeof AbortSignal !== "undefined" && "timeout" in AbortSignal ? AbortSignal.timeout(8000) : undefined;
      const r = await fetchFn(`/api/logo-domain?symbol=${encodeURIComponent(symbol)}`, { signal: timeout });
      if (r.status === 404) { remember(symbol, null, true); return null; }
      const body = r.ok ? ((await r.json()) as { domain?: unknown }) : null;
      if (body && isDomain(body.domain)) { remember(symbol, body.domain, true); return body.domain; }
    } catch { /* offline, dev server, a bad answer: none for this page */ }
    remember(symbol, null, false);
    return null;
  }).finally(() => inflight.delete(symbol));
  inflight.set(symbol, p);
  return p;
}

// ------------------------------------------------------------------ tile colours (the rule of scripts/logo_colors.py)
export type Tile = { bg: string; fg?: string; pad: number };
const SOFT_WHITE = "#F4F5F7", DARK_TILE = "#1B1E23", PAD = 12, SIDE = 96;
type RGB = [number, number, number];
const hexc = (c: number[]) => "#" + c.slice(0, 3).map((v) => Math.round(v).toString(16).toUpperCase().padStart(2, "0")).join("");
const rgbOf = (h: string): RGB => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)) as RGB;
function lum(c: number[]) {
  const ch = (v: number) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2]);
}
function contrast(a: number[], b: number[]) {
  const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}
const mean = (px: number[][], i: number) => px.reduce((s, p) => s + p[i], 0) / px.length;
const pstdev = (px: number[][], i: number) => { const m = mean(px, i); return Math.sqrt(px.reduce((s, p) => s + (p[i] - m) ** 2, 0) / px.length); };

/** RGBA pixels (already at most 96 px a side) -> the tile: the dominant opaque edge colour when the logo has a solid
 *  background (white stays the soft white tile with padding, any other colour goes full-bleed), otherwise a neutral
 *  tile with the better contrast against the logo's ink. null for an empty image. */
export function tileFromPixels(data: ArrayLike<number>, w: number, h: number): Tile | null {
  const band = Math.max(2, Math.floor(w / 25));
  const edge: number[][] = [], ink: number[][] = [];
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const o = (y * w + x) * 4, p = [data[o], data[o + 1], data[o + 2], data[o + 3]];
    if (Math.min(x, y, w - 1 - x, h - 1 - y) < band) edge.push(p);
    if (p[3] > 200) ink.push(p);
  }
  const opaque = edge.filter((p) => p[3] > 240);
  if (edge.length && opaque.length > 0.9 * edge.length) {
    const m = [0, 1, 2].map((i) => mean(opaque, i));
    const spread = Math.max(...[0, 1, 2].map((i) => pstdev(opaque, i)));
    if (spread < 18) return lum(m) > 0.8 ? { bg: SOFT_WHITE, pad: PAD } : { bg: hexc(m), pad: 0 };
  }
  if (!ink.length) return null;
  const c = [0, 1, 2].map((i) => mean(ink, i));
  const bg = contrast(c, rgbOf(SOFT_WHITE)) >= contrast(c, rgbOf(DARK_TILE)) ? SOFT_WHITE : DARK_TILE;
  return { bg, fg: hexc(c), pad: PAD };
}

export interface CanvasLike {
  width: number; height: number;
  getContext(kind: "2d", opts?: { willReadFrequently?: boolean }): {
    drawImage(img: CanvasImageSource, x: number, y: number, w: number, h: number): void;
    getImageData(x: number, y: number, w: number, h: number): { data: ArrayLike<number> };
  } | null;
}
const newCanvas = (): CanvasLike => document.createElement("canvas") as unknown as CanvasLike;

/** Read a loaded image's tile on a small canvas. null when it cannot be read: a tainted canvas (no CORS), no canvas,
 *  an empty or broken image. Never throws. */
export function sampleImage(img: HTMLImageElement, makeCanvas: () => CanvasLike = newCanvas): Tile | null {
  try {
    const nw = img.naturalWidth, nh = img.naturalHeight;
    if (!nw || !nh) return null;
    const k = Math.min(1, SIDE / nw, SIDE / nh);
    const w = Math.max(1, Math.round(nw * k)), h = Math.max(1, Math.round(nh * k));
    const c = makeCanvas();
    c.width = w; c.height = h;
    const ctx = c.getContext("2d", { willReadFrequently: true });
    if (!ctx) return null;
    ctx.drawImage(img, 0, 0, w, h);
    return tileFromPixels(ctx.getImageData(0, 0, w, h).data, w, h);
  } catch { return null; }
}

let tiles: Record<string, Tile> | null = null;
const tileMap = () => (tiles ??= readStore<Tile>(TILES_KEY));
const isTile = (t: unknown): t is Tile => !!t && typeof t === "object" && /^#[0-9A-F]{6}$/.test((t as Tile).bg) && typeof (t as Tile).pad === "number";

/** The browser's sample for this domain's logo, if one was taken (on this or an earlier visit). */
export function cachedTile(domain: string | null | undefined): Tile | undefined {
  if (!domain) return undefined;
  const t = tileMap()[domain];
  return isTile(t) ? t : undefined;
}

/** Sample a loaded tickerlogos image and keep the answer for its domain. null (and nothing kept) when it cannot be read. */
export function sampleAndStore(domain: string, img: HTMLImageElement, makeCanvas?: () => CanvasLike): Tile | null {
  const t = sampleImage(img, makeCanvas);
  if (!t) return null;
  const map = tileMap();
  map[domain] = t;
  writeStore(TILES_KEY, map);
  return t;
}

/** Tests only: forget this page's caches (and, unless asked to keep it, what localStorage holds). */
export function _resetLogoCaches({ keepStorage = false } = {}) {
  saved = null; tiles = null; session.clear(); inflight.clear(); queue.length = 0; active = 0;
  if (!keepStorage) try { globalThis.localStorage?.removeItem(DOMAINS_KEY); globalThis.localStorage?.removeItem(TILES_KEY); } catch { /* none */ }
}
