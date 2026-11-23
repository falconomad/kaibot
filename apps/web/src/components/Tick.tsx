// The number tick (v4 PR 7, redone 23 Nov 2026 for the live price stream: "the animation when a price changes could
// be better"). When a figure moves:
//   - it changes in place: same element, tabular figures, nothing remounts, so nothing around it moves;
//   - a soft wash in the app's up/down tint comes in behind the figure in 120 ms and fades out in 560 ms (opacity only);
//   - only the characters that changed take the up/down colour, roll in by a fraction of an em, and fade back;
//   - a small ▲ or ▼ beside it says the direction, so colour is never the only cue (drawn by CSS and hidden from screen
//     readers: the figure itself is the information, and it is not in a live region, so a price is never read out);
//     a gain or a loss keeps only its own ▲/▼ and sign, and the roll's direction is the second cue there.
// Bursts are coalesced (lib/tick.ts): at most one tick per figure every 250 ms. A value that arrives inside the window
// is held (the figure shown stays) and the latest one is shown at the window's end, ticking against what was shown.
// An equal value, or one that formats the same, does not tick. The first render never ticks. `from` names where the
// figure comes from (live prices, the engine's minute row, a booked close): a change of source replaces the figure at
// once, without a tick, because it is not a move of the market.
// The colour, roll and wash are CSS animations restarted by a key, so the only timers are the coalescing window and
// the tick's end. Reduced motion: the global rule turns them all off, so the figure just changes (no flash).
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { TINT_MS, changedPart, tickDelay, tickDir, type Dir } from "../lib/tick";

export { TINT_MS, tickDir } from "../lib/tick";

/** The text of a figure made of strings and numbers only; null for anything richer (then the whole figure ticks). */
export const textOf = (node: ReactNode): string | null =>
  (Array.isArray(node) ? node : [node]).every((x) => typeof x === "string" || typeof x === "number") ? [node].flat().join("") : null;

interface TickState { n: number; dir?: Dir | null; parts?: string[] | null }

export function Tick(props: { value: number | null | undefined; children: ReactNode; className?: string; from?: string }) {
  const { value, children, from } = props, text = textOf(children);
  // v, x, f, c: the value, text, source and content on screen; p: the latest props; at: when it last ticked;
  // w (a value waits) and e (the tick's end): the timers
  const s = useRef({ v: value, x: text, f: from, c: children, p: props, at: null as number | null, w: 0 as unknown, e: 0 as unknown }).current;
  s.p = props;
  // the same value with other words (a label that changed) is shown as it is, unless a value waits
  if (!s.w && value === s.v && from === s.f) { s.c = children; s.x = text; }
  const [t, setT] = useState<TickState>({ n: 0 });

  const show = () => {
    const l = s.p, lt = textOf(l.children), moved = l.from !== s.f;
    const dir = moved ? null : tickDir(s.v, l.value), parts = dir && s.x !== null && lt !== null ? changedPart(s.x, lt) : null;
    Object.assign(s, { v: l.value, x: lt, f: l.from, c: l.children, w: 0 });
    if (!dir || (parts && !parts[1])) return setT((x) => (moved ? { n: x.n } : { ...x }));
    s.at = Date.now();
    clearTimeout(s.e as number);
    setT((x) => ({ n: x.n + 1, dir, parts }));
    s.e = setTimeout(() => setT((x) => ({ n: x.n })), TINT_MS);
  };

  // A layout effect, so a value that has to wait is never painted before the one shown.
  useLayoutEffect(() => {
    const wait = from === s.f && tickDir(s.v, value) ? tickDelay(s.at, Date.now()) : 0;
    if (!wait) { clearTimeout(s.w as number); return show(); }
    if (!s.w) { s.w = setTimeout(show, wait); setT((x) => ({ ...x })); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, from]);
  useEffect(() => () => { clearTimeout(s.w as number); clearTimeout(s.e as number); }, [s]);

  const p = t.parts, held = !!s.w;
  let body: ReactNode = held ? s.c : children;
  if (t.dir && !held) body = p ? (text === p.join("") ? <>{p[0]}<span key={t.n} className="tk-mid">{p[1]}</span>{p[2]}</> : children)
    : <span key={t.n} className="tk-mid">{children}</span>;
  return (
    <span className={["tk", props.className, t.dir && `tint-${t.dir}`].filter(Boolean).join(" ")}>
      {t.dir && <span key={-t.n} className="tk-fx" aria-hidden="true" />}
      {body}
    </span>
  );
}
