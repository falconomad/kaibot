// Shared loading, error and empty states, so every page says the same thing the same way.
// Error: role="alert", a plain sentence, the server's message as detail, and a Retry button when the query can retry.
// Empty: a plain sentence with what to expect next. Loading: the shaped skeleton (role="status").
import type { ReactNode } from "react";
import { TEST_PHASE, RETIRED_TEXT, ruleName, ruleStatus, ruleStatusText } from "../lib/explain";
import { Skeleton } from "./Skeleton";

export const Loading = Skeleton;

const message = (e: unknown) => (e instanceof Error ? e.message : typeof e === "string" ? e : "unknown error");

export function ErrorState({ what, error, retry, compact }: { what: string; error: unknown; retry?: () => void; compact?: boolean }) {
  const offline = typeof navigator !== "undefined" && navigator.onLine === false;
  const body = (
    <div className="state state-error" role="alert">
      <b>{what} couldn't load.</b>{" "}
      <span className="muted">{offline ? "You seem to be offline." : "The server didn't answer as expected."} Nothing in the engine changed.</span>
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

/** A rule's plain name, with "retired, systems test only" (or "test only") as a tag when it applies. */
export function RuleName({ code, tag = true }: { code: string; tag?: boolean }) {
  const st = ruleStatus(code), txt = ruleStatusText(code);
  return (
    <span title={`code: ${code}`}>{ruleName(code)}
      {tag && txt && <> <span className={`tag ${st === "retired" ? "retired" : ""}`}>{st === "retired" ? RETIRED_TEXT : txt}</span></>}
    </span>
  );
}
