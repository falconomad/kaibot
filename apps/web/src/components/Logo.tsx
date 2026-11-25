// The logo, drawn in one place: Design's "Rising k" (#255; picked by the stakeholder, 16 Nov 2026). The header, sign-in
// and New run show the wordmark alone, as Design drew it: the k's blue arm is the pot's line and the blue point sits on
// the i. The paths come straight from Design's files in src/brand/logo/ (read by brand/mark.ts); the favicon and app
// icons are drawn from the files beside them (`npm run icons`). A new logo is a swap of those files.
//
// Motion (docs/design/logo, storyboard): once per app load the stem rises, the leg sets, the arm draws and the point
// appears with one soft pulse, in under a second; then the mark is still. Only position and opacity move, nothing
// changes size around it, and with reduced motion the finished mark simply shows.
//
// Idle (stakeholder, 24 Nov 2026; 25 Nov 2026 13:21 and 16:38): once the intro has ended, the blue point hops (a few px
// up, a soft squashed landing, 600 ms) and, 2 s later, the k's blue arm replays its intro draw in short (it slides back
// into its lower end and draws out again along its own line, 500 ms); then the point again, and so on: one motion every
// 2 s, alternating, in one 4 s cycle, the same on sign-in, /register, the app's header and New run. CSS only (app.css,
// kb-logo-hop and kb-logo-arm share one animation-delay): the classes go on when the intro ends, so the 2 s delay counts
// from there. The point and the arm are then each their own <svg> layer (below), so they move on the compositor.
// Paused while the tab is hidden; none with reduced motion. Only their transforms move.
import { useEffect, useId, useState, type CSSProperties } from "react";
import wordDark from "../brand/logo/wordmark-on-dark.svg?raw";
import wordLight from "../brand/logo/wordmark-on-light.svg?raw";
import symbolDark from "../brand/logo/symbol-on-dark.svg?raw";
import symbolLight from "../brand/logo/symbol-on-light.svg?raw";
import { BRAND } from "../brand/brand";
import { capsule, circle, drawnFrom, parseMark, type Mark } from "../brand/mark";
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
/** The idle motion in seconds (app.css): one cycle, the arm's turn this far into it, and the delay after the intro. */
export const IDLE = { cycle: 4, offset: 2, after: 2 } as const;
/** The logo's height in px (app.css .logo and .logo-lg): the app's header, and sign-in and /register (`large`), on phones
 *  (760 px wide and less) and above (stakeholder, 25 Nov 2026: "still too small"; it was 26/28 and 32.5/35). */
export const SIZE = { header: { phone: 30, wide: 32 }, large: { phone: 40, wide: 44 } } as const;
/** The storyboard's timing in seconds: [starts at, lasts]. The pulse ends at 0.96 s. */
export const TIMING = { stem: [0, 0.2], leg: [0.12, 0.24], arm: [0.26, 0.3], point: [0.56, 0.18], pulse: [0.66, 0.3] } as const;

// Once per app load: the first logo that asks for the motion plays it; route changes and data refreshes never do.
let played = false;
/** Tests only. */
export const resetLogoMotion = () => { played = false; };

/** `size`: the height of the letters in px, from the top of the k to the baseline (without it, the stylesheet sizes the
 *  logo: SIZE.header). `symbolOnly`: the k alone, carrying its point. `on`: the background it stands on. Without it
 *  (`"theme"`) the logo follows the page's theme (#673): each shape carries the on-dark file's colour as its fill and
 *  `light-dark(on-light, on-dark)` over it, so the light theme (tokens.css `color-scheme: light`) draws Design's on-light
 *  file, with no extra request, no class and no stylesheet rule. Both files have the same paths (brand.test.tsx), so
 *  every layer keeps its place. A browser without `light-dark()` keeps the on-dark fill. `animate`: play the load
 *  motion if no logo has played it yet. `idle`: after it, the point hops and the arm redraws, in turn. `large`:
 *  SIZE.large (sign-in and /register). */
export function Logo({ size, symbolOnly = false, on = "theme", animate = false, idle, large = false }:
  { size?: number; symbolOnly?: boolean; on?: "dark" | "light" | "theme"; animate?: boolean; idle?: boolean; large?: boolean }) {
  const kind = symbolOnly ? "symbol" : "word";
  const m = mark(kind, on === "light" ? "light" : "dark"), role = ROLES[kind];
  const lit = on === "theme" ? mark(kind, "light") : null;
  /** A shape's colour: the file's, and with the theme, the on-light file's in the light theme. */
  const paint = (i: number, key: "fill" | "stroke" = "fill"): CSSProperties | undefined =>
    lit && lit.parts[i].fill !== m.parts[i].fill ? { [key]: `light-dark(${lit.parts[i].fill}, ${m.parts[i].fill})` } : undefined;
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
  const dot = circle(m.parts[role.point].d), arm = capsule(m.parts[role.arm].d);
  const split = hops && !!dot && !!arm;
  const letters = (
    <svg className={`logo${symbolOnly ? "" : " logo-word"}${large ? " logo-lg" : ""}${moving ? " logo-moving" : ""}`} viewBox={`${x} ${y} ${w} ${h}`} role="img" aria-label={BRAND} style={style}>
      <g transform={`translate(${m.shift[0]} ${m.shift[1]})`}>
        {moving && <defs>{(["stem", "leg", "arm"] as const).map((k) => <clipPath key={k} id={`${id}${k}`}><path d={m.parts[role[k]].d} /></clipPath>)}</defs>}
        {m.parts.map((p, i) => {
          const path = (extra?: { className: string; style: CSSProperties }) =>
            <path key={i} fill={p.fill} fillRule={p.evenodd ? "evenodd" : undefined} d={p.d} {...extra} style={paint(i) || extra ? { ...paint(i), ...extra?.style } : undefined} />;
          if (!moving) return split && (i === role.point || i === role.arm || i === role.leg) ? null : path();
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
          style={{ ...paint(role.point, "stroke"), ...time("pulse") }} onAnimationEnd={() => setMoving(false)} />}
      </g>
    </svg>
  );
  if (!split) return letters;
  // The point and the arm are each their own <svg> laid over their place in the letters, not a <path> inside them:
  // Chromium runs a transform animation on an SVG child on the main thread (style, layout and a repaint of the whole logo
  // every frame), but on an HTML-level box it runs on the compositor and repaints nothing. Same place, size and colour
  // as Design's. The arm goes under the white leg, which overlaps its lower end (as in the file), so the leg is drawn
  // over it in a still <svg> of its own, and the rest of the letters stay below both, off any layer. The point, the arm
  // and the leg all have the letters' own box, with their 8% lift in the viewBox rather than a transform: an <svg> is
  // snapped to whole pixels and a layer is not redrawn for a fraction of one, so their pixels at rest are the letters'
  // own. (A box the point's own size, placed in %, landed it up to 0.7 px off and cut its lower edge at some sizes.)
  const lift = symbolOnly ? 0 : 8; // .logo-word is lifted 8% of its height (app.css); the point and the arm go with it
  const vb = `${x} ${round(y + (lift / 100) * h)} ${w} ${h}`;
  const [sx, sy] = m.shift, [cx, cy] = [dot!.c[0] + sx, dot!.c[1] + sy], r = dot!.r;
  const ap = m.parts[role.arm], [fx, fy] = drawnFrom(ap.d, "left");
  // The clip, in % of the logo's box: everything ahead of the arm's lower end, and that end's round cap 0.75 px wider, so
  // the arm's own edge at rest is never cut (its pixels stay the file's). The leg's cap covers that end (same centre and
  // size), so the arm slides out of sight under it as the intro's draw did inside its outline; at most a sub-pixel rim
  // of it shows past the leg while it moves.
  const [p0, p1] = arm!.ends, [c0, e0] = fx < 0 ? [p0, p1] : [p1, p0], len = Math.hypot(e0[0] - c0[0], e0[1] - c0[1]);
  const [ux, uy] = [(e0[0] - c0[0]) / len, (e0[1] - c0[1]) / len], K = 400;
  const at = (a: number, b: number, px = 0) => {
    const [dx, dy] = px ? [ux * a - uy * b, uy * a + ux * b].map((v) => v / Math.hypot(a, b)) : [0, 0];
    const X = pc(c0[0] + sx + ux * a - uy * b - x, w), Y = `${round(((c0[1] + sy + uy * a + ux * b - y) / h) * 100 - lift)}%`;
    return px ? `calc(${X} + ${round(dx * px)}px) calc(${Y} + ${round(dy * px)}px)` : `${X} ${Y}`;
  };
  const clip = [at(K, K), at(K, -K), at(0, -K), ...Array.from({ length: 19 }, (_, i) => {
    const t = ((i - 9) * Math.PI) / 18; return at(-arm!.r * Math.cos(t), arm!.r * Math.sin(t), 0.75);
  }), at(0, K)];
  const shape = (i: number) => <path fill={m.parts[i].fill} d={m.parts[i].d} transform={`translate(${sx} ${sy})`} style={paint(i)} />;
  const paused = hidden ? { animationPlayState: "paused" } : null;
  return (
    <span className="logo-box">
      {letters}
      <span className="logo-clip" style={{ clipPath: `polygon(${clip.join(",")})` }}>
        <svg className="logo-arm" viewBox={vb} aria-hidden="true"
          style={{ "--fx": pc(fx, w), "--fy": pc(fy, h), ...paused } as CSSProperties}>
          {shape(role.arm)}
        </svg>
      </span>
      <svg className="logo-leg" viewBox={vb} aria-hidden="true">{shape(role.leg)}</svg>
      {/* the hop rises 60% of the point's height (--up, in % of this box) and squashes about the point's foot */}
      <svg className="logo-hop" viewBox={vb} aria-hidden="true"
        style={{ "--up": `${round(-60 * ((2 * r) / h))}%`, transformOrigin: `${pc(cx - x, w)} ${round(((cy + r - y) / h) * 100 - lift)}%`, ...paused } as CSSProperties}>
        {shape(role.point)}
      </svg>
    </span>
  );
}

const pc = (v: number, of: number) => `${round((v / of) * 100)}%`;
const round = (v: number) => Math.round(v * 100) / 100;
