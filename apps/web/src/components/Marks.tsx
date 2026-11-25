// Live marks on the page (stakeholder, 19 Nov 2026: "why the amounts on the dashboard are not updating in realtime"):
// the poll of /api/marks, the stream, the quiet "Prices paused" marker, and a holding's change today. Stakeholder,
// 23 Nov 2026: no line counting seconds ("Live · prices 4 s ago", "Updated a minute ago"). #604: the prices' age is
// one input of the shared freshness cue (lib/freshness.ts bookFreshness, components/Fresh.tsx). The maths and the rules for when the prices may be used are in lib/marks.ts.
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api, type Overview, type Run } from "../lib/api";
import { pct, tone } from "../lib/fmt";
import { type Marks, STALE_MS } from "../lib/marks";
import { MARKS_MS, marksPollMs, poll } from "../lib/poll";
import { STREAM_STALE_MS, type Stream, applyStream, mergeStream } from "../lib/stream";
import { EVERY, nextChange } from "../lib/freshness";
import { useVisible } from "../lib/visible";
import { Tick } from "./Tick";

/** The paper account's prices, asked for every 10 s while the tab is visible AND the market is open (lib/poll.ts
 *  marksPollMs, lib/engineclock.ts marketHours). Outside market hours no request is made and there are no marks: the
 *  booked close is the truth then. `waiting`: prices are on their way and none on screen are fresh (the first answer,
 *  or the first after the tab was hidden), so rows can keep the place of their "today" line. Not while the function
 *  says it cannot answer or its requests fail: nothing is coming then. */
/**  `pricesAt`: when the held stock with the oldest price last got one (the poll or the stream), null before the first
 *  answer. `watching`: the market is open, the page can be seen and is not waiting for its first answer: only then can
 *  the figures be late (lib/freshness.ts bookFreshness). */
export function useMarks(run: Run, overview: Overview | undefined): { marks: Marks | null; waiting: boolean; pricesAt: number | null; watching: boolean } {
  const open = marksPollMs(run, overview) !== false;
  const [, again] = useState(0);
  // The open and the close arrive while the page is up: look at the clock again every half minute. No request here.
  useEffect(() => {
    const id = setInterval(() => again((n) => n + 1), 30_000);
    return () => clearInterval(id);
  }, []);
  // One try per poll, never a retry: the function already shares the engine's request budget at Alpaca.
  const q = useQuery({ queryKey: ["marks"], queryFn: api.marks, enabled: open, retry: false, staleTime: MARKS_MS / 2, ...poll(open ? MARKS_MS : false) });
  const polled = open ? q.data ?? null : null;
  // The stream only draws: while the app is hidden its channel is left (lib/visible.ts), and joined again when shown.
  const visible = useVisible();
  const { s: stream, last: streamed } = usePriceStream(open && visible);
  const fresh = !!polled && Date.now() - polled.at <= STALE_MS;
  // Streamed prices over the polled ones while the stream is live (lib/stream.applyStream); the poll's alone otherwise.
  const marks = open ? applyStream(polled, stream) : null;
  // When each held stock (the poll's list) last got a price: the poll's answer or a streamed message, the newer.
  const last: Record<string, number> = {};
  if (polled) for (const sym of Object.keys(polled.by)) last[sym] = Math.max(polled.at, streamed[sym] ?? 0);
  const times = Object.values(last);
  const pricesAt = times.length ? Math.min(...times) : null;
  // Draw again by itself when something turns by the clock alone: the polled prices at 30 s (a failed poll keeps the
  // last answer, and it must stop being live), and the oldest price's freshness (lib/freshness.ts).
  const now = Date.now();
  const next = Math.min(...[polled ? polled.at + STALE_MS + 50 : 0, nextChange(pricesAt, EVERY.prices, now) ?? 0].filter((t) => t > now));
  useEffect(() => {
    if (next === Infinity) return;
    const id = setTimeout(() => again((n) => n + 1), next - Date.now());
    return () => clearTimeout(id);
  }, [next]);
  const waiting = open && (q.isPending || (q.isFetching && !q.isError && !!q.data && !fresh));
  // Nothing is late while the tab is hidden (nothing is asked then), nor while the first answer after its return is on
  // its way: the prices are old because the page slept, not because they stopped.
  return { marks, waiting, pricesAt, watching: open && !waiting && globalThis.document?.visibilityState !== "hidden" };
}

/** The relay's streamed prices (api.prices, scripts/price_stream.py) while `on` (the same market-hours rule as the
 *  poll): the last message, or null. The channel is left when `on` turns off or the page goes. A message that stops
 *  coming is dropped 5 s after the last one, so the page goes back to the poll by itself. `last`: when each stock was
 *  last in a message. */
function usePriceStream(on: boolean): { s: Stream | null; last: Record<string, number> } {
  const [s, set] = useState<Stream | null>(null);
  const last = useRef<Record<string, number>>({});
  useEffect(() => {
    last.current = {};
    if (!on) { set(null); return; }
    const off = api.prices((m) => {
      for (const sym of Object.keys(m.prices)) last.current[sym] = m.at;
      set((old) => mergeStream(old && m.at - old.at <= STREAM_STALE_MS ? old : null, m));
    });
    return () => { if (typeof off === "function") off(); set(null); };
  }, [on]);
  useEffect(() => {
    if (!s) return;
    const id = setTimeout(() => set((cur) => (cur === s ? null : cur)), Math.max(0, s.at + STREAM_STALE_MS - Date.now()) + 50);
    return () => clearTimeout(id);
  }, [s]);
  return { s, last: last.current };
}

/** A holding's change today against the previous close, in percent: shown only while the prices are live. `waiting`
 *  keeps the line's place until the first prices arrive, so the rows do not grow under the reader's thumb. */
export function DayChange({ pct: p, waiting = false }: { pct: number | null | undefined; waiting?: boolean }) {
  if (p === null || p === undefined) return waiting ? <span className="day" aria-hidden="true">{" "}</span> : null;
  const r = Math.round(p * 100) / 100;
  return <span className="day"><Tick value={r} className={tone(r)}>{pct(r)}</Tick> today</span>;
}
