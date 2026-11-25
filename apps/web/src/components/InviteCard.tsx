import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, fixInviteList, type InviteMade, type InviteRow } from "../lib/api";
import { NAME } from "../brand/brand";

// "Invite someone" (stakeholder, 24 Nov 2026; migration 0045). The dashboard holds no service key, so it cannot send
// an email itself: it shows the link and the code to copy or share by mail. Supabase Auth sends the invitee their own
// email (the link that creates the account) when they register.

export const REVOKE_NOT_APPLIED = "Revoking needs the database update for revoking invites (migration 0046), which isn't applied yet.";

export const NOT_APPLIED = "Invites need the database update for invites (migration 0045), which isn't applied yet. Until then sign-in works as before for existing accounts.";

export function absoluteLink(path: string, origin = typeof window === "undefined" ? "" : window.location.origin): string {
  return `${origin}${path}`;
}

export function mailto(made: InviteMade, link: string): string {
  const subject = `An invite to ${NAME}`;
  const body = `You're invited to ${NAME}, a paper-trading dashboard.\n\nRegister here: ${link}\n`
    + `Or open ${link.split("?")[0]} and type the code ${made.code}.\n\n`
    + `Use this email address (${made.email}). The invite works once and expires in 7 days. `
    + `When you register, you'll get an email with a link to finish.`;
  return `mailto:${encodeURIComponent(made.email)}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
}

const STATUS = { open: "open", used: "registered", expired: "expired", revoked: "revoked" } as const;

/** Revoke (0046, #635): shown only where kb_invite_list says can_revoke (absent before 0046), asked once more before
 * it is sent. A revoked code admits nobody; a new invite for the email gets a new code. */
function RevokeCell({ invite, onDone }: { invite: InviteRow; onDone: (msg: string, ok: boolean, email: string) => void }) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  if (!invite.can_revoke || invite.status !== "open") return null;
  async function revoke() {
    setBusy(true);
    try {
      const r = await api.inviteRevoke(invite.id);
      if (r === null) onDone(REVOKE_NOT_APPLIED, false, invite.email);
      else onDone(`Invite for ${invite.email} revoked. Its link and code no longer work.`, true, invite.email);
    } catch (x) { onDone((x as Error).message, false, invite.email); }
    setBusy(false); setAsking(false);
  }
  if (!asking) {
    return <button type="button" aria-label={`Revoke invite for ${invite.email}`} onClick={() => setAsking(true)}>Revoke</button>;
  }
  return (
    <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
      <button type="button" className="danger" disabled={busy} aria-label={`Yes, revoke invite for ${invite.email}`}
        onClick={revoke}>{busy ? "Revoking…" : "Yes, revoke"}</button>
      <button type="button" disabled={busy} onClick={() => setAsking(false)}>Keep</button>
    </span>
  );
}

export function InviteCard() {
  const qc = useQueryClient();
  // select: the answer as the card reads it, whoever fetched it (fixInviteList is idempotent; api.inviteList applies it too)
  const list = useQuery({ queryKey: ["invites"], queryFn: api.inviteList, staleTime: 30_000, select: fixInviteList });
  const [email, setEmail] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "error">("idle");
  const [err, setErr] = useState("");
  const [made, setMade] = useState<InviteMade | null>(null);
  const [copied, setCopied] = useState("");
  const [revoked, setRevoked] = useState<{ msg: string; ok: boolean } | null>(null);

  function afterRevoke(msg: string, ok: boolean, email: string) {
    setRevoked({ msg, ok });
    if (!ok) return;
    void qc.invalidateQueries({ queryKey: ["invites"] });
    if (made?.email === email) setMade(null);  // the link shown above no longer works
  }

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setState("sending"); setCopied("");
    try {
      const r = await api.inviteCreate(email.trim());
      if (r === null) { setErr(NOT_APPLIED); setState("error"); return; }
      setMade(r); setEmail(""); setState("idle");
      void qc.invalidateQueries({ queryKey: ["invites"] });
    } catch (x) { setErr((x as Error).message); setState("error"); }
  }

  async function copy(what: "link" | "code", value: string) {
    try { await navigator.clipboard.writeText(value); setCopied(what === "link" ? "Link copied." : "Code copied."); }
    catch { setCopied("Couldn't copy: select the text and copy it."); }
  }

  if (list.data === null) {
    return (
      <section className="card" aria-labelledby="inv-h">
        <h2 id="inv-h">Invite someone</h2>
        <p className="muted" role="status" style={{ marginBottom: 0 }}>{NOT_APPLIED}</p>
      </section>
    );
  }
  // the places left, only when both counts came (fixInviteList makes a missing one null)
  const cap = list.data?.cap ?? null, taken = list.data?.taken ?? null;
  const left = cap !== null && taken !== null ? Math.max(0, cap - taken) : null;
  const link = made ? absoluteLink(made.link) : "";
  return (
    <section className="card" aria-labelledby="inv-h">
      <h2 id="inv-h">Invite someone</h2>
      <p className="muted" style={{ fontSize: 13 }}>They get a link and a code from you, and their sign-in email from {NAME} when they
        register. Invites work once and expire in 7 days.{left !== null && ` ${left} of ${cap} places left.`}</p>
      <form onSubmit={create} style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "end" }}>
        <span style={{ display: "grid", gap: 4, flex: "1 1 220px" }}>
          <label className="cap" htmlFor="inv-email">Their email</label>
          <input id="inv-email" type="email" required autoComplete="off" value={email} onChange={(e) => setEmail(e.target.value)} />
        </span>
        <button className="primary" disabled={state === "sending" || !email.trim() || left === 0}>
          {state === "sending" ? "Creating…" : "Create invite"}</button>
      </form>
      {state === "error" && <p className="loss" role="alert">{err}</p>}
      {list.isError && <p className="loss" role="alert">Couldn't load your invites: {(list.error as Error).message}</p>}
      {made && (
        <div role="status" style={{ display: "grid", gap: 6, marginTop: 10 }}>
          <p style={{ margin: 0 }}>{made.reused ? "There's already an open invite for" : "Invite ready for"} <b>{made.email}</b>.
            Send them the link or the code; they get their sign-in email when they register.</p>
          <label className="cap" htmlFor="inv-link">Link</label>
          <input id="inv-link" readOnly value={link} onFocus={(e) => e.currentTarget.select()} />
          <span className="cap">Code</span>
          <span className="num" style={{ fontSize: 16, letterSpacing: 1 }}>{made.code}</span>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button type="button" onClick={() => copy("link", link)}>Copy link</button>
            <button type="button" onClick={() => copy("code", made.code)}>Copy code</button>
            <a className="btn" style={{ display: "inline-flex", alignItems: "center", textDecoration: "none" }} href={mailto(made, link)}>Share by email</a>
          </div>
          {copied && <p className="muted" style={{ margin: 0 }}>{copied}</p>}
        </div>
      )}
      {!!list.data?.invites.length && (
        <table className="list" aria-label="Invites" style={{ marginTop: 10 }}>
          <thead><tr><th>Email</th><th>Status</th><th className="r">Expires</th>
            {/* position: relative keeps the absolute .sr-only label inside the table's sideways scroller on a phone; without
                it the label sat past the screen edge and made the page 397 px wide at 375 and 390 px */}
            {list.data.invites.some((i) => i.can_revoke) && <th style={{ position: "relative" }}><span className="sr-only">Revoke</span></th>}</tr></thead>
          <tbody>{list.data.invites.map((i) => (
            <tr key={i.id}><td>{i.email}</td><td>{STATUS[i.status] ?? i.status}</td>
              <td className="r num">{i.status === "open" ? new Date(i.expires_at).toLocaleDateString() : "—"}</td>
              {list.data!.invites.some((x) => x.can_revoke) && <td className="r"><RevokeCell invite={i} onDone={afterRevoke} /></td>}</tr>
          ))}</tbody>
        </table>
      )}
      {revoked && <p className={revoked.ok ? "muted" : "loss"} role={revoked.ok ? "status" : "alert"}>{revoked.msg}</p>}
    </section>
  );
}
