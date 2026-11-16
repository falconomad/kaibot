// #210: the paper experiment's badge (with its cap and stop line) and the daily path's status card (Home, Engine).
// Everything comes from the engine heartbeat; with no `experiments` / `multi_day` fields nothing renders.
import { useState, type KeyboardEvent, type MouseEvent } from "react";
import { Sheet } from "./Sheet";
import type { Halt, Heartbeat } from "../lib/api";
import { ruleName } from "../lib/explain";
import { type Experiment, experimentView, haltTone, retirementUndone, experimentsFrom, findExperiment, multiDayFrom, noDecisionText, parseRetired, retiredText } from "../lib/experiment";
import { NAME } from "../brand/brand";

export const engineDetail = (beats: Heartbeat[] | undefined) => beats?.find((b) => b.process === "engine")?.detail ?? null;
export const experimentsOf = (beats: Heartbeat[] | undefined) => experimentsFrom(engineDetail(beats));

/** The calm badge: "Experiment · failed the ten-year test · paper only". Hover shows the one-line explanation; a tap
 *  (or Enter) opens it in a bottom sheet with the rule's limits (v4). Clicks never reach a clickable row underneath. */
export function ExperimentBadge({ e, limits = false }: { e: Experiment; limits?: boolean }) {
  const [open, setOpen] = useState(false);
  const v = experimentView(e);
  const stop = (ev: MouseEvent | KeyboardEvent) => ev.stopPropagation();
  return (
    <span className="exp" onClick={stop} onKeyDown={stop}>
      <button type="button" className="exp-btn" title={v.tip} aria-haspopup="dialog" aria-expanded={open}
        onClick={() => setOpen(true)}><span className="tag exp-tag">{v.text}</span></button>
      {limits && (v.cap || v.stop) && <span className="muted exp-limits">{[v.cap, v.stop].filter(Boolean).join(" · ")}</span>}
      <Sheet open={open} onClose={() => setOpen(false)} title={ruleName(e.strategy)}><ExperimentSheetBody e={e} /></Sheet>
    </span>
  );
}

/** The one experiment sheet (Home, Engine, Positions, Activity, every badge): the rule's name is the sheet's title,
 *  then one pill, the explanation, the cap and the stop line. Nothing is said twice (stakeholder, iPhone, 16 Nov). */
export function ExperimentSheetBody({ e, md }: { e: Experiment; md?: ReturnType<typeof multiDayFrom> }) {
  const v = experimentView(e);
  const st = md?.rules.find((r) => r.rule === e.strategy) ?? null;
  return (
    <>
      <span className="tag info exp-pill">{v.text}</span>
      <p style={{ marginTop: 12 }}>{v.tip}</p>
      {(v.cap || v.stop) && (
        <div className="kv">
          {v.cap && <><span>Most it may hold</span><span /><span className="v">{v.cap}</span></>}
          {v.stop && <><span>Stop line</span><span /><span className="v">{v.stop}</span></>}
        </div>
      )}
      {st?.retired && <p className="exp-ended" role="status">{retiredText(ruleName(e.strategy), parseRetired(st.note ?? "").fellPct, parseRetired(st.note ?? "").session)}</p>}
      {!st?.retired && st?.note && <p className="exp-note" role="status" title={`engine: ${st.note}`}>{noDecisionText(st.note)}</p>}
      <p className="muted" style={{ fontSize: 14 }}>There is no trade-by-trade control: {NAME} decides on its own within these limits.</p>
    </>
  );
}

/** A rule's badge when the heartbeat names it as an experiment; nothing otherwise. */
export function RuleExperiment({ code, beats, limits }: { code: string; beats: Heartbeat[] | undefined; limits?: boolean }) {
  const e = findExperiment(code, experimentsOf(beats));
  return e ? <> <ExperimentBadge e={e} limits={limits} /></> : null;
}

/** Home and Engine: each experiment with its limits, the daily path's "no decision" note, and a retirement as an
 *  amber "Experiment ended" state (not a red breach). Renders nothing for an engine without these fields. */
export function ExperimentCard({ beats }: { beats: Heartbeat[] | undefined }) {
  const detail = engineDetail(beats);
  const exps = experimentsFrom(detail);
  const md = multiDayFrom(detail);
  const rules = [...new Set([...exps.map((e) => e.strategy), ...(md?.rules.map((r) => r.rule) ?? [])])];
  if (!rules.length) return null;
  return (
    <section className="card" aria-labelledby="exp-h">
      <h2 id="exp-h">{exps.some((e) => e.experiment) ? "Paper experiment" : "Multi-day rules"}</h2>
      {rules.map((code) => {
        const e = exps.find((x) => x.strategy === code) ?? null;
        const st = md?.rules.find((r) => r.rule === code) ?? null;
        const v = e ? experimentView(e) : null;
        const ret = st?.retired ? parseRetired(st.note ?? "") : null;
        return (
          <div key={code} className="exp-rule">
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
              <b title={`code: ${code}`}>{ruleName(code)}</b>
              {e && <ExperimentBadge e={e} />}
              {st?.retired && <span className="tag downgrade">Experiment ended</span>}
            </div>
            {v && <p className="muted" style={{ margin: "6px 0 0", fontSize: 13 }}>{v.tip}</p>}
            {v && (v.cap || v.stop) && (
              <div className="kv" style={{ marginTop: 8 }}>
                {v.cap && <><span>Most it may hold</span><span /><span className="v">{v.cap}</span></>}
                {v.stop && <><span>Stop line</span><span /><span className="v">{v.stop}</span></>}
              </div>
            )}
            {ret && (
              <p className="exp-ended" role="status" title={st?.note ?? undefined}>{retiredText(ruleName(code), ret.fellPct, ret.session)}</p>
            )}
            {!st?.retired && st?.note && <p className="exp-note" role="status" title={`engine: ${st.note}`}>{noDecisionText(st.note)}</p>}
          </div>
        );
      })}
      {md?.bookEnded && !md.rules.some((r) => r.retired) && (
        <p className="exp-ended" role="status">The multi-day book reached its stop line: no new multi-day trades in this run.</p>
      )}
    </section>
  );
}

/** A halt row's tag (Engine, Report). Red stays for genuine breaches; the experiment's planned end (book stop line,
 *  retirement) is amber; switching a rule back on is info. Watchdog and dead-man notes keep no tag. */
export function HaltTag({ x, all }: { x: Halt; all: Halt[] }) {
  const t = haltTone(x.kind);
  if (t === "ended") return <> <span className="tag downgrade">{retirementUndone(x, all) ? "experiment ended · switched back on later" : "experiment ended"}</span></>;
  if (t === "info") return <> <span className="tag info">info</span></>;
  if (t === "breach") return <> <span className="tag veto">halt</span></>;
  return null;
}
