// M9 1.14 (#439): the sheet behind the Engine page's "Books and broker" check. It shows every difference between the
// engine's books and the broker (the stock, the shares on each side, the cash difference) and lets the owner accept
// them: a reason, a tick that the numbers were checked, and one button that says what it does. The command carries
// what this screen showed; the engine reads the broker again and accepts only if it still finds exactly that.
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";
import { NAME } from "../brand/brand";
import { api } from "../lib/api";
import { timeBoth } from "../lib/fmt";
import { REASON_MAX, REASON_MIN, type ReconState, acceptable, diffView, queueErrorText, refusedText, shownOf } from "../lib/reconcile";

export function ReconcileAccept({ state, engineLive, waiting }: { state: ReconState; engineLive: boolean; waiting: boolean }) {
  const qc = useQueryClient();
  const [reason, setReason] = useState("");
  // The tick is for the numbers that were on the screen when it was set: when the engine's next check shows other
  // numbers, the tick is gone and the owner compares again before anything can be sent.
  const numbers = JSON.stringify(shownOf(state));
  const [tickedFor, setTickedFor] = useState<string | null>(null);
  const checked = tickedFor === numbers;
  const refusedAtSend = useRef<string | null>(null);
  const rid = useId(), cid = useId();
  const cmd = useMutation({
    mutationFn: () => {
      refusedAtSend.current = state.refused?.ts ?? null;
      return api.command("accept_reconcile", { reason: reason.trim(), shown: shownOf(state) });
    },
    onSuccess: () => { setTickedFor(null); void qc.invalidateQueries({ queryKey: ["health"] }); },
  });
  if (state.ok) {
    return (
      <>
        <p>{NAME}'s books and the broker's account agree{state.ts ? ` (checked ${timeBoth(state.ts)})` : ""}. Nothing to do.</p>
        <p className="muted">{NAME} compares its own record of shares and cash with the broker at every start, at every session start and through the day.
          If they differ it stops new buys until they agree again or you accept the difference. Sales and stops always go on.</p>
      </>
    );
  }
  const can = acceptable(state);
  const views = state.diffs.map(diffView);
  const some = views.some((v) => !v.canAccept) && views.some((v) => v.canAccept);
  // The engine answered this send with a refusal (a newer one than was on the screen when it was sent): the sheet
  // stops saying "Sent", says why, and the owner can look again and accept the new numbers.
  const answered = cmd.isSuccess && !waiting && !!state.refused && state.refused.ts !== refusedAtSend.current;
  const sent = waiting || (cmd.isSuccess && !answered);
  const ready = can && engineLive && checked && reason.trim().length >= REASON_MIN && !cmd.isPending && !sent;
  return (
    <>
      <p><b>{NAME}'s books and the broker don't agree, so new buys are stopped.</b> Sales, stops and "sell everything" still work.</p>
      {state.refused && !sent && <p role="alert" className="loss">{refusedText(state.refused.why)}</p>}
      <ul className="recon-list" aria-label="Differences">{views.map((v, i) => (
        <li key={`${state.diffs[i].kind}-${state.diffs[i].symbol ?? ""}-${i}`}>
          <b>{v.head}</b>
          {v.rows.length > 0 && <div className="kv">{v.rows.map(([k, val]) => (
            <span key={k} style={{ display: "contents" }}><span>{k}</span><span /><span className="v">{val}</span></span>))}</div>}
          {v.note && <p className="muted" style={{ fontSize: 14 }}>{v.note}</p>}
        </li>))}</ul>
      {state.more > 0 && <p className="muted">And {state.more} more that don't fit here, so the list can't be accepted from this page. It can be accepted on the computer that runs the engine (see docs/ops/ledger.md).</p>}
      <p className="muted" style={{ fontSize: 14 }}>{state.ts ? `As the engine saw it at ${timeBoth(state.ts)}. ` : ""}A late booking often clears a difference by itself within the hour.</p>
      {can && (
        <form onSubmit={(e) => { e.preventDefault(); if (ready) cmd.mutate(); }}>
          <h3 className="section-h" style={{ marginTop: 16 }}>Accept the difference</h3>
          <p>Accepting means: {NAME} keeps its books as they are, treats {views.filter((v) => v.canAccept).length === 1 ? "this gap" : "these gaps"} as known,
            and <b>starts buying again</b>. Nothing is bought or sold to close the gap. If a number changes later, buys stop again.
            {some ? " The differences marked as not acceptable stay open and keep their own stop on buys." : ""}</p>
          <p>Do this only after you have compared these numbers with the broker's account and know why they differ.</p>
          <label htmlFor={rid} style={{ display: "block", fontWeight: 600, marginBottom: 6 }}>Why they differ (kept in the engine's record)</label>
          <input id={rid} type="text" value={reason} maxLength={REASON_MAX} autoComplete="off" style={{ width: "100%" }}
            placeholder="for example: a broker fee the books don't include" onChange={(e) => setReason(e.target.value)} disabled={sent} />
          <label htmlFor={cid} style={{ display: "flex", gap: 10, alignItems: "flex-start", margin: "14px 0" }}>
            <input id={cid} type="checkbox" checked={checked} onChange={(e) => setTickedFor(e.target.checked ? numbers : null)} disabled={sent} style={{ flex: "none", marginTop: 2 }} />
            <span>I compared these numbers with the broker's account.</span>
          </label>
          {!engineLive && <p className="muted">The engine isn't running, so it can't accept anything now. Start it, then come back.</p>}
          <button type="submit" className="primary" style={{ width: "100%" }} disabled={!ready} aria-disabled={!ready}>
            {cmd.isPending ? "Sending…" : "Accept the difference and resume buys"}</button>
          {cmd.isError && <p role="alert" className="loss" style={{ marginTop: 12 }}>{queueErrorText(cmd.error)}</p>}
          {sent && <p role="status" style={{ marginTop: 12 }}>Sent. The engine reads the broker once more and accepts only if the numbers are still the same.
            This takes about 10 seconds; this page updates by itself.</p>}
        </form>
      )}
    </>
  );
}
