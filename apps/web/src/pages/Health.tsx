import { useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Accordion } from "../components/Accordion";
import { StatusRow, allRed, noDecisionRows } from "../components/Home";
import { Sheet } from "../components/Sheet";
import { engineChip, useRunCtx } from "../components/Shell";
import { api, type Heartbeat } from "../lib/api";
import { ageS, ageText, haltResolved, haltText, scanSummary, sessionTime } from "../lib/derive";
import { engineVersion } from "../lib/engine";
import { commandText, ruleName } from "../lib/explain";
import { Empty, ErrorState, RuleName } from "../components/States";
import { poll } from "../lib/poll";
import { budgetText } from "../lib/roomview";
import { dateEt, day, timeBoth, timeEt } from "../lib/fmt";
import { Skeleton } from "../components/Skeleton";
import { ExperimentSheetBody, HaltTag, engineDetail, experimentsOf } from "../components/Experiment";
import { findExperiment, multiDayFrom } from "../lib/experiment";
import { NAME } from "../brand/brand";
import { caEvent } from "../lib/corpactions";
import { type ShadowReview, shadowLine, shadowReviews } from "../lib/engineevents";
import { reconLine, reconcileFrom } from "../lib/reconcile";
import { ReconcileAccept } from "../components/ReconcileAccept";

const PROC: Record<string, { name: string; what: string; staleS: number }> = {
  engine: { name: "Engine", what: "trades the run: reads prices, decides, places bracket orders", staleS: 90 },
  watchdog: { name: "Watchdog", what: "separate process: cancels entries if the engine goes quiet, flattens at 15:55 ET", staleS: 60 },
};

function Proc({ h }: { h: Heartbeat }) {
  const p = PROC[h.process] ?? { name: h.process, what: "", staleS: 120 };
  const age = ageS(h.ts);
  const d = (h.detail ?? {}) as Record<string, unknown>;
  const ok = age <= p.staleS && d.state !== "stopped" && d.state !== "error";
  return (
    <tr>
      <td><div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <svg width="10" height="10" aria-hidden="true"><circle cx="5" cy="5" r="4" fill={ok ? "var(--sea)" : age > p.staleS ? "var(--stale)" : "var(--warn)"} /></svg>
          <b>{p.name}</b><span className="sr-only">{ok ? "healthy" : "not healthy"}</span></div>
        <div className="muted" style={{ fontSize: 13 }}>{p.what}</div></td>
      <td className="num">{String(d.state ?? "—")}</td>
      <td className="num">{ageText(age)}</td>
      <td className="muted num" style={{ fontSize: 13 }}>
        {d.code_sha ? `code version ${d.code_sha} · ` : ""}{d.host ? `host ${String(d.host).slice(0, 12)} · ` : ""}
        {d.account_clean === false ? "account NOT clean · " : ""}{d.halted ? "entries halted · " : ""}
        {typeof d.engine_heartbeat_age_s === "number" ? `sees engine ${Math.round(d.engine_heartbeat_age_s)} s` : ""}
      </td>
    </tr>
  );
}

const EV: Record<string, string> = {
  engine_start: "Engine started", engine_stop: "Engine stopped", engine_error: "Engine error (retrying)",
  session_start: "Session started", session_end: "Session closed", shadow_session_end: "Shadow strategies scored",
  halt: "Halt", halt_cleared: "Halt cleared at the new session", account_not_clean: "Paper account not clean",
  calendar_stale: "Event calendar out of date", run_paused: "Run paused", moc_refused: "Market-on-close refused",
  watchdog_flatten: "Watchdog flattened", watchdog_recovered: "Engine heartbeat back", shadow_error: "Shadow strategy error",
  // #207: the live daily path (multi-day rules)
  multi_day_book_ended: "Experiment ended: the multi-day book reached its stop line", multi_day_retired: "Experiment ended: rule retired",
  multi_day_reenabled: "Multi-day rule switched back on by you", multi_day_reenable_refused: "Switching a rule back on was refused",
  multi_day_no_decision: "Multi-day rule: no decision today", multi_day_waiting: "Multi-day rule waiting for the daily prices",
  multi_day_error: "Multi-day step failed (no orders sent)", multi_day_overnight_off: "Overnight holding is off: buys skipped",
  multi_day_unfilled: "Order not filled: expired at the close", daily_note: "Daily note written",
  multi_day_review_shadow: "Pre-open reviewer (observer, no effect on trades)",
  // #354: the engine's news poller (heartbeat `news`)
  news_feed_unhealthy: "News feed down or empty (stock summaries and the reviewer see no new news)",
  news_feed_ok: "News feed back",
  // 0033: the engine's health events (#392 item 4)
  multi_day_carry: "Unfilled buys carried to the next session, or skipped",
  corporate_actions_error: "Couldn't read today's dividends and splits",
  ledger_mismatch: "Books differ from the broker",
  reconcile_blocked: "Books differ from the broker: new buys stopped",
  dividend_difference: "Dividend paid differs from the one expected",
  venue_fee_unread: "Couldn't read a trading fee",
  replica_conflict: "The dashboard's copy differs from the engine's record (the engine's is kept)",
  stop_state_fallback: "Stops shown as missing until a database update is applied",
  engine_lease_takeover: "Engine took over from another computer",
  journal_adopted: "Engine started from a damaged local record (you allowed it)",
  order_adopted: "Order left by a crash taken back",
  session_finalized_late: "Session closed late, after a restart",
  watchdog_protect: "Watchdog put protective stops back",
  multi_day_exit_recovered: "Sale sent before a crash found and booked",
  multi_day_shorting_on: "Shorting is on at the broker: multi-day buys held back",
  // 0036: sent under a dynamic name (#410, #131)
  multi_day_column_missing: "Minute prices of multi-day holdings wait for a database update",
  multi_day_review: "Pre-open reviewer (can veto or halve buys)",
  // 0039: M9 engine safety (#437, #435, #442)
  replica_poison: "A record the dashboard's database refuses was skipped (the engine keeps it)",
  replica_backlog: "The dashboard's copy is falling behind the engine's record",
  clock_skew: "The engine's clock against the broker's (new orders wait while they differ)",
  calendar_expiring: "A yearly calendar runs out within 30 days and needs extending",
  // 0039: M9 commands and the book's run end (#427, #440, #426)
  command_waiting: "Flatten and stop, or a new run, is waiting until every position is sold",
  command_rejected: "A command from the dashboard was refused by the engine",
  multi_day_book_unchecked: "Today's prices for some holdings can't be read (the last close is used)",
  multi_day_book_unconfirmed: "Some holdings have had no confirmed price for a while (the last close is used)",
};


const STATE_TAG = { ok: ["allow", "ok"], rules_only: ["downgrade", "rules-only"], no_entries: ["veto", "no entries"] } as const;
const usd = (v: number) => `$${v.toFixed(v < 10 ? 3 : 2)}`;

/** S32/S36: today's LLM spend against the caps. */
function LlmBudgetCard() {
  const q = useQuery({ queryKey: ["llm_budget"], queryFn: api.llmBudget, ...poll(30_000) });
  // Stock summaries (0023): their own caps, spent by the dashboard's /api/summary. Null until 0023 is applied.
  const sq = useQuery({ queryKey: ["summary_spend"], queryFn: api.summarySpend, retry: false, ...poll(60_000) });
  if (q.isPending) return <section className="card"><h2>LLM budget</h2><p className="skeleton" role="status">Loading…</p></section>;
  if (q.isError) return <section className="card"><h2>LLM budget</h2><ErrorState compact what="The LLM budget" error={q.error} retry={() => q.refetch()} /></section>;
  const b = q.data, t = budgetText(b), [cls, label] = STATE_TAG[b.state] ?? STATE_TAG.ok;
  const bar = (frac: number) => <div className="prog bar-row" aria-hidden="true"><i style={{ width: `${Math.min(100, frac * 100)}%`,
    background: frac >= 1 ? "var(--fail)" : frac >= 0.8 ? "var(--warn)" : undefined }} /></div>;
  const purposes = Object.entries(b.by_purpose);
  const ss = sq.data;
  // 0026/0027: one $150 cap on every AI dollar (the engine's calls and the stock summaries together), all time
  const fin = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);
  const life = fin(b.lifetime_usd) ?? fin(ss?.lifetime_usd);
  const lifeCap = fin(b.caps.lifetime_hard) ?? fin(ss?.lifetime_cap) ?? 150;
  const lifeSumm = fin(b.lifetime_summaries_usd);
  const over = fin(ss?.over_reservation_calls) ?? 0;
  return (
    <section className="card" aria-labelledby="llm-h">
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <h2 id="llm-h" style={{ margin: 0 }}>LLM budget</h2><span className={`tag ${cls}`}>{label}</span>
      </div>
      <p style={{ fontSize: 14 }} className={t.tone === "breach" ? "loss" : undefined}>{t.text}</p>
      <div className="kv">
        <span>Today</span><span /><span className="v">{usd(b.day_usd)} / ${b.caps.daily_soft}</span>{bar(b.day_usd / b.caps.daily_soft)}
        <span>This month</span><span /><span className="v">{usd(b.month_usd)} / ${b.caps.monthly_hard}</span>{bar(b.month_usd / b.caps.monthly_hard)}
        <span>Calls today</span><span /><span className="v">{b.calls_today}{b.open_reserved_usd > 0 ? ` · ${usd(b.open_reserved_usd)} reserved` : ""}</span>
        {purposes.length > 0 && (<><span>By purpose</span><span /><span className="v">{purposes.map(([k, v]) => `${k} ${usd(v)}`).join(" · ")}</span></>)}
        {ss && (<><span>Stock summaries</span><span /><span className="v">{usd(ss.day_usd)} / ${ss.day_cap.toFixed(2)} today · {usd(ss.month_usd)} / ${ss.month_cap.toFixed(2)} this month · {ss.cards_today} written today</span></>)}
        {life !== null && (<><span>All time</span><span /><span className="v">{usd(life)} / ${lifeCap}{lifeSumm !== null && lifeSumm > 0 ? ` · ${usd(lifeSumm)} of it stock summaries` : ""}</span>{bar(life / lifeCap)}</>)}
        {over > 0 && (<><span>Above reservation</span><span /><span className="v" style={{ color: "var(--warn-text)" }}>{over} summary call{over === 1 ? "" : "s"} cost more than {over === 1 ? "it" : "they"} reserved</span></>)}
      </div>
      <p className="muted" style={{ fontSize: 13, marginBottom: 0 }}>Above ${b.caps.daily_soft} a day the rules decide alone; ${b.caps.monthly_alert} a month raises an alert;
        at ${b.caps.monthly_hard} no new entries{life !== null ? <>; at ${lifeCap} in all, no AI call at all</> : null}. Each call reserves its worst case first, so the caps can't be overshot.</p>
    </section>
  );
}

function ScanCard({ beat }: { beat: Heartbeat | undefined }) {
  const d = beat?.detail ?? null;
  const { headline, lines } = scanSummary(d?.scan, d?.entries_today ?? 0);
  const near = d?.scan?.recent_near ?? [];
  return (
    <section className="card" aria-labelledby="scan-h">
      <h2 id="scan-h">Today's scan</h2>
      <p style={{ fontWeight: 600, marginTop: 0 }}>{headline}</p>
      <ul style={{ fontSize: 14, margin: "0 0 8px", paddingLeft: 18 }}>{lines.map((l) => <li key={l}>{l}</li>)}</ul>
      {near.length > 0 && (
        <div>
          <table className="list" tabIndex={0} aria-label="Today's scan">
            <thead><tr><th>Near-miss</th><th>Time (ET)</th><th>Dip (σ)</th><th>Why it didn't qualify</th></tr></thead>
            <tbody>{near.map((n, i) => <tr key={`${n.symbol}-${n.m}-${i}`}><td>{n.symbol}</td><td>{sessionTime(n.m)}</td><td>{n.z.toFixed(2)}</td><td>{n.why.replace(/ sigma/g, "σ")}</td></tr>)}</tbody>
          </table>
        </div>
      )}
      <p className="muted" style={{ fontSize: 13, marginBottom: 0 }}>The dip rule (retired, systems test only) buys only a fall of at least 1.5σ that its sector doesn't explain, on calm volume, with no company news in 24 h, after a 5-minute higher low. Quiet up-days often have none.</p>
    </section>
  );
}

/** Which code the engine runs (heartbeat) and the strategy fingerprint it logged at its last start. */
function VersionCard({ h }: { h: HealthData }) {
  const v = engineVersion(h);
  const fp = v.start?.fingerprint;
  return (
    <section className="card" aria-labelledby="ver-h">
      <h2 id="ver-h">Engine version</h2>
      <div className="kv">
        <span>Code version running now</span><span /><span className="v num">{v.running ?? "not reported"}</span>
        <span>Last start</span><span /><span className="v num">{v.start ? `${dateEt(v.start.ts)} ${timeEt(v.start.ts)} ET${v.start.codeSha ? ` · code ${v.start.codeSha}` : ""}` : "not in the recent log"}</span>
        {fp && !fp.unknown && fp.all && (<><span>Strategy fingerprint</span><span /><span className="v num">{fp.all}</span></>)}
        {fp?.core && (<><span>Shared core</span><span /><span className="v num" title="code: core">{fp.core}</span></>)}
        {fp?.rules.map((p) => (
          <span key={p.key} style={{ display: "contents" }}><RuleName code={p.key} exp={findExperiment(p.key, experimentsOf(h.heartbeat))} /><span /><span className="v num">{p.hash}</span></span>
        ))}
      </div>
      {fp?.unknown && <p className="loss" style={{ fontSize: 14 }}>The engine couldn't compute its fingerprint at start, so no trade from this start counts toward a verdict.</p>}
      {v.mismatch && <p className="muted" style={{ fontSize: 14 }}>The running code differs from the code at the last logged start; the start may be older than this log.</p>}
      <p className="muted" style={{ fontSize: 13, marginBottom: 0 }}>Evidence for a rule only counts while its part of the fingerprint (shared core plus the rule's own files) stays the same.
        Changing one rule doesn't restart another rule's count.</p>
    </section>
  );
}

type HealthData = Awaited<ReturnType<typeof api.health>>;

/** v4 Engine (docs/design/mobile_first_v4.md, "Engine"): is it running, and is it safe. The status, a halt, four
 *  safety checks and today first; the technical detail (version, fingerprints, LLM budget, processes, halts, the
 *  operations log) behind one "Technical details" row. */
export function Health() {
  const q = useQuery({ queryKey: ["health"], queryFn: api.health, ...poll(15_000) });
  const { run, overview, risk } = useRunCtx();
  if (q.isPending) return <Skeleton />;
  if (q.isError) return <ErrorState what="Engine health" error={q.error} retry={() => q.refetch()} />;
  const h = q.data;
  const procs = h.heartbeat.filter((b) => b.process in PROC);
  const lastDead = h.deadman.runs[0];
  const engine = h.heartbeat.find((b) => b.process === "engine");
  const watchdog = h.heartbeat.find((b) => b.process === "watchdog");
  const beatTs = engine?.ts;
  const recovered = h.events.filter((e) => e.type === "watchdog_recovered").map((e) => e.ts);
  const chip = engineChip({ ...(overview ?? {}), heartbeat: h.heartbeat } as never, run);
  const d = (engine?.detail ?? {}) as Record<string, unknown>;
  const wd = (watchdog?.detail ?? {}) as Record<string, unknown>;
  const wdOk = !!watchdog && ageS(watchdog.ts) <= PROC.watchdog.staleS;
  const exps = experimentsOf(h.heartbeat).filter((e) => e.experiment);
  const md = multiDayFrom(engineDetail(h.heartbeat));
  const scan = scanSummary(engine?.detail?.scan, engine?.detail?.entries_today ?? 0);
  const recon = reconcileFrom(engine?.detail);
  const shadow = shadowReviews(md, h.events.filter((e) => e.run_id === null || e.run_id === run.id));
  return (
    <>
      <h1 className="page-title">Engine</h1>
      <div className="headline">
        <i className={chip.live ? "ok-dot live-dot" : "ok-dot"} style={{ background: chip.live ? "var(--up)" : "var(--text-3)" }} aria-hidden="true" />
        <span><b>{chip.text}</b><br /><span className="muted">{beatTs ? `Checked in ${ageText(ageS(beatTs))}` : "Has not checked in yet"}</span></span>
      </div>
      <div className="status-rows">
        {risk && risk.severity !== "ok" && (
          <StatusRow tone={risk.severity === "breach" || risk.red?.length ? "breach" : "warn"} phoneDup={allRed(risk)}
            title={risk.severity === "breach" || risk.red?.length ? "Limit or safety halt in force" : "Heads up"}
            text={<>{risk.items[0]}{risk.items.length > 1 ? <span className="muted"> · {risk.items.length - 1} more</span> : null}</>}>
            <ul className="sheet-list">{risk.items.map((t) => <li key={t}>{t}</li>)}</ul>
            <p className="muted">{NAME} applies its limits by itself. Open trades keep their stop at the broker.</p>
          </StatusRow>
        )}
        {noDecisionRows(md)}
      </div>

      <h2 className="section-h">Safety checks</h2>
      <ul className="rows">
        <CheckRow ok={chip.live || chip.asleep} name="Engine" line={!engine ? "never connected" : chip.asleep ? `${chip.text} · ${chip.sub}`
          : `${String(d.state ?? "—")} · ${String(d.host ?? "").slice(0, 16) || "host unknown"}`}>
          <p>{PROC.engine.what}.</p>
          <div className="kv"><span>State</span><span /><span className="v">{String(d.state ?? "—")}</span>
            <span>Last heartbeat</span><span /><span className="v">{beatTs ? ageText(ageS(beatTs)) : "never"}</span>
            {typeof d.code_sha === "string" && <><span>Code version</span><span /><span className="v num">{d.code_sha}</span></>}</div>
        </CheckRow>
        <CheckRow ok={wdOk} name="Watchdog" line={watchdog ? `${String(wd.state ?? "watching")} · ${ageText(ageS(watchdog.ts))}` : "not reporting"}>
          <p>A separate process: it cancels entries if the engine goes quiet and flattens at 15:55 ET.</p>
        </CheckRow>
        <CheckRow ok={h.deadman.armed} name="Safety net" line={h.deadman.armed ? `Armed · ${lastDead ? `last check ${timeEt(lastDead.start)} ET` : "15:56 ET"}` : "Not armed"}>
          <p>If the engine's heartbeat is more than 3 minutes old at 15:56 ET, Supabase closes every paper position by itself, even if your Mac is off.</p>
          <div className="kv">
            <span>Armed (paper keys in Vault)</span><span /><span className={`v ${h.deadman.armed ? "gain" : "loss"}`}>{h.deadman.armed ? "yes" : "no: see docs/ops/failsafe.md"}</span>
            <span>Schedules</span><span /><span className="v">{h.deadman.jobs.filter((j) => j.active).length} active</span>
            <span>Last check</span><span /><span className="v">{lastDead ? `${dateEt(lastDead.start)} ${timeEt(lastDead.start)} ET · ${lastDead.status}` : "not run yet"}</span>
          </div>
        </CheckRow>
        <CheckRow ok={d.account_clean !== false} name="Paper account" line={d.account_clean === false ? `Has positions or orders ${NAME} didn't place` : "Clean"}>
          <p>{d.account_clean === false ? `The paper account holds positions or orders ${NAME} didn't place. Entries stay off until it is clean.`
            : `The paper account holds only what ${NAME} placed.`}</p>
        </CheckRow>
        {recon && (
          <CheckRow ok={recon.ok} name="Books and broker" line={reconLine(recon)}>
            <ReconcileAccept state={recon} engineLive={chip.live} waiting={h.pending_commands.some((c) => c.kind === "accept_reconcile")} />
          </CheckRow>
        )}
      </ul>

      <h2 className="section-h">Today</h2>
      <ul className="rows">
        <SheetRow head="Today's scan" line={scan.headline}><ScanCard beat={engine} /></SheetRow>
        {shadow.length > 0 && <ShadowRow reviews={shadow} />}
        {exps.length > 0 && (
          <SheetRow head={ruleName(exps[0].strategy)} line={exps[0].label}><ExperimentSheetBody e={exps[0]} md={md} /></SheetRow>
        )}
        <li><Link to="/gate" className="row"><span className="row-mid"><b>Go / no-go</b><span className="muted">The ten-year test and the bar for real money</span></span>
          <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M9 6 L15 12 L9 18" /></svg></Link></li>
      </ul>
      {h.pending_commands.length > 0 && <p className="muted" style={{ fontSize: 14, margin: 0 }}>Waiting for the engine: {h.pending_commands.map((c) => commandText(c.kind)).join(", ")}</p>}

      <Accordion title="Technical details" hint="version, processes, LLM budget, halts, log">
        <VersionCard h={h} />
        <section className="card" aria-labelledby="proc-h">
          <h2 id="proc-h">Processes</h2>
          <table className="list" tabIndex={0} aria-label="Processes">
            <thead><tr><th>Process</th><th>State</th><th>Last heartbeat</th><th>Detail</th></tr></thead>
            <tbody>{procs.length ? procs.map((b) => <Proc key={b.process} h={b} />) : <tr><td colSpan={4} className="muted">No process has reported yet.</td></tr>}</tbody>
          </table>
        </section>
        <LlmBudgetCard />
        <section className="card" aria-labelledby="halt-h">
          <h2 id="halt-h">Recent halts</h2>
          {h.halts.length ? (
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 14 }}>{h.halts.slice(0, 12).map((x, i) => (
              <li key={i}><span className="num">{dateEt(x.ts)} {timeEt(x.ts)}</span> <span title={`code: ${x.kind}`}>{haltText(x)}</span><HaltTag x={x} all={h.halts} />
                {haltResolved(x, beatTs, recovered) && <> <span className="tag allow" title="The engine's heartbeat came back after this halt">resolved</span></>}
                {x.reason && <div className="muted" style={{ fontSize: 13 }}>{x.reason}</div>}</li>))}</ul>
          ) : <Empty>No halts recorded.</Empty>}
        </section>
        <section className="card" aria-labelledby="ev-h">
          <h2 id="ev-h">Operations log</h2>
          <table className="list" tabIndex={0} aria-label="Operations log">
            <thead><tr><th>When</th><th>What</th><th>Detail</th></tr></thead>
            <tbody>{!h.events.length && <tr><td colSpan={3} className="muted">Nothing logged yet.</td></tr>}{h.events.map((e, i) => (
              <tr key={i}>
                <td className="num" title={timeBoth(e.ts)}>{dateEt(e.ts)} {timeEt(e.ts)}</td>
                <td title={`code: ${e.type}`}>{eventText(e)}</td>
                <td className="muted num" style={{ fontSize: 13, maxWidth: 560, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {summary(e.payload)}</td>
              </tr>))}</tbody>
          </table>
        </section>
      </Accordion>
    </>
  );
}

/** One safety check: a dot, the name, one line; the detail opens in a sheet. */
function CheckRow({ ok, name, line, children }: { ok: boolean; name: string; line: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <li>
      <button type="button" className="row" aria-haspopup="dialog" onClick={() => setOpen(true)}>
        <i className="ok-dot" style={{ width: 10, height: 10, borderRadius: 999, flex: "none", background: ok ? "var(--up)" : "var(--down)" }} aria-hidden="true" />
        <span className="row-mid"><b>{name}</b><span className="muted">{line}</span></span>
        <span className="sr-only">{ok ? "OK" : "Problem"}</span>
      </button>
      <Sheet open={open} onClose={() => setOpen(false)} title={name}>{children}</Sheet>
    </li>
  );
}

function SheetRow({ head, line, children }: { head: string; line: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <li>
      <button type="button" className="row" aria-haspopup="dialog" onClick={() => setOpen(true)}>
        <span className="row-mid"><b>{head}</b><span className="muted">{line}</span></span>
      </button>
      <Sheet open={open} onClose={() => setOpen(false)} title={head}>{children}</Sheet>
    </li>
  );
}

/** #310: the shadow pre-open reviewer. An observer only: what it would have said, never what happened. */
function ShadowRow({ reviews }: { reviews: ShadowReview[] }) {
  const [open, setOpen] = useState(false);
  const first = reviews[0];
  const when = (r: ShadowReview) => (r.session ? ` · ${day(r.session)}` : "");
  return (
    <li>
      <button type="button" className="row" aria-haspopup="dialog" onClick={() => setOpen(true)}>
        <span className="row-mid"><b>Pre-open reviewer</b>
          <span className="muted">{shadowLine(first)}{reviews.length > 1 ? ` · ${ruleName(first.rule)}` : ""}</span>
          <span className="observer-tag">observer · no effect on trades</span></span>
      </button>
      <Sheet open={open} onClose={() => setOpen(false)} title="Pre-open reviewer">
        <p><span className="observer-tag">observer · no effect on trades</span></p>
        <p>Before the open an AI reviewer reads each multi-day plan and says what it would allow, halve or veto. It only
          watches: the plan, the orders and the fills are the same with it on or off. Its record is kept to judge it later.</p>
        <ul className="shadow-list">{reviews.map((r) => (
          <li key={r.rule}><b>{ruleName(r.rule)}</b><span className="muted">{when(r)}</span><br />{shadowLine(r)}
            {r.reasons.length > 0 && <ul className="sheet-list">{r.reasons.map((x) => (
              <li key={x.symbol}><b>{x.symbol}</b> · {x.verdict === "veto" ? "would veto" : "would halve"}: {x.why}</li>))}</ul>}
            {r.costUsd !== null && r.costUsd > 0 && <div className="muted" style={{ fontSize: 13 }}>AI cost {usd(r.costUsd)}</div>}
          </li>))}</ul>
      </Sheet>
    </li>
  );
}

/** One event in plain words. Halts name their kind; commands say what was asked. */
function eventText(e: HealthData["events"][number]): string {
  if (e.type === "halt" && typeof e.payload?.kind === "string") return `Halt: ${haltText({ kind: e.payload.kind, reason: String(e.payload.reason ?? ""), ts: e.ts })}`;
  if (e.type.startsWith("command_")) return `You asked to ${commandText(e.type.slice("command_".length))}`;
  const ca = caEvent(e); // #266 corporate actions (kb_health lists them once 0021 is applied)
  if (ca) return ca.head;
  return EV[e.type] ?? e.type.replace(/_/g, " ");
}

/** Payload detail: engine_start's code version and fingerprint have their own card, so only their short form shows here. */
function summary(p: Record<string, unknown>) {
  return Object.entries(p ?? {}).filter(([k, v]) => v !== null && typeof v !== "object" && k !== "kind").slice(0, 6)
    .map(([k, v]) => k === "fingerprint" ? `fingerprint: ${String(v).split(";")[0]}` : `${k.replace(/_/g, " ")}: ${String(v)}`).join(" · ");
}
