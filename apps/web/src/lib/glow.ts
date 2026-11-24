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

/** Put the glow at `anchor`. The first place shown sets it without motion (or with sign-in's entrance, `arrive`, see
 *  primeGlowArrival); leaving sign-in is the long drift. */
export function setGlow(anchor: Anchor, doc: Document | undefined = typeof document === "undefined" ? undefined : document,
  opts: { arrive?: boolean } = {}) {
  const b = doc?.body;
  if (!b) return;
  const was = b.dataset.glow as Anchor | undefined;
  if (was === anchor) return;
  b.dataset.glowMove = !was ? (opts.arrive ? "arrive" : "none") : was === "signin" ? "long" : "short";
  const [x, y] = ANCHORS[anchor];
  b.style.setProperty("--glow-x", `${x}vw`);
  b.style.setProperty("--glow-y", `${y}vh`);
  b.dataset.glow = anchor;
}

export function useGlow(anchor: Anchor) {
  useEffect(() => { setGlow(anchor); }, [anchor]);
}

/** The emailed sign-in link opens a fresh page, so sign-in and Home are never on screen in the same page load. The
 *  link returns to `/?from=signin` (SignIn.tsx): on that load the glow starts at sign-in's place and drifts to Home
 *  (the long move), and the marker leaves the address bar. Call once, before the first render. */
export const FROM_SIGNIN = "from=signin";
export function primeGlowFromUrl(loc: Pick<Location, "search" | "pathname" | "hash"> | undefined = typeof location === "undefined" ? undefined : location,
  doc: Document | undefined = typeof document === "undefined" ? undefined : document) {
  if (!loc || !doc || !new URLSearchParams(loc.search).has("from")) return false;
  if (new URLSearchParams(loc.search).get("from") !== "signin") return false;
  setGlow("signin", doc);
  const rest = new URLSearchParams(loc.search); rest.delete("from");
  const q = rest.toString();
  history.replaceState(history.state, "", `${loc.pathname}${q ? `?${q}` : ""}${loc.hash}`);
  return true;
}

/** Sign-in's entrance (stakeholder, 24 Nov 2026), the mirror of the sign-out drift. Signing out moves the light from the
 *  page's anchor (Home: top-right) down-left into sign-in's corner: setGlow's "short" move, 600 ms on --ease, the
 *  `body::before` transition in app.css. On a first load of sign-in or /register the light sweeps the other way, up and
 *  to the right, from past the bottom-left corner into sign-in's place, on the same 600 ms and --ease: sign-in reads
 *  as arriving, sign-out as leaving (and the drift to Home after the link keeps going up-right). Only the fixed glow
 *  layer's transform moves (kb-glow-arrive); the form is never animated, so it takes focus and clicks from frame 1.
 *
 *  It is decided before the first render, so the light is never shown at rest and then pulled back: only when the URL
 *  is not a sign-in return or an auth error and no session is stored (the page will be sign-in or /register). It sets
 *  the glow once per page load: a re-render, a failed attempt or a new message never touches it (setGlow returns on
 *  the same place), and a later sign-out in the same load is the ordinary drift. Reduced motion: the global rule turns
 *  the animation off, and the glow stands still in sign-in's corner. */
export function primeGlowArrival(loc: Pick<Location, "search" | "pathname" | "hash"> | undefined = typeof location === "undefined" ? undefined : location,
  doc: Document | undefined = typeof document === "undefined" ? undefined : document,
  store: Pick<Storage, "length" | "key"> | undefined = typeof localStorage === "undefined" ? undefined : localStorage) {
  if (!loc || !doc?.body || doc.body.dataset.glow) return false;
  const q = new URLSearchParams(loc.search);
  if (q.has("from") || q.has("error") || (q.has("code") && loc.pathname !== "/register")) return false;
  if (/(access_token|error)=/.test(loc.hash)) return false;
  if (hasStoredSession(store)) return false;
  setGlow("signin", doc, { arrive: true });
  return true;
}

/** A session supabase-js keeps (`sb-<project ref>-auth-token`, lib/supa.ts). A store that throws counts as one, so
 *  nothing moves when we can't tell. */
function hasStoredSession(store: Pick<Storage, "length" | "key"> | undefined) {
  if (!store) return true;
  try {
    for (let i = 0; i < store.length; i++) if (/^sb-.+-auth-token$/.test(store.key(i) ?? "")) return true;
    return false;
  } catch { return true; }
}
