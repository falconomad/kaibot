// Is the app on screen? (frontend performance pass, 23 Nov 2026.) A phone keeps an installed web app alive in the
// background for a long time; work that only draws (the price stream's once-a-second messages) stops while the page is
// hidden and starts again when it is shown. The polls already pause by themselves (react-query, lib/poll.ts).
// `visibilitychange` is fired at the document and bubbles to the window, where react-query listens too.
import { useSyncExternalStore } from "react";

function subscribe(on: () => void): () => void {
  window.addEventListener("visibilitychange", on);
  return () => window.removeEventListener("visibilitychange", on);
}

/** True while the page is shown; false while it is hidden (another app, the screen off, a background tab). */
export const useVisible = (): boolean => useSyncExternalStore(subscribe, () => document.visibilityState !== "hidden", () => true);
