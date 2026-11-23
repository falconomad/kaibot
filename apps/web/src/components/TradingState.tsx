// N1 / N2 (docs/design/ui_after_engine_v3.md): the trading-state line and the damaged-journal steps. A lazy chunk:
// the shell loads it after the first screen (components/Shell.tsx, useTradingState), Engine imports it directly.
import { NAME } from "../brand/brand";
import type { TradingState } from "../lib/tradingstate";

export { tradingLine, tradingState } from "../lib/tradingstate";

const TONE_DOT: Record<TradingState["tone"], string> = { ok: "var(--live)", calm: "var(--text-3)", warn: "var(--warn)", red: "var(--fail)" };

/** N1: the one trading-state line ("Selling only — paused by you"). The label is the words, so colour is never the
 *  only cue; the dot only repeats it. Used in the top bar and on the Engine screen. */
export function TradingLine({ s, className = "" }: { s: TradingState; className?: string }) {
  return (
    <span className={`trading-line ${s.tone} ${className}`} data-kind={s.kind}>
      <i className="trading-dot" aria-hidden="true" style={{ background: TONE_DOT[s.tone] }} />
      <b>{s.label}</b><span className="trading-reason"> — {s.reason}</span>
      {s.needsYou && <span className="sr-only"> (needs you)</span>}
    </span>
  );
}

/** N2: what the owner does at the Mac after a damaged-journal stop (docs/ops/ledger.md, "The journal"). */
export function JournalSteps() {
  return (
    <>
      <p>An entry in {NAME}'s local journal failed its check, so the engine stopped and will refuse every start until the
        file is dealt with. This is not the engine's night: it will not start by itself.</p>
      <p>At the Mac: move the damaged journal file aside, then restore last night's backup, or start a fresh journal from
        Supabase ("Taking over" in docs/ops/ledger.md). Do not edit the file.</p>
      <p className="muted">Stops already at the broker stay there, and the watchdog keeps watching meanwhile.</p>
    </>
  );
}

