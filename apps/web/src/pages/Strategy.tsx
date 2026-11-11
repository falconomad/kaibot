// M5 (decision 12, S45/S46/S47/S52): the Strategy page. `Strategy` is the layout route for /strategy and
// /strategy/fine-tune: its Editor owns the one draft, the autosave and the outcome/review bar, and hands them to both
// pages through StrategyProvider, so switching pages keeps every edit and a reload restores the saved draft.
// Every control is bounded by `kb_get_config.bounds`, so a setting can only make Kaibot pickier or smaller. Presets and
// Boldness are shortcuts that write the same body fields. Changes apply at the next session open as a new version.
// Paper trading only: there is no live-trading control here, and server text is always rendered as plain text.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { Outlet, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { livePot, useRunCtx } from "../components/Shell";
import { PillSwitch, RadioGroup } from "../components/ConfigControls";
import { StockBadge } from "../components/StockBadge";
import { Skeleton } from "../components/Skeleton";
import { api } from "../lib/api";
import {
  CAUTION, HOURS, MIN_FOCUS, PRESETS, boldnessSteps, cautionOn, clampBody, diffConfig, dollars, focusSet, hoursShape, hoursWindow,
  levelDefaults, matchBoldness, matchPreset, nextOpenEt, openLabel, presetBody, setBoldness, setCaution, toggleTheme, validateBody,
  type Bounds, type ConfigBody, type ConfigResp,
} from "../lib/config";
import { estimateTrades, paceText } from "../lib/derive";
import { DayBar, EST, LOCKED, NeverTraded, StrategyProvider, useCtx, useStrategy, usd0, type Ctx, type StrategyState } from "./StrategyShared";

export const FINE_TUNE = "/strategy/fine-tune";

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
    <div className="kb-strategy" role="status" aria-busy="true" aria-label="Loading your setup">
      <span className="sk" style={{ width: 220, height: 40, borderRadius: 12, flex: "none" }} />
      <div className="kb-presets">{[0, 1, 2, 3].map((i) => <span key={i} className="sk" style={{ height: 110, borderRadius: 20 }} />)}</div>
      <div className="kb-twocol">
        <span className="sk" style={{ height: 420, borderRadius: 24 }} />
        <div className="sk-stack"><span className="sk" style={{ height: 190, borderRadius: 24 }} /><span className="sk" style={{ height: 150, borderRadius: 24 }} /></div>
      </div>
    </div>
  );
}

/** Move focus to the page heading (h1 with tabIndex -1) without scrolling: deep links scroll on their own. */
function focusHeading(root: HTMLElement | null) {
  root?.querySelector<HTMLElement>("h1[tabindex]")?.focus({ preventScroll: true });
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
  const [params] = useSearchParams();
  const navigate = useNavigate();
  // The old ?view=fine deep link now lives at its own route.
  useEffect(() => { if (params.get("view") === "fine") navigate(FINE_TUNE, { replace: true }); }, [params, navigate]);

  const baseline = useMemo(() => clampBody(resp.pending?.body ?? resp.applied?.body ?? null, b, themes, universe), [resp, b, themes, universe]);
  const [body, setBody] = useState<ConfigBody>(() => clampBody(resp.draft?.body ?? resp.pending?.body ?? resp.applied?.body ?? null, b, themes, universe));
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const [busy, setBusy] = useState(false);
  const [actionErr, setActionErr] = useState<string | null>(null);
  const [justApplied, setJustApplied] = useState<{ version: number; applies_from: string } | null>(null);
  const [sheet, setSheet] = useState(false);
  const dirty = useRef(false);
  const timer = useRef<number | undefined>(undefined);
  const root = useRef<HTMLDivElement>(null);

  // Strategy <-> Fine-tune swaps the whole page under the same layout, and the button that was pressed is gone.
  // Put focus on the new page's heading so keyboard and screen-reader users start at its top, not at <body>.
  const { pathname } = useLocation();
  const shownPath = useRef(pathname);
  useEffect(() => {
    if (shownPath.current === pathname) return;
    shownPath.current = pathname;
    focusHeading(root.current);
  }, [pathname]);

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

  const shared: StrategyState = { body, edit, ctx, baseline, est, usd, pot, reset, busy };
  return (
    <StrategyProvider value={shared}>
    <div className="kb-strategy" ref={root}>
      <Suspense fallback={<Skeleton />}><Outlet /></Suspense>
      {pending && (
        <p className="banner warn" role="status" style={{ margin: 0 }}>
          <b>Version {pending.version} is scheduled.</b> It applies at the next open, {openLabel(pending.applies_from)}. Open trades keep the rules they were opened under.
        </p>
      )}
      <OutcomeBar est={est.perMonth} maxLoss={usd.maxLoss} changes={diff.length} save={save} pendingDate={pending?.applies_from ?? null}
        canApply={canApply} invalid={errors[0] ?? null} onReview={() => { setActionErr(null); setSheet(true); }} onUndo={diff.length ? undo : null} />
      <ReviewSheet open={sheet} onClose={() => setSheet(false)} fallbackFocus={() => focusHeading(root.current)} diff={diff} est={est} estBase={estBase.perMonth} maxLoss={usd.maxLoss}
        maxLossBase={dollars(baseline, pot).maxLoss} nextOpen={nextOpenEt()} nextVersion={nextVersion} canApply={canApply} busy={busy}
        error={actionErr ?? (save.kind === "error" ? save.text : null)} errors={errors} onApply={apply} onReset={reset} levelName={ctx.levelName} />
    </div>
    </StrategyProvider>
  );
}

// ---------------------------------------------------------------- Strategy (one screen)
/** /strategy (index route): the one-screen tune page (boards 09 / 09m). */
export function StrategyHome() {
  const { body, edit, ctx, est, usd, pot } = useStrategy();
  const navigate = useNavigate();
  const toFine = (hash = "") => navigate(FINE_TUNE + hash);
  const { b, themes, universe, levelName } = ctx;
  const preset = matchPreset(body, b, themes, universe);
  return (<>
    <div className="kb-head">
      <div style={{ flex: "1 1 360px", display: "flex", flexDirection: "column", gap: 6 }}>
        <h1 className="kb-h1" tabIndex={-1}>Strategy</h1>
        <span className="muted">Kaibot trades on its own. Pick a starting point, nudge it, done. Settings can only make it pickier or smaller than {levelName}.</span>
      </div>
      <button type="button" className="kb-ghost" onClick={() => toFine()}>
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
          const blurb = p.blurb.replace("your level", levelName);
          const stats = `≈ ${Math.round(e.perMonth)} trades/mo · ${usd0(dollars(x, pot).maxLoss)} max a trade`;
          return { value: p.key, label: p.name, description: `${blurb} ${presetStatsSpoken(e.perMonth, usd0(dollars(x, pot).maxLoss))}`,
            content: <PresetCard icon={p.icon} name={p.name} blurb={blurb} stats={stats} /> };
        }), (() => {
          const blurb = preset === "custom" ? "Your own mix. Keep shaping it below." : "Change anything below and it lands here.";
          return { value: "custom" as const, label: "Custom", disabled: preset !== "custom",
            description: preset === "custom" ? `${blurb} ${presetStatsSpoken(est.perMonth, usd0(usd.maxLoss))}` : blurb,
            content: <PresetCard icon="✎" name="Custom" blurb={blurb}
              stats={preset === "custom" ? `≈ ${Math.round(est.perMonth)} trades/mo · ${usd0(usd.maxLoss)} max a trade` : "—"} /> };
        })()]} />
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

/** The card's numbers in words: the radio's accessible name is only the preset name, so this is its description. */
export const presetStatsSpoken = (perMonth: number, maxLoss: string) => `About ${Math.round(perMonth)} trades a month, ${maxLoss} max loss a trade.`;

function PresetCard({ icon, name, blurb, stats }: { icon: string; name: string; blurb: string; stats: string }) {
  return (<>
    <span className="kb-preset-top"><span aria-hidden="true" style={{ fontSize: 20 }}>{icon}</span><span className="kb-preset-name">{name}</span><span aria-hidden="true" className="kb-radio" /></span>
    <span className="kb-preset-blurb">{blurb}</span>
    <span className="num kb-preset-stats">{stats}</span>
  </>);
}

function themeRate(symbols: string[], body: ConfigBody, b: Bounds) {
  // Excluded stocks are never traded, so they don't count towards a theme either.
  const x = { ...body, focus: { themes: [], symbols, exclude: body.focus.exclude, mode: "only" as const } };
  return estimateTrades(x, EST, {}, b.entry).perMonth;
}

function FocusCard({ body, edit, ctx, toFine }: { body: ConfigBody; edit: (b: ConfigBody) => void; ctx: Ctx; toFine: (hash?: string) => void }) {
  const { b, themes, universe } = ctx;
  const set = focusSet(body, themes);
  const all = body.focus.mode === "all";
  const tooFew = body.focus.mode === "only" && set.length < MIN_FOCUS;
  const note = tooFew ? `“Only” needs at least ${MIN_FOCUS} stocks. Add a theme or switch to Prefer.`
    : all ? (body.focus.exclude.length ? `All ${universe.length} stocks except ${body.focus.exclude.length} you never trade.` : `All ${universe.length} stocks. Pick themes to narrow it.`)
    : `${body.focus.mode === "only" ? "Only these" : "These first, then the rest"} · ${set.length} stocks: ${set.slice(0, 4).join(", ")}${set.length > 4 ? ` +${set.length - 4}` : ""}`;
  const cards: { key: string | null; name: string; syms: string[]; on: boolean }[] = [
    { key: null, name: "All stocks", syms: universe.filter((s) => !body.focus.exclude.includes(s)), on: all },
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
            <span className="num kb-tmeta">{c.syms.filter((s) => !body.focus.exclude.includes(s)).length} stocks · ≈ {Math.round(themeRate(c.syms, body, b) * 10) / 10}/mo</span>
            {c.on && <span aria-hidden="true" className="kb-check">✓</span>}
          </button>
        ))}
      </div>
      <div className="kb-row" style={{ fontSize: 13 }}>
        <span className={`num swap ${tooFew ? "kb-warn" : "muted"}`} style={{ flex: 1 }} role={tooFew ? "alert" : undefined}>{note}</span>
        <button type="button" className="linkbtn" style={{ fontSize: 13, fontWeight: 600, textDecoration: "none" }} onClick={() => toFine("#exclude")}>Exclude a stock</button>
      </div>
      <NeverTraded body={body} edit={edit} />
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

function HoursCard({ body, edit, ctx }: { body: ConfigBody; edit: (b: ConfigBody) => void; ctx: Ctx }) {
  const { b } = ctx;
  const shape = hoursShape(body, b);
  return (
    <section className="card kb-card" aria-labelledby="hr-h">
      <h2 id="hr-h" className="kb-h2">Trading hours</h2>
      <RadioGroup labelledBy="hr-h" className="kb-pills" optClass="kb-pill" value={shape}
        onChange={(k) => edit({ ...body, timing: { ...body.timing, ...hoursWindow(k, b) } })}
        options={HOURS.map((h) => ({ value: h.key, label: h.name }))} />
      <DayBar body={body} />
      <span className="num swap muted" style={{ fontSize: 12 }}>
        {shape === null && "Custom hours (Fine-tune) · "}New trades {body.timing.entry_start}–{body.timing.entry_end} ET · amber = Fed/CPI quiet (±{body.timing.event_window_min} min) · red = sell-all 15:55
      </span>
    </section>
  );
}

function SafetyCard({ body, edit, ctx, toFine }: { body: ConfigBody; edit: (b: ConfigBody) => void; ctx: Ctx; toFine: (hash?: string) => void }) {
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
      <button type="button" className="linkbtn" style={{ alignSelf: "flex-start", fontSize: 13, textDecoration: "none" }} onClick={() => toFine("#g-timing")}>Exact minutes and levels in Fine-tune</button>
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
      {invalid && changes > 0 && <span id="kb-dock-why" className="kb-warn hide-sm" style={{ fontSize: 12, maxWidth: 200 }}>{invalid}</span>}
      {onUndo && <button type="button" className="kb-dock-undo" onClick={onUndo}>Undo</button>}
      <button type="button" className="primary kb-dock-go" disabled={!canApply} aria-describedby={invalid && changes > 0 ? "kb-dock-why" : undefined} onClick={onReview}>Review &amp; apply</button>
    </div>
  );
}

function ReviewSheet({ open, onClose, fallbackFocus, diff, est, estBase, maxLoss, maxLossBase, nextOpen, nextVersion, canApply, busy, error, errors, onApply, onReset, levelName }: {
  open: boolean; onClose: () => void; fallbackFocus: () => void; diff: ReturnType<typeof diffConfig>; est: ReturnType<typeof estimateTrades>; estBase: number; maxLoss: number;
  maxLossBase: number; nextOpen: string; nextVersion: number; canApply: boolean; busy: boolean; error: string | null; errors: string[];
  onApply: () => void; onReset: () => void; levelName: string;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const wasOpen = useRef(false);
  // A modal <dialog> (showModal) gives the focus trap, Escape and the inert page behind it natively, and hands focus
  // back to the button that opened it. After "Apply" that button is disabled, so focus would fall to <body>: catch that.
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal?.();
    if (!open && d.open) d.close();
    if (!open && wasOpen.current) {
      const a = document.activeElement as HTMLElement | null;
      if (!a || a === document.body || (a as HTMLButtonElement).disabled) fallbackFocus();
    }
    wasOpen.current = open;
  }, [open, fallbackFocus]);
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
