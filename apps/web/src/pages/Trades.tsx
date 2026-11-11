import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { useState } from "react";
import { useRunCtx } from "../components/Shell";
import { api } from "../lib/api";
import { TradeRows } from "./Live";
import { Skeleton } from "../components/Skeleton";

const PAGE = 50;

export function Trades() {
  const { run } = useRunCtx();
  const [page, setPage] = useState(0);
  const q = useQuery({ queryKey: ["trades", run.id, page], queryFn: () => api.trades(run.id, PAGE, page * PAGE), placeholderData: keepPreviousData });
  const total = q.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE));
  return (
    <section className="card">
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 10 }}>
        <h2 style={{ margin: 0 }}>All trades · {run.label ?? `Run ${run.id}`}</h2>
        <span className="muted num">{total} total</span>
        <span className="spacer" />
        <button disabled={page === 0} onClick={() => setPage(page - 1)}>Newer</button>
        <span className="num muted">{page + 1} / {pages}</span>
        <button disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>Older</button>
      </div>
      {q.isPending ? <Skeleton /> : q.isError ? <p className="loss">{String(q.error)}</p> : <TradeRows rows={q.data.rows} />}
    </section>
  );
}
