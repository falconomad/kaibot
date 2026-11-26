// N10 (docs/design/ui_after_engine_v3.md, #724): the "Since you last looked" card on Home. A lazy chunk of its own (with
// its CSS), like the market notices: nothing shows until it arrives, and nothing when there was no earlier visit or
// nothing happened since. The lines come from lib/sincelast.ts; each opens its Activity row (or the Engine log).
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type Run } from "../lib/api";
import { timeBoth } from "../lib/fmt";
import { digest, takeLook } from "../lib/sincelast";
import "./since.css";

export default function SinceLastLook({ run }: { run: Run }) {
  // read once per mount: the last visit, and this one recorded (try/catch inside takeLook)
  const [since] = useState(() => takeLook(run.id));
  const on = since !== null;
  // the same cache as Engine's and Activity's health query; the tape's first page under its own key
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, enabled: on, retry: false, staleTime: 60_000 });
  const tape = useQuery({ queryKey: ["tape-since", run.id], queryFn: () => api.tape(run.id, 100), enabled: on, retry: false, staleTime: 60_000 });
  if (since === null || (health.isPending && tape.isPending) || (health.isError && tape.isError)) return null;
  const d = digest(since, health.data?.events, tape.data, run.id);
  if (!d.lines.length) return null;
  return (
    <section className="since-card" aria-labelledby="since-h" data-since={new Date(since).toISOString()}>
      <h2 id="since-h" className="since-h">Since you last looked <span className="since-when">· {timeBoth(new Date(since).toISOString())}</span></h2>
      <ul className="since-rows">
        {d.lines.map((l) => (
          <li key={l.key}>
            <Link to={l.to} className={`since-row ${l.tone}`}>
              <i className="since-dot" aria-hidden="true" />
              <span className="since-text">{l.text}</span>
              <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M9 6 L15 12 L9 18" /></svg>
            </Link>
          </li>
        ))}
      </ul>
      {d.more > 0 && <p className="since-more"><Link to="/activity">{d.more} more on Activity</Link></p>}
      {(health.isError || tape.isError) && <p className="since-more">Part of the record couldn't be read: {health.isError ? "engine events" : "buys and sells"}.</p>}
    </section>
  );
}
