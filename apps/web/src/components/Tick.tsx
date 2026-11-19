// v4 PR 7 (docs/design/mobile_first_v4.md, "Motion: Number changes"): when a polled figure changes, the new value fades
// in over 200 ms and the figure tints green (up) or red (down) for 600 ms, then returns to its colour. No rolling
// digits; only figures that changed animate; the first render never animates. With reduced motion the fade is off
// (global rule) but the tint stays, because colour is not motion.
// 19 Nov 2026 (live marks): `from` names where the figure comes from (live prices, the engine's minute row, a booked
// close). When it changes together with the value, the figure is replaced without a tint: a change of source is not
// a move of the market.
import { useEffect, useRef, useState, type ReactNode } from "react";

export const TINT_MS = 600;

/** Which way a figure moved between two polls; null when it didn't change or either side is unknown. */
export function tickDir(was: number | null | undefined, now: number | null | undefined): "up" | "down" | null {
  if (was === null || was === undefined || now === null || now === undefined || !Number.isFinite(was) || !Number.isFinite(now) || was === now) return null;
  return now > was ? "up" : "down";
}

export function Tick({ value, children, className = "", from }: { value: number | null | undefined; children: ReactNode; className?: string; from?: string }) {
  const prev = useRef({ value, from });
  const [tick, setTick] = useState<{ n: number; dir: "up" | "down" | null }>({ n: 0, dir: null });
  useEffect(() => {
    const was = prev.current;
    prev.current = { value, from };
    const dir = was.from === from ? tickDir(was.value, value) : null;
    if (!dir) return;
    setTick((t) => ({ n: t.n + 1, dir }));
    const id = setTimeout(() => setTick((t) => ({ ...t, dir: null })), TINT_MS);
    return () => clearTimeout(id);
  }, [value, from]);
  const cls = [className, tick.n ? "tick" : "", tick.dir ? `tint-${tick.dir}` : ""].filter(Boolean).join(" ");
  // A new key on each change restarts the fade on the new value only.
  return <span key={tick.n} className={cls || undefined}>{children}</span>;
}
