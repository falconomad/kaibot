// The theme (audit 2 ticket 8.2, #720): Settings offers System (the default), Dark and Light. System follows the
// device's `prefers-color-scheme` and changes with it while the page is open. The choice is kept in this browser's
// localStorage; a browser that refuses storage (private mode, blocked) keeps System and nothing breaks.
//
// The theme is the `data-theme` attribute on <html>, which tokens.css reads (each theme also sets its `color-scheme`,
// which the logo's light-dark() fills read, #674). It is set before the first paint by the inline script in
// index.html (PREPAINT below is that script, word for word, checked in theme.dom.test.tsx), so a light page never
// flashes dark; this module then keeps it right: a choice in Settings, the device switching between light and dark,
// another tab of this browser changing the choice. Unit-tested in theme.dom.test.tsx.
import { useSyncExternalStore } from "react";

export const THEME_KEY = "kb-theme";
export type ThemeChoice = "system" | "dark" | "light";
export type Theme = "dark" | "light";
export const THEME_CHOICES: readonly ThemeChoice[] = ["system", "dark", "light"];

/** index.html's inline script, before any stylesheet: the saved choice, or the device's scheme. The CSP allows it by
 *  its hash (vercel.json, csp.test.ts). */
export const PREPAINT = `try{var t=localStorage.getItem("${THEME_KEY}");if(t!=="dark"&&t!=="light")t=matchMedia("(prefers-color-scheme: light)").matches?"light":"dark";document.documentElement.dataset.theme=t}catch(e){}`;

const listeners = new Set<() => void>();
/** The choice as this page last read or set it (also when storage refuses it). */
let memo: ThemeChoice | null = null;

/** The saved choice; System when nothing (or something unknown) is saved, or storage throws. */
export function savedTheme(): ThemeChoice {
  try {
    const v = localStorage.getItem(THEME_KEY);
    return v === "dark" || v === "light" ? v : "system";
  } catch { return "system"; }
}

const lightQuery = () => (typeof window !== "undefined" ? window.matchMedia?.("(prefers-color-scheme: light)") : undefined);
const choiceNow = () => (memo ??= savedTheme());

/** The theme a choice draws: System is the device's scheme (dark when the device says nothing). */
export const resolveTheme = (c: ThemeChoice): Theme => (c === "system" ? (lightQuery()?.matches ? "light" : "dark") : c);

/** Put the theme on <html>, and the browser's bar colour (meta theme-color) on the page colour it draws. */
export function applyTheme(doc: Document | undefined = typeof document === "undefined" ? undefined : document): Theme {
  const t = resolveTheme(choiceNow());
  const html = doc?.documentElement;
  if (!html) return t;
  if (html.dataset.theme !== t) html.dataset.theme = t;
  const meta = doc.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
  const paper = doc.defaultView?.getComputedStyle(html).getPropertyValue("--paper").trim();
  if (meta && paper) meta.content = paper;
  return t;
}

export function setTheme(c: ThemeChoice): void {
  try { if (c === "system") localStorage.removeItem(THEME_KEY); else localStorage.setItem(THEME_KEY, c); } catch { /* kept for this page only */ }
  memo = c;
  applyTheme();
  for (const f of [...listeners]) f();
}

let started = false;
/** Call once at start-up (main.tsx): applies the theme and follows the device and other tabs from then on. */
export function startTheme(): void {
  applyTheme();
  if (started || typeof window === "undefined") return;
  started = true;
  // the device switches between light and dark (only System follows it; applyTheme reads the choice)
  lightQuery()?.addEventListener?.("change", () => { applyTheme(); for (const f of [...listeners]) f(); });
  // another tab of this browser changes the choice
  window.addEventListener("storage", (e) => {
    if (e.key !== THEME_KEY && e.key !== null) return;
    memo = null; applyTheme(); for (const f of [...listeners]) f();
  });
}

function subscribe(f: () => void): () => void {
  listeners.add(f);
  return () => { listeners.delete(f); };
}

/** The choice, for Settings. The app renders on the client only (no hydration), so a server render reads the same. */
export const useThemeChoice = () => useSyncExternalStore(subscribe, choiceNow, choiceNow);

/** Tests only. */
export const resetThemeForTests = () => { memo = null; started = false; listeners.clear(); };
