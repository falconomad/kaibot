// "How kaibo decides" (#724, ticket 8.6 of the second audit): one static page in plain words, for the owner who wants
// the whole picture without the Engine page's detail. No data, no queries: what is written here does not change with a
// run. Lazy (routes.ts); only the shared card and heading classes, so no CSS of its own. Words that do not advise
// (docs/design/ui_after_engine_v3.md, section 5): it says what kaibo does, never what anyone should do.
import { Link } from "react-router-dom";
import { NAME } from "../brand/brand";
import { TEST_PHASE } from "../lib/explain";

const STEPS: { h: string; p: string[] }[] = [
  { h: "1. It looks at yesterday's closing prices", p: [
    `After each close ${NAME} fetches the day's closing prices for the stocks it follows. Until they are in, it makes no new decision; sales and safety stops go on as usual.`,
    "It uses only prices that were known at the time of the decision, never a later one." ] },
  { h: "2. One rule picks the stocks", p: [
    "The rule running on paper is Trend momentum: it holds the stocks with the strongest steady uptrends and replaces them as the list changes. It decides on a fixed day of the week, not every day.",
    "The rule is a written formula. A language model never picks a stock, a price, a size, a stop or the moment to sell." ] },
  { h: "3. Fixed limits decide how much", p: [
    "Every order passes a set of checks before it can go out: how much one stock may take of the pot, how much the whole book may hold, the daily stop and the run's drawdown limit for your level.",
    "If a check fails, the order is made smaller or not sent. The limits can only be tightened, never loosened by the engine." ] },
  { h: "4. A reviewer can only say no", p: [
    "Before the open a language model reads the news of the planned buys. It may hold back a buy or halve it, and nothing else. Its verdicts are recorded either way." ] },
  { h: "5. Orders go out near the close", p: [
    "The day's buys and sells are sent shortly before the market closes, so they trade near the closing price. An order the market does not fill by 16:00 ET expires, and the next session decides again." ] },
  { h: "6. Every holding has a safety stop at the broker", p: [
    "Each stock it holds has a stop order waiting at the broker. The stop stays there when the engine, the Mac or this dashboard is down.",
    "The stop line and the P&L use confirmed closing prices. Live prices on screen are from one exchange and are for watching only." ] },
  { h: "7. When something is off, it stops buying", p: [
    `If its books and the broker disagree, its clock and the broker's differ, the paper account holds something ${NAME} did not place, or a limit is hit, ${NAME} stops new buys. It keeps selling by its rule and keeps the stops. A halt shows as a red line on every page.`,
    "A separate watchdog watches the engine. If the engine is silent near the close, a safety net in the database cancels new buys and keeps the stops in place, even with the Mac off." ] },
];

export function HowItDecides() {
  return (
    <>
      <h1 className="page-title">How {NAME} decides</h1>
      <p className="muted" style={{ margin: 0 }}>Paper trading only: simulated money, a paper account at the broker. {TEST_PHASE.text}</p>
      {STEPS.map((s) => (
        <section key={s.h} className="card" aria-label={s.h}>
          <h2 style={{ margin: "0 0 8px" }}>{s.h}</h2>
          {s.p.map((t) => <p key={t} style={{ margin: "0 0 8px" }}>{t}</p>)}
        </section>
      ))}
      <p className="muted" style={{ fontSize: 14, margin: 0 }}>
        What it did today is on <Link to="/activity">Activity</Link>; whether it is running and safe is on <Link to="/health">Engine</Link>.
      </p>
    </>
  );
}
