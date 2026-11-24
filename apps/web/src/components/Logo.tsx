// The logo, drawn in one place: Design's "Rising k" (#255; picked by the stakeholder, 16 Nov 2026). The header, sign-in
// and New run show the wordmark alone, as Design drew it: the k's blue arm is the pot's line and the blue point sits on
// the i. The paths come straight from Design's files in src/brand/logo/ (read by brand/mark.ts); the favicon and app
// icons are drawn from the files beside them (`npm run icons`). A new logo is a swap of those files.
//
// Motion (docs/design/logo, storyboard): once per app load the stem rises, the leg sets, the arm draws and the point
// appears with one soft pulse, in under a second; then the mark is still. Only position and opacity move, nothing
// changes size around it, and with reduced motion the finished mark simply shows.
//
// Idle (stakeholder, 24 Nov 2026): once the intro has ended, the blue point now and then hops, a few px up with a soft,
// squashed landing in 600 ms. CSS only (app.css, kb-logo-hop): the class goes on the point when the intro ends, so its
// 6 s delay counts from there; the keyframes hold three uneven hops per cycle, so the beat never reads as a metronome.
// The point then is its own small <svg> over the letters (below), so the hop runs on the compositor.
// "often" (sign-in, /register): every 8.7-9.4 s; "rare" (the app's header): every 29-31 s. Paused while the tab is
// hidden; none with reduced motion. Only the point's transform moves.
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
/** The idle hop's cycle in seconds per kind: three hops at 0, 32.2% and 67% of it (app.css). */
export const IDLE = { often: 27, rare: 90, after: 6 } as const;
/** The sign-in and /register logo is this much larger than the header's (stakeholder, 24 Nov 2026; app.css .logo-lg). */
export const LARGE = 1.1;
/** The storyboard's timing in seconds: [starts at, lasts]. The pulse ends at 0.96 s. */
export const TIMING = { stem: [0, 0.2], leg: [0.12, 0.24], arm: [0.26, 0.3], point: [0.56, 0.18], pulse: [0.66, 0.3] } as const;

// Once per app load: the first logo that asks for the motion plays it; route changes and data refreshes never do.
let played = false;
/** Tests only. */
export const resetLogoMotion = () => { played = false; };

/** `size`: the height of the letters in px, from the top of the k to the baseline (without it, the stylesheet sizes the
 *  logo: 26 px on phones, 28 px on wider screens). `symbolOnly`: the k alone, carrying its point. `on`: the background
 *  it stands on; the app is dark. `animate`: play the load motion if no logo has played it yet. `idle`: after it,
 *  the point hops now and then. `large`: 10% larger than the stylesheet's size (sign-in and /register). */
export function Logo({ size, symbolOnly = false, on = "dark", animate = false, idle, large = false }:
  { size?: number; symbolOnly?: boolean; on?: "dark" | "light"; animate?: boolean; idle?: "often" | "rare"; large?: boolean }) {
  const kind = symbolOnly ? "symbol" : "word";
  const m = mark(kind, on), role = ROLES[kind];
  const id = useId().replace(/[^a-zA-Z0-9]/g, "");
  const [moving, setMoving] = useState(() => animate && !played && !reducedMotion());
  useEffect(() => { if (moving) played = true; }, [moving]);
  const hops = !!idle && !moving && !reducedMotion();
  // Paused, not restarted, while the tab is hidden: a listener, no timer.
  const [hidden, setHidden] = useState(false);
  useEffect(() => {
    if (!hops) return;
    const seen = () => setHidden(document.hidden);
    seen();
    document.addEventListener("visibilitychange", seen);
    return () => document.removeEventListener("visibilitychange", seen);
  }, [hops]);
  const [x, y, w, h] = m.box;
  const style: CSSProperties = { aspectRatio: `${w} / ${h}`, ...(size ? { height: size } : null) };
  const time = (k: keyof typeof TIMING): CSSProperties => ({ animationDelay: `${TIMING[k][0]}s`, animationDuration: `${TIMING[k][1]}s` });
  const dot = circle(m.parts[role.point].d);
  const pt = m.parts[role.point];
  const letters = (
    <svg className={`logo${symbolOnly ? "" : " logo-word"}${large ? " logo-lg" : ""}${moving ? " logo-moving" : ""}`} viewBox={`${x} ${y} ${w} ${h}`} role="img" aria-label={BRAND} style={style}>
      <g transform={`translate(${m.shift[0]} ${m.shift[1]})`}>
        {moving && <defs>{(["stem", "leg", "arm"] as const).map((k) => <clipPath key={k} id={`${id}${k}`}><path d={m.parts[role[k]].d} /></clipPath>)}</defs>}
        {m.parts.map((p, i) => {
          const path = (extra?: { className: string; style: CSSProperties }) =>
            <path key={i} fill={p.fill} fillRule={p.evenodd ? "evenodd" : undefined} d={p.d} {...extra} />;
          if (!moving) return hops && dot && i === role.point ? null : path();
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
  if (!hops || !dot) return letters;
  // The hopping point is its own small <svg> laid over the letters' point, not a <path> inside them: Chromium runs a
  // transform animation on an SVG child on the main thread (style, layout and a repaint of the whole logo every frame),
  // but on an HTML-level box it runs on the compositor and repaints nothing. Same place, size and colour as Design's.
  const [cx, cy] = [dot.c[0] + m.shift[0], dot.c[1] + m.shift[1]], r = dot.r, pc = (v: number, of: number) => `${round((v / of) * 100)}%`;
  const lift = symbolOnly ? 0 : 8; // .logo-word is lifted 8% of its height (app.css); the point goes with it
  return (
    <span className="logo-box">
      {letters}
      <svg className={`logo-hop${idle === "rare" ? " rare" : ""}`} viewBox={`${cx - r} ${cy - r} ${2 * r} ${2 * r}`} aria-hidden="true"
        style={{ left: pc(cx - r - x, w), top: `${round(((cy - r - y) / h) * 100 - lift)}%`, width: pc(2 * r, w), height: pc(2 * r, h),
          ...(hidden ? { animationPlayState: "paused" } : null) }}>
        <path fill={pt.fill} d={pt.d} transform={`translate(${m.shift[0]} ${m.shift[1]})`} />
      </svg>
    </span>
  );
}

const round = (v: number) => Math.round(v * 100) / 100;
