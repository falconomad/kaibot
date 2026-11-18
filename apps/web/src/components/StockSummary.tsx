// Stock summary card (display-only AI; /api/summary and migration 0023): what the company is, its latest news,
// the next earnings date and any announced corporate action, in plain words, with who wrote it and from what.
// Shown in the stock sheets (Positions), on the trade page and behind an accepted chip in My lists. The first view of
// a stock on a day writes that day's card; later views read it. Nothing here changes a trade. With no card (none
// yet, summaries unavailable on this deployment, or 0023 not applied) it says there is none yet.
import { useQuery } from "@tanstack/react-query";
import { useId } from "react";
import { api } from "../lib/api";
import { day } from "../lib/fmt";
import { actionsLine, creditLine, earningsLine, newsNote } from "../lib/summary";
import { NAME } from "../brand/brand";

export function StockSummary({ symbol }: { symbol: string }) {
  const id = useId();
  const q = useQuery({ queryKey: ["stockSummary", symbol], queryFn: () => api.stockSummary(symbol), staleTime: 30 * 60_000,
    refetchOnWindowFocus: false, retry: false });
  const s = q.data;
  return (
    <section className="ss" aria-labelledby={id}>
      <h3 id={id} className="ss-h">In plain words</h3>
      {q.isPending ? (
        <div className="ss-sk" role="status" aria-busy="true" aria-label="Loading the summary">
          <span className="sk" style={{ height: 15, borderRadius: 6 }} /><span className="sk" style={{ height: 15, width: "70%", borderRadius: 6 }} />
        </div>
      ) : q.isError ? (
        <p className="ss-meta">Couldn't load the summary. The rest of this page is unaffected.</p>
      ) : !s ? (
        <p className="ss-body muted">No summary for {symbol} yet. {NAME} writes one the first time a stock is opened on a trading day, when summaries are switched on.</p>
      ) : (
        <>
          {s.about && <p className="ss-body">{s.about}</p>}
          {s.news.length > 0 ? (
            <ul className="ss-news" aria-label="Latest news">
              {s.news.map((n, i) => <li key={i}><span className="ss-body">{n.text}</span><span className="ss-meta">{n.source ? `${n.source} · ` : ""}{day(n.date)}</span></li>)}
            </ul>
          ) : <p className="ss-body muted">{newsNote(s)}</p>}
          <p className="ss-body">{earningsLine(s)}</p>
          <p className="ss-body">{actionsLine(s)}</p>
          <p className="ss-meta">{creditLine(s)}</p>
        </>
      )}
    </section>
  );
}
