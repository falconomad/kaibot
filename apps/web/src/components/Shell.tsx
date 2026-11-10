import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, NavLink, Outlet, useOutletContext } from "react-router-dom";
import { api, supa, type Overview, type Run } from "../lib/api";
import { LEVELS, curve } from "../lib/model";
import { pct } from "../lib/fmt";

export interface RunCtx { run: Run; runs: Run[]; overview: Overview | undefined }
export const useRunCtx = () => useOutletContext<RunCtx>();

export function Logo() {
  return (
    <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true">
      <path d="M3 7.5H19" stroke="var(--ink)" strokeWidth="1.8" />
      <path d="M3 14.5H19" stroke="var(--ai)" strokeWidth="1.8" strokeDasharray="1.6 2.4" />
      <path d="M11 7.5V14.5" stroke="var(--sea)" strokeWidth="1.2" />
      <rect x="8.75" y="8.75" width="4.5" height="4.5" fill="var(--sea)" />
    </svg>
  );
}

function Meter({ label, value, frac }: { label: string; value: string; frac: number }) {
  return (
    <div className="meter">
      <div className="row"><span className="cap">{label}</span><span className="num" style={{ fontSize: 12 }}>{value}</span></div>
      <div className="bar" role="meter" aria-label={label} aria-valuenow={Math.round(frac * 100)} aria-valuemin={0} aria-valuemax={100}>
        <i style={{ width: `${Math.min(100, Math.max(0, frac * 100))}%`, background: frac >= 0.8 ? "var(--fail)" : undefined }} />
      </div>
    </div>
  );
}

function engineChip(o: Overview | undefined, run: Run) {
  const beat = o?.heartbeat.find((h) => h.process === "engine");
  if (run.kind === "replay")
    return { dot: "var(--ai)", text: "Replay", sub: "engine not started · M1" };
  if (!beat) return { dot: "var(--stale)", text: "Engine offline", sub: "no heartbeat" };
  const age = (Date.now() - new Date(beat.ts).getTime()) / 1000;
  return age < 120 ? { dot: "var(--sea)", text: "Running", sub: `beat ${Math.round(age)} s ago` }
    : { dot: "var(--warn)", text: "Stale", sub: `last beat ${Math.round(age / 60)} min ago` };
}

export function Shell() {
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.runs, refetchInterval: 30_000 });
  const [picked, setPicked] = useState<number | null>(() => Number(localStorage.getItem("kb.run")) || null);
  const list = runs.data ?? [];
  const run = list.find((r) => r.id === picked) ?? list[0];
  const overview = useQuery({ queryKey: ["overview", run?.id], queryFn: () => api.overview(run!.id), enabled: !!run,
    refetchInterval: run?.kind === "paper" ? 5_000 : false });

  if (runs.isPending) return <div className="center skeleton">Loading runs…</div>;
  if (runs.isError) return <div className="center"><div className="card"><b>Couldn't load runs</b><span className="muted">{String(runs.error)}</span></div></div>;
  if (!run) return (
    <div className="center"><div className="card">
      <b>No runs yet</b><span className="muted">Start a paper run to give Kaibot an amount and a level.</span>
      <Link to="/start"><button className="primary">Start a run</button></Link>
    </div></div>
  );

  const lv = run.level ? LEVELS[run.level] : null;
  const o = overview.data;
  const chip = engineChip(o, run);
  const last = o?.daily.at(-1);
  const c = o ? curve(run.amount_usd, o.daily) : [];
  const nowPot = c.at(-1)?.pot ?? run.amount_usd;
  const peak = Math.max(run.amount_usd, ...c.map((p) => p.pot));
  const dd = ((peak - nowPot) / peak) * 100;
  const dayPct = last ? (last.pnl / (nowPot - last.pnl)) * 100 : 0;

  return (
    <div className="shell">
      <header className="top">
        <Link to="/" className="brand" aria-label="kaibot home"><Logo /><span>kaibot</span></Link>
        <span className="pill">PAPER{lv ? ` · ${lv.name}` : ""}</span>
        <span className="chip"><svg width="10" height="10" aria-hidden="true"><circle cx="5" cy="5" r="4" fill={chip.dot} /></svg>
          {chip.text} <span className="num muted">{chip.sub}</span></span>
        {list.length > 1 && (
          <select aria-label="Run" className="chip" value={run.id}
            onChange={(e) => { setPicked(Number(e.target.value)); localStorage.setItem("kb.run", e.target.value); }}>
            {list.map((r) => <option key={r.id} value={r.id}>{r.label ?? `Run ${r.id}`}</option>)}
          </select>
        )}
        <div className="spacer" />
        {lv && <Meter label={run.kind === "replay" ? "Last day vs stop" : "Day vs stop"}
          value={`${pct(dayPct)} / −${lv.stop.toFixed(2)}%`} frac={dayPct < 0 ? -dayPct / lv.stop : 0} />}
        {lv && <Meter label="Run drawdown" value={`${dd.toFixed(1)} / ${lv.runDd.toFixed(1)}%`} frac={dd / lv.runDd} />}
      </header>
      <div className="body">
        <nav className="side" aria-label="Main">
          <NavLink to="/" end>Live</NavLink>
          <NavLink to="/trades">Trades</NavLink>
          <span title="Designed for M2">Report <small>M2</small></span>
          <span title="Designed for M2">Engine health <small>M2</small></span>
          <span title="Designed for M2">Run &amp; settings <small>M2</small></span>
          <div className="grow" />
          <NavLink to="/start">New run</NavLink>
          <a href="#" onClick={(e) => { e.preventDefault(); supa.auth.signOut(); }}>Sign out</a>
        </nav>
        <main><Outlet context={{ run, runs: list, overview: o } satisfies RunCtx} /></main>
      </div>
      <div className="foot">Paper trading only · simulated money · times ET (CET in brackets)</div>
    </div>
  );
}
