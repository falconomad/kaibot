// The installable web app (M13 4.7, #481): the service worker's registration and what the Settings page needs to offer
// "add to home screen". Loaded on demand (main.tsx imports ./start after the first paint), so it adds nothing to the
// first load.

/** Chrome's and Edge's install offer (Android and desktop). Safari has none: there the owner uses the Share menu. */
export interface InstallPrompt extends Event {
  prompt(): Promise<unknown>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

let offer: InstallPrompt | null = null;
const subs = new Set<() => void>();
const tell = () => { for (const f of [...subs]) f(); };

/** The browser's install offer, once it has made one (useSyncExternalStore: `subscribe`, `installOffer`). */
export const installOffer = () => offer;
export const subscribe = (f: () => void) => { subs.add(f); return () => { subs.delete(f); }; };
/** Tests only. */
export const resetInstall = () => { offer = null; subs.clear(); };

type Win = Pick<Window, "addEventListener"> & { navigator: { serviceWorker?: { register(url: string): Promise<unknown> } } };

/** Keep the browser's install offer for the Settings page, and register the service worker (the app shell only,
 *  pwa/sw.ts). A browser without service workers, or a failed registration, changes nothing: the site works as before. */
export function startPwa(w: Win = window, swUrl = "/sw.js"): void {
  w.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); offer = e as InstallPrompt; tell(); });
  w.addEventListener("appinstalled", () => { offer = null; tell(); });
  void w.navigator.serviceWorker?.register(swUrl).catch(() => {});
}

type Display = { matchMedia?: (q: string) => { matches: boolean }; navigator: { standalone?: boolean } };
/** Opened from the home screen, in its own window (iOS reports it on `navigator.standalone`). */
export const isStandalone = (w: Display = window as unknown as Display): boolean =>
  w.navigator.standalone === true || !!w.matchMedia?.("(display-mode: standalone)").matches;

export type Platform = "ios" | "android" | "other";
/** iPadOS says it is a Mac; a Mac with a touch screen is an iPad. */
export function platform(ua: string, touchPoints = 0): Platform {
  if (/iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && touchPoints > 1)) return "ios";
  return /Android/.test(ua) ? "android" : "other";
}

/** How to put the app on the home screen, in words, for the browser the owner is in. */
export function installSteps(p: Platform): string[] {
  if (p === "ios") return ["Open this page in Safari.", "Tap the Share button (the square with an arrow).", "Choose \"Add to Home Screen\", then \"Add\"."];
  if (p === "android") return ["Open the browser's menu (the three dots).", "Choose \"Install app\" or \"Add to Home screen\".", "Confirm with \"Install\"."];
  return ["On a phone: open this page in Safari (iPhone) or Chrome (Android).", "Use the browser's Share or menu button.", "Choose \"Add to Home Screen\" or \"Install app\"."];
}
