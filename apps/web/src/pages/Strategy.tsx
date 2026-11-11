// M5 (decision 12, S45/S46/S47): the Strategy page and its Fine-tune view. The owner shapes what the engine may do;
// every control is bounded by `kb_get_config.bounds`, so a setting can only make Kaibot pickier or smaller. Presets and
// Boldness are shortcuts that write the same body fields. Changes apply at the next session open as a new version.
// Paper trading only: there is no live-trading control here, and server text is always rendered as plain text.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { livePot, useRunCtx } from "../components/Shell";
import { PillSwitch, RadioGroup, Slider, Stepper, Switch } from "../components/ConfigControls";
import { StockBadge } from "../components/StockBadge";
import { api, type Level } from "../lib/api";
import {
  CAUTION, HOURS, LABEL, MIN_FOCUS, MIN_WINDOW_MIN, PRESETS, SESSION_OPEN, STEP, STRATEGY_NAME, boldnessSteps, cautionOn, clampBody, diffConfig,
  dollars, focusSet, fmtNum, hoursShape, hoursWindow, levelDefaults, matchBoldness, matchPreset, nextOpenEt, normalizeBounds, openLabel,
  presetBody, setBoldness, setCaution, toHhmm, toMin, toggleTheme, validateBody,
  type AdvKey, type Bounds, type ConfigBody, type ConfigResp, type NumKey, type RiskKey, type Themes, type TimingNumKey,
} from "../lib/config";
import { estimateTrades, paceText, type EstimatorData } from "../lib/derive";
import { LEVELS } from "../lib/model";
import { money } from "../lib/fmt";
import estimatorJson from "../data/estimator.json";
import themesJson from "../data/themes.json";

const EST = estimatorJson as unknown as EstimatorData;
const UNIVERSE = Object.keys(EST.dip_per_month);
const usd0 = (v: number) => "$" + Math.round(v).toLocaleString("en-US");
const LOCKED = [
  "News veto: no entry on company news in the last 24 h",
  "Sell everything at 15:55 ET, every day",
  "No shorts, no leverage, no overnight holds",
  "Broker-side stop and target on every trade, fresh quotes only",
];

interface Ctx { b: Bounds; themes: Themes; universe: string[]; level: Level; levelName: string }

function useCtx(resp: ConfigResp): Ctx {
  return useMemo(() => {
    const level = (resp.level ?? "balanced") as Level;
    const themes = resp.themes && Object.keys(resp.themes).length ? resp.themes : (themesJson as Themes);
    const universe = Array.isArray(resp.universe) && resp.universe.length ? resp.universe : UNIVERSE;
    return { b: normalizeBounds(resp.bounds, level), themes, universe, level, levelName: LEVELS[level]?.name ?? level };
  }, [resp]);
}

export function Strategy() {
  const { run, overview } = useRunCtx();
  const q = useQuery({ queryKey: ["config", run.id], queryFn: () => api.getConfig(run.id), enabled: run.kind === "paper", staleTime: 60_000,
    refetchOnWindowFocus: false });
  if (run.kind !== "paper")
    return (
      <section className="card">
        <h2>Strategy</h2>
        <p className="muted" style={{ margin: 0 }}>This is a replay on past data, so there is nothing to tune. Strategy settings belong to a paper run:
          pick it in the run menu at the top.</p>
      </section>
    );
  if (q.isPending) return <StrategySkeleton />;
  if (q.isError || !q.data)
    return (
      <section className="card" role="alert">
        <h2>Strategy settings aren't available</h2>
        <p className="muted" style={{ margin: 0 }}>The server couldn't load your settings: <span className="loss">{String((q.error as Error)?.message ?? "no data")}</span>.
          Kaibot keeps trading on its level's defaults meanwhile.</p>
      </section>
    );
  const pot = livePot(run, overview).pot;
  return <Editor key={run.id} runId={run.id} resp={q.data} pot={Number.isFinite(pot) ? pot : run.amount_usd} />;
}

function StrategySkeleton() {
  return (
    <div className="kb-strategy" aria-busy="true" aria-label="Loading your setup">
      <span className="sk" style={{ width: 220, height: 40, borderRadius: 12, flex: "none" }} />
      <div className="kb-presets">{[0, 1, 2, 3].map((i) => <span key={i} className="sk" style={{ height: 110, borderRadius: 20 }} />)}</div>
      <div className="kb-twocol">
        <span className="sk" style={{ height: 420, borderRadius: 24 }} />
        <div className="sk-stack"><span className="sk" style={{ height: 190, borderRadius: 24 }} /><span className="sk" style={{ height: 150, borderRadius: 24 }} /></div>
      </div>
    </div>
  );
}

type SaveState = { kind: "idle" | "saving" | "saved" } | { kind: "error"; text: string };
const errText = (e: unknown) => {
  const x = e as { message?: string; code?: string };
  return x?.code === "22023" ? `The server refused this setting: ${x.message}` : `Couldn't reach the server: ${x?.message ?? String(e)}`;
};

function Editor({ runId, resp, pot }: { runId: number; resp: ConfigResp; pot: number }) {
  const ctx = useCtx(resp);
  const { b, themes, universe } = ctx;
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const fine = params.get("view") === "fine";

  const baseline = useMemo(() => clampBody(resp.pending?.body ?? resp.applied?.body ?? null, b, themes, universe), [resp, b, themes, universe]);
  const [body, setBody] = useState<ConfigBody>(() => clampBody(resp.draft?.body ?? resp.pending?.body ?? resp.applied?.body ?? null, b, themes, universe));
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const [busy, setBusy] = useState(false);
  const [actionErr, setActionErr] = useState<string | null>(null);
  const [justApplied, setJustApplied] = useState<{ version: number; applies_from: string } | null>(null);
  const [sheet, setSheet] = useState(false);
  const dirty = useRef(false);
  const timer = useRef<number | undefined>(undefined);

  // Drafts survive a reload: save ~800 ms after the last edit. The body is always clamped first.
  useEffect(() => {
    if (!dirty.current) return;
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      setSave({ kind: "saving" });
      api.saveConfig(runId, clampBody(body, b, themes, universe)).then(() => { dirty.current = false; setSave({ kind: "saved" }); },
        (e) => setSave({ kind: "error", text: errText(e) }));
    }, 800);
    return () => window.clearTimeout(timer.current);
  }, [body, runId, b, themes, universe]);

  const edit = (next: ConfigBody) => {
    const x = clampBody(next, b, themes, universe);
    x.preset = matchPreset(x, b, themes, universe);
    x.boldness = matchBoldness(x.risk, b);
    dirty.current = true;
    setJustApplied(null);
    setBody(x);
  };

  const est = estimateTrades(body, EST, themes, b.entry);
  const estBase = estimateTrades(baseline, EST, themes, b.entry);
  const usd = dollars(body, pot);
  const diff = diffConfig(baseline, body, themes);
  const errors = validateBody(body, b, themes);
  const canApply = diff.length > 0 && errors.length === 0 && !busy;
  const pending = justApplied ?? (resp.pending ? { version: resp.pending.version, applies_from: resp.pending.applies_from } : null);
  const nextVersion = Math.max(0, ...(resp.versions ?? []).map((v) => Number(v.version) || 0), resp.applied?.version ?? 0, resp.pending?.version ?? 0) + 1;

  async function apply() {
    setBusy(true); setActionErr(null);
    window.clearTimeout(timer.current);
    try {
      await api.saveConfig(runId, clampBody(body, b, themes, universe));
      dirty.current = false;
      const r = await api.applyConfig(runId);
      setJustApplied(r);
      setSheet(false);
      await qc.invalidateQueries({ queryKey: ["config", runId] });
      qc.invalidateQueries({ queryKey: ["chapters", runId] });
    } catch (e) { setActionErr(errText(e)); } finally { setBusy(false); }
  }
  async function reset() {
    setBusy(true); setActionErr(null);
    window.clearTimeout(timer.current);
    try {
      await api.resetConfig(runId);
      dirty.current = false;
      setBody(levelDefaults(b));
      setSave({ kind: "idle" });
      qc.invalidateQueries({ queryKey: ["config", runId] });
    } catch (e) { setActionErr(errText(e)); } finally { setBusy(false); }
  }
  const undo = () => edit(baseline);
  const view = (v: "fine" | null) => { const p = new URLSearchParams(params); if (v) p.set("view", v); else p.delete("view"); setParams(p); window.scrollTo({ top: 0 }); };

  const shared = { body, edit, ctx, baseline, est, usd, pot };
  return (
    <div className="kb-strategy">
      {fine ? <FineTune {...shared} back={() => view(null)} onReset={reset} busy={busy} /> : <TunePage {...shared} toFine={() => view("fine")} />}
      {pending && (
        <p className="banner warn" role="status" style={{ margin: 0 }}>
          <b>Version {pending.version} is scheduled.</b> It applies at the next open, {openLabel(pending.applies_from)}. Open trades keep the rules they were opened under.
        </p>
      )}
      <OutcomeBar est={est.perMonth} maxLoss={usd.maxLoss} changes={diff.length} save={save} pendingDate={pending?.applies_from ?? null}
        canApply={canApply} invalid={errors[0] ?? null} onReview={() => { setActionErr(null); setSheet(true); }} onUndo={diff.length ? undo : null} />
      <ReviewSheet open={sheet} onClose={() => setSheet(false)} diff={diff} est={est} estBase={estBase.perMonth} maxLoss={usd.maxLoss}
        maxLossBase={dollars(baseline, pot).maxLoss} nextOpen={nextOpenEt()} nextVersion={nextVersion} canApply={canApply} busy={busy}
        error={actionErr ?? (save.kind === "error" ? save.text : null)} errors={errors} onApply={apply} onReset={reset} levelName={ctx.levelName} />
    </div>
  );
}

interface Shared {
  body: ConfigBody; edit: (b: ConfigBody) => void; ctx: Ctx; baseline: ConfigBody; est: ReturnType<typeof estimateTrades>;
  usd: ReturnType<typeof dollars>; pot: number;
}

// ---------------------------------------------------------------- Strategy (one screen)
function TunePage({ body, edit, ctx, est, usd, pot, toFine }: Shared & { toFine: () => void }) {
  const { b, themes, universe, levelName } = ctx;
  const preset = matchPreset(body, b, themes, universe);
  return (<>
    <div className="kb-head">
      <div style={{ flex: "1 1 360px", display: "flex", flexDirection: "column", gap: 6 }}>
        <h1 className="kb-h1">Strategy</h1>
        <span className="muted">Kaibot trades on its own. Pick a starting point, nudge it, done. Settings can only make it pickier or smaller than {levelName}.</span>
      </div>
      <button type="button" className="kb-ghost" onClick={toFine}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M4 7 H14 M18 7 H20 M4 17 H8 M12 17 H20" strokeLinecap="round" /><circle cx="16" cy="7" r="2" /><circle cx="10" cy="17" r="2" /></svg>
        Fine-tune all settings
      </button>
    </div>

    <section aria-labelledby="pr-h" className="kb-sect">
      <h2 id="pr-h" className="cap kb-cap">Start from</h2>
      <RadioGroup labelledBy="pr-h" className="kb-presets" optClass="kb-preset" value={preset}
        onChange={(k) => { if (k !== "custom") edit(presetBody(k, b, themes, universe)); }}
        options={[...PRESETS.map((p) => {
          const x = presetBody(p.key, b, themes, universe);
          const e = estimateTrades(x, EST, themes, b.entry);
          return { value: p.key, label: p.name, content: <PresetCard icon={p.icon} name={p.name} blurb={p.blurb.replace("your level", levelName)}
            stats={`≈ ${Math.round(e.perMonth)} trades/mo · ${usd0(dollars(x, pot).maxLoss)} max a trade`} /> };
        }), { value: "custom" as const, label: "Custom", disabled: preset !== "custom",
          content: <PresetCard icon="✎" name="Custom" blurb={preset === "custom" ? "Your own mix. Keep shaping it below." : "Change anything below and it lands here."}
            stats={preset === "custom" ? `≈ ${Math.round(est.perMonth)} trades/mo · ${usd0(usd.maxLoss)} max a trade` : "—"} /> }]} />
    </section>

    <div className="kb-twocol">
      <FocusCard body={body} edit={edit} ctx={ctx} toFine={toFine} />
      <div className="sk-stack" style={{ gap: 20 }}>
        <BoldCard body={body} edit={edit} ctx={ctx} usd={usd} />
        <HoursCard body={body} edit={edit} ctx={ctx} />
        <SafetyCard body={body} edit={edit} ctx={ctx} toFine={toFine} />
      </div>
    </div>
  </>);
}

function PresetCard({ icon, name, blurb, stats }: { icon: string; name: string; blurb: string; stats: string }) {
  return (<>
    <span className="kb-preset-top"><span aria-hidden="true" style={{ fontSize: 20 }}>{icon}</span><span className="kb-preset-name">{name}</span><span aria-hidden="true" className="kb-radio" /></span>
    <span className="kb-preset-blurb">{blurb}</span>
    <span className="num kb-preset-stats">{stats}</span>
  </>);
}

function themeRate(symbols: string[], body: ConfigBody, b: Bounds) {
  const x = { ...body, focus: { themes: [], symbols, exclude: [], mode: "only" as const } };
  return estimateTrades(x, EST, {}, b.entry).perMonth;
}

function FocusCard({ body, edit, ctx, toFine }: { body: ConfigBody; edit: (b: ConfigBody) => void; ctx: Ctx; toFine: () => void }) {
  const { b, themes, universe } = ctx;
  const set = focusSet(body, themes);
  const all = body.focus.mode === "all";
  const tooFew = body.focus.mode === "only" && set.length < MIN_FOCUS;
  const note = tooFew ? `“Only” needs at least ${MIN_FOCUS} stocks. Add a theme or switch to Prefer.`
    : all ? `All ${universe.length} stocks. Pick themes to narrow it.`
    : `${body.focus.mode === "only" ? "Only these" : "These first, then the rest"} · ${set.length} stocks: ${set.slice(0, 4).join(", ")}${set.length > 4 ? ` +${set.length - 4}` : ""}`;
  const cards: { key: string | null; name: string; syms: string[]; on: boolean }[] = [
    { key: null, name: "All stocks", syms: universe, on: all },
    ...Object.entries(themes).map(([k, t]) => ({ key: k, name: t.label, syms: t.symbols, on: !all && body.focus.themes.includes(k) })),
  ];
  return (
    <section className="card kb-card" aria-labelledby="fo-h">
      <div className="kb-row">
        <h2 id="fo-h" className="kb-h2" style={{ flex: 1 }}>Focus</h2>
        <RadioGroup label="Focus mode" className="kb-seg" optClass="kb-seg-opt" value={all ? null : body.focus.mode}
          onChange={(m) => edit({ ...body, focus: { ...body.focus, mode: m } })}
          options={[{ value: "prefer" as const, label: "Prefer", disabled: all }, { value: "only" as const, label: "Only", disabled: all }]} />
      </div>
      <div className="kb-tgrid" role="group" aria-label="Themes">
        {cards.map((c) => (
          <button key={c.key ?? "_all"} type="button" aria-pressed={c.on} className="kb-tcard" onClick={() => edit(toggleTheme(body, c.key))}>
            <span aria-hidden="true" className="kb-faces">{(c.key ? c.syms : ["AAPL", "JPM", "XOM"]).slice(0, 3).map((s) => <StockBadge key={s} symbol={s} size={24} />)}</span>
            <span className="kb-tname">{c.name}</span>
            <span className="num kb-tmeta">{c.syms.length} stocks · ≈ {Math.round(themeRate(c.syms, body, b) * 10) / 10}/mo</span>
            {c.on && <span aria-hidden="true" className="kb-check">✓</span>}
          </button>
        ))}
      </div>
      <div className="kb-row" style={{ fontSize: 13 }}>
        <span className={`num swap ${tooFew ? "kb-warn" : "muted"}`} style={{ flex: 1 }} role={tooFew ? "alert" : undefined}>{note}</span>
        <button type="button" className="linkbtn" style={{ fontSize: 13, fontWeight: 600, textDecoration: "none" }} onClick={toFine}>Exclude a stock</button>
      </div>
      {!all && body.focus.mode === "prefer" && <span className="muted" style={{ fontSize: 12 }}>Prefer still trades all {universe.length} stocks; your focus goes first when slots compete.</span>}
    </section>
  );
}

function BoldCard({ body, edit, ctx, usd }: { body: ConfigBody; edit: (b: ConfigBody) => void; ctx: Ctx; usd: ReturnType<typeof dollars> }) {
  const { b, levelName } = ctx;
  const steps = boldnessSteps(b);
  const cur = matchBoldness(body.risk, b);
  const lastOn = steps.map((s) => s.enabled).lastIndexOf(true);
  const name = cur === null ? "Fine-tuned" : cur === lastOn ? `Max for ${levelName}` : steps[cur].name;
  return (
    <section className="card kb-card" aria-labelledby="bo-h">
      <div className="kb-row" style={{ alignItems: "baseline" }}>
        <h2 id="bo-h" className="kb-h2" style={{ flex: 1 }}>Boldness</h2>
        <span className={`swap ${cur === lastOn ? "kb-warn" : ""}`} style={{ fontWeight: 600 }} aria-live="polite">{name}</span>
      </div>
      <RadioGroup labelledBy="bo-h" className="kb-bold" optClass="kb-bold-step" value={cur} onChange={(i) => edit(setBoldness(body, i, b))}
        options={steps.map((s, i) => ({
          value: i, label: i === lastOn ? `${s.name} (max for ${levelName})` : s.enabled ? s.name : `${s.name}: above ${levelName}`, disabled: !s.enabled,
          className: cur !== null && i <= cur ? (i === cur ? "on" : "past") : undefined,
          content: <span aria-hidden="true" className="tick" style={{ height: 8 + i * 5 }} />,
        }))} />
      <div className="kb-row muted" style={{ justifyContent: "space-between", fontSize: 12 }}><span>Careful</span><span>Max for {levelName}</span></div>
      {lastOn < 4 && <span className="muted" style={{ fontSize: 12 }}>Steps above {steps[lastOn].name} would exceed {levelName}, so they're off.</span>}
      <div className="num kb-stats">
        <Stat k="Max loss a trade" v={usd0(usd.maxLoss)} />
        <Stat k="Largest position" v={usd0(usd.position)} />
        <Stat k="Stops for the day at" v={"−" + usd0(usd.dailyStop)} />
        <Stat k="Open at once" v={`${body.risk.max_positions} trades`} />
      </div>
    </section>
  );
}
const Stat = ({ k, v }: { k: string; v: string }) => <span className="kb-stat"><span>{k}</span><b className="swap">{v}</b></span>;

function DayBar({ body, b, wide }: { body: ConfigBody; b: Bounds; wide?: boolean }) {
  const W = wide ? 780 : 400;
  const x = (m: number) => ((m - SESSION_OPEN) / 390) * W;
  const s = toMin(body.timing.entry_start), e = toMin(body.timing.entry_end), q = body.timing.event_window_min;
  const fomc = 14 * 60;
  return (
    <svg viewBox={`0 0 ${W} 40`} width="100%" height="40" role="img"
      aria-label={`New trades from ${body.timing.entry_start} to ${body.timing.entry_end} Eastern; Fed and CPI quiet window plus or minus ${q} minutes; sell-all at 15:55`}>
      <rect x="0" y="6" width={W} height="14" rx="7" fill="var(--surface-2)" />
      <rect className="kb-anim" x={x(s)} y="6" width={Math.max(0, x(e) - x(s))} height="14" rx="7" fill="var(--sea)" />
      <rect className="kb-anim" x={x(fomc - q)} y="3" width={x(fomc + q) - x(fomc - q)} height="20" rx="4" fill="var(--warn)" opacity=".45" />
      <line x1={x(15 * 60 + 55)} y1="0" x2={x(15 * 60 + 55)} y2="26" stroke="var(--loss)" strokeWidth="2" />
      <text x="0" y="38" fill="var(--stale)" fontSize="10">09:30</text>
      <text x={W / 2} y="38" fill="var(--stale)" fontSize="10" textAnchor="middle">12:45</text>
      <text x={W} y="38" fill="var(--stale)" fontSize="10" textAnchor="end">16:00</text>
    </svg>
  );
}

function HoursCard({ body, edit, ctx }: { body: ConfigBody; edit: (b: ConfigBody) => void; ctx: Ctx }) {
  const { b } = ctx;
  const shape = hoursShape(body, b);
  return (
    <section className="card kb-card" aria-labelledby="hr-h">
      <h2 id="hr-h" className="kb-h2">Trading hours</h2>
      <RadioGroup labelledBy="hr-h" className="kb-pills" optClass="kb-pill" value={shape}
        onChange={(k) => edit({ ...body, timing: { ...body.timing, ...hoursWindow(k, b) } })}
        options={HOURS.map((h) => ({ value: h.key, label: h.name }))} />
      <DayBar body={body} b={b} />
      <span className="num swap muted" style={{ fontSize: 12 }}>
        {shape === null && "Custom hours (Fine-tune) · "}New trades {body.timing.entry_start}–{body.timing.entry_end} ET · amber = Fed/CPI quiet (±{body.timing.event_window_min} min) · red = sell-all 15:55
      </span>
    </section>
  );
}

function SafetyCard({ body, edit, ctx, toFine }: { body: ConfigBody; edit: (b: ConfigBody) => void; ctx: Ctx; toFine: () => void }) {
  const { b } = ctx;
  const on = cautionOn(body, b);
  const t = body.timing;
  const note = [on.vix ? `No new trades when VIX is above ${t.vix_max}.` : null,
    on.pause ? `${t.pause_losses} losses in a row → ${t.pause_minutes} min pause.` : null,
    on.quiet ? `±${t.event_window_min} min around Fed and CPI.` : null].filter(Boolean).join(" ")
    || `Standard: VIX above ${b.vix_max.max} stops new trades, ${b.pause_losses.max} losses → ${b.pause_minutes.min} min pause, ±${b.event_window_min.min} min around Fed and CPI.`;
  return (
    <section className="card kb-card" aria-labelledby="sf-h">
      <h2 id="sf-h" className="kb-h2">Extra caution</h2>
      <div className="kb-pills">
        {CAUTION.map((c) => <PillSwitch key={c.key} label={c.name} on={on[c.key]} onChange={(v) => edit(setCaution(body, c.key, v, b))} />)}
      </div>
      <span className="swap muted" style={{ fontSize: 12, lineHeight: 1.5 }}>{note}</span>
      <details className="kb-locked">
        <summary>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--gain)" strokeWidth="2.5" aria-hidden="true"><path d="M5 12 L10 17 L19 7" strokeLinecap="round" strokeLinejoin="round" /></svg>
          {LOCKED.length} safety rules always on
        </summary>
        <ul>{LOCKED.map((l) => <li key={l}>{l}</li>)}</ul>
      </details>
      <button type="button" className="linkbtn" style={{ alignSelf: "flex-start", fontSize: 13, textDecoration: "none" }} onClick={toFine}>Exact minutes and levels in Fine-tune</button>
    </section>
  );
}

// ---------------------------------------------------------------- Fine-tune (every MVP control)
function FineTune({ body, edit, ctx, baseline, est, usd, pot, back, onReset, busy }: Shared & { back: () => void; onReset: () => void; busy: boolean }) {
  const { b, levelName } = ctx;
  const [open, setOpen] = useState<string | null>("risk_per_trade_pct");
  const num = (k: NumKey) => (k in body.risk ? body.risk[k as RiskKey] : k in body.advanced ? body.advanced[k as AdvKey] : body.timing[k as TimingNumKey]);
  const base = (k: NumKey) => (k in baseline.risk ? baseline.risk[k as RiskKey] : k in baseline.advanced ? baseline.advanced[k as AdvKey] : baseline.timing[k as TimingNumKey]);
  const setNum = (k: NumKey, v: number) => {
    if (k in body.risk) edit({ ...body, risk: { ...body.risk, [k]: v } });
    else if (k in body.advanced) edit({ ...body, advanced: { ...body.advanced, [k]: v } });
    else edit({ ...body, timing: { ...body.timing, [k]: v } });
  };
  const note: Partial<Record<NumKey, string>> = {
    risk_per_trade_pct: `≈ ${usd0(usd.maxLoss)} max loss a trade`, max_position_pct: `≈ ${usd0(usd.position)} max a position`,
    daily_stop_pct: `≈ −${usd0(usd.dailyStop)} stops the day`, run_drawdown_end_pct: `ends near ${usd0(usd.runEndsNear)}`,
    max_positions: `${levelName} max ${b.max_positions.max}`, max_entries_per_day: `${levelName} max ${b.max_entries_per_day.max}`,
    vix_max: num("vix_max") < b.vix_max.max ? "stricter than the crisis gate" : "only the crisis gate", pause_minutes: num("pause_minutes") > b.pause_minutes.min ? "longer than standard" : "standard",
  };
  const slider = (k: NumKey, label = LABEL[k]) => (
    <Slider key={k} id={`ft-${k}`} label={label} value={num(k)} min={b[k].min} max={b[k].max} step={STEP[k]} onChange={(v) => setNum(k, v)}
      text={fmtNum(k, num(k))} lo={fmtNum(k, b[k].min)} hi={fmtNum(k, b[k].max)} note={note[k]} />
  );
  const row = (k: NumKey, inner?: ReactNode, value?: string, changed?: boolean) => (
    <Row key={k} id={k} label={LABEL[k]} value={value ?? fmtNum(k, num(k))} changed={changed ?? num(k) !== base(k)} open={open === k}
      onToggle={() => setOpen(open === k ? null : k)}>{inner ?? slider(k)}</Row>
  );
  const s = toMin(body.timing.entry_start), e = toMin(body.timing.entry_end);
  const lo = toMin(b.entry.start), hi = toMin(b.entry.end);
  const setWin = (ns: number, ne: number) => edit({ ...body, timing: { ...body.timing, entry_start: toHhmm(ns), entry_end: toHhmm(ne) } });
  return (<>
    <div className="kb-fine-head">
      <button type="button" className="linkbtn" style={{ fontSize: 14, fontWeight: 600, textDecoration: "none", alignSelf: "flex-start" }} onClick={back}>← Strategy</button>
      <h1 className="kb-h1">Fine-tune</h1>
      <span className="muted">Every setting, one row each. Pickier or smaller, never riskier than {levelName}.</span>
      <div className="num muted" style={{ display: "flex", flexWrap: "wrap", gap: "6px 16px", fontSize: 14 }}>
        <span><b style={{ color: "var(--ink)" }}>≈ {Math.round(est.perMonth)}</b> trades a month</span>
        <span><b style={{ color: "var(--ink)" }}>{usd0(usd.maxLoss)}</b> max loss a trade</span>
        <span><b style={{ color: "var(--ink)" }}>{usd0(usd.position)}</b> largest position</span>
        <span><b style={{ color: "var(--ink)" }}>−{usd0(usd.dailyStop)}</b> daily stop</span>
        <span>on a {money(pot)} pot</span>
      </div>
    </div>

    <FocusEditor body={body} edit={edit} ctx={ctx} />

    <Group title="Risk" sub={`Can only go down from ${levelName}.`}>
      {row("risk_per_trade_pct")}
      {row("daily_stop_pct")}
      {row("max_positions", <Stepper label={LABEL.max_positions} value={body.risk.max_positions} min={b.max_positions.min} max={b.max_positions.max}
        text={`${body.risk.max_positions} at once`} onChange={(v) => setNum("max_positions", v)} />)}
      {row("max_position_pct")}
      {row("max_entries_per_day")}
      {row("run_drawdown_end_pct")}
    </Group>

    <Group title="Strategies" sub="At least one stays on.">
      {(["dip_reversal", "spy_late_momentum"] as const).map((k) => {
        const allowed = b.strategies.includes(k);
        return (
          <div key={k} className="kb-srow-static">
            <span style={{ flex: 1, display: "flex", flexDirection: "column", gap: 2 }}>
              <span>{STRATEGY_NAME[k]}</span>
              <span className="muted" style={{ fontSize: 12 }}>{!allowed ? `Not part of ${levelName}` : k === "dip_reversal" ? "No-news dips in the 59 stocks" : "Up to 1 a day on qualifying days · not estimated yet"}</span>
            </span>
            <Switch label={STRATEGY_NAME[k]} on={body.strategies[k]} disabled={!allowed || (body.strategies[k] && !Object.entries(body.strategies).some(([o, v]) => o !== k && v))}
              onChange={(v) => edit({ ...body, strategies: { ...body.strategies, [k]: v } })} />
          </div>
        );
      })}
      <div className="kb-srow-static"><span className="muted" style={{ flex: 1, fontSize: 13 }}>Opening-range breakout · News continuation</span><span className="tag downgrade">under test</span></div>
    </Group>

    <Group title="Timing">
      <figure style={{ margin: 0, padding: "18px 20px 6px" }}><DayBar body={body} b={b} wide /></figure>
      <Row id="entry_start" label="First new trade" value={body.timing.entry_start} changed={body.timing.entry_start !== baseline.timing.entry_start}
        open={open === "entry_start"} onToggle={() => setOpen(open === "entry_start" ? null : "entry_start")}>
        <Slider label="First new trade" value={s} min={lo} max={hi - MIN_WINDOW_MIN} step={5} text={`${toHhmm(s)} ET`} lo={b.entry.start} hi={toHhmm(hi - MIN_WINDOW_MIN)}
          note={s > lo ? `skips the first ${s - SESSION_OPEN} min` : `from ${b.entry.start}`} onChange={(v) => setWin(v, Math.max(e, v + MIN_WINDOW_MIN))} />
      </Row>
      <Row id="entry_end" label="Last new trade" value={body.timing.entry_end} changed={body.timing.entry_end !== baseline.timing.entry_end}
        open={open === "entry_end"} onToggle={() => setOpen(open === "entry_end" ? null : "entry_end")}>
        <Slider label="Last new trade" value={e} min={lo + MIN_WINDOW_MIN} max={hi} step={5} text={`${toHhmm(e)} ET`} lo={toHhmm(lo + MIN_WINDOW_MIN)} hi={b.entry.end}
          note={`${levelName} max ${b.entry.end}`} onChange={(v) => setWin(Math.min(s, v - MIN_WINDOW_MIN), v)} />
      </Row>
      {row("event_window_min")}
      {row("vix_max")}
      <Row id="pause" label="Pause after a losing streak" value={`${body.timing.pause_losses} losses → ${body.timing.pause_minutes} min`}
        changed={body.timing.pause_losses !== baseline.timing.pause_losses || body.timing.pause_minutes !== baseline.timing.pause_minutes}
        open={open === "pause"} onToggle={() => setOpen(open === "pause" ? null : "pause")}>
        <span className="muted" style={{ fontSize: 12 }}>Losses in a row</span>
        <Stepper label={LABEL.pause_losses} value={body.timing.pause_losses} min={b.pause_losses.min} max={b.pause_losses.max}
          text={`${body.timing.pause_losses} losses`} onChange={(v) => setNum("pause_losses", v)} />
        <span className="muted" style={{ fontSize: 12 }}>Pause length</span>
        {slider("pause_minutes")}
      </Row>
    </Group>

    <Group title="More">
      <Row id="adv" label="Advanced liquidity floors" value={`${fmtNum("min_price_usd", body.advanced.min_price_usd)} · ${fmtNum("min_adv_usd", body.advanced.min_adv_usd)} · ${fmtNum("max_spread_bp", body.advanced.max_spread_bp)}`}
        changed={(["min_price_usd", "min_adv_usd", "max_spread_bp"] as const).some((k) => body.advanced[k] !== baseline.advanced[k])}
        open={open === "adv"} onToggle={() => setOpen(open === "adv" ? null : "adv")}>
        {(["min_price_usd", "min_adv_usd", "max_spread_bp"] as const).map((k) => (
          <div key={k} style={{ display: "flex", flexDirection: "column", gap: 2 }}><span className="muted" style={{ fontSize: 12 }}>{LABEL[k]} (tighten only)</span>{slider(k)}</div>
        ))}
      </Row>
      <div className="kb-locked-head"><LockIcon /> Safety rules <span className="spacer" /><b className="gain">Always on</b></div>
      <ul className="kb-locked-rows" aria-label="Locked safety rules">
        {LOCKED.map((l) => <li key={l}><LockIcon />{l}<span className="sr-only"> (locked, cannot be changed)</span></li>)}
      </ul>
    </Group>

    <div className="kb-row" style={{ gap: 12 }}>
      <button type="button" onClick={onReset} disabled={busy}>Reset to level defaults</button>
      <span className="muted" style={{ fontSize: 13, flex: "1 1 260px" }}>Coming later, once each passes a replay: exit style presets, more stocks, your own themes, days off, news-room strictness.</span>
    </div>
  </>);
}

const LockIcon = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--gain)" strokeWidth="2" aria-hidden="true"><rect x="5" y="11" width="14" height="10" rx="2" /><path d="M8 11 V8 a4 4 0 0 1 8 0 V11" /></svg>
);

function Group({ title, sub, children }: { title: string; sub?: string; children: ReactNode }) {
  const id = `g-${title.toLowerCase().replace(/\W+/g, "-")}`;
  return (
    <section className="kb-sect" aria-labelledby={id}>
      <div style={{ padding: "0 4px" }}><h2 id={id} className="cap kb-cap">{title}</h2>{sub && <span className="muted" style={{ fontSize: 13 }}>{sub}</span>}</div>
      <div className="card kb-group">{children}</div>
    </section>
  );
}

function Row({ id, label, value, changed, open, onToggle, children }: {
  id: string; label: string; value: string; changed: boolean; open: boolean; onToggle: () => void; children: ReactNode;
}) {
  return (
    <div className="kb-srow">
      <button type="button" aria-expanded={open} aria-controls={`row-${id}`} onClick={onToggle}>
        <span style={{ flex: 1 }}>{label}</span>
        {changed && <span className="kb-dot" role="img" aria-label="changed" />}
        <span className="num" style={{ fontWeight: 600 }}>{value}</span>
        <svg className={`kb-chev${open ? " open" : ""}`} width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--stale)" strokeWidth="2" aria-hidden="true"><path d="M9 6 L15 12 L9 18" strokeLinecap="round" strokeLinejoin="round" /></svg>
      </button>
      {open && <div id={`row-${id}`} className="kb-expand" role="region" aria-label={label}>{children}</div>}
    </div>
  );
}

function FocusEditor({ body, edit, ctx }: { body: ConfigBody; edit: (b: ConfigBody) => void; ctx: Ctx }) {
  const { themes, universe } = ctx;
  const [add, setAdd] = useState("");
  const set = focusSet(body, themes);
  const all = body.focus.mode === "all";
  const addable = universe.filter((s) => !set.includes(s));
  const doAdd = () => {
    const s = add.trim().toUpperCase();
    if (!universe.includes(s)) return;
    const f = body.focus;
    edit({ ...body, focus: { ...f, symbols: f.symbols.includes(s) ? f.symbols : [...f.symbols, s], exclude: f.exclude.filter((x) => x !== s),
      mode: f.mode === "all" ? "prefer" : f.mode } });
    setAdd("");
  };
  const tooFew = body.focus.mode === "only" && set.length < MIN_FOCUS;
  return (
    <section className="card kb-card" aria-labelledby="fe-h" id="focus">
      <div className="kb-row">
        <h2 id="fe-h" className="kb-h2" style={{ flex: 1 }}>What to trade</h2>
        <RadioGroup label="Focus mode" className="kb-seg" optClass="kb-seg-opt" value={body.focus.mode}
          onChange={(m) => edit(m === "all" ? toggleTheme(body, null) : { ...body, focus: { ...body.focus, mode: m } })}
          options={[{ value: "all" as const, label: "All" }, { value: "prefer" as const, label: "Prefer", disabled: all && !set.length },
            { value: "only" as const, label: "Only these", disabled: all && !set.length }]} />
      </div>
      <div className="kb-pills" role="group" aria-label="Themes">
        {Object.entries(themes).map(([k, t]) => (
          <button key={k} type="button" className="kb-pill" aria-pressed={!all && body.focus.themes.includes(k)} onClick={() => edit(toggleTheme(body, k))}>{t.label}</button>
        ))}
      </div>
      <div className="kb-row">
        <label className="kb-search">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="M20 20 L16 16" strokeLinecap="round" /></svg>
          <span className="sr-only">Add a stock</span>
          <input type="text" list="kb-universe" value={add} placeholder="Add a stock, e.g. COST" autoComplete="off"
            onChange={(e) => setAdd(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); doAdd(); } }} />
          <datalist id="kb-universe">{addable.map((s) => <option key={s} value={s} />)}</datalist>
        </label>
        <button type="button" onClick={doAdd} disabled={!universe.includes(add.trim().toUpperCase())}>Add</button>
      </div>
      {all ? <span className="muted" style={{ fontSize: 13 }}>All {universe.length} stocks. Pick themes or add stocks to set a focus; then you can leave single stocks out.</span> : (<>
        <span className="cap">Your focus · {set.length} stocks</span>
        <ul className="kb-symlist" aria-label="Stocks in your focus">
          {set.map((s) => (
            <li key={s}><StockBadge symbol={s} size={22} /><b>{s}</b>
              <button type="button" aria-label={`Leave ${s} out`} onClick={() => edit({ ...body, focus: { ...body.focus, exclude: [...body.focus.exclude, s],
                symbols: body.focus.symbols.filter((x) => x !== s) } })}>×</button></li>
          ))}
        </ul>
        {body.focus.exclude.length > 0 && (<>
          <span className="cap">Never traded</span>
          <ul className="kb-symlist out" aria-label="Stocks never traded">
            {body.focus.exclude.map((s) => (
              <li key={s}><b>{s}</b><button type="button" aria-label={`Put ${s} back`} onClick={() => edit({ ...body, focus: { ...body.focus, exclude: body.focus.exclude.filter((x) => x !== s) } })}>↺</button></li>
            ))}
          </ul>
        </>)}
      </>)}
      <span className={tooFew ? "kb-warn" : "muted"} style={{ fontSize: 13 }} role={tooFew ? "alert" : undefined}>
        {tooFew ? `“Only these” needs at least ${MIN_FOCUS} stocks.` : body.focus.mode === "only" ? "Kaibot trades only these stocks."
          : body.focus.mode === "prefer" ? "Kaibot looks at your focus first, then the rest, when slots compete." : "No focus: every stock is equal."}
      </span>
    </section>
  );
}

// ---------------------------------------------------------------- outcome bar + review sheet (S47)
function OutcomeBar({ est, maxLoss, changes, save, pendingDate, canApply, invalid, onReview, onUndo }: {
  est: number; maxLoss: number; changes: number; save: SaveState; pendingDate: string | null; canApply: boolean; invalid: string | null;
  onReview: () => void; onUndo: (() => void) | null;
}) {
  const saveText = save.kind === "saving" ? "Saving draft…" : save.kind === "saved" ? "Draft saved" : save.kind === "error" ? "Draft not saved" : "";
  return (
    <div className="kb-dock" role="region" aria-label="Result of these settings">
      <div className="num kb-dock-stats">
        <span><b className="swap">≈ {Math.round(est)}</b> trades/mo</span>
        <span><b className="swap">{usd0(maxLoss)}</b> max a trade</span>
        <span className="hide-sm" style={{ color: changes ? "var(--sea-ink)" : "var(--stale)", fontWeight: 600 }}>
          {changes ? `${changes} change${changes === 1 ? "" : "s"}` : pendingDate ? `Applies ${openLabel(pendingDate)}` : "Same as now"}
        </span>
        <span className={`hide-sm ${save.kind === "error" ? "loss" : "muted"}`} aria-live="polite" style={{ fontSize: 12 }}>{saveText}</span>
      </div>
      {invalid && changes > 0 && <span className="kb-warn hide-sm" style={{ fontSize: 12, maxWidth: 200 }}>{invalid}</span>}
      {onUndo && <button type="button" className="kb-dock-undo" onClick={onUndo}>Undo</button>}
      <button type="button" className="primary kb-dock-go" disabled={!canApply} onClick={onReview}>Review &amp; apply</button>
    </div>
  );
}

function ReviewSheet({ open, onClose, diff, est, estBase, maxLoss, maxLossBase, nextOpen, nextVersion, canApply, busy, error, errors, onApply, onReset, levelName }: {
  open: boolean; onClose: () => void; diff: ReturnType<typeof diffConfig>; est: ReturnType<typeof estimateTrades>; estBase: number; maxLoss: number;
  maxLossBase: number; nextOpen: string; nextVersion: number; canApply: boolean; busy: boolean; error: string | null; errors: string[];
  onApply: () => void; onReset: () => void; levelName: string;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal?.();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog ref={ref} className="kb-sheet" aria-labelledby="rv-h" onClose={onClose} onClick={(e) => { if (e.target === ref.current) onClose(); }}>
      {open && (
        <div className="kb-sheet-in">
          <div className="kb-row">
            <h2 id="rv-h" style={{ margin: 0, flex: 1, fontSize: 20 }}>Review changes</h2>
            <button type="button" className="kb-x" aria-label="Close" onClick={onClose}>×</button>
          </div>
          {diff.length ? (
            <ul className="kb-diff" aria-label="What changes">
              {diff.map((d) => (
                <li key={d.key}><span className="muted" style={{ flex: "1 1 160px" }}>{d.label}</span>
                  <span className="num kb-from"><span className="sr-only">from </span>{d.from}</span>
                  <span className="num" style={{ fontWeight: 600 }}><span className="sr-only">to </span>{d.to}</span></li>
              ))}
            </ul>
          ) : <p className="muted" style={{ margin: 0 }}>Nothing differs from the settings that apply now.</p>}
          <div className="kb-impact">
            <span className="cap">Impact</span>
            <span className="num" style={{ fontSize: 26, fontWeight: 700, letterSpacing: "-0.02em" }}>≈ {Math.round(est.perMonth)} trades a month
              <span className="muted" style={{ fontSize: 13, fontWeight: 500 }}> now ≈ {Math.round(estBase)}</span></span>
            <span className="num" style={{ fontSize: 14 }}>Max loss a trade <b>{usd0(maxLoss)}</b> <span className="muted">now {usd0(maxLossBase)}</span></span>
            <span className="muted" style={{ fontSize: 13, lineHeight: 1.5 }}>{paceText(est)}{est.spyNotEstimated && est.perMonth > 0 ? " SPY momentum adds up to 1 a day on qualifying days (not estimated yet)." : ""}</span>
          </div>
          <span className="muted" style={{ fontSize: 13, lineHeight: 1.5 }}>
            Applies at the next open, likely {openLabel(nextOpen)}. Open trades keep their rules. <b style={{ color: "var(--ink)" }}>Starts a new chapter in your report</b> (version {nextVersion}).
          </span>
          {errors.length > 0 && <ul className="kb-warn" style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>{errors.map((x) => <li key={x}>{x}</li>)}</ul>}
          {error && <p className="loss" role="alert" style={{ margin: 0, fontSize: 13 }}>{error}</p>}
          <button type="button" className="primary" style={{ minHeight: 52, fontSize: 16 }} disabled={!canApply} onClick={onApply}>{busy ? "Applying…" : "Apply at next open"}</button>
          <button type="button" onClick={onReset} disabled={busy}>Reset to {levelName} defaults</button>
        </div>
      )}
    </dialog>
  );
}
