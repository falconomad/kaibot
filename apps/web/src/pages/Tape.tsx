// v4 Activity (docs/design/mobile_first_v4.md, "Activity"; was Tape): what Kaibot did and passed on, newest first,
// grouped by day. One row per event: what happened, one plain sentence why, one number. A tap opens the row's sheet:
// the full "why", prices, the counterfactual, the agent room's transcript and the trade. Test-only (shadow) rows are
// off by default; the "Test only" chip turns them on (Product, 16 Nov 2026). Today's halts are listed as alerts.
import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useRunCtx } from "../components/Shell";
import { poll } from "../lib/poll";
import { api, type Halt, type TapeItem } from "../lib/api";
import { RoomBySignal } from "../components/Room";
import { fmtR, haltText } from "../lib/derive";
import { roomOutcome } from "../lib/roomview";
import { causeText, exitText, explainSignal, ruleName } from "../lib/explain";
import { type Experiment, experimentView, findExperiment, haltTone } from "../lib/experiment";
import { dateEt, money, px, signedMoney, timeEt } from "../lib/fmt";
import { ExperimentSheetBody, experimentsOf } from "../components/Experiment";
import { StatusRow } from "../components/Home";
import { Sheet } from "../components/Sheet";
import { Skeleton } from "../components/Skeleton";
import { Empty, ErrorState } from "../components/States";
import { NAME } from "../brand/brand";

const PAGE = 100;
type Filter = "all" | "fills" | "skipped" | "room" | "alerts";
const FILTERS: [Filter, string][] = [["all", "All"], ["fills", "Buys & sells"], ["skipped", "Skipped"], ["room", "Agent room"], ["alerts", "Alerts"]];
type Signal = Extract<TapeItem, { kind: "signal" }>;
type Row = { kind: "item"; it: TapeItem; ts: string } | { kind: "halt"; h: Halt; ts: string };
const tone = (v: number | null | undefined) => (!v ? "flat" : v > 0 ? "gain" : "loss");

const taken = (it: Signal) => it.mode === "live" && !!it.approved && !it.veto && (it.qty ?? 0) > 0;
function roomWhy(it: Signal) {
  const room = it.room ?? null;
  if (!room || taken(it)) return null;
  const blocked = room.status === "done" ? room.verdict === "veto" : ["timeout", "invalid", "error"].includes(room.status);
  const out = roomOutcome(room.status, room.verdict);
  return blocked && out ? `${out.label}${room.cause ? `: ${causeText(room.cause)}` : ""}` : null;
}

/** What one row says: the word for what happened, the stock, one plain sentence, one number. */
function rowView(it: TapeItem) {
  if (it.kind === "fill") {
    const sold = it.side === "sell";
    return { icon: sold ? "sold" : "bought", head: `${sold ? "Sold" : "Bought"} ${it.symbol}`,
      line: sold ? `${it.exit_reason ? exitText(it.exit_reason) : "sold"} · ${it.qty} at ${px(it.venue_px)}` : `${it.qty} at ${px(it.venue_px)} · ${ruleName(it.strategy)}`,
      value: sold ? signedMoney(it.pnl_usd) : money(it.qty * it.venue_px), valueTone: sold ? tone(it.pnl_usd) : "" };
  }
  const ex = explainSignal(it, { taken: taken(it), roomWhy: roomWhy(it) });
  const cf = it.counterfactual ?? (!taken(it) ? it.room?.counterfactual ?? null : null);
  const word = it.mode === "shadow" ? "Test" : taken(it) ? "Took" : "Skipped";
  return { icon: it.mode === "shadow" ? "test" : taken(it) ? "took" : "skipped", head: `${word} ${it.symbol}`,
    line: ex.outcome.replace(/^Not bought: /, "").replace(/\.$/, ""),
    value: cf?.pnl_usd !== undefined ? `${it.mode === "shadow" ? "" : "would have "}${signedMoney(cf.pnl_usd)}` : taken(it) ? `${it.qty} sh` : "",
    valueTone: cf?.pnl_usd !== undefined ? tone(cf.pnl_usd) : "" };
}

const ICON: Record<string, string> = {
  bought: "M12 19 V5 M6 11 L12 5 L18 11", sold: "M12 5 V19 M6 13 L12 19 L18 13", took: "M5 12 L10 17 L19 7",
  skipped: "M7 7 L17 17 M17 7 L7 17", test: "M9 3 H15 M10 3 V9 L5 19 H19 L14 9 V3", halt: "M9 6 V18 M15 6 V18",
};

export function Tape() {
  const { run, overview } = useRunCtx();
  const [filter, setFilter] = useState<Filter>("all");
  const [testOnly, setTestOnly] = useState(false);
  const [open, setOpen] = useState<Row | null>(null);
  const q = useInfiniteQuery({
    queryKey: ["tape", run.id],
    queryFn: ({ pageParam }) => api.tape(run.id, PAGE, pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => (last.length === PAGE ? last[last.length - 1].ts : undefined),
    ...poll(run.kind === "paper" ? 15_000 : false),
  });
  const exps = experimentsOf(overview?.heartbeat);
  const all = q.data?.pages.flat() ?? [];
  const halts: Row[] = (overview?.halts_today ?? []).map((h) => ({ kind: "halt", h, ts: h.ts }));
  const items: Row[] = all.filter((it) => testOnly || !(it.kind === "signal" && it.mode === "shadow"))
    .filter((it) => filter === "all" || (filter === "fills" ? it.kind === "fill"
      : filter === "skipped" ? it.kind === "signal" && it.mode === "live" && !taken(it)
      : filter === "room" ? it.kind === "signal" && !!it.room : false))
    .map((it) => ({ kind: "item", it, ts: it.ts }));
  const rows = [...(filter === "all" || filter === "alerts" ? halts : []), ...items].sort((a, b) => b.ts.localeCompare(a.ts));
  const expShown = exps.find((e) => e.experiment && rows.some((r) => r.kind === "item" && findExperiment(r.it.strategy, [e]))) ?? null;
  const shadowN = all.filter((it) => it.kind === "signal" && it.mode === "shadow").length;
  let lastDay = "";
  return (
    <>
      <section aria-labelledby="act-h" className="pos-head">
        <h1 id="act-h" className="page-title">Activity</h1>
        <p className="muted desk-only" style={{ margin: 0 }}>What {NAME} did, and what it passed on.</p>
        <div className="chips chips-scroll" role="group" aria-label="Show">
          {FILTERS.map(([k, l]) => <button key={k} type="button" className="chip-btn" aria-pressed={filter === k} onClick={() => setFilter(k)}>{l}</button>)}
          <button type="button" className="chip-btn" aria-pressed={testOnly} onClick={() => setTestOnly(!testOnly)}
            title="Rules that are only measured, never traded">Test only{shadowN ? ` · ${shadowN}` : ""}</button>
        </div>
      </section>
      {expShown && (
        <StatusRow tone="exp" title={ruleName(expShown.strategy)} text={expShown.label}>
          <ExperimentSheetBody e={expShown} />
        </StatusRow>
      )}
      {q.isPending ? <Skeleton /> : q.isError ? <ErrorState compact what="The activity" error={q.error} retry={() => q.refetch()} /> : !rows.length ? (
        <Empty>{run.kind === "replay" ? "Replays don't record activity: only live paper runs do."
          : all.length ? "Nothing of this kind in the loaded part. Choose All, or load older entries."
          : "Nothing yet. Setups are checked every 5 minutes from 09:35 to 15:45 ET."}</Empty>
      ) : (
        <ul className="rows act-rows" aria-label="Activity">
          {rows.map((r, i) => {
            const d = dateEt(r.ts), head = d !== lastDay;
            lastDay = d;
            const v = r.kind === "halt"
              ? { icon: "halt", head: haltText(r.h), line: `${haltTone(r.h.kind) === "breach" ? "Limit or safety halt" : "Note"} · ${timeEt(r.ts)}`, value: "", valueTone: "" }
              : rowView(r.it);
            const exp = r.kind === "item" ? findExperiment(r.it.strategy, exps) : null;
            return (
              <li key={`${r.kind}-${r.ts}-${i}`}>
                {head && <h2 className="day-head">{d}</h2>}
                <button type="button" className={`row ${r.kind === "halt" && haltTone(r.h.kind) === "breach" ? "alert" : ""}`} aria-haspopup="dialog" onClick={() => setOpen(r)}>
                  <span className={`row-ico ${v.icon}`} aria-hidden="true"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d={ICON[v.icon]} /></svg></span>
                  <span className="row-mid"><b>{v.head}</b><span className="muted">{v.line}{r.kind === "item" ? ` · ${timeEt(r.ts)}` : ""}</span>
                    {exp && <span className="row-tag" aria-label={exp.label} title={exp.label}>Experiment</span>}</span>
                  {v.value && <span className={`row-end num ${v.valueTone}`}><b>{v.value}</b></span>}
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {q.hasNextPage && <p><button disabled={q.isFetchingNextPage} onClick={() => q.fetchNextPage()}>{q.isFetchingNextPage ? "Loading…" : "Older"}</button></p>}
      <Sheet open={open !== null} onClose={() => setOpen(null)} title={open ? (open.kind === "halt" ? "Halt" : rowView(open.it).head) : ""}>
        {open && (open.kind === "halt" ? <HaltSheet h={open.h} /> : <ItemSheet it={open.it} exp={findExperiment(open.it.strategy, exps)} />)}
      </Sheet>
    </>
  );
}

function HaltSheet({ h }: { h: Halt }) {
  return (
    <>
      <p>{haltText(h)}</p>
      {h.reason && <p className="muted" style={{ fontSize: 14 }}>{h.reason}</p>}
      <p className="muted" style={{ fontSize: 14 }}>{dateEt(h.ts)} {timeEt(h.ts)} ET · <Link to="/health">Engine health</Link></p>
    </>
  );
}

function ItemSheet({ it, exp }: { it: TapeItem; exp: Experiment | null }) {
  const label = exp ? <p style={{ margin: "0 0 12px" }}><span className="tag info exp-pill">{experimentView(exp).text}</span></p> : null;
  if (it.kind === "fill") {
    const cost = Math.abs(it.venue_px - it.shadow_px) * it.qty + it.fees;
    return (
      <>
        {label}
        <div className="kv">
          <span>{it.side === "buy" ? "Bought" : "Sold"}</span><span /><span className="v num">{it.qty} at {px(it.venue_px)}</span>
          <span>Shadow price</span><span /><span className="v num">{px(it.shadow_px)}</span>
          <span>Fees</span><span /><span className="v num">{money(it.fees)}</span>
          <span>Costs vs the shadow model</span><span /><span className="v num">{money(cost)}</span>
          <span>Rule</span><span /><span className="v">{ruleName(it.strategy)}</span>
          {it.side === "sell" && <><span>Why it sold</span><span /><span className="v">{exitText(it.exit_reason)}</span></>}
          {it.side === "sell" && <><span>Result</span><span /><span className={`v num ${tone(it.pnl_usd)}`}>{signedMoney(it.pnl_usd)}</span></>}
        </div>
        <p className="muted" style={{ fontSize: 13 }}>{dateEt(it.ts)} {timeEt(it.ts)} ET</p>
        {it.trade_id && <Link to={`/positions/${it.trade_id}`} className="btn sheet-cta">Full trade details</Link>}
      </>
    );
  }
  const ex = explainSignal(it, { taken: taken(it), roomWhy: roomWhy(it) });
  const cf = it.counterfactual ?? (!taken(it) ? it.room?.counterfactual ?? null : null);
  return (
    <>
      {label}
      <p>{ex.outcome}</p>
      <p className="muted">{ex.saw}</p>
      <div className="kv">
        {it.entry !== null && <><span>Reference price</span><span /><span className="v num">{px(it.entry)}</span></>}
        {it.stop !== null && <><span>Stop</span><span /><span className="v num">{px(it.stop)}</span></>}
        {it.target !== null && <><span>Target</span><span /><span className="v num">{px(it.target)}</span></>}
        {cf?.pnl_usd !== undefined && <><span>{it.mode === "shadow" ? "Result if traded" : "Would have made"}</span><span /><span className={`v num ${tone(cf.pnl_usd)}`}>{signedMoney(cf.pnl_usd)} · {fmtR(cf.r_multiple)}</span></>}
      </div>
      {ex.detail && <p className="muted" style={{ fontSize: 13 }} title="engine codes">{ex.detail}</p>}
      {it.signal_id !== undefined && it.room && <RoomBySignal signalId={it.signal_id} />}
      {it.trade_id && <Link to={`/positions/${it.trade_id}`} className="btn sheet-cta">Full trade details</Link>}
    </>
  );
}
