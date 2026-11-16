// Where the page's light sits (stakeholder, 16 Nov 2026). One glow behind every page (app.css, body::before); each
// place has its own anchor, and moving between places drifts the light: about 900 ms from sign-in to Home, about
// 600 ms between tabs. Only a transform of the fixed layer moves (no repaint). Scrolling and data refreshes never
// move it, and with reduced motion it jumps (the global rule turns transitions off).
import { useEffect } from "react";

/** The glow's centre, as an offset from the middle of the screen in viewport units. */
export const ANCHORS = {
  signin: [-50, 50],      // bottom-left, as sign-in always had it
  home: [50, -50],        // top-right
  positions: [36, -50],
  activity: [50, -34],
  engine: [24, -50],
  more: [50, -20],
  start: [50, -50],
} as const satisfies Record<string, readonly [number, number]>;
export type Anchor = keyof typeof ANCHORS;

export const anchorFor = (path: string): Anchor =>
  path === "/" ? "home" : path.startsWith("/positions") ? "positions" : path.startsWith("/activity") ? "activity"
    : path.startsWith("/health") || path.startsWith("/gate") ? "engine" : path.startsWith("/start") ? "start" : "more";

/** Put the glow at `anchor`. The first place shown sets it without motion; leaving sign-in is the long drift. */
export function setGlow(anchor: Anchor, doc: Document | undefined = typeof document === "undefined" ? undefined : document) {
  const b = doc?.body;
  if (!b) return;
  const was = b.dataset.glow as Anchor | undefined;
  if (was === anchor) return;
  b.dataset.glowMove = !was ? "none" : was === "signin" ? "long" : "short";
  const [x, y] = ANCHORS[anchor];
  b.style.setProperty("--glow-x", `${x}vw`);
  b.style.setProperty("--glow-y", `${y}vh`);
  b.dataset.glow = anchor;
}

export function useGlow(anchor: Anchor) {
  useEffect(() => { setGlow(anchor); }, [anchor]);
}
