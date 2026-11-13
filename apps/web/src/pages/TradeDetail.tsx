import { useQuery } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { RoomTranscript } from "../components/Room";
import { onTabKeys, tabProps } from "../lib/a11y";
import { api, type Trade, type TradeDetail } from "../lib/api";
import { R, dateEt, money, px, signedMoney, timeBoth, timeEt, tone } from "../lib/fmt";
import { RULES, bindingText, checkText, exitText, ruleName } from "../lib/explain";
import { ErrorState, RuleName } from "../components/States";
import { STALE } from "../lib/poll";
import { Skeleton } from "../components/Skeleton";
import { StockBadge, companyName } from "../components/StockBadge";
import { RuleExperiment, experimentsOf } from "../components/Experiment";
import { useRunCtx } from "../components/Shell";
import { type Experiment, findExperiment } from "../lib/experiment";

// Price ladder: entry, stop, target and exit on one vertical price scale.
function Ladder({ t }: { t: Trade }) {
  const pts = [
    { k: "target", v: t.target_px, c: "var(--gain)", l: "target" },
    { k: "entry", v: t.entry_px, c: "var(--ink)", l: "entry" },
    { k: "stop", v: t.stop_px, c: "var(--loss)", l: "stop" },
    { k: "exit", v: t.exit_px, c: "var(--sea)", l: "exit" },
  ].filter((p) => p.v !== null) as { k: string; v: number; c: string; l: string }[];
  const lo = Math.min(...pts.map((p) => p.v)), hi = Math.max(...pts.map((p) => p.v));
  const H = 200, pad = 20, y = (v: number) => pad + (1 - (v - lo) / (hi - lo || 1)) * (H - 2 * pad);
  // stagger labels that would overlap
  const placed = [...pts].sort((a, b) => y(a.v) - y(b.v)).map((p) => ({ ...p, ly: y(p.v) }));
  for (let i = 1; i < placed.length; i++) if (placed[i].ly - placed[i - 1].ly < 16) placed[i].ly = placed[i - 1].ly + 16;
  return (
    <svg viewBox={`0 0 420 ${H + 10}`} style={{ width: "100%", maxWidth: 460 }} role="img"
      aria-label={`Entry ${px(t.entry_px)}, stop ${px(t.stop_px)}, target ${px(t.target_px)}, exit ${px(t.exit_px)}`}>
      {t.stop_px !== null && <rect x="40" y={Math.min(y(t.entry_px), y(t.stop_px))} width="120" height={Math.abs(y(t.stop_px) - y(t.entry_px))} fill="var(--loss)" opacity="0.10" />}
      {t.target_px !== null && <rect x="40" y={Math.min(y(t.entry_px), y(t.target_px))} width="120" height={Math.abs(y(t.target_px) - y(t.entry_px))} fill="var(--gain)" opacity="0.10" />}
      {placed.map((p) => (
        <g key={p.k}>
          <line x1="40" x2="170" y1={y(p.v)} y2={y(p.v)} stroke={p.c} strokeWidth={p.k === "exit" ? 2 : 1.5} strokeDasharray={p.k === "exit" ? "4 3" : undefined} />
          <path d={`M170,${y(p.v)} L190,${p.ly}`} stroke="var(--rule)" fill="none" />
          <text x="196" y={p.ly + 4} fontSize="12" fill="var(--ink)" className="num">{p.l} {px(p.v)}</text>
        </g>
      ))}
    </svg>
  );
}

const TABS = [["why", "Why"], ["gate", "Gate checks"], ["room", "Agent room"], ["fills", "Fills & costs"]] as const;

function NotRecorded({ what, replay }: { what: string; replay: boolean }) {
  return <p className="muted">{replay
    ? `Not part of the replay. ${what} only exists for live paper trades: the agent room isn't replayed on past data, because a model trained on later news would see the future.`
    : `No ${what.toLowerCase()} was recorded for this trade.`}</p>;
}

function Tab({ id, d, exp }: { id: (typeof TABS)[number][0]; d: TradeDetail; exp?: Experiment | null }) {
  const t = d.trade, replay = d.run.kind === "replay";
  if (id === "why") {
    const LABELS: Record<string, string> = { z: "Drop since the open (σ)", resid_z: "Stock-specific part (σ)",
      turnover: "Volume vs normal for this time", news_24h: "News items in 24 h", vix: "VIX (previous close)",
      atr5: "5-minute ATR ($)", vwap: "VWAP ($)", quote_age_s: "Quote age (s)", room: "Agent room" };
    const f = Object.fromEntries(Object.entries(t.features ?? {}).filter(([k]) => k in LABELS)
      .map(([k, v]) => [LABELS[k], typeof v === "number" ? Number(v.toFixed(3)) : v]));
    return (
      <div className="kv" style={{ gridTemplateColumns: "1fr auto" }}>
        <span>Rule</span><span className="v"><RuleName code={t.strategy} exp={exp} limits />{RULES[t.strategy] && <div className="muted" style={{ fontSize: 12, fontWeight: 400 }}>{RULES[t.strategy].what}</div>}</span>
        <span>Exit</span><span className="v" title={t.exit_reason ? `code: ${t.exit_reason}` : undefined}>{exitText(t.exit_reason)}{t.exit_ts ? ` · ${timeEt(t.exit_ts)} ET` : ""}</span>
        <span>Risk (1R)</span><span className="v">{money(t.risk_usd)}</span>
        <span>Result</span><span className={`v ${tone(t.pnl_usd)}`}>{signedMoney(t.pnl_usd)} · {R(t.pnl_usd, t.risk_usd)}</span>
        {Object.entries(f).map(([k, v]) => <Fragment key={k}><span>{k}</span><span className="v">{String(v)}</span></Fragment>)}
        {replay && <span className="muted" style={{ gridColumn: "1 / -1", fontSize: 12 }}>Replay trades store prices and the exit only. Live trades also store every feature the rules used (z-score, residual, turnover, news check, quality score).</span>}
      </div>
    );
  }
  if (id === "gate") {
    if (!d.gate) return <NotRecorded what="The gate record" replay={replay} />;
    const rows = Object.entries(d.gate.checks as Record<string, { state: string; actual: unknown; limit: unknown }>);
    return (
      <>
        <p className="muted" style={{ marginTop: 0, fontSize: 13 }}>{d.gate.qty} shares; the size was limited by <span title={d.gate.binding ? `code: ${d.gate.binding}` : undefined}>{bindingText(d.gate.binding)}</span>.</p>
        <p className="muted num" style={{ marginTop: 0, fontSize: 12 }}>policy {d.gate.policy_hash} · code version {d.gate.code_sha}</p>
        <table className="list"><thead><tr><th>Check</th><th>Result</th><th className="r">Actual</th><th className="r">Limit</th></tr></thead>
          <tbody>{rows.map(([k, v]) => (
            <tr key={k}><td title={`code: ${k}`}>{checkText(k)}</td>
              <td className={v.state === "pass" ? "" : v.state === "warn" ? "loss" : "loss"} style={{ color: v.state === "pass" ? "var(--pass)" : v.state === "warn" ? "var(--warn)" : "var(--fail)" }}>
                {v.state === "pass" ? "✓ pass" : v.state === "warn" ? "▲ warn" : "✕ fail"}</td>
              <td className="r num">{String(v.actual)}</td><td className="r num">{String(v.limit)}</td></tr>
          ))}</tbody>
        </table>
      </>
    );
  }
  if (id === "room") {
    if (!d.room) return replay ? <NotRecorded what="The agent room's debate" replay /> :
      <p className="muted">No room session for this trade. Only dip-rule candidates that pass every rule go to the room (the SPY late-day rule never uses it);
        trades from before M3, or with the room switched off, are rules-only. The room can only ever veto or halve a trade, never size or price it.</p>;
    return <RoomTranscript s={d.room} trade={{ id: t.id, qty: t.qty, pnl_usd: t.pnl_usd, risk_usd: t.risk_usd, exit_reason: t.exit_reason, exit_ts: t.exit_ts }} />;
  }
  if (!d.fills.length)
    return <p className="muted">{replay ? "Replay fills use the shadow cost model only: next-bar open at the modelled bid/ask plus impact, and SEC/FINRA fees on sells. No broker fills exist for a replay." : "No fills recorded."}</p>;
  return (
    <table className="list"><thead><tr><th>Time</th><th>Side</th><th className="r">Qty</th><th className="r">Broker</th><th className="r">Shadow</th><th className="r">Fees</th></tr></thead>
      <tbody>{d.fills.map((f, i) => <tr key={i}><td className="num">{timeEt(f.ts)}</td><td>{f.side}</td><td className="r num">{f.qty}</td>
        <td className="r num">{px(Number(f.venue_px))}</td><td className="r num">{px(Number(f.shadow_px))}</td><td className="r num">{money(Number(f.fees))}</td></tr>)}</tbody>
    </table>
  );
}

export function TradeDetailPage() {
  const { id } = useParams();
  const beats = useRunCtx()?.overview?.heartbeat;
  const [params] = useSearchParams();
  const initial = TABS.find(([k]) => k === params.get("tab"))?.[0] ?? "why";
  const [tab, setTab] = useState<(typeof TABS)[number][0]>(initial);
  // A closed trade no longer changes: keep it for minutes instead of refetching on every focus.
  const q = useQuery({ queryKey: ["trade", id], queryFn: () => api.trade(Number(id)),
    staleTime: (qq) => (qq.state.data?.trade.exit_ts ? STALE.daily : STALE.default) });
  if (q.isPending) return <Skeleton variant="detail" />;
  if (q.isError) return <ErrorState what="This trade" error={q.error} retry={() => q.refetch()} />;
  if (!q.data) return <section className="card"><p style={{ marginTop: 0 }}>Trade not found. It may belong to a run you can't see.</p><Link to="/trades">All trades</Link></section>;
  const d = q.data, t = d.trade;
  return (
    <>
      <Link to="/">← Home</Link>
      <section className="card">
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "baseline" }}>
          <span style={{ display: "flex", alignItems: "center", gap: 14 }}><StockBadge symbol={t.symbol} size={52} /><span style={{ display: "flex", flexDirection: "column" }}><h1 style={{ margin: 0, fontSize: 28, fontWeight: 700, letterSpacing: "-0.02em" }}>{companyName(t.symbol)}</h1><span className="muted" style={{ fontSize: 14 }}>{t.symbol} · <span title={`code: ${t.strategy}`}>{ruleName(t.strategy)}</span> · {t.exit_ts ? "closed" : "open"}<RuleExperiment code={t.strategy} beats={beats} /></span></span></span>
          <span className={`num ${tone(t.pnl_usd)}`} style={{ fontSize: 18 }}>{signedMoney(t.pnl_usd)} · {R(t.pnl_usd, t.risk_usd)}</span>
        </div>
        <p className="muted num" style={{ margin: "4px 0 0" }}>
          {dateEt(t.entry_ts)} · in {timeBoth(t.entry_ts)}{t.exit_ts ? ` · out ${timeEt(t.exit_ts)} ET` : ""} · {t.qty} sh · {d.run.label ?? `run ${d.run.id}`}
        </p>
      </section>
      <div className="grid trade-grid">
        <section className="card"><h2>Price levels</h2><Ladder t={t} /></section>
        <section className="card">
          <div className="tabs" role="tablist" aria-label="Trade details" onKeyDown={onTabKeys}>
            {TABS.map(([k, l]) => <button key={k} id={`tab-${k}`} aria-controls="trade-panel" {...tabProps(tab === k)} onClick={() => setTab(k)}>{l}</button>)}
          </div>
          <div role="tabpanel" id="trade-panel" aria-labelledby={`tab-${tab}`} tabIndex={0} style={{ paddingTop: 12 }}><Tab id={tab} d={d} exp={findExperiment(t.strategy, experimentsOf(beats))} /></div>
        </section>
      </div>
    </>
  );
}
