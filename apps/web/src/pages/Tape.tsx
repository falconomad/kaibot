// v4 Activity (docs/design/mobile_first_v4.md, "Activity"; was Tape): what Kaibot did and passed on, newest first,
// grouped by day. One row per event: what happened, one plain sentence why, one number. A tap opens the row's sheet:
// the full "why", prices, the counterfactual, the agent room's transcript and the trade. Test-only (shadow) rows are
// off by default; the "Test only" chip turns them on (Product, 16 Nov 2026). Today's halts are listed as alerts.
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
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
import { dateEt, day, money, px, signedMoney, timeEt } from "../lib/fmt";
import { ExperimentSheetBody, experimentsOf } from "../components/Experiment";
import { StatusRow } from "../components/Home";
import { Sheet } from "../components/Sheet";
import { Skeleton } from "../components/Skeleton";
import { Empty, ErrorState } from "../components/States";
import { NAME } from "../brand/brand";
import { type CaEvent, caEvents } from "../lib/corpactions";
import { type Unfilled, unfilledDetail, unfilledFrom, unfilledLine, unfilledNext, unfilledText } from "../lib/engineevents";
import { StockBadge, SystemTile } from "../components/StockBadge";
import { useBook } from "../components/useBook";
import { parsePlan, reasonText } from "../lib/multiday";
import { carriedToday, lastPlan, sessionDate } from "../lib/plantoday";
import { carriesFrom } from "../lib/carry";

const PAGE = 100;
type Filter = "all" | "fills" | "skipped" | "room" | "alerts";
const FILTERS: [Filter, string][] = [["all", "All"], ["fills", "Buys & sells"], ["skipped", "Skipped"], ["room", "Agent room"], ["alerts", "Alerts"]];
type Signal = Extract<TapeItem, { kind: "signal" }>;
type Row = { kind: "item"; it: TapeItem; ts: string } | { kind: "halt"; h: Halt; ts: string } | { kind: "ca"; c: CaEvent; ts: string }
  | { kind: "unf"; u: Unfilled; ts: string };
const tone = (v: number | null | undefined) => (!v ? "flat" : v > 0 ? "gain" : "loss");

const taken = (it: Signal) => it.mode === "live" && !!it.approved && !it.veto && (it.qty ?? 0) > 0;
function roomWhy(it: Signal) {
  const room = it.room ?? null;
  if (!room || taken(it)) return null;
  const blocked = room.status === "done" ? room.verdict === "veto" : ["timeout", "invalid", "error"].includes(room.status);
  const out = roomOutcome(room.status, room.verdict);
  return blocked && out ? `${out.label}${room.cause ? `: ${causeText(room.cause)}` : ""}` : null;
}

type Fill = Extract<TapeItem, { kind: "fill" }>;
/** Why a sale sold: a multi-day fill (0050) carries the rule's sell code, an intraday fill the trade's exit reason. */
function fillWhy(it: Fill) {
  if (it.source === "multi_day") return reasonText(it.exit_reason);
  return it.exit_reason ? exitText(it.exit_reason) : "sold";
}

/** What one row says: the word for what happened, the stock, one plain sentence, one number. */
function rowView(it: TapeItem) {
  if (it.kind === "fill") {
    const sold = it.side === "sell";
    // 0050: a multi-day sale has no trade row, so no result: the row shows what it sold for instead
    const result = sold && (it.source !== "multi_day" || it.pnl_usd !== null);
    return { icon: sold ? "sold" : "bought", head: `${sold ? "Sold" : "Bought"} ${it.symbol}`,
      line: sold ? `${fillWhy(it)} · ${it.qty} at ${px(it.venue_px)}` : `${it.qty} at ${px(it.venue_px)} · ${ruleName(it.strategy)}`,
      value: result ? signedMoney(it.pnl_usd) : money(it.qty * it.venue_px), valueTone: result ? tone(it.pnl_usd) : "" };
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
  // #266: buys the multi-day rule left out because of an announced corporate action (engine events, read through
  // kb_health; none until the engine and the RPC send them). The same query as Engine's, so it is shared.
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, enabled: run.kind === "paper", retry: false, ...poll(60_000) });
  const caRows: Row[] = caEvents((health.data?.events ?? []).filter((e) => e.run_id === null || e.run_id === run.id))
    .filter((c) => filter === "all" || (filter === "skipped" ? c.type === "multi_day_ca_skip" : filter === "alerts" ? c.tone !== "note"
      : filter === "fills" ? c.type === "spin_off_adopted" : false))
    .map((c) => ({ kind: "ca", c, ts: c.ts }));
  // #333: an order of the multi-day rule the venue let expire at the close with shares left (kb_health's events)
  const unfRows: Row[] = filter === "all" || filter === "fills"
    ? unfilledFrom(health.data?.events, run.id).map((u) => ({ kind: "unf", u, ts: u.ts })) : [];
  // Product, 24 Nov 2026: a plan whose fill session is over is not shown as planned buys anywhere; here it is one quiet
  // line, how it ended (lib/plantoday.ts). Same queries as Positions', so they are shared.
  const paper = run.kind === "paper";
  const planQ = useQuery({ queryKey: ["md-plan", run.id], queryFn: () => api.multidayPlan(run.id), enabled: paper, retry: false, ...poll(60_000) });
  const { book } = useBook(run);
  const plans = parsePlan(planQ.data), today = sessionDate();
  const last = paper ? lastPlan(plans, unfilledFrom(health.data?.events, run.id), book.data, today,
    carriedToday(plans, carriesFrom(health.data?.events, run.id), today)) : null;
  const all = q.data?.pages.flat() ?? [];
  const halts: Row[] = (overview?.halts_today ?? []).map((h) => ({ kind: "halt", h, ts: h.ts }));
  const items: Row[] = all.filter((it) => testOnly || !(it.kind === "signal" && it.mode === "shadow"))
    .filter((it) => filter === "all" || (filter === "fills" ? it.kind === "fill"
      : filter === "skipped" ? it.kind === "signal" && it.mode === "live" && !taken(it)
      : filter === "room" ? it.kind === "signal" && !!it.room : false))
    .map((it) => ({ kind: "item", it, ts: it.ts }));
  const rows = [...(filter === "all" || filter === "alerts" ? halts : []), ...caRows, ...unfRows, ...items]
    .sort((a, b) => b.ts.localeCompare(a.ts));
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
      {last && <p className="muted last-plan" style={{ margin: "0 0 8px" }}>{last.text}</p>}
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
              : r.kind === "ca" ? caView(r.c) : r.kind === "unf" ? unfView(r.u) : rowView(r.it);
            const exp = r.kind === "item" ? findExperiment(r.it.strategy, exps) : null;
            return (
              <li key={`${r.kind}-${r.ts}-${i}`}>
                {head && <h2 className="day-head">{d}</h2>}
                <button type="button" className={`row ${(r.kind === "halt" && haltTone(r.h.kind) === "breach") || (r.kind === "ca" && r.c.tone === "alert") ? "alert" : ""}`} aria-haspopup="dialog" onClick={() => setOpen(r)}>
                  {/* the company's tile when the row is about a stock; a neutral icon tile otherwise, so the column lines up */}
                  {(() => {
                    const sym = r.kind === "item" ? r.it.symbol : r.kind === "ca" ? r.c.symbol : r.kind === "unf" ? r.u.symbol : null;
                    return sym ? <StockBadge symbol={sym} size={36} />
                      : <SystemTile size={36} tone={v.icon}><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d={ICON[v.icon]} /></svg></SystemTile>;
                  })()}
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
      <Sheet open={open !== null} onClose={() => setOpen(null)} title={open ? (open.kind === "halt" ? "Halt" : open.kind === "ca" ? caView(open.c).head : open.kind === "unf" ? unfilledText(open.u) : rowView(open.it).head) : ""}>
        {open && (open.kind === "halt" ? <HaltSheet h={open.h} /> : open.kind === "ca" ? <CaSheet c={open.c} /> : open.kind === "unf" ? <UnfilledSheet u={open.u} />
          : <ItemSheet it={open.it} exp={findExperiment(open.it.strategy, exps)} />)}
      </Sheet>
    </>
  );
}

/** A corporate-action event as a row: its words come from lib/corpactions. */
function caView(c: CaEvent) {
  return { icon: c.icon, head: c.head, line: `${c.line} · ${timeEt(c.ts)}`, value: "", valueTone: "" };
}

/** #333: not filled at the close. */
function unfView(u: Unfilled) {
  return { icon: "skipped", head: u.symbol, line: `${unfilledLine(u)} · ${timeEt(u.ts)}`, value: "", valueTone: "" };
}

function UnfilledSheet({ u }: { u: Unfilled }) {
  return (
    <>
      <p>{unfilledDetail(u)}</p>
      <p className="muted">{unfilledNext(u)} A near-close order that is not filled by 16:00 ET expires.</p>
      <p className="muted" style={{ fontSize: 15 }}>{ruleName(u.rule)}{u.session ? ` · close of ${day(u.session)}` : ""} · reported {dateEt(u.ts)} {timeEt(u.ts)} ET</p>
    </>
  );
}

function CaSheet({ c }: { c: CaEvent }) {
  return <p>{c.detail}</p>;
}

function HaltSheet({ h }: { h: Halt }) {
  return (
    <>
      <p>{haltText(h)}</p>
      {h.reason && <p className="muted" style={{ fontSize: 15 }}>{h.reason}</p>}
      <p className="muted" style={{ fontSize: 15 }}>{dateEt(h.ts)} {timeEt(h.ts)} ET · <Link to="/health">Engine health</Link></p>
    </>
  );
}

function ItemSheet({ it, exp }: { it: TapeItem; exp: Experiment | null }) {
  const label = exp ? <p style={{ margin: "0 0 12px" }}><span className="tag info exp-pill">{experimentView(exp).text}</span></p> : null;
  if (it.kind === "fill") {
    // 0050: a multi-day fill has no shadow price or fees unless the engine sends them; those lines are left out
    const md = it.source === "multi_day";
    const cost = it.shadow_px !== null ? Math.abs(it.venue_px - it.shadow_px) * it.qty + (it.fees ?? 0) : null;
    return (
      <>
        {label}
        <div className="kv">
          <span>{it.side === "buy" ? "Bought" : "Sold"}</span><span /><span className="v num">{it.qty} at {px(it.venue_px)}</span>
          {md && <><span>{it.side === "buy" ? "Cost" : "Proceeds"}</span><span /><span className="v num">{money(it.qty * it.venue_px)}</span></>}
          {it.shadow_px !== null && <><span>Shadow price</span><span /><span className="v num">{px(it.shadow_px)}</span></>}
          {it.fees !== null && <><span>Fees</span><span /><span className="v num">{money(it.fees)}</span></>}
          {cost !== null && <><span>Costs vs the shadow model</span><span /><span className="v num">{money(cost)}</span></>}
          <span>Rule</span><span /><span className="v">{ruleName(it.strategy)}</span>
          {it.side === "sell" && <><span>Why it sold</span><span /><span className="v">{md ? fillWhy(it) : exitText(it.exit_reason)}</span></>}
          {it.side === "sell" && (!md || it.pnl_usd !== null) && <><span>Result</span><span /><span className={`v num ${tone(it.pnl_usd)}`}>{signedMoney(it.pnl_usd)}</span></>}
        </div>
        {md && it.side === "sell" && <p className="muted" style={{ fontSize: 14 }}>The result of the holding is under <Link to="/positions">Positions</Link>, Closed.</p>}
        <p className="muted" style={{ fontSize: 14 }}>{dateEt(it.ts)} {timeEt(it.ts)} ET</p>
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
      {ex.detail && <p className="muted" style={{ fontSize: 14 }} title="engine codes">{ex.detail}</p>}
      {it.signal_id !== undefined && it.room && <RoomBySignal signalId={it.signal_id} />}
      {it.trade_id && <Link to={`/positions/${it.trade_id}`} className="btn sheet-cta">Full trade details</Link>}
    </>
  );
}
