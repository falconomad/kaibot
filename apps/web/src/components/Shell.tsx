import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useOutletContext } from "react-router-dom";
import { api, supa, type Overview, type Run } from "../lib/api";
import { LEVELS, curve } from "../lib/model";
import { defaultRun, riskStatus, runEndedRed } from "../lib/derive";
import { Skeleton } from "./Skeleton";
import { ErrorState } from "./States";
import { STALE, overviewPollMs, poll } from "../lib/poll";
import { bookChecks } from "../lib/book";
import { Sheet } from "./Sheet";
import { preloadRoute } from "../routes";
import { anchorFor, useGlow } from "../lib/glow";
import { BRAND, NAME } from "../brand/brand";
import { Logo } from "./Logo";

/** Warm a lazy route's chunk when the pointer or keyboard focus reaches its nav link. */
const warm = (to: string) => ({ onPointerEnter: () => { preloadRoute(to); }, onFocus: () => { preloadRoute(to); } });

export interface RunCtx {
  run: Run; runs: Run[]; overview: Overview | undefined;
  /** v4: the header's "Day vs stop" and "Run drawdown" meters, for pages that show them on phones (Home, More). */
  meters?: { dayPct: number; dd: number; replay: boolean };
  /** v4: the risk strip's state; Home shows it as its own status row instead of the strip. */
  risk?: { severity: "ok" | "warn" | "breach"; items: string[]; red?: string[] };
  /** v4: the red kinds in force (halts, the run ended, the daily stop, watchdog or dead-man, book problems). */
  red?: string[];
  /** v4: pick another run (the picker lives in More on phones). */
  pickRun?: (id: number) => void;
  /** Set when the overview failed and has no data to show; pages show an error with a retry instead of a skeleton. */
  overviewError?: unknown; retryOverview?: () => void;
}
export const useRunCtx = () => useOutletContext<RunCtx>();

// v4 (docs/design/mobile_first_v4.md, "Navigation"). Desktop and tablet: the top navigation. Phones: five tabs; the
// rest is under More (Report, Go / no-go, Strategy, Holdings, Run & settings, New run, the run picker, Sign out).
const NAV: { to: string; label: string; icon: string }[] = [
  { to: "/", label: "Home", icon: "M3 11 L12 4 L21 11 V20 H14 V14 H10 V20 H3 Z" },
  { to: "/positions", label: "Positions", icon: "M4 7 H20 M4 12 H20 M4 17 H14" },
  { to: "/activity", label: "Activity", icon: "M4 6 H20 M4 10 H16 M4 14 H20 M4 18 H12" },
  { to: "/report", label: "Report", icon: "M5 20 V12 M12 20 V5 M19 20 V9" },
  { to: "/gate", label: "Go / no-go", icon: "M5 12 L10 17 L19 7" },
  { to: "/health", label: "Engine", icon: "M3 12 H7 L10 5 L14 19 L17 12 H21" },
  { to: "/strategy", label: "Strategy", icon: "M4 7 H14 M18 7 H20 M4 17 H8 M12 17 H20 M16 5 A2 2 0 1 0 16 9 A2 2 0 1 0 16 5 M10 15 A2 2 0 1 0 10 19 A2 2 0 1 0 10 15" },
  { to: "/settings", label: "Run & settings", icon: "M12 9 A3 3 0 1 0 12 15 A3 3 0 1 0 12 9 M12 3 V6 M12 18 V21 M3 12 H6 M18 12 H21" },
];
const MORE_ICON = "M5 12 H5.01 M12 12 H12.01 M19 12 H19.01";
export const TABS = ["/", "/positions", "/activity", "/health", "/more"] as const;
/** Pages reached from More: the More tab is the active one on them. */
export const UNDER_MORE = ["/more", "/report", "/gate", "/strategy", "/settings"];

/** The Engine tab's dot: red while a halt or breach is in force, amber while the engine is offline. */
export type EngineDot = "breach" | "offline" | null;

function TabBar({ dot }: { dot: EngineDot }) {
  const { pathname } = useLocation();
  useGlow(anchorFor(pathname));
  const moreActive = UNDER_MORE.some((p) => pathname === p || pathname.startsWith(p + "/"));
  return (
    <nav className="tabbar" aria-label="Main">
      {TABS.map((to) => {
        const n = NAV.find((x) => x.to === to);
        const label = n?.label ?? "More";
        const more = to === "/more";
        return (
          <NavLink key={to} to={to} end={to === "/"} {...warm(to)} className={({ isActive }) => (isActive || (more && moreActive) ? "active" : "")}
            aria-label={to === "/health" && dot ? `Engine: ${dot === "breach" ? "a halt is in force" : "offline"}` : undefined}>
            <span className="tab-ico">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={n?.icon ?? MORE_ICON} /></svg>
              {to === "/health" && dot && <i className={`tab-dot ${dot}`} aria-hidden="true" />}
            </span>
            {label}
          </NavLink>
        );
      })}
    </nav>
  );
}

export function Meter({ label, value, frac }: { label: string; value: string; frac: number }) {
  return (
    <div className="meter">
      <div className="row"><span className="cap">{label}</span><span className="num" style={{ fontSize: 13 }}>{value}</span></div>
      <div className="bar" role="meter" aria-label={label} aria-valuenow={Math.round(frac * 100)} aria-valuemin={0} aria-valuemax={100}>
        <i style={{ transform: `scaleX(${Math.min(1, Math.max(0, frac))})`, background: frac >= 0.8 ? "var(--fail)" : undefined }} />
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
  return { dot: state === "error" ? "var(--warn)" : "var(--live)", text: STATE_TEXT[state] ?? state,
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

/** Phones (Product, 16 Nov 2026): a red halt or safety problem shows on every page as one slim line under the header
 *  (36 px to see, 44 px to tap). It opens the list in a sheet. Amber and info states keep the Engine dot and rows. */
function PhoneHaltBar({ items }: { items: string[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="haltbar-wrap" role="alert">
      <button type="button" className="haltbar" aria-haspopup="dialog" onClick={() => setOpen(true)}>
        <span className="haltbar-in"><i aria-hidden="true" /><span className="haltbar-text">{items[0]}</span>
          {items.length > 1 && <span className="haltbar-more">+{items.length - 1}</span>}</span>
      </button>
      <Sheet open={open} onClose={() => setOpen(false)} title="Limit or safety halt in force">
        <ul className="sheet-list">{items.map((t) => <li key={t}>{t}</li>)}</ul>
        <p className="muted">{NAME} applies its limits by itself. Open trades keep their stop at the broker.</p>
        <p><Link to="/health" onClick={() => setOpen(false)}>Engine health</Link></p>
      </Sheet>
    </div>
  );
}

/** v4 PR 7: a detail page (two path segments: /positions/12, /strategy/fine-tune) is a push; a tab is a fade. */
export const navKind = (path: string) => (path.split("/").filter(Boolean).length >= 2 ? "push" : "tab");

export function Shell() {
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.runs, ...poll(60_000) });
  const [picked, setPicked] = useState<number | null>(() => Number(localStorage.getItem("kb.run")) || null);
  const list = runs.data ?? [];
  const run = list.find((r) => r.id === picked) ?? defaultRun(list);
  const pickRun = (id: number) => { setPicked(id); localStorage.setItem("kb.run", String(id)); };
  // 5 s only while the engine is live in a session; slower when the market is closed or the engine is off (lib/poll.ts).
  const overview = useQuery({ queryKey: ["overview", run?.id], queryFn: () => api.overview(run!.id), enabled: !!run,
    refetchInterval: (q) => overviewPollMs(run, q.state.data), refetchIntervalInBackground: false });
  // v4 (Product, 16 Nov 2026): the multi-day book's safety problems are red kinds too (an unprotected stock, books
  // that disagree with the broker), so the phone bar can show them on every page.
  const bookQ = useQuery({ queryKey: ["book", run?.id], queryFn: () => api.book(run!.id), enabled: !!run && run.kind === "paper",
    staleTime: STALE.daily, ...poll(300_000) });
  // Focus order: after a navigation, move focus to the page so keyboard and screen-reader users start at the content.
  const { pathname } = useLocation();
  const mainRef = useRef<HTMLElement>(null);
  const first = useRef(true);
  useEffect(() => {
    if (first.current) { first.current = false; return; }
    window.scrollTo?.(0, 0); // v4: a new tab or page starts at its top
    mainRef.current?.focus({ preventScroll: true });
  }, [pathname]);

  if (runs.isPending) return <div className="shell"><main><Skeleton variant="dashboard" /></main></div>;
  if (runs.isError) return <div className="center"><ErrorState what="Your runs" error={runs.error} retry={() => runs.refetch()} /></div>;
  if (!run) return (
    <div className="center"><div className="card">
      <b>No runs yet</b><span className="muted">Start a paper run to give {NAME} an amount and a level.</span>
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
  const active = run.kind === "paper" && ["scheduled", "running", "paused"].includes(run.status);
  const risk = active ? riskStatus(run, o, dayPct, dd) : { severity: "ok" as const, items: [] as string[], red: [] as string[] };
  const checks = bookQ.data?.session ? bookChecks(bookQ.data) : null;
  const red = [...risk.red, ...(run.kind === "paper" ? runEndedRed(run) : []),
    ...(checks?.missing.length ? [`${checks.missing.length} stock${checks.missing.length === 1 ? " has" : "s have"} no safety stop (${checks.missing.join(", ")})`] : []),
    ...(checks?.reconciled && !checks.reconciled.ok ? [`${NAME}'s books and the broker disagree`] : [])];
  const dot: EngineDot = risk.severity === "breach" ? "breach" : active && !chip.live ? "offline" : null;

  return (
    <div className="shell">
      <a className="skip" href="#main">Skip to content</a>
      <header className="top">
        <Link to="/" className="brand" aria-label={`${BRAND} home`}><Logo animate /></Link>
        <nav className="topnav" aria-label="Main">
          {NAV.map((n) => <NavLink key={n.to} to={n.to} end={n.to === "/"} {...warm(n.to)}>{n.label}</NavLink>)}
        </nav>
        <div className="spacer" />
        <span className="pill" title={lv ? `Paper trading only · ${lv.name} level` : "Paper trading only"}>PAPER<span className="hide-sm">{lv ? ` · ${lv.name.toUpperCase()}` : ""}</span></span>
        {list.length > 1 && (
          <select aria-label="Run" className="chip hide-sm" value={run.id} onChange={(e) => pickRun(Number(e.target.value))}>
            {list.map((r) => <option key={r.id} value={r.id}>{r.label ?? `Run ${r.id}`}</option>)}
          </select>
        )}
        <Link to="/health" className="chip engine-chip" aria-label={`Engine: ${chip.text}`} title={chip.sub}>
          <span className={chip.live ? "live-dot" : undefined} aria-hidden="true"
            style={{ width: 8, height: 8, borderRadius: 999, display: "inline-block", background: chip.dot, flex: "none" }} />
          <b>{chip.text}</b> <span className="num hide-sm">{chip.sub}</span>
        </Link>
      </header>
      {red.length > 0 && <PhoneHaltBar items={red} />}
      {risk.severity !== "ok" && pathname !== "/" && pathname !== "/health" && (
        <div className={`riskstrip ${risk.severity}`} role={risk.severity === "breach" ? "alert" : "status"}>
          <b>{risk.severity === "breach" ? "Limit or safety halt in force:" : "Heads up:"}</b> {risk.items.join(" · ")}
          {" "}<Link to="/health">Engine health</Link>
        </div>
      )}
      {/* v4 PR 1 (audit #9): the halt strip above depends on the overview. Until its first answer the page shows a
          placeholder block of its own (one screen tall, so the footer stays below the fold); the real page is then
          inserted once, with the strip, instead of being pushed down by it. */}
      {overview.isPending ? (
        <div className="body body-pending" aria-busy="true"><main id="main" tabIndex={-1}><Skeleton variant="dashboard" /></main></div>
      ) : (
        <div className="body" key="ready">
          <main key={run.id} id="main" ref={mainRef} tabIndex={-1} data-nav={navKind(pathname)}>
            <Outlet context={{ run, runs: list, overview: o, overviewError: !o && overview.isError ? overview.error : undefined,
              retryOverview: () => { void overview.refetch(); }, meters: { dayPct, dd, replay: run.kind === "replay" }, pickRun, risk, red } satisfies RunCtx} />
          </main>
        </div>
      )}
      <TabBar dot={red.length ? "breach" : dot} />
      <div className="foot">Paper trading only · simulated money · times ET (CET in brackets) · <Link to="/start">New run</Link> · <a href="#" onClick={(e) => { e.preventDefault(); supa.auth.signOut(); }}>Sign out</a></div>
    </div>
  );
}
