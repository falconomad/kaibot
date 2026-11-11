import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useRunCtx } from "../components/Shell";
import { api, type TapeItem } from "../lib/api";
import { fmtR } from "../lib/derive";
import { EXIT_LABEL, dateEt, money, px, signedMoney, timeEt, tone } from "../lib/fmt";

const PAGE = 100;
const STRAT: Record<string, string> = {
  dip_reversal: "dip reversal", spy_late_momentum: "SPY momentum", orb_stocks_in_play: "ORB", news_continuation: "news continuation",
};
type Filter = "all" | "trades" | "live" | "shadow";

/** What happened, in one line, with the reason. */
function Line({ it }: { it: TapeItem }) {
  if (it.kind === "fill") {
    const cost = Math.abs(it.venue_px - it.shadow_px) * it.qty + it.fees;
    return (
      <>
        <td><span className={`tag ${it.side === "buy" ? "allow" : "downgrade"}`}>{it.side === "buy" ? "bought" : "sold"}</span></td>
        <td>{it.trade_id ? <Link to={`/trades/${it.trade_id}`}><b>{it.symbol}</b></Link> : <b>{it.symbol}</b>}</td>
        <td className="num">{it.qty} @ {px(it.venue_px)} <span className="muted">(shadow {px(it.shadow_px)}, fees {money(it.fees)})</span>
          {it.side === "sell" && it.exit_reason && <span className="muted"> · {EXIT_LABEL[it.exit_reason] ?? it.exit_reason}</span>}
          {it.tag && <span className="muted"> · {it.tag.replace(/_/g, " ")}</span>}</td>
        <td className={`r num ${tone(it.pnl_usd)}`} title={`Costs on this fill vs shadow model: ${money(cost)}`}>
          {it.side === "sell" ? signedMoney(it.pnl_usd) : ""}</td>
      </>
    );
  }
  const taken = it.mode === "live" && it.approved && !it.veto && (it.qty ?? 0) > 0;
  const label = it.mode === "shadow" ? "shadow" : taken ? "taken" : "skipped";
  const why = it.veto ?? (it.mode === "live" && !it.approved ? `gate: ${it.binding ?? "not approved"}` : null);
  const cf = it.counterfactual;
  return (
    <>
      <td><span className={`tag ${taken ? "allow" : it.mode === "shadow" ? "" : "veto"}`}>{label}</span></td>
      <td>{it.trade_id ? <Link to={`/trades/${it.trade_id}`}><b>{it.symbol}</b></Link> : <b>{it.symbol}</b>}
        <span className="muted"> · {STRAT[it.strategy] ?? it.strategy}</span></td>
      <td>{it.reasons.join(" · ")}{why && <span className="muted"> — {why}</span>}
        {it.entry !== null && <span className="muted num"> · ref {px(it.entry)}, stop {px(it.stop)}{it.target ? `, target ${px(it.target)}` : ""}</span>}</td>
      <td className={`r num ${tone(cf?.pnl_usd)}`}>{cf && cf.pnl_usd !== undefined ? <span title="Counterfactual, never traded">{signedMoney(cf.pnl_usd)} · {fmtR(cf.r_multiple)}</span> : taken ? <span className="muted">{it.qty} sh</span> : ""}</td>
    </>
  );
}

export function Tape() {
  const { run } = useRunCtx();
  const [filter, setFilter] = useState<Filter>("all");
  const q = useInfiniteQuery({
    queryKey: ["tape", run.id],
    queryFn: ({ pageParam }) => api.tape(run.id, PAGE, pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => (last.length === PAGE ? last[last.length - 1].ts : undefined),
    refetchInterval: run.kind === "paper" ? 15_000 : false,
  });
  const all = q.data?.pages.flat() ?? [];
  const rows = all.filter((it) => filter === "all" || (filter === "trades" ? it.kind === "fill"
    : it.kind === "signal" && it.mode === filter));
  let lastDay = "";
  return (
    <section className="card" aria-labelledby="tape-h">
      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <h2 id="tape-h" style={{ margin: 0 }}>Tape</h2>
        <span className="muted" style={{ fontSize: 13 }}>Every setup Kaibot saw, what it did, why, and every fill after costs. Newest first.</span>
        <span className="spacer" />
        <div className="tabs" role="tablist" aria-label="Show">
          {([["all", "All"], ["trades", "Fills"], ["live", "Setups"], ["shadow", "Shadow"]] as [Filter, string][]).map(([k, l]) => (
            <button key={k} role="tab" aria-selected={filter === k} onClick={() => setFilter(k)}>{l}</button>))}
        </div>
      </div>
      {q.isPending ? <p className="skeleton">Loading…</p> : q.isError ? <p className="loss">{String(q.error)}</p> : !rows.length ? (
        <p className="muted">{run.kind === "replay" ? "Replays don't record a tape: only live paper runs do." : "Nothing yet. Setups are checked every 5 minutes from 09:35 to 15:45 ET."}</p>
      ) : (
        <table className="list" style={{ marginTop: 12 }}>
          <thead><tr><th>Time (ET)</th><th>What</th><th>Stock</th><th>Why</th><th className="r">Result</th></tr></thead>
          <tbody>{rows.map((it, i) => {
            const d = dateEt(it.ts);
            const head = d !== lastDay;
            lastDay = d;
            return (
              <tr key={`${it.kind}-${it.ts}-${i}`} style={head ? { borderTop: "2px solid var(--rule)" } : undefined}>
                <td className="num">{head ? <b>{d} </b> : null}{timeEt(it.ts)}</td>
                <Line it={it} />
              </tr>
            );
          })}</tbody>
        </table>
      )}
      {q.hasNextPage && <p><button disabled={q.isFetchingNextPage} onClick={() => q.fetchNextPage()}>{q.isFetchingNextPage ? "Loading…" : "Older"}</button></p>}
    </section>
  );
}
