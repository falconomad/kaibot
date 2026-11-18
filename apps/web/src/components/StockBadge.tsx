import { useState, type CSSProperties, type ReactNode } from "react";
import meta from "../data/symbols.json";
import tiles from "../data/logo_colors.json";

type Meta = Record<string, { name: string; logo: string | null; industry: string | null }>;
const META = meta as Meta;
/** Per logo, sampled at build time by scripts/logo_colors.py from the logo itself (never in the browser): the tile's
 *  colour, the logo's ink, its padding in percent (0 = full-bleed). `sampled: false`: the initials tile. */
export type TileColours = { bg: string | null; fg?: string; pad: number; sampled: boolean; unreadable?: boolean };
/** Not sampled yet (the script needs network access to the logo host): a soft white tile, as most logos are drawn on. */
const PENDING = { bg: "var(--grey-50)", pad: 12 };
export const TILES = tiles as Record<string, TileColours>;
const TINTS = ["#1E3A5F", "#3A2E1A", "#1F3A2C", "#3B1E2A", "#2A2540", "#1C3540", "#3A2A22", "#26323F", "#2F2440", "#3A2228"];

export const companyName = (s: string) => META[s]?.name ?? s;

/** A second logo source for every ticker (stakeholder, 18 Nov 2026): Financial Modeling Prep's public logo images, no
 *  key. Used when our list has no logo (custom-list tickers such as NBIS, the funds) or its logo fails to load. */
export const fmpLogo = (s: string) => `https://financialmodelingprep.com/image-stock/${encodeURIComponent(s.toUpperCase())}.png`;
export function logoSources(symbol: string): string[] {
  const own = META[symbol]?.logo;
  const unreadable = TILES[symbol]?.unreadable;
  const fmp = /^[A-Z]{1,5}([.][A-Z])?$/.test(symbol) ? fmpLogo(symbol) : null;
  return [own && !unreadable ? own : null, fmp].filter((x): x is string => !!x);
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

export function StockBadge({ symbol, size: base = 40 }: { symbol: string; size?: number }) {
  const size = scaled(base);
  const [step, setStep] = useState(0);  // which source is showing: our list's logo, then FMP's, then the initials
  const [loaded, setLoaded] = useState(false);
  const sources = logoSources(symbol);
  const src = sources[step];
  const t = TILES[symbol];
  // A sampled colour belongs to our own logo; FMP's logo, or one not sampled yet, sits on the soft white tile.
  const tile = src && src === META[symbol]?.logo && t?.sampled && t.bg ? { bg: t.bg, pad: t.pad } : PENDING;
  const box = { width: size, height: size, flex: "none" as const };
  if (src)
    return (
      <span className="badge logo" style={{ ...box, "--tile-bg": tile.bg, "--tile-pad": `${tile.pad}%` } as CSSProperties}>
        <img key={src} src={src} alt="" width={size} height={size} loading="lazy" decoding="async" referrerPolicy="no-referrer" className={loaded ? "loaded" : undefined}
          onLoad={() => setLoaded(true)} onError={() => { setLoaded(false); setStep((n) => n + 1); }} />
      </span>
    );
  // The initials fit inside the tile's curve (about 84% of its width, a bold capital about 0.72 of the size wide), at the
  // 12 px minimum: four letters where they fit, else three.
  const letters = symbol.replace(/\W/g, "");
  const fit = (n: number) => Math.min(Math.round(size * 0.3), Math.floor((size * 0.84) / (n * 0.72)));
  const n = Math.min(letters.length, fit(4) >= 12 ? 4 : 3);
  return (
    <span className="badge" style={{ ...box, background: tint(symbol), fontSize: Math.max(12, fit(n)) }} aria-hidden="true">
      {letters.slice(0, n)}
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
