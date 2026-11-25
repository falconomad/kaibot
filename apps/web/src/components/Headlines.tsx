// The headline line on Home (migration 0043; docs in lib/headlines.ts). A cycling card: one headline, about 6 s, a
// short fade, the next. Held and decided stocks come first. Tapping the card pauses it (tap again to go on); a person
// who asked for less motion gets a short static list and nothing moves. The card also holds still while a keyboard or
// screen reader has focus on it, or a mouse is over it, so nobody is read or shown a moving target (#606 QA). Its name
// is its visible text (WCAG 2.5.3, label in name); whether it is paused is `aria-pressed`, the hint a description. Lazy (pages/Live.tsx, through components/Lines.tsx): this file,
// lib/headlines.ts and headlines.css are a chunk of their own, not first load.
//
// Why a card and not a ticker: a moving ticker cannot be read on a phone (the eye has to chase the text and a 140-
// character line never fits), it cannot be paused without stopping mid-word, and under reduced motion it has to
// become something else anyway. A card holds still while it is read, fits two lines, and its fade is the only motion.
import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import { MIN, poll } from "../lib/poll";
import { DWELL_MS, type Headline, SOURCE_LABEL, STATIC_ROWS, TAGS, type Viewer, age, fetchHeadlines, nextIndex } from "../lib/headlines";
import "./headlines.css";
import type { Freshness } from "../lib/freshness";
import { FreshNote } from "./Fresh";

const FADE_MS = 300;

function useReducedMotion(): boolean {
  const q = typeof window !== "undefined" ? window.matchMedia?.("(prefers-reduced-motion: reduce)") : undefined;
  const [reduce, setReduce] = useState(!!q?.matches);
  useEffect(() => {
    if (!q) return;
    const on = () => setReduce(q.matches);
    q.addEventListener?.("change", on);
    return () => q.removeEventListener?.("change", on);
  }, [q]);
  return reduce;
}

/** Home's headlines: nothing while loading, without headlines or before migration 0043; nothing on an error either
 *  (the line is extra, never a reason for an error row). Polls every 2 minutes, only while the tab is visible. */
/** `fresh` (#604): the news feed's freshness; the card says it when the feed is not live. */
export default function Headlines({ viewer = "owner", fresh }: { viewer?: Viewer; fresh?: Freshness | null }) {
  const q = useQuery({ queryKey: ["headlines", viewer], queryFn: () => fetchHeadlines(viewer), staleTime: MIN, retry: 1, ...poll(2 * MIN) });
  if (!q.data?.length) return null;
  return <HeadlineCard items={q.data} fresh={fresh} />;
}

function Line({ h, now }: { h: Headline; now: number }) {
  const tag = h.tags[0];
  return (
    <>
      <span className="hl-meta">
        <span className="hl-sym num">{h.symbols[0]}</span>
        {tag && <span className="hl-chip">{TAGS[tag]}</span>}
        <span className="hl-when">{SOURCE_LABEL[h.source]} · {age(h.created_at, now)}</span>
      </span>
      {/* text node only: a headline is never HTML */}
      <span className="hl-text">{h.headline}</span>
    </>
  );
}

function Open({ h }: { h: Headline }) {
  if (!h.url) return null;
  return (
    <a className="hl-open" href={h.url} target="_blank" rel="noopener noreferrer" aria-label={`Open the story about ${h.symbols[0]}`}>
      <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" /></svg>
    </a>
  );
}

export function HeadlineCard({ items, now = Date.now(), fresh }: { items: Headline[]; now?: number; fresh?: Freshness | null }) {
  const reduce = useReducedMotion();
  const [i, setI] = useState(0);
  const [paused, setPaused] = useState(false);
  const [fading, setFading] = useState(false);
  // Held while a mouse is over the card or focus is on it from a keyboard or screen reader. Focus that comes with a
  // tap is not a hold (a tap already pauses; a held card would not go on after the second tap).
  const [hover, setHover] = useState(false);
  const [focus, setFocus] = useState(false);
  const tapped = useRef(false);
  const hint = useId();
  const n = items.length;
  const at = n ? i % n : 0;
  const held = hover || focus;

  useEffect(() => {
    if (reduce || paused || held || n < 2) return;
    let fade: ReturnType<typeof setTimeout> | undefined;
    const t = setInterval(() => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      setFading(true);
      fade = setTimeout(() => { setI((x) => nextIndex(x, n)); setFading(false); }, FADE_MS);
    }, DWELL_MS);
    return () => { clearInterval(t); clearTimeout(fade); setFading(false); };
  }, [reduce, paused, held, n]);

  if (!n) return null;
  if (reduce)
    return (
      <section className="hl" aria-label="Stock headlines">
        <ul className="hl-list">
          {items.slice(0, STATIC_ROWS).map((h) => (
            <li key={`${h.source}-${h.id}-${h.headline}`} className="hl-item"><span className="hl-body"><Line h={h} now={now} /></span><Open h={h} /></li>
          ))}
        </ul>
        <FreshNote f={fresh} what="Headlines" />
      </section>
    );
  const h = items[at];
  return (
    <section className="hl" aria-label="Stock headlines"
      onPointerEnter={(e) => { if (e.pointerType === "mouse") setHover(true); }} onPointerLeave={() => setHover(false)}>
      <div className="hl-item">
        {/* the name is the visible text; the pause state is aria-pressed, the hint a description */}
        <button type="button" className={`hl-body hl-card${fading ? " fading" : ""}`} aria-pressed={paused} aria-describedby={hint}
          onPointerDown={() => { tapped.current = true; }} onKeyDown={() => { tapped.current = false; }}
          onFocus={() => { setFocus(!tapped.current); tapped.current = false; }} onBlur={() => setFocus(false)}
          onClick={() => setPaused((p) => !p)}>
          <Line h={h} now={now} />
        </button>
        <span id={hint} className="sr-only">{paused ? "Paused. Press to go on." : "Press to pause the headlines."}</span>
        <Open h={h} />
      </div>
      {n > 1 && <p className="hl-count" aria-hidden="true">{paused ? "Paused · " : ""}{at + 1} of {n}</p>}
      <FreshNote f={fresh} what="Headlines" />
    </section>
  );
}
