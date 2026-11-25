import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Logo } from "../components/Logo";
import { useGlow } from "../lib/glow";
import { BRAND, NAME } from "../brand/brand";
import { api, type Level } from "../lib/api";
import { LEVELS } from "../lib/model";
import { money } from "../lib/fmt";
import { Sheet } from "../components/Sheet";
import { experimentsOf } from "../components/Experiment";
import { experimentView } from "../lib/experiment";
import { ruleName } from "../lib/explain";

const STEPS = ["Amount", "Level", "Contract", "Start"];

export function Onboarding() {
  useGlow("start");
  const [step, setStep] = useState(1);
  const [amount, setAmount] = useState(5000);
  const [level, setLevel] = useState<Level>("balanced");
  const [agreed, setAgreed] = useState(false);
  const [levelInfo, setLevelInfo] = useState(false);
  const nav = useNavigate();
  const qc = useQueryClient();
  const start = useMutation({
    mutationFn: () => api.newRun(amount, level),
    onSuccess: () => { qc.invalidateQueries(); nav("/"); },
  });
  // What the engine runs now: with the multi-day path on (heartbeat `experiments`, #205) the contract says that the
  // paper experiment holds stocks overnight. No heartbeat, or an older engine: the day-only contract, as before.
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, retry: false, staleTime: 60_000 });
  const exps = experimentsOf(health.data?.heartbeat).filter((e) => e.experiment);
  const lv = LEVELS[level];
  const clamp = (v: number) => Math.min(100000, Math.max(1000, Math.round(v / 500) * 500));
  const canNext = step !== 3 || agreed;

  return (
    <div className="shell">
      <header className="top">
        <Link to="/" className="brand" aria-label={`${BRAND} home`}><Logo animate idle /></Link><span className="pill">PAPER</span>
      </header>
      <main style={{ maxWidth: 880, margin: "0 auto", width: "100%" }}>
        <div className="steps" aria-label="Progress">
          {STEPS.map((s, i) => <span key={s} className={i + 1 === step ? "on" : ""} aria-current={i + 1 === step ? "step" : undefined}>{i + 1} {s}</span>)}
        </div>

        {step === 1 && (
          <section className="card">
            <h1 style={{ margin: "0 0 4px", fontSize: 20 }}>How much paper money should {NAME} trade?</h1>
            <p className="muted" style={{ marginTop: 0 }}>Simulated money only. {NAME} keeps its own ledger at exactly this amount and never uses more.</p>
            <div style={{ display: "flex", alignItems: "center", gap: 12, maxWidth: "100%" }}>
              <button aria-label="Less" onClick={() => setAmount(clamp(amount - 500))}>−</button>
              <input type="text" inputMode="numeric" aria-label="Amount in US dollars" className="hero" style={{ width: 260, minWidth: 0, flex: "0 1 260px", height: 56, fontSize: 32, textAlign: "center" }}
                value={money(amount).replace(".00", "")} onChange={(e) => { const v = Number(e.target.value.replace(/[^0-9]/g, "")); if (v) setAmount(Math.min(100000, v)); }}
                onBlur={() => setAmount(clamp(amount))} />
              <button aria-label="More" onClick={() => setAmount(clamp(amount + 500))}>+</button>
            </div>
            <p className="muted" style={{ fontSize: 13 }}>$1,000 to $100,000 · trades in USD</p>
          </section>
        )}

        {step === 2 && (
          <section className="card">
            <h1 style={{ margin: "0 0 4px", fontSize: 20 }}>Choose a level</h1>
            <button type="button" className="linkbtn" aria-haspopup="dialog" onClick={() => setLevelInfo(true)} style={{ minHeight: 44, fontSize: 15 }}>What does a level mean?</button>
            <Sheet open={levelInfo} onClose={() => setLevelInfo(false)} title="What a level means">
              <p>A level sets how much {NAME} may risk: the loss allowed on one trade, the worst day before it stops for the day, the largest
                single position, and the drawdown that ends the run.</p>
              <p className="muted">{NAME} applies the level by itself. You can change it later; a change starts at the next session.</p>
            </Sheet>
            <p className="muted" style={{ marginTop: 0 }}>Higher levels risk more per trade and allow bigger losing days. None of them use leverage or shorts{exps.length
              ? `. The day rule ends each day flat; the ${exps.map((e) => ruleName(e.strategy)).join(" and ")} paper experiment holds stocks for weeks, within the level's cap and stop line.`
              : " or overnight holds."}</p>
            <div className="levels">
              {(Object.keys(LEVELS) as Level[]).map((k) => {
                const l = LEVELS[k];
                return (
                  <button key={k} aria-pressed={level === k} onClick={() => setLevel(k)}>
                    <b>{l.name}</b>
                    <span className="num muted">{l.risk}% risk per trade · {l.maxPositions} positions max</span>
                    <span>Worst allowed day <b className="num loss">−{money((amount * l.stop) / 100)}</b> <span className="muted">(−{l.stop}%)</span></span>
                    <span className="muted" style={{ fontSize: 13 }}>{l.strategies}</span>
                  </button>
                );
              })}
            </div>
            <p className="muted" style={{ fontSize: 13 }}>Only the loss cap is shown, because it is a rule, not a forecast. In the M0 replay no level showed a reliable edge.</p>
          </section>
        )}

        {step === 3 && (
          <section className="card">
            <h1 style={{ margin: "0 0 8px", fontSize: 20 }}>The contract</h1>
            {exps.length ? (
              <p><b>What {NAME} does:</b> trades liquid US stocks with paper money, on its own. The intraday rule ends each day flat.
                {exps.map((e) => {
                  const v = experimentView(e), same = e.level === level;
                  const limits = same && (v.cap || v.stop) ? ` (at ${lv.name}: ${[v.cap, v.stop].filter(Boolean).join(", ")})` : "";
                  return <span key={e.strategy}> The {ruleName(e.strategy)} paper experiment, which failed the ten-year test, holds stocks for weeks,
                    overnight included, within the level's cap and stop line{limits}. It never trades real money.</span>;
                })} No shorts or leverage, ever.</p>
            ) : (
              <p><b>What {NAME} does:</b> trades liquid US stocks with paper money, on its own, ending every day flat. No overnight positions, shorts or leverage.</p>
            )}
            <p><b>What to expect:</b> success means beating SPY and cash after costs, by a small margin. Expect 4–5 losing days in 10, and some red weeks.
              The replays so far show no reliable edge.</p>
            <p><b>What it won't do:</b> promise profit, or go live on its own. That takes 900+ trades over 3+ months passing a test set in advance, then a conversation.</p>
            <p><b>What you keep:</b> pause entries, and flatten and stop. Level changes apply from the next session.</p>
            <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} />
              I understand this is an experiment and losing days are part of it.
            </label>
          </section>
        )}

        {step === 4 && (
          <section className="card">
            <h1 style={{ margin: "0 0 8px", fontSize: 20 }}>Ready</h1>
            <p className="num" style={{ fontSize: 18 }}>{money(amount)} · {lv.name} · worst allowed day −{money((amount * lv.stop) / 100)}</p>
            <p className="muted">The run is queued for the engine on your Mac. If the market is open it starts at the next 5-minute check,
              otherwise at the next open (09:35 ET, 15:35 CET). Starting a new run closes the current run's positions first.</p>
            {start.isError && <p className="loss" role="alert">{String(start.error)}</p>}
          </section>
        )}

        <div style={{ display: "flex", gap: 12 }}>
          {step > 1 ? <button onClick={() => setStep(step - 1)}>Back</button> : <Link to="/"><button>Cancel</button></Link>}
          <span className="spacer" />
          {step < 4
            ? <button className="primary" disabled={!canNext} onClick={() => setStep(step + 1)}>Next</button>
            : <button className="primary" disabled={start.isPending} onClick={() => start.mutate()}>{start.isPending ? "Queuing…" : "Start paper run"}</button>}
        </div>
      </main>
    </div>
  );
}
