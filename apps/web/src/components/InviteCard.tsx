import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type InviteMade } from "../lib/api";
import { NAME } from "../brand/brand";

// "Invite someone" (stakeholder, 24 Nov 2026; migration 0045). The dashboard holds no service key, so it cannot send
// an email itself: it shows the link and the code to copy or share by mail. Supabase Auth sends the invitee their own
// email (the link that creates the account) when they register.

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

const STATUS = { open: "open", used: "registered", expired: "expired" } as const;

export function InviteCard() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["invites"], queryFn: api.inviteList, staleTime: 30_000 });
  const [email, setEmail] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "error">("idle");
  const [err, setErr] = useState("");
  const [made, setMade] = useState<InviteMade | null>(null);
  const [copied, setCopied] = useState("");

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
  const left = list.data ? Math.max(0, list.data.cap - list.data.taken) : null;
  const link = made ? absoluteLink(made.link) : "";
  return (
    <section className="card" aria-labelledby="inv-h">
      <h2 id="inv-h">Invite someone</h2>
      <p className="muted" style={{ fontSize: 13 }}>They get a link and a code from you, and their sign-in email from {NAME} when they
        register. Invites work once and expire in 7 days.{left !== null && ` ${left} of ${list.data!.cap} places left.`}</p>
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
          <thead><tr><th>Email</th><th>Status</th><th className="r">Expires</th></tr></thead>
          <tbody>{list.data.invites.map((i) => (
            <tr key={i.id}><td>{i.email}</td><td>{STATUS[i.status]}</td>
              <td className="r num">{i.status === "open" ? new Date(i.expires_at).toLocaleDateString() : "—"}</td></tr>
          ))}</tbody>
        </table>
      )}
    </section>
  );
}
