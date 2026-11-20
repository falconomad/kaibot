// Live marks on the page (stakeholder, 19 Nov 2026: "why the amounts on the dashboard are not updating in realtime"):
// the poll of /api/marks, the one line that says how fresh the figures are, and a holding's change today.
// The maths and the rules for when the prices may be used are in lib/marks.ts.
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, type Overview, type Run } from "../lib/api";
import { pct, tone } from "../lib/fmt";
import { type Fresh, type Marks, STALE_MS, freshText } from "../lib/marks";
import { MARKS_MS, marksPollMs, poll } from "../lib/poll";
import { STREAM_STALE_MS, type Stream, applyStream, mergeStream } from "../lib/stream";
import { Tick } from "./Tick";

/** The paper account's prices, asked for every 10 s while the tab is visible AND the market is open (lib/poll.ts
 *  marksPollMs, lib/engineclock.ts marketHours). Outside market hours no request is made and there are no marks: the
 *  booked close is the truth then. `waiting`: prices are on their way and none on screen are fresh (the first answer,
 *  or the first after the tab was hidden), so rows can keep the place of their "today" line. Not while the function
 *  says it cannot answer or its requests fail: nothing is coming then. */
export function useMarks(run: Run, overview: Overview | undefined): { marks: Marks | null; waiting: boolean } {
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
  const stream = usePriceStream(open);
  // Prices that stop arriving (a failed poll keeps the last answer) must stop being live: draw again as they turn 30 s old.
  useEffect(() => {
    if (!polled) return;
    const id = setTimeout(() => again((n) => n + 1), Math.max(0, polled.at + STALE_MS - Date.now()) + 50);
    return () => clearTimeout(id);
  }, [polled]);
  const fresh = !!polled && Date.now() - polled.at <= STALE_MS;
  // Streamed prices over the polled ones while the stream is live (lib/stream.applyStream); the poll's alone otherwise.
  const marks = open ? applyStream(polled, stream) : null;
  return { marks, waiting: open && (q.isPending || (q.isFetching && !q.isError && !!q.data && !fresh)) };
}

/** The relay's streamed prices (api.prices, scripts/price_stream.py) while `on` (the same market-hours rule as the
 *  poll): the last message, or null. The channel is left when `on` turns off or the page goes. A message that stops
 *  coming is dropped 5 s after the last one, so the page goes back to the poll by itself. */
function usePriceStream(on: boolean): Stream | null {
  const [s, set] = useState<Stream | null>(null);
  useEffect(() => {
    if (!on) { set(null); return; }
    const off = api.prices((m) => set((old) => mergeStream(old && m.at - old.at <= STREAM_STALE_MS ? old : null, m)));
    return () => { if (typeof off === "function") off(); set(null); };
  }, [on]);
  useEffect(() => {
    if (!s) return;
    const id = setTimeout(() => set((cur) => (cur === s ? null : cur)), Math.max(0, s.at + STREAM_STALE_MS - Date.now()) + 50);
    return () => clearTimeout(id);
  }, [s]);
  return s;
}

/** One small muted line near the figures: "Live · prices 4 s ago", "Updated a minute ago by the engine" or
 *  "At Wednesday's close" (lib/marks.freshText), so a glance tells whether a number is live. The age is words, so the
 *  line is drawn again as it grows (each second while live); nothing animates. */
export function FreshLine({ fresh }: { fresh: Fresh | null }) {
  const [, again] = useState(0);
  const kind = fresh?.kind;
  useEffect(() => {
    if (!kind || kind === "close") return;
    const id = setInterval(() => again((n) => n + 1), kind === "live" ? 1000 : 30_000);
    return () => clearInterval(id);
  }, [kind]);
  if (!fresh) return null;
  // While live, a small tag says how the prices arrive: "live" (streamed, about once a second) or "10 s" (the poll).
  const feed = fresh.kind === "live" ? fresh.feed : undefined;
  return (
    <p className={kind === "live" ? "fresh live" : "fresh"}>{freshText(fresh)}
      {feed && <span className="feed" title={feed === "stream" ? "Prices stream about once a second (IEX)" : "Prices update every 10 s"}>{feed === "stream" ? "live" : "10 s"}</span>}
    </p>
  );
}

/** A holding's change today against the previous close, in percent: shown only while the prices are live. `waiting`
 *  keeps the line's place until the first prices arrive, so the rows do not grow under the reader's thumb. */
export function DayChange({ pct: p, waiting = false }: { pct: number | null | undefined; waiting?: boolean }) {
  if (p === null || p === undefined) return waiting ? <span className="day" aria-hidden="true">{" "}</span> : null;
  const r = Math.round(p * 100) / 100;
  return <span className="day"><Tick value={r} className={tone(r)}>{pct(r)}</Tick> today</span>;
}
