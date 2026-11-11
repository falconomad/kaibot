import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, NavLink, Outlet, useOutletContext } from "react-router-dom";
import { api, supa, type Overview, type Run } from "../lib/api";
import { LEVELS, curve } from "../lib/model";
import { pct } from "../lib/fmt";
import { riskStatus } from "../lib/derive";
import { Skeleton } from "./Skeleton";

export interface RunCtx { run: Run; runs: Run[]; overview: Overview | undefined }
export const useRunCtx = () => useOutletContext<RunCtx>();

export function Logo({ size = 26 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 28 28" aria-hidden="true">
      <rect x="1" y="1" width="26" height="26" rx="8" fill="#F4F5F7" />
      <path d="M8 18 L12.5 12.5 L15.5 15.5 L20 9.5" stroke="#0A0B0D" strokeWidth="2.4" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

const NAV: { to: string; label: string; short: string; icon: string; tab?: boolean }[] = [
  { to: "/", label: "Home", short: "Home", icon: "M3 11 L12 4 L21 11 V20 H14 V14 H10 V20 H3 Z", tab: true },
  { to: "/trades", label: "Trades", short: "Trades", icon: "M4 7 H20 M4 12 H20 M4 17 H14", tab: true },
  { to: "/tape", label: "Tape", short: "Tape", icon: "M4 6 H20 M4 10 H16 M4 14 H20 M4 18 H12", tab: true },
  { to: "/report", label: "Report", short: "Report", icon: "M5 20 V12 M12 20 V5 M19 20 V9", tab: true },
  { to: "/health", label: "Engine", short: "Engine", icon: "M3 12 H7 L10 5 L14 19 L17 12 H21" },
  { to: "/settings", label: "Run & settings", short: "Run", icon: "M12 9 A3 3 0 1 0 12 15 A3 3 0 1 0 12 9 M12 3 V6 M12 18 V21 M3 12 H6 M18 12 H21", tab: true },
];

function TabBar() {
  return (
    <nav className="tabbar" aria-label="Main">
      {NAV.filter((n) => n.tab).map((n) => (
        <NavLink key={n.to} to={n.to} end={n.to === "/"}>
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={n.icon} /></svg>
          {n.short}
        </NavLink>
      ))}
    </nav>
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

const STATE_TEXT: Record<string, string> = {
  trading: "Running", managing: "Running · no new entries", market_closed: "Market closed", idle: "Idle",
  starting: "Starting", error: "Error, retrying", preparing: "Loading price history",
};

export function engineChip(o: Overview | undefined, run: Run) {
  const beat = o?.heartbeat.find((h) => h.process === "engine");
  if (run.kind === "replay") return { dot: "var(--ai)", text: "Replay", sub: "past data", live: false };
  if (!beat) return { dot: "var(--stale)", text: "Engine offline", sub: "never connected", live: false };
  const age = (Date.now() - new Date(beat.ts).getTime()) / 1000;
  const state = beat.detail?.state ?? "unknown";
  if (state === "stopped" || age > 90)
    return { dot: "var(--stale)", text: "Engine offline", sub: `last seen ${ago(age)}`, live: false };
  return { dot: state === "error" ? "var(--warn)" : "var(--sea)", text: STATE_TEXT[state] ?? state,
    sub: `${beat.detail?.host ?? ""} · ${Math.round(age)} s ago`, live: true };
}

function ago(s: number) {
  return s < 120 ? `${Math.round(s)} s ago` : s < 7200 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`;
}

/** Paper runs: pot now = worse of broker and shadow equity in the latest ledger row (today), else last close. */
const etDay = (d: Date) => new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(d);

export function livePot(run: Run, o: Overview | undefined) {
  const today = etDay(new Date());
  const daily = o?.daily ?? [];
  const before = run.amount_usd + daily.filter((d) => d.session < today).reduce((a, d) => a + d.pnl, 0);
  const closedAll = run.amount_usd + daily.reduce((a, d) => a + d.pnl, 0);
  const l = o?.ledger;
  const fresh = !!l && etDay(new Date(l.ts)) === today;
  const pot = fresh ? Math.min(l!.equity_venue, l!.equity_shadow) : closedAll;
  const dayStart = fresh ? before : pot;
  return { pot, dayPnl: pot - dayStart, dayPct: ((pot - dayStart) / dayStart) * 100, positions: fresh ? l!.positions : {} };
}

export function Shell() {
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.runs, refetchInterval: 30_000 });
  const [picked, setPicked] = useState<number | null>(() => Number(localStorage.getItem("kb.run")) || null);
  const list = runs.data ?? [];
  const run = list.find((r) => r.id === picked) ?? list[0];
  const overview = useQuery({ queryKey: ["overview", run?.id], queryFn: () => api.overview(run!.id), enabled: !!run,
    refetchInterval: run?.kind === "paper" ? 5_000 : false });

  if (runs.isPending) return <div className="shell"><main><Skeleton variant="dashboard" /></main></div>;
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
  const live = run.kind === "paper" ? livePot(run, o) : null;
  const nowPot = live?.pot ?? c.at(-1)?.pot ?? run.amount_usd;
  const peak = Math.max(run.amount_usd, run.peak_equity ?? 0, ...c.map((p) => p.pot));
  const dd = ((peak - nowPot) / peak) * 100;
  const dayPct = live ? live.dayPct : last ? (last.pnl / (nowPot - last.pnl)) * 100 : 0;
  const risk = run.kind === "paper" && ["scheduled", "running", "paused"].includes(run.status)
    ? riskStatus(run, o, dayPct, dd) : { severity: "ok" as const, items: [] };

  return (
    <div className="shell">
      <header className="top">
        <Link to="/" className="brand" aria-label="kaibot home"><Logo /><span>kaibot</span></Link>
        <nav className="topnav" aria-label="Main">
          {NAV.map((n) => <NavLink key={n.to} to={n.to} end={n.to === "/"}>{n.label}</NavLink>)}
        </nav>
        <div className="spacer" />
        {list.length > 1 && (
          <select aria-label="Run" className="chip" value={run.id}
            onChange={(e) => { setPicked(Number(e.target.value)); localStorage.setItem("kb.run", e.target.value); }}>
            {list.map((r) => <option key={r.id} value={r.id}>{r.label ?? `Run ${r.id}`}</option>)}
          </select>
        )}
        <Link to="/health" className="chip" aria-label={`Engine: ${chip.text}`}>
          <span className={chip.live ? "live-dot" : undefined} aria-hidden="true"
            style={{ width: 8, height: 8, borderRadius: 999, display: "inline-block", background: chip.dot }} />
          <b>{chip.text}</b> <span className="num hide-sm">{chip.sub}</span>
        </Link>
        <span className="pill">PAPER{lv ? ` · ${lv.name.toUpperCase()}` : ""}</span>
        {lv && (
          <div className="meters">
            <Meter label={run.kind === "replay" ? "Last day vs stop" : "Day vs stop"}
              value={`${pct(dayPct)} / −${lv.stop.toFixed(2)}%`} frac={dayPct < 0 ? -dayPct / lv.stop : 0} />
            <Meter label="Run drawdown" value={`${dd.toFixed(1)} / ${lv.runDd.toFixed(1)}%`} frac={dd / lv.runDd} />
          </div>
        )}
      </header>
      {risk.severity !== "ok" && (
        <div className={`riskstrip ${risk.severity}`} role={risk.severity === "breach" ? "alert" : "status"}>
          <b>{risk.severity === "breach" ? "Limit or safety halt in force:" : "Heads up:"}</b> {risk.items.join(" · ")}
          {" "}<Link to="/health">Engine health</Link>
        </div>
      )}
      <div className="body">
        <main key={run.id}><Outlet context={{ run, runs: list, overview: o } satisfies RunCtx} /></main>
      </div>
      <TabBar />
      <div className="foot">Paper trading only · simulated money · times ET (CET in brackets) · <Link to="/start">New run</Link> · <a href="#" onClick={(e) => { e.preventDefault(); supa.auth.signOut(); }}>Sign out</a></div>
    </div>
  );
}
