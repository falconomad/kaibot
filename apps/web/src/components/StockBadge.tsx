import { useState } from "react";
import meta from "../data/symbols.json";

type Meta = Record<string, { name: string; logo: string | null; industry: string | null }>;
const META = meta as Meta;
const TINTS = ["#1E3A5F", "#3A2E1A", "#1F3A2C", "#3B1E2A", "#2A2540", "#1C3540", "#3A2A22", "#26323F", "#2F2440", "#3A2228"];

export const companyName = (s: string) => META[s]?.name ?? s;

function tint(s: string) {
  let h = 0;
  for (const ch of s) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return TINTS[h % TINTS.length];
}

/** Round company logo (Finnhub, free tier) with an initials fallback for ETFs, missing logos or load errors. */
export function StockBadge({ symbol, size = 40 }: { symbol: string; size?: number }) {
  const [failed, setFailed] = useState(false);
  const logo = META[symbol]?.logo;
  const box = { width: size, height: size, flex: "none" as const };
  if (logo && !failed)
    return (
      <span className="badge logo" style={box}>
        <img src={logo} alt="" width={size} height={size} loading="lazy" decoding="async" referrerPolicy="no-referrer"
          onError={() => setFailed(true)} />
      </span>
    );
  return (
    <span className="badge" style={{ ...box, background: tint(symbol), fontSize: Math.round(size * 0.3) }} aria-hidden="true">
      {symbol.slice(0, 4)}
    </span>
  );
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
