// v4 "More" tab (docs/design/mobile_first_v4.md, "Navigation"): every page that is not one of the four main tabs, the
// run picker, the day and run limits, and sign out. Phones reach it from the tab bar; desktop has it in the top bar.
import { Link } from "react-router-dom";
import { Meter, useRunCtx } from "../components/Shell";
import { supa } from "../lib/api";
import { LEVELS } from "../lib/model";
import { pct } from "../lib/fmt";

const ITEMS: { to: string; label: string; line: string }[] = [
  { to: "/report", label: "Report", line: "One day in detail, and what worked" },
  { to: "/gate", label: "Go / no-go", line: "The ten-year test and the bar for real money" },
  { to: "/strategy", label: "Strategy", line: "Starting point, focus and settings" },
  { to: "/settings", label: "Run & settings", line: "This run, its level, pause or stop" },
  { to: "/start", label: "New run", line: "Start again with a new amount or level" },
];

export function More() {
  const { run, runs, meters, pickRun } = useRunCtx();
  const lv = run.level ? LEVELS[run.level] : null;
  return (
    <>
      <h1 className="page-title">More</h1>
      {lv && meters && (
        <section className="card more-meters" aria-label="Limits">
          <Meter label={meters.replay ? "Last day vs stop" : "Day vs stop"} value={`${pct(meters.dayPct)} / −${lv.stop.toFixed(2)}%`}
            frac={meters.dayPct < 0 ? -meters.dayPct / lv.stop : 0} />
          <Meter label="Run drawdown" value={`${meters.dd.toFixed(1)} / ${lv.runDd.toFixed(1)}%`} frac={meters.dd / lv.runDd} />
        </section>
      )}
      {runs.length > 1 && pickRun && (
        <section className="card" aria-labelledby="run-h">
          <h2 id="run-h" style={{ margin: "0 0 8px" }}>Run</h2>
          <select aria-label="Run" value={run.id} onChange={(e) => pickRun(Number(e.target.value))} style={{ width: "100%" }}>
            {runs.map((r) => <option key={r.id} value={r.id}>{r.label ?? `Run ${r.id}`}</option>)}
          </select>
        </section>
      )}
      <nav aria-label="More pages" className="card more-list">
        {ITEMS.map((i) => (
          <Link key={i.to} to={i.to} className="more-row">
            <span><b>{i.label}</b><span className="muted">{i.line}</span></span>
            <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M9 6 L15 12 L9 18" /></svg>
          </Link>
        ))}
      </nav>
      <p className="muted" style={{ fontSize: 13, margin: 0 }}>Paper trading only · simulated money · times ET (CET in brackets)</p>
      <button type="button" className="more-signout" onClick={() => { void supa.auth.signOut(); }}>Sign out</button>
    </>
  );
}
