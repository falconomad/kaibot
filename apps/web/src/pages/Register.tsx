import { useState } from "react";
import { api, supa, type InviteReason } from "../lib/api";
import { Logo } from "../components/Logo";
import { FROM_SIGNIN, useGlow } from "../lib/glow";
import { TOO_MANY, isSignupRefused, takeAttempt } from "../lib/attempts";

// /register?code=... (stakeholder, 24 Nov 2026): the invitee types their name and the invited email. The code is
// checked first (kb_invite_check, migration 0045), then Supabase Auth's own email sends the link that creates the
// account (shouldCreateUser: true, with the name and the code as user_metadata). The database refuses the account
// anyway unless the email has an open invite and the code is that invite's (0045's trigger), so skipping this page
// gains nothing. After the click the invite is marked used and the profile is made.

export const REASON: Record<InviteReason, string> = {
  unknown: "That code isn't an invite. Check it, or ask for a new invite.",
  revoked: "That invite was withdrawn by the person who sent it. Ask them for a new one.",
  used: "That invite has been used. Sign in with your email instead.",
  expired: "That invite has expired (invites last 7 days). Ask for a new one.",
  email_mismatch: "This invite is for a different email. Use the address the invite was sent to.",
};
export const NOT_OPEN = "Registration isn't open yet: the database update for invites (migration 0045) hasn't been applied. Ask the owner.";
export const REFUSED = "This invite can't create an account any more (it may have expired or the account limit is reached). Ask for a new invite.";

export function codeFromUrl(search: string): string {
  return (new URLSearchParams(search).get("code") ?? "").trim().slice(0, 40);
}

export function Register({ search = typeof window === "undefined" ? "" : window.location.search }: { search?: string }) {
  useGlow("signin"); // sign-in's place, and its entrance on a first load (lib/glow.ts primeGlowArrival)
  const [code, setCode] = useState(() => codeFromUrl(search));
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [err, setErr] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const turn = takeAttempt();
    if (!turn.ok) { setErr(TOO_MANY(turn.waitMs)); setState("error"); return; }
    setState("sending");
    try {
      const check = await api.inviteCheck(code, email);
      if (check === null) { setErr(NOT_OPEN); setState("error"); return; }
      if (!check.ok) { setErr(REASON[check.reason ?? "unknown"]); setState("error"); return; }
      const { error } = await supa.auth.signInWithOtp({ email, options: { shouldCreateUser: true,
        data: { name: name.trim().slice(0, 60), invite_code: code.trim() }, emailRedirectTo: `${window.location.origin}/?${FROM_SIGNIN}` } });
      if (error) { setErr(isSignupRefused(error) ? REFUSED : error.message); setState("error"); return; }
      setState("sent");
    } catch (x) {
      setErr((x as Error).message || "Something went wrong. Try again."); setState("error");
    }
  }

  return (
    <div className="center">
      <form className="card" onSubmit={submit}>
        <div className="brand-row"><Logo animate idle large />
          <span className="pill">PAPER</span></div>
        {state === "sent" ? (
          <>
            <h1 style={{ margin: 0, fontSize: 18 }}>Check your email</h1>
            <p className="muted" style={{ margin: 0 }}>We sent a link to {email}. Open it on this device to finish registering.
              After that, sign in any time with your email on the sign-in page.</p>
            <a href="/">Go to sign-in</a>
          </>
        ) : (
          <>
            <h1 style={{ margin: 0, fontSize: 18 }}>Register with an invite</h1>
            <p className="muted" style={{ margin: 0 }}>Use the email the invite was for. We'll email you a link to finish; no password.</p>
            <label className="cap" htmlFor="code">Invite code</label>
            <input id="code" required autoComplete="off" spellCheck={false} placeholder="XXXX-XXXX-XXXX-XXXX" value={code}
              onChange={(e) => setCode(e.target.value)} />
            <label className="cap" htmlFor="name">Your name</label>
            <input id="name" required maxLength={60} autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} />
            <label className="cap" htmlFor="email">Email</label>
            <input id="email" type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />
            {state === "error" && <p className="loss" style={{ margin: 0 }} role="alert">{err}</p>}
            <button className="primary" disabled={state === "sending" || !code.trim() || !name.trim() || !email}>
              {state === "sending" ? "Checking…" : "Register"}</button>
            <p className="muted" style={{ margin: 0, fontSize: 13 }}>Have an account? <a href="/">Sign in</a>.</p>
          </>
        )}
      </form>
    </div>
  );
}
