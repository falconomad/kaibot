// Sign-in inside the installed app (M13 4.7, #481). An app opened from the home screen keeps its own storage, and on
// an iPhone the emailed link opens in Safari, which the installed app never sees. So the installed app also takes the
// six-digit code from the same email. In a browser tab this renders nothing: the link works there.
// Needs the code in the email: the Supabase "Magic Link" template must contain {{ .Token }} (docs/design/m13_installable_web_app.md).
import { useId, useState } from "react";
import { supa } from "../lib/api";
import { isStandalone } from "./install";

export default function CodeEntry({ email }: { email: string }) {
  const [code, setCode] = useState("");
  const [state, setState] = useState<"idle" | "checking" | "error">("idle");
  const id = useId();
  if (!isStandalone()) return null;
  const digits = code.replace(/\D/g, "");
  async function check(e: React.FormEvent | React.MouseEvent) {
    e.preventDefault();
    if (digits.length !== 6) return;
    setState("checking");
    const { error } = await supa.auth.verifyOtp({ email, token: digits, type: "email" });
    setState(error ? "error" : "idle"); // signed in: the app's session listener shows the dashboard
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <p className="muted" style={{ margin: 0 }}>In this installed app the link opens in your browser instead. Type the 6-digit code from the same email here.</p>
      <label className="cap" htmlFor={id}>Code from the email</label>
      <input id={id} type="text" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9 ]*" maxLength={7} value={code}
        onChange={(e) => { setCode(e.target.value); if (state === "error") setState("idle"); }}
        onKeyDown={(e) => { if (e.key === "Enter") void check(e); }} />
      {state === "error" && <p className="loss" style={{ margin: 0 }} role="alert">That code didn't work. Check the digits, or ask for a new email: a code works once and expires in an hour.</p>}
      <button type="button" className="primary" disabled={digits.length !== 6 || state === "checking"} onClick={(e) => void check(e)}>
        {state === "checking" ? "Checking…" : "Sign in with the code"}</button>
    </div>
  );
}
