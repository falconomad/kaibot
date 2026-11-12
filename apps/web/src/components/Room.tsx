// S35: the agent room transcript. Model text is untrusted: it is only ever rendered as React text nodes (never as HTML),
// and links come only from the evidence pack through safeUrl (http/https).
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type PackItem, type Room, type RoomSession } from "../lib/api";
import { fmtR } from "../lib/derive";
import { causeText } from "../lib/explain";
import { ErrorState } from "./States";
import { roomOutcome, roomView, safeDate, type ResolvedRef } from "../lib/roomview";
import { dateEt, signedMoney, timeEt, tone } from "../lib/fmt";

const cause = causeText;
const when = (iso: string | undefined) => {
  const d = iso ? safeDate(iso) : null;
  return d ? `${dateEt(d.toISOString())} ${timeEt(d.toISOString())} ET` : iso || "time unknown";
};

function PackCard({ item, id }: { item: PackItem; id: string }) {
  return (
    <>
      <div className="ev-head">
        {item.url
          ? <a href={item.url} target="_blank" rel="noopener noreferrer">{item.headline || "(no headline)"}</a>
          : <span>{item.headline || "(no headline)"}</span>}
      </div>
      <div className="muted ev-meta num">
        {id} · {item.source || "unknown source"} · {when(item.published_at)}
        {item.n_outlets ? ` · ${item.n_outlets} outlet${item.n_outlets === 1 ? "" : "s"}` : ""}
        {item.triage?.cause ? ` · triage: ${cause(item.triage.cause)}${item.triage.materiality ? `, ${item.triage.materiality}` : ""}` : ""}
      </div>
      {item.triage?.summary && <div className="ev-sum">{item.triage.summary}</div>}
    </>
  );
}

function Refs({ refs }: { refs: ResolvedRef[] }) {
  if (!refs.length) return <p className="muted ev-none">No evidence cited.</p>;
  return (
    <ul className="evlist">
      {refs.map((r) => (
        <li key={r.id} className={r.item ? "" : "unverified"}>
          {r.item ? <PackCard item={r.item} id={r.id} /> : (
            <>
              <div className="ev-head"><span className="tag veto">unverified</span> not in the evidence pack</div>
              <div className="muted ev-meta num">{r.id} · {r.source || "unknown source"} · {when(r.published_at)}</div>
            </>
          )}
        </li>
      ))}
    </ul>
  );
}

export function RoomTranscript({ s, trade, compact }: { s: RoomSession; trade?: Room["trade"]; compact?: boolean }) {
  const v = roomView(s);
  const out = roomOutcome(s.status, s.verdict, s.error, s.rules_only);
  const cf = s.counterfactual;
  const answered = !!(v.proposer || v.critic || v.judge);
  return (
    <div className="room">
      <div className="room-top">
        <span className={`tag ${out.tag}`}>{out.label}</span>
        {v.judge && <span>Cause <b>{cause(v.judge.cause)}</b></span>}
        {v.judge?.confidence && <span>Confidence <b>{v.judge.confidence}</b></span>}
        <span className="spacer" />
        <span className="muted num">
          {s.latency_ms !== null && s.latency_ms !== undefined ? `${(s.latency_ms / 1000).toFixed(1)} s` : "—"}
          {" · "}{s.cost_usd !== null && s.cost_usd !== undefined ? `$${Number(s.cost_usd).toFixed(3)}` : "—"}
        </span>
      </div>
      <p className="room-out">{out.text}</p>
      {s.status === "error" && s.error && <p className="muted num" style={{ fontSize: 12 }}>error: {s.error.slice(0, 300)}</p>}
      {v.unverified > 0 && (
        <div className="banner warn" role="status">{v.unverified} citation{v.unverified === 1 ? " is" : "s are"} not in the evidence pack.
          The engine rejects such answers; treat this transcript as unverified.</div>
      )}

      {(cf?.pnl_usd !== undefined || trade) && (
        <div className="kv room-res">
          {trade && (<><span>Actual trade{s.verdict === "downgrade" ? " (half size)" : ""}</span><span />
            <span className={`v ${tone(trade.pnl_usd)}`}>{trade.exit_ts ? signedMoney(trade.pnl_usd) : "open"}
              {trade.exit_ts && trade.risk_usd ? ` · ${fmtR((trade.pnl_usd ?? 0) / trade.risk_usd)}` : ""}</span></>)}
          {cf?.pnl_usd !== undefined && (<><span>Rules-only, full size (counterfactual)</span><span />
            <span className={`v ${tone(cf.pnl_usd)}`}>{signedMoney(cf.pnl_usd)} · {fmtR(cf.r_multiple)}{cf.exit_reason ? ` · ${cf.exit_reason}` : ""}</span></>)}
        </div>
      )}

      {answered && (
        <>
          <div className="room-sides">
            <section className="room-role" aria-label="Proposer">
              <div className="cap">Proposer · the case for</div>
              {v.proposer ? (<>
                <p className="serif room-text">{v.proposer.thesis || "(no thesis)"}</p>
                <p className="muted room-cause">Cause: {cause(v.proposer.cause)}</p>
                <Refs refs={v.proposer.evidence} />
              </>) : <p className="muted">No answer.</p>}
            </section>
            <section className="room-role critic" aria-label="Critic">
              <div className="cap">Critic · strongest counter</div>
              {v.critic ? (<>
                <p className="serif room-text">{v.critic.counter || "(no counter)"}</p>
                <p className="muted room-cause">Cause: {cause(v.critic.cause)}</p>
                {v.critic.redFlags.length > 0 && (
                  <ul className="flags">{v.critic.redFlags.map((f, i) => <li key={i}>{f}</li>)}</ul>
                )}
                <Refs refs={v.critic.evidence} />
              </>) : <p className="muted">No answer.</p>}
            </section>
          </div>
          <section className="room-role judge" aria-label="Judge">
            <div className="cap">Judge</div>
            {v.judge ? (<>
              <div className="kv" style={{ gridTemplateColumns: "auto 1fr" }}>
                <span className="muted">Verdict</span><span>{v.judge.verdict || "—"} · {cause(v.judge.cause)} · {v.judge.confidence || "—"} confidence</span>
                <span className="muted">Invalidation</span><span className="serif">{v.judge.invalidation || "—"}</span>
                <span className="muted">Horizon</span><span className="num">{v.judge.horizon ? `${v.judge.horizon} min` : "—"}</span>
              </div>
              <div className="cap" style={{ marginTop: 10 }}>Evidence the judge relied on</div>
              <Refs refs={v.judge.evidence} />
            </>) : <p className="muted">No verdict.</p>}
          </section>
        </>
      )}

      {v.pack.length > 0 && (
        <details className="pack" open={!compact && !answered}>
          <summary>Evidence pack: {v.pack.length} item{v.pack.length === 1 ? "" : "s"} ({v.pack.filter((p) => v.cited.has(p.id)).length} cited)</summary>
          <ul className="evlist">{v.pack.map((p) => (
            <li key={p.id}><PackCard item={p} id={p.id} />{!v.cited.has(p.id) && <div className="muted ev-meta">not cited</div>}</li>
          ))}</ul>
        </details>
      )}
      <p className="muted" style={{ fontSize: 11, margin: 0 }}>Model text is shown as written and is not advice. The room can only veto or halve a trade; it never sets price, size, stop or exit.</p>
    </div>
  );
}

/** For setups that were never traded: fetch the session by signal (kb_room). */
export function RoomBySignal({ signalId }: { signalId: number }) {
  const q = useQuery({ queryKey: ["room", signalId], queryFn: () => api.room(signalId) });
  if (q.isPending) return <p className="skeleton">Loading the transcript…</p>;
  if (q.isError) return <ErrorState compact what="The transcript" error={q.error} retry={() => q.refetch()} />;
  if (!q.data) return <p className="muted">No room session for this setup.</p>;
  return (
    <>
      {q.data.trade && <p style={{ margin: "0 0 8px" }}><Link to={`/trades/${q.data.trade.id}`}>Open the trade →</Link></p>}
      <RoomTranscript s={q.data.session} trade={q.data.trade} compact />
    </>
  );
}
