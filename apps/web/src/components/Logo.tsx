// The logo, drawn in one place: Design's "Rising k" (#255; picked by the stakeholder, 16 Nov 2026). The header, sign-in
// and New run show the wordmark alone, as Design drew it: the k's blue arm is the pot's line and the blue point sits on
// the i. The paths come straight from Design's files in src/brand/logo/ (read by brand/mark.ts); the favicon and app
// icons are drawn from the files beside them (`npm run icons`). A new logo is a swap of those files.
//
// Motion (docs/design/logo, storyboard): once per app load the stem rises, the leg sets, the arm draws and the point
// appears with one soft pulse, in under a second; then the mark is still. Only position and opacity move, nothing
// changes size around it, and with reduced motion the finished mark simply shows.
import { useEffect, useId, useState, type CSSProperties } from "react";
import wordDark from "../brand/logo/wordmark-on-dark.svg?raw";
import wordLight from "../brand/logo/wordmark-on-light.svg?raw";
import symbolDark from "../brand/logo/symbol-on-dark.svg?raw";
import symbolLight from "../brand/logo/symbol-on-light.svg?raw";
import { BRAND } from "../brand/brand";
import { circle, drawnFrom, parseMark, type Mark } from "../brand/mark";
import { reducedMotion } from "../lib/a11y";

const FILES = { word: { dark: wordDark, light: wordLight }, symbol: { dark: symbolDark, light: symbolLight } };
const cache = new Map<string, Mark>();
export function mark(kind: "word" | "symbol", on: "dark" | "light"): Mark {
  const key = `${kind}-${on}`;
  if (!cache.has(key)) cache.set(key, parseMark(FILES[kind][on]));
  return cache.get(key)!;
}

/** Which path is which, by its place in Design's file (brand.test.tsx checks the files still have this shape). */
export const ROLES = { word: { arm: 0, leg: 1, stem: 2, point: 6 }, symbol: { arm: 0, point: 1, leg: 2, stem: 3 } } as const;
/** The storyboard's timing in seconds: [starts at, lasts]. The pulse ends at 0.96 s. */
export const TIMING = { stem: [0, 0.2], leg: [0.12, 0.24], arm: [0.26, 0.3], point: [0.56, 0.18], pulse: [0.66, 0.3] } as const;

// Once per app load: the first logo that asks for the motion plays it; route changes and data refreshes never do.
let played = false;
/** Tests only. */
export const resetLogoMotion = () => { played = false; };

/** `size`: the height of the letters in px, from the top of the k to the baseline (without it, the stylesheet sizes the
 *  logo: 26 px on phones, 28 px on wider screens). `symbolOnly`: the k alone, carrying its point. `on`: the background
 *  it stands on; the app is dark. `animate`: play the load motion if no logo has played it yet. */
export function Logo({ size, symbolOnly = false, on = "dark", animate = false }: { size?: number; symbolOnly?: boolean; on?: "dark" | "light"; animate?: boolean }) {
  const kind = symbolOnly ? "symbol" : "word";
  const m = mark(kind, on), role = ROLES[kind];
  const id = useId().replace(/[^a-zA-Z0-9]/g, "");
  const [moving, setMoving] = useState(() => animate && !played && !reducedMotion());
  useEffect(() => { if (moving) played = true; }, [moving]);
  const [x, y, w, h] = m.box;
  const style: CSSProperties = { aspectRatio: `${w} / ${h}`, ...(size ? { height: size } : null) };
  const time = (k: keyof typeof TIMING): CSSProperties => ({ animationDelay: `${TIMING[k][0]}s`, animationDuration: `${TIMING[k][1]}s` });
  const dot = circle(m.parts[role.point].d);
  return (
    <svg className={`logo${symbolOnly ? "" : " logo-word"}${moving ? " logo-moving" : ""}`} viewBox={`${x} ${y} ${w} ${h}`} role="img" aria-label={BRAND} style={style}>
      <g transform={`translate(${m.shift[0]} ${m.shift[1]})`}>
        {moving && <defs>{(["stem", "leg", "arm"] as const).map((k) => <clipPath key={k} id={`${id}${k}`}><path d={m.parts[role[k]].d} /></clipPath>)}</defs>}
        {m.parts.map((p, i) => {
          const path = (extra?: { className: string; style: CSSProperties }) =>
            <path key={i} fill={p.fill} fillRule={p.evenodd ? "evenodd" : undefined} d={p.d} {...extra} />;
          if (!moving) return path();
          const k = i === role.stem ? "stem" : i === role.leg ? "leg" : i === role.arm ? "arm" : null;
          if (k) {
            // The stroke slides out along its own line inside its own outline, so it is drawn with a round end.
            const [fx, fy] = drawnFrom(p.d, k === "stem" ? "bottom" : "left");
            return <g key={i} clipPath={`url(#${id}${k})`}>{path({ className: "logo-draw", style: { "--fx": `${fx}px`, "--fy": `${fy}px`, ...time(k) } as CSSProperties })}</g>;
          }
          if (i === role.point) return path({ className: "logo-pop", style: time("point") });
          return path();
        })}
        {moving && dot && <circle className="logo-pulse" cx={dot.c[0]} cy={dot.c[1]} r={dot.r} fill="none" stroke={m.parts[role.point].fill} strokeWidth={2.5}
          style={time("pulse")} onAnimationEnd={() => setMoving(false)} />}
      </g>
    </svg>
  );
}
