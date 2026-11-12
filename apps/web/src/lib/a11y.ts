// Keyboard support for the tab rows (role="tablist"): arrow keys, Home and End move between tabs and select them,
// and only the selected tab is in the Tab order (roving tabindex), as the WAI-ARIA tabs pattern expects.
import type { KeyboardEvent } from "react";

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
