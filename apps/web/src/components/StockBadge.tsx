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

function tint(s: string) {
  let h = 0;
  for (const ch of s) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return TINTS[h % TINTS.length];
}

/** A company tile: an iOS-style squircle (app.css .badge) with the company's logo on its own colour, or its initials.
 *  The tile is laid out at its size before the logo arrives, and the logo fades in when it has loaded. */
export function StockBadge({ symbol, size = 40 }: { symbol: string; size?: number }) {
  const [failed, setFailed] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const logo = META[symbol]?.logo;
  const t = TILES[symbol];
  const tile = t?.sampled && t.bg ? { bg: t.bg, pad: t.pad } : t?.unreadable ? null : PENDING;
  const box = { width: size, height: size, flex: "none" as const };
  if (logo && tile && !failed)
    return (
      <span className="badge logo" style={{ ...box, "--tile-bg": tile.bg, "--tile-pad": `${tile.pad}%` } as CSSProperties}>
        <img src={logo} alt="" width={size} height={size} loading="lazy" decoding="async" referrerPolicy="no-referrer" className={loaded ? "loaded" : undefined}
          onLoad={() => setLoaded(true)} onError={() => setFailed(true)} />
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
export function SystemTile({ size = 36, tone, children }: { size?: number; tone?: string; children: ReactNode }) {
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
