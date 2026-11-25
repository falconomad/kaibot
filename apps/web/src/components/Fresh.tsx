// The one freshness cue (#604; the rules are in lib/freshness.ts). Nothing while a figure is live; otherwise its state
// in words, small and quiet ("A few minutes old"), amber when stale ("Stale · 25 min old"). Said once to a screen
// reader when it appears; nothing counts or animates.
import type { Freshness } from "../lib/freshness";

/** `what`: the figure it is about, for a screen reader ("Pot"). `inline`: inside a line of text (the engine chip). */
export function FreshNote({ f, what, inline = false, className = "" }: { f: Freshness | null | undefined; what?: string; inline?: boolean; className?: string }) {
  if (!f || f.state === "live" || !f.text) return null;
  const Tag = inline ? "span" : "p";
  return (
    <Tag className={`fresh ${f.state} ${className}`.trim()} role="status">
      {what && <span className="sr-only">{what}: </span>}{f.text}
    </Tag>
  );
}
