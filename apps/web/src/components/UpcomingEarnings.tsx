// "Upcoming earnings" on Home (lib/earnings.ts, migration 0054): the held stocks' next reports within 30 days. One calm
// card: symbol, date, days until, before/after market when the calendar says, the estimated EPS. Display only: no
// link, no order, never a warning colour. Lazy (pages/Live.tsx, through components/Lines.tsx): not in the first load.
import { useQuery } from "@tanstack/react-query";
import { MIN, poll } from "../lib/poll";
import { EMPTY_TEXT, SOURCE_TEXT, TIME_LABEL, type Upcoming, dateText, estimateText, fetchUpcoming, staleText, untilText } from "../lib/earnings";
import "./earnings.css";

/** Nothing while loading, before migration 0054 or on an error (the card is extra, never a reason for an error row).
 *  Polls every 30 minutes while the tab is visible: the calendar changes once a day. */
export default function UpcomingEarnings() {
  const q = useQuery({ queryKey: ["upcoming-earnings"], queryFn: fetchUpcoming, staleTime: 10 * MIN, retry: 1, ...poll(30 * MIN) });
  if (!q.data) return null;
  return <EarningsCard data={q.data} />;
}

export function EarningsCard({ data, now = Date.now() }: { data: Upcoming; now?: number }) {
  const stale = staleText(data.fetchedAt, now);
  return (
    <section className="er" aria-labelledby="er-h">
      <h2 id="er-h" className="er-h">Upcoming earnings</h2>
      {data.items.length ? (
        <ul className="er-list">
          {data.items.map((r) => {
            const est = estimateText(r);
            return (
              <li key={`${r.symbol}-${r.reportDate}`} className="er-row">
                <span className="er-sym num">{r.symbol}</span>
                <span className="er-when">
                  <span className="er-date">{dateText(r.reportDate)}</span>
                  <span className="er-sub">{untilText(r.daysUntil)}{r.time ? ` · ${TIME_LABEL[r.time]}` : ""}</span>
                </span>
                {est && <span className="er-est num">{est}</span>}
              </li>
            );
          })}
        </ul>
      ) : <p className="er-empty">{EMPTY_TEXT}</p>}
      <p className="er-src">{SOURCE_TEXT}{stale ? <span className="er-stale"> · {stale}</span> : null}</p>
    </section>
  );
}
