import { useInfiniteQuery } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { useRunCtx } from "../components/Shell";
import { onTabKeys, tabProps } from "../lib/a11y";
import { poll } from "../lib/poll";
import { api, type TapeItem } from "../lib/api";
import { RoomBySignal } from "../components/Room";
import { fmtR } from "../lib/derive";
import { roomOutcome } from "../lib/roomview";
import { causeText, exitText, explainSignal, ruleLabel, ruleName } from "../lib/explain";
import { dateEt, money, px, signedMoney, timeEt, tone } from "../lib/fmt";
import { Skeleton } from "../components/Skeleton";
import { Empty, ErrorState } from "../components/States";
import { StockCell } from "../components/StockBadge";
import { RuleExperiment } from "../components/Experiment";
import type { Heartbeat } from "../lib/api";

const PAGE = 100;
type Filter = "all" | "trades" | "live" | "room" | "shadow";

/** What happened, in one line, with the reason. */
function Line({ it, open, onToggle, beats }: { it: TapeItem; open?: boolean; onToggle?: () => void; beats?: Heartbeat[] }) {
  if (it.kind === "fill") {
    const cost = Math.abs(it.venue_px - it.shadow_px) * it.qty + it.fees;
    return (
      <>
        <td><span className={`tag ${it.side === "buy" ? "allow" : "downgrade"}`}>{it.side === "buy" ? "bought" : "sold"}</span></td>
        <td>{it.trade_id ? <Link to={`/trades/${it.trade_id}`} style={{ color: "inherit" }}><StockCell symbol={it.symbol} /></Link> : <StockCell symbol={it.symbol} />}</td>
        <td className="num">{it.qty} @ {px(it.venue_px)} <span className="muted">(shadow {px(it.shadow_px)}, fees {money(it.fees)})</span>
          {it.side === "sell" && it.exit_reason && <span className="muted" title={`code: ${it.exit_reason}`}> · {exitText(it.exit_reason)}</span>}
          {it.tag && <span className="muted" title={`code: ${it.tag}`}> · {it.tag.replace(/_/g, " ")}</span>}
          <RuleExperiment code={it.strategy} beats={beats} /></td>
        <td className={`r num ${tone(it.pnl_usd)}`} title={`Costs on this fill vs shadow model: ${money(cost)}`}>
          {it.side === "sell" ? signedMoney(it.pnl_usd) : ""}</td>
      </>
    );
  }
  const taken = it.mode === "live" && it.approved && !it.veto && (it.qty ?? 0) > 0;
  const label = it.mode === "shadow" ? "shadow" : taken ? "taken" : "skipped";
  const room = it.room ?? null;
  const out = room ? roomOutcome(room.status, room.verdict) : null;
  const roomBlocked = !!room && !taken && (room.status === "done" ? room.verdict === "veto"
    : ["timeout", "invalid", "error"].includes(room.status));
  const roomWhy = roomBlocked && out ? `${out.label}${room!.cause ? `: ${causeText(room!.cause)}` : ""}` : null;
  const ex = explainSignal(it, { taken: !!taken, roomWhy });
  // Shadow strategies carry their own counterfactual; room-blocked setups show the rules-only arm's.
  const cf = it.counterfactual ?? (!taken ? room?.counterfactual ?? null : null);
  return (
    <>
      <td><span className={`tag ${taken ? "allow" : it.mode === "shadow" ? "" : "veto"}`}>{label}</span></td>
      <td>{it.trade_id ? <Link to={`/trades/${it.trade_id}`} style={{ color: "inherit" }}><StockCell symbol={it.symbol} /></Link> : <StockCell symbol={it.symbol} />}
        <span className="muted" title={`${ruleLabel(it.strategy)} · code: ${it.strategy}`}> · {ruleName(it.strategy)}</span>
        <RuleExperiment code={it.strategy} beats={beats} /></td>
      <td title={ex.detail}><div>{ex.outcome}</div><div className="muted" style={{ fontSize: 13 }}>{ex.saw}</div>
        {it.entry !== null && <span className="muted num"> · ref {px(it.entry)}, stop {px(it.stop)}{it.target ? `, target ${px(it.target)}` : ""}</span>}
        {room && out && (
          <div className="room-line">
            <span className={`tag ${out.tag}`} title={out.text}>room: {out.label}</span>
            {room.confidence && <span className="muted"> {room.confidence} confidence</span>}
            {it.signal_id !== undefined && onToggle && (
              <> · <button className="linkbtn" aria-expanded={open} onClick={onToggle}>{open ? "hide transcript" : "transcript"}</button></>
            )}
          </div>
        )}</td>
      <td className={`r num ${tone(cf?.pnl_usd)}`}>{cf && cf.pnl_usd !== undefined
        ? <span title={it.mode === "shadow" ? "Counterfactual, never traded" : "Rules-only counterfactual at full size: what the room's veto avoided or missed"}>
            {it.mode === "shadow" ? "" : "would have "}{signedMoney(cf.pnl_usd)} · {fmtR(cf.r_multiple)}</span>
        : taken ? <span className="muted">{it.qty} sh</span> : ""}</td>
    </>
  );
}

export function Tape() {
  const { run, overview } = useRunCtx();
  const [filter, setFilter] = useState<Filter>("all");
  const [open, setOpen] = useState<Set<number>>(new Set());
  const toggle = (id: number) => setOpen((o) => { const n = new Set(o); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const q = useInfiniteQuery({
    queryKey: ["tape", run.id],
    queryFn: ({ pageParam }) => api.tape(run.id, PAGE, pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => (last.length === PAGE ? last[last.length - 1].ts : undefined),
    ...poll(run.kind === "paper" ? 15_000 : false),
  });
  const all = q.data?.pages.flat() ?? [];
  const rows = all.filter((it) => filter === "all" || (filter === "trades" ? it.kind === "fill"
    : filter === "room" ? it.kind === "signal" && !!it.room : it.kind === "signal" && it.mode === filter));
  let lastDay = "";
  return (
    <section className="card" aria-labelledby="tape-h">
      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <h2 id="tape-h" style={{ margin: 0 }}>Tape</h2>
        <span className="muted" style={{ fontSize: 13 }}>Every setup Kaibot saw, what it did, why, and every fill after costs. Newest first.</span>
        <span className="spacer" />
        <div className="tabs" role="tablist" onKeyDown={onTabKeys} aria-label="Show">
          {([["all", "All"], ["trades", "Fills"], ["live", "Setups"], ["room", "Agent room"], ["shadow", "Shadow"]] as [Filter, string][]).map(([k, l]) => (
            <button key={k} {...tabProps(filter === k)} onClick={() => setFilter(k)}>{l}</button>))}
        </div>
      </div>
      {q.isPending ? <Skeleton /> : q.isError ? <ErrorState compact what="The tape" error={q.error} retry={() => q.refetch()} /> : !rows.length ? (
        <Empty>{run.kind === "replay" ? "Replays don't record a tape: only live paper runs do."
          : all.length ? "Nothing of this kind in the loaded part of the tape. Choose All, or load older entries."
          : "Nothing yet. Setups are checked every 5 minutes from 09:35 to 15:45 ET."}</Empty>
      ) : (
        <table className="list" style={{ marginTop: 12 }}>
          <thead><tr><th>Time (ET)</th><th>What</th><th>Stock</th><th>Why</th><th className="r">Result</th></tr></thead>
          <tbody>{rows.map((it, i) => {
            const d = dateEt(it.ts);
            const head = d !== lastDay;
            lastDay = d;
            const sid = it.kind === "signal" ? it.signal_id : undefined;
            const isOpen = sid !== undefined && open.has(sid);
            return (
              <Fragment key={`${it.kind}-${it.ts}-${i}`}>
                <tr style={head ? { borderTop: "2px solid var(--rule)" } : undefined}>
                  <td className="num">{head ? <b>{d} </b> : null}{timeEt(it.ts)}</td>
                  <Line it={it} beats={overview?.heartbeat} open={isOpen} onToggle={sid !== undefined ? () => toggle(sid) : undefined} />
                </tr>
                {isOpen && (
                  <tr className="expand"><td colSpan={5}><div className="expand-inner"><RoomBySignal signalId={sid!} /></div></td></tr>
                )}
              </Fragment>
            );
          })}</tbody>
        </table>
      )}
      {q.hasNextPage && <p><button disabled={q.isFetchingNextPage} onClick={() => q.fetchNextPage()}>{q.isFetchingNextPage ? "Loading…" : "Older"}</button></p>}
    </section>
  );
}
