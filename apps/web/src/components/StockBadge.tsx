import { useEffect, useState, type CSSProperties, type ReactNode, type SyntheticEvent } from "react";
import meta from "../data/symbols.json";
import tiles from "../data/logo_colors.json";
import { TICKER, TICKERLOGOS, cachedTile, lookupDomain, peekDomain, sampleAndStore, tickerLogo } from "../lib/logos";

type Meta = Record<string, { name: string; logo: string | null; industry: string | null; domain?: string | null }>;
const META = meta as Meta;
/** Per logo, sampled at build time by scripts/logo_colors.py from one image, whose URL is `from`: the tile's colour,
 *  the logo's ink, its padding in percent (0 = full-bleed). `sampled: false`: not sampled. A colour is only ever used for
 *  the image it was sampled from. */
export type TileColours = { bg: string | null; fg?: string; pad: number; sampled: boolean; unreadable?: boolean; from?: string };
/** Not sampled yet: a soft white tile, as most logos are drawn on. */
const PENDING = { bg: "var(--grey-50)", pad: 12 };
export const TILES = tiles as Record<string, TileColours>;
export const TINTS = ["#1E3A5F", "#3A2E1A", "#1F3A2C", "#3B1E2A", "#2A2540", "#1C3540", "#3A2A22", "#26323F", "#2F2440", "#3A2228"];

export const companyName = (s: string) => META[s]?.name ?? s;

/** Financial Modeling Prep's public logo images, no key (stakeholder, 18 Nov 2026): the third source. */
export const fmpLogo = (s: string) => `https://financialmodelingprep.com/image-stock/${encodeURIComponent(s.toUpperCase())}.png`;
/** The sources, in order (stakeholder, 18 Nov 2026): AllInvestView's tickerlogos for the company's domain, our list's
 *  Finnhub logo (unless it was found unreadable), FMP's; the initials when none loads. */
export function logoSources(symbol: string, domain: string | null): string[] {
  const own = META[symbol]?.logo;
  const unreadable = TILES[symbol]?.unreadable;
  const ticker = TICKER.test(symbol);
  return [ticker && domain ? tickerLogo(domain) : null, own && !unreadable ? own : null, ticker ? fmpLogo(symbol) : null]
    .filter((x): x is string => !!x);
}

/** The symbol's domain: known at once for our list and for anything looked up before; otherwise `undefined` while
 *  /api/logo-domain is asked (at most 6 at once per page, lib/logos.ts), then the answer. */
function useLogoDomain(symbol: string) {
  const [, rerender] = useState(0);
  const hit = peekDomain(symbol);
  useEffect(() => {
    if (hit) return;
    let live = true;
    void lookupDomain(symbol).then(() => { if (live) rerender((n) => n + 1); });
    return () => { live = false; };
  }, [symbol, !!hit]);
  return hit;
}

/** Sample a loaded tickerlogos image off the render path (when the browser is idle), then redraw the tile. */
function scheduleSample(domain: string, img: HTMLImageElement, done: () => void) {
  const run = () => { if (sampleAndStore(domain, img)) done(); };
  const idle = (globalThis as { requestIdleCallback?: (cb: () => void, o?: { timeout: number }) => number }).requestIdleCallback;
  if (idle) idle(run, { timeout: 1000 }); else setTimeout(run, 0);
}

function tint(s: string) {
  let h = 0;
  for (const ch of s) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return TINTS[h % TINTS.length];
}

/** A company tile: an iOS-style squircle (app.css .badge) with the company's logo on its own colour, or its initials.
 *  The tile is laid out at its size before the logo arrives, and the logo fades in when it has loaded. */
/** Every tile is drawn 8% larger than the size it is given (stakeholder, 17 Nov 2026: "increase logo sizes in all
 *  screens up by 5-10%"), so call sites keep their sizes and the scale lives here. */
export const TILE_SCALE = 1.08;
const scaled = (n: number) => Math.round(n * TILE_SCALE);

/** For screen readers (#327) a tile is decoration: it sits beside its ticker, so it is hidden and the ticker is read once,
 *  from the text. `label`: the tile stands alone as the only label (no ticker in text beside it), so it is an image
 *  named by its ticker. */
export function StockBadge({ symbol, size: base = 40, label = false }: { symbol: string; size?: number; label?: boolean }) {
  const size = scaled(base);
  const sr = label ? { role: "img" as const, "aria-label": symbol } : { "aria-hidden": true as const };
  const found = useLogoDomain(symbol);
  const [failed, setFailed] = useState<string[]>([]);  // sources that did not load, so the next one is tried
  const [loaded, setLoaded] = useState(false);
  const [, resampled] = useState(0);
  const box = { width: size, height: size, flex: "none" as const };
  // Still looking the domain up: the soft white tile, laid out at its size, and no image yet (no flash of another logo).
  if (!found)
    return <span className="badge logo" style={{ ...box, "--tile-bg": PENDING.bg, "--tile-pad": `${PENDING.pad}%` } as CSSProperties} {...sr} />;
  const domain = found.domain;
  const src = logoSources(symbol, domain).find((s) => !failed.includes(s));
  const cdn = !!src && !!domain && src.startsWith(TICKERLOGOS);
  const t = TILES[symbol];
  // A precomputed colour only for the image it was sampled from; the tickerlogos image otherwise takes the browser's
  // sample for its domain once there is one; anything else (FMP, not sampled yet) sits on the soft white tile.
  const own = src && t?.sampled && t.bg && t.from === src ? { bg: t.bg, pad: t.pad } : null;
  const tile = own ?? (cdn ? cachedTile(domain) : undefined) ?? PENDING;
  if (src)
    return (
      <span className="badge logo" style={{ ...box, "--tile-bg": tile.bg, "--tile-pad": `${tile.pad}%` } as CSSProperties} {...sr}>
        <img key={src} src={src} alt="" width={size} height={size} loading="lazy" decoding="async" referrerPolicy="no-referrer"
          crossOrigin={cdn ? "anonymous" : undefined} className={loaded ? "loaded" : undefined}
          onLoad={(e: SyntheticEvent<HTMLImageElement>) => { setLoaded(true); if (cdn && !own && !cachedTile(domain)) scheduleSample(domain!, e.currentTarget, () => resampled((n) => n + 1)); }}
          onError={() => { setLoaded(false); setFailed((f) => [...f, src]); }} />
      </span>
    );
  // The initials fit inside the tile's curve (about 84% of its width, a bold capital about 0.72 of the size wide), at the
  // 12 px minimum: four letters where they fit, else three.
  const letters = symbol.replace(/\W/g, "");
  const fit = (n: number) => Math.min(Math.round(size * 0.3), Math.floor((size * 0.84) / (n * 0.72)));
  const n = Math.min(letters.length, fit(4) >= 12 ? 4 : 3);
  return (
    <span className="badge" style={{ ...box, background: tint(symbol), fontSize: Math.max(12, fit(n)) }} {...sr}>
      {label ? <span aria-hidden="true">{letters.slice(0, n)}</span> : letters.slice(0, n)}
    </span>
  );
}

/** The same tile for a row with no company (a halt, a note): a neutral squircle with a small icon, so rows line up. */
export function SystemTile({ size: base = 36, tone, children }: { size?: number; tone?: string; children: ReactNode }) {
  const size = scaled(base);
  return <span className={`badge sys${tone ? ` ${tone}` : ""}`} style={{ width: size, height: size, flex: "none" }} aria-hidden="true">{children}</span>;
}

/** Logo + ticker + company name, for tables and lists. */
export function StockCell({ symbol, sub }: { symbol: string; sub?: React.ReactNode }) {
  return (
    <span className="stockcell">
      <StockBadge symbol={symbol} size={36} />
      <span className="stockcell-text">
        <b>{symbol}</b>
        <span className="muted">{sub ?? companyName(symbol)}</span>
      </span>
    </span>
  );
}
