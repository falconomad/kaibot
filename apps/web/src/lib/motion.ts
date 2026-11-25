// "Pause animations" (audit, WCAG 2.2.2 Pause, Stop, Hide): the logo's idle hop and arm loop for as long as the page is
// open, so Settings has a switch that stops them. Default: they play (stakeholder). The choice is kept in this
// browser's localStorage; a browser that refuses storage (private mode, blocked) keeps the default and nothing breaks.
// Reduced motion stops them too, whatever the switch says, and a change of either takes effect at once.
// Unit-tested in motion.dom.test.tsx.
import { useSyncExternalStore } from "react";

export const PAUSE_KEY = "kb-pause-animations";
const listeners = new Set<() => void>();
/** The switch as this page last read or set it (also when storage refuses it). */
let memo: boolean | null = null;

/** True when this browser was asked to pause the animations. */
export function animationsPaused(): boolean {
  try { return localStorage.getItem(PAUSE_KEY) === "1"; } catch { return false; }
}

export function setAnimationsPaused(paused: boolean): void {
  try { if (paused) localStorage.setItem(PAUSE_KEY, "1"); else localStorage.removeItem(PAUSE_KEY); } catch { /* kept for this page only */ }
  memo = paused;
  for (const f of [...listeners]) f();
}

const pausedNow = () => (memo ??= animationsPaused());
const reduceQuery = () => (typeof window !== "undefined" ? window.matchMedia?.("(prefers-reduced-motion: reduce)") : undefined);

function subscribe(f: () => void): () => void {
  listeners.add(f);
  const q = reduceQuery();
  q?.addEventListener?.("change", f);
  // another tab of this browser flips the switch
  const storage = (e: StorageEvent) => { if (e.key === PAUSE_KEY) { memo = null; f(); } };
  window.addEventListener("storage", storage);
  return () => { listeners.delete(f); q?.removeEventListener?.("change", f); window.removeEventListener("storage", storage); };
}

const still = () => pausedNow() || !!reduceQuery()?.matches;
// The app renders on the client only (no hydration), so a server render (tests) reads the same snapshot.
/** The switch's own state, for Settings. */
export const useAnimationsPaused = () => useSyncExternalStore(subscribe, pausedNow, pausedNow);

/** True when the looping motion must not run: the switch is on, or the system asks for reduced motion. */
export const useMotionStill = () => useSyncExternalStore(subscribe, still, still);

/** Tests only. */
export const resetMotionMemo = () => { memo = null; };
