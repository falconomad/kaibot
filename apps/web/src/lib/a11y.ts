// Keyboard support for the tab rows (role="tablist"): arrow keys, Home and End move between tabs and select them,
// and only the selected tab is in the Tab order (roving tabindex), as the WAI-ARIA tabs pattern expects.
import { useEffect, useRef, useState, type KeyboardEvent } from "react";

/** What to say in a polite live region as a list of things with a state changes (#327; My lists' ticker checks).
 *  `words` gets the items that are new or whose state changed since the last render and returns one sentence or more.
 *  Nothing is said for the list as it first appears, for a removed item, or for an item that arrives already accepted
 *  (a saved list being opened): only for what the person just did and the answers to it. The last message stays in
 *  the region until the next one, as a live region expects. */
export function useAnnounce<T extends { symbol: string; state: string }>(items: T[], words: (changed: T[]) => string): string {
  const seen = useRef<Map<string, string> | null>(null);
  const [said, setSaid] = useState("");
  useEffect(() => {
    const before = seen.current;
    seen.current = new Map(items.map((i) => [i.symbol, i.state]));
    if (!before) return;
    const changed = items.filter((i) => (before.has(i.symbol) ? before.get(i.symbol) !== i.state : i.state !== "accepted"));
    if (changed.length) setSaid(words(changed));
  }, [items]); // eslint-disable-line react-hooks/exhaustive-deps
  return said;
}

export function onTabKeys(e: KeyboardEvent<HTMLElement>): void {
  const tabs = [...e.currentTarget.querySelectorAll<HTMLElement>('[role="tab"]:not(:disabled)')];
  const i = tabs.indexOf(document.activeElement as HTMLElement);
  if (i < 0 || !tabs.length) return;
  const n = tabs.length;
  const j = e.key === "ArrowRight" ? (i + 1) % n : e.key === "ArrowLeft" ? (i - 1 + n) % n
    : e.key === "Home" ? 0 : e.key === "End" ? n - 1 : -1;
  if (j < 0) return;
  e.preventDefault();
  tabs[j].focus();
  tabs[j].click();
}

/** Props for one tab button. */
export const tabProps = (selected: boolean) => ({ role: "tab" as const, "aria-selected": selected, tabIndex: selected ? 0 : -1 });

/** True when the person asked the system for less motion. */
export const reducedMotion = () => typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
