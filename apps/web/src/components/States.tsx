// Shared loading, error and empty states, so every page says the same thing the same way.
// Error: role="alert", a plain sentence, the server's message as detail, and a Retry button when the query can retry.
// Empty: a plain sentence with what to expect next. Loading: the shaped skeleton (role="status").
import type { ReactNode } from "react";
import { EXPERIMENT_LABEL, STATUS_TAG, TEST_PHASE, RETIRED_TEXT, ruleName, ruleStatus, ruleStatusText } from "../lib/explain";
import { Skeleton } from "./Skeleton";
import { experimentView, type Experiment } from "../lib/experiment";
import { ExperimentBadge } from "./Experiment";
import { DB_DOWN, DB_DOWN_WHY, dbUnreachable } from "../lib/ownernotes";

export const Loading = Skeleton;

const message = (e: unknown) => (e instanceof Error ? e.message : typeof e === "string" ? e : "unknown error");

export function ErrorState({ what, error, retry, compact }: { what: string; error: unknown; retry?: () => void; compact?: boolean }) {
  const offline = typeof navigator !== "undefined" && navigator.onLine === false;
  // N9 (ui_after_engine_v3, #724): the database out of reach is its own state, never read as a dead engine
  const db = !offline && dbUnreachable(error);
  const body = (
    <div className="state state-error" role="alert" data-state={db ? "db-unreachable" : undefined}>
      <b>{db ? `${DB_DOWN}.` : `${what} couldn't load.`}</b>{" "}
      <span className="muted">{offline ? "You seem to be offline. Nothing in the engine changed." : db ? `${what} couldn't load. ${DB_DOWN_WHY}`
        : "The server didn't answer as expected. Nothing in the engine changed."}</span>
      <span className="muted state-detail num">{message(error)}</span>
      {retry && <span><button type="button" onClick={retry}>Try again</button></span>}
    </div>
  );
  return compact ? body : <section className="card">{body}</section>;
}

export function Empty({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return <div className="state state-empty"><p className="muted" style={{ margin: 0 }}>{children}</p>{action}</div>;
}

/** Engine v3 test phase: one sentence and the plan (Live, Report). */
export function TestPhaseBanner() {
  return (
    <div className="banner" role="note" aria-label="Test phase">
      {TEST_PHASE.text} <a href={TEST_PHASE.url} target="_blank" rel="noopener noreferrer">{TEST_PHASE.link}<span className="sr-only"> (opens in a new tab)</span></a>
    </div>
  );
}

/** A plain word for an engine code; the code stays in the tooltip. */
export function Code({ text, code }: { text: string; code: string | null | undefined }) {
  return code ? <span title={`code: ${code}`}>{text}</span> : <>{text}</>;
}

/** A rule's plain name, with "retired, systems test only" (or "test only") as a tag when it applies. A rule the engine
 *  runs as a paper experiment (#210) carries the experiment badge instead (with its cap and stop line if `limits`). */
/** `plain`: the experiment's label as words, not as the badge that opens a sheet. For a name that already sits inside a
 *  control (an accordion's summary): a button inside a control cannot be reached by keyboard or screen reader (#327). */
export function RuleName({ code, tag = true, exp, limits, plain }: { code: string; tag?: boolean; exp?: Experiment | null; limits?: boolean; plain?: boolean }) {
  const st = ruleStatus(code), txt = ruleStatusText(code);
  // An experiment keeps its badge without the heartbeat's label row (market closed, older engine), only without limits.
  const e = exp ?? (st === "experiment" ? { strategy: code, label: EXPERIMENT_LABEL, evidence: "failed_gate_b", experiment: true,
    realMoney: false, level: null, capPct: null, retirePct: null } : null);
  return (
    <span title={`code: ${code}`}>{ruleName(code)}
      {tag && e && (plain ? <> <span className="tag exp-tag">{experimentView(e).text}</span></> : <> <ExperimentBadge e={e} limits={limits} /></>)}
      {tag && !e && txt && st && <> <span className={`tag ${STATUS_TAG[st]}`}>{st === "retired" ? RETIRED_TEXT : txt}</span></>}
    </span>
  );
}
