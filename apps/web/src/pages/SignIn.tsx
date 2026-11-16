import { useState } from "react";
import { supa } from "../lib/api";
import { Logo } from "../components/Logo";
import { useGlow } from "../lib/glow";
import { NAME } from "../brand/brand";

export function SignIn() {
  useGlow("signin");
  const [email, setEmail] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [err, setErr] = useState("");

  async function send(e: React.FormEvent) {
    e.preventDefault();
    setState("sending");
    const { error } = await supa.auth.signInWithOtp({ email, options: { emailRedirectTo: window.location.origin } });
    if (error) { setErr(error.message); setState("error"); } else setState("sent");
  }

  return (
    <div className="center">
      <form className="card" onSubmit={send}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}><Logo animate />
          <span className="pill">PAPER</span></div>
        {state === "sent" ? (
          <>
            <h1 style={{ margin: 0, fontSize: 18 }}>Check your email</h1>
            <p className="muted" style={{ margin: 0 }}>We sent a sign-in link to {email}. Open it on this device. It expires in an hour.</p>
            <button type="button" onClick={() => setState("idle")}>Use a different email</button>
          </>
        ) : (
          <>
            <h1 style={{ margin: 0, fontSize: 18 }}>Sign in</h1>
            <p className="muted" style={{ margin: 0 }}>Private dashboard. We'll email you a one-time link; no password.</p>
            <label className="cap" htmlFor="email">Email</label>
            <input id="email" type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />
            {state === "error" && <p className="loss" style={{ margin: 0 }} role="alert">{err}</p>}
            <button className="primary" disabled={state === "sending" || !email}>{state === "sending" ? "Sending…" : "Email me a link"}</button>
          </>
        )}
      </form>
    </div>
  );
}

/** Signed in, but this dashboard belongs to another account (or the check failed): nothing of it is shown. */
export function NotOwner({ failed, retry }: { failed: boolean; retry: () => void }) {
  return (
    <div className="center"><div className="card">
      <h1 style={{ margin: 0, fontSize: 18 }}>{failed ? "Couldn't check who owns this dashboard" : "Not your dashboard"}</h1>
      <p className="muted">{failed ? "The server didn't answer. Try again in a moment." : `This ${NAME} belongs to another account. Nothing here is visible to you.`}</p>
      {failed && <button onClick={retry}>Try again</button>}
      <button onClick={() => supa.auth.signOut()}>Sign out</button>
    </div></div>
  );
}
