// The logo: the symbol and the wordmark, drawn in one place (#254). The header, sign-in and New run use it, and the
// favicon and the app icons are drawn from the same symbol file (`npm run icons`). So a new logo is one file to swap,
// src/brand/symbol.svg, and a new name is one line in src/brand/brand.ts.
// ?inline: the symbol travels inside the app's JavaScript, so the header never waits for a second request to draw it.
import symbol from "../brand/symbol.svg?inline";
import { BRAND } from "../brand/brand";

/** `size` is the symbol's height in px (the wordmark scales with it); `symbolOnly` leaves the wordmark out and gives the
 *  symbol the name as its text alternative. */
export function Logo({ size = 26, symbolOnly = false }: { size?: number; symbolOnly?: boolean }) {
  return (
    <span className="logo" style={size === 26 ? undefined : { fontSize: Math.round((size * 18) / 26) }}>
      {/* Next to the wordmark the symbol is decoration (empty alt); alone, it carries the name. */}
      <img className="logo-symbol" src={symbol} alt={symbolOnly ? BRAND : ""} width={size} height={size} style={{ width: size, height: size }} />
      {!symbolOnly && <span className="logo-word">{BRAND}</span>}
    </span>
  );
}
