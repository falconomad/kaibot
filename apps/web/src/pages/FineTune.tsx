// M5 S52 (decision 12, board 09b): /strategy/fine-tune, every setting as one expandable row, one open at a time.
// It is a child route of Strategy: the draft, the autosave and the outcome/review bar come from the Strategy editor
// (useStrategy), so edits here and on /strategy are the same draft. Paper trading only; no RPCs are called here.
import { useEffect, useState, type KeyboardEvent, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { RadioGroup, Slider, Stepper, Switch } from "../components/ConfigControls";
import { StockBadge } from "../components/StockBadge";
import {
  LABEL, MAX_EXCLUDED, MIN_FOCUS, MIN_WINDOW_MIN, SESSION_OPEN, STEP, STRATEGY_NAME, excludeStock, fmtNum, focusSet, toHhmm, toMin, toggleTheme,
  type AdvKey, type ConfigBody, type NumKey, type RiskKey, type TimingNumKey,
} from "../lib/config";
import { money } from "../lib/fmt";
import { DayBar, ExcludeEditor, LOCKED, LockIcon, reducedMotion, useStrategy, usd0, type Ctx } from "./StrategyShared";

/** Up/Down/Home/End move between the accordion's row headers (Enter/Space toggle them, as native buttons). */
function rowKeys(e: KeyboardEvent<HTMLButtonElement>) {
  const keys = ["ArrowDown", "ArrowUp", "Home", "End"];
  if (!keys.includes(e.key)) return;
  const all = [...(e.currentTarget.closest(".kb-strategy")?.querySelectorAll<HTMLButtonElement>(".kb-srow > button[aria-expanded]") ?? [])];
  const i = all.indexOf(e.currentTarget);
  if (i < 0 || !all.length) return;
  e.preventDefault();
  const n = e.key === "Home" ? 0 : e.key === "End" ? all.length - 1 : (i + (e.key === "ArrowDown" ? 1 : -1) + all.length) % all.length;
  all[n].focus();
}

export function FineTune() {
  const { body, edit, ctx, baseline, est, usd, pot, reset, busy } = useStrategy();
  const { b, levelName } = ctx;
  const location = useLocation();
  // One row open at a time: opening a row closes the one that was open.
  const [open, setOpen] = useState<string | null>("risk_per_trade_pct");
  const toggle = (id: string) => setOpen((cur) => (cur === id ? null : id));

  // Deep links: /strategy/fine-tune#exclude or #g-timing scroll to that part; otherwise start at the top.
  useEffect(() => {
    const id = location.hash.slice(1);
    const el = id ? document.getElementById(id) : null;
    if (el) el.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "start" });
    else window.scrollTo({ top: 0 });
  }, [location.hash]);

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
  const row = (k: NumKey, inner?: ReactNode) => (
    <Row key={k} id={k} label={LABEL[k]} value={fmtNum(k, num(k))} changed={num(k) !== base(k)} open={open === k} onToggle={() => toggle(k)}>
      {inner ?? slider(k)}
    </Row>
  );
  const s = toMin(body.timing.entry_start), e = toMin(body.timing.entry_end);
  const lo = toMin(b.entry.start), hi = toMin(b.entry.end);
  const setWin = (ns: number, ne: number) => edit({ ...body, timing: { ...body.timing, entry_start: toHhmm(ns), entry_end: toHhmm(ne) } });
  return (<>
    <div className="kb-fine-head">
      <Link to="/strategy" className="kb-back">← Strategy</Link>
      <h1 className="kb-h1" tabIndex={-1}>Fine-tune</h1>
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
      <figure style={{ margin: 0, padding: "18px 20px 6px" }}><DayBar body={body} wide /></figure>
      <Row id="entry_start" label="First new trade" value={body.timing.entry_start} changed={body.timing.entry_start !== baseline.timing.entry_start}
        open={open === "entry_start"} onToggle={() => toggle("entry_start")}>
        <Slider label="First new trade" value={s} min={lo} max={hi - MIN_WINDOW_MIN} step={5} text={`${toHhmm(s)} ET`} lo={b.entry.start} hi={toHhmm(hi - MIN_WINDOW_MIN)}
          note={s > lo ? `skips the first ${s - SESSION_OPEN} min` : `from ${b.entry.start}`} onChange={(v) => setWin(v, Math.max(e, v + MIN_WINDOW_MIN))} />
      </Row>
      <Row id="entry_end" label="Last new trade" value={body.timing.entry_end} changed={body.timing.entry_end !== baseline.timing.entry_end}
        open={open === "entry_end"} onToggle={() => toggle("entry_end")}>
        <Slider label="Last new trade" value={e} min={lo + MIN_WINDOW_MIN} max={hi} step={5} text={`${toHhmm(e)} ET`} lo={toHhmm(lo + MIN_WINDOW_MIN)} hi={b.entry.end}
          note={`${levelName} max ${b.entry.end}`} onChange={(v) => setWin(Math.min(s, v - MIN_WINDOW_MIN), v)} />
      </Row>
      {row("event_window_min")}
      {row("vix_max")}
      <Row id="pause" label="Pause after a losing streak" value={`${body.timing.pause_losses} losses → ${body.timing.pause_minutes} min`}
        changed={body.timing.pause_losses !== baseline.timing.pause_losses || body.timing.pause_minutes !== baseline.timing.pause_minutes}
        open={open === "pause"} onToggle={() => toggle("pause")}>
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
        open={open === "adv"} onToggle={() => toggle("adv")}>
        {(["min_price_usd", "min_adv_usd", "max_spread_bp"] as const).map((k) => (
          <div key={k} style={{ display: "flex", flexDirection: "column", gap: 2 }}><span className="muted" style={{ fontSize: 12 }}>{LABEL[k]} (tighten only)</span>{slider(k)}</div>
        ))}
      </Row>
      <Row id="locked" label="Safety rules" icon={<LockIcon />} value="Always on" valueClass="gain" changed={false} open={open === "locked"} onToggle={() => toggle("locked")}>
        <ul className="kb-locked-rows" aria-label="Locked safety rules">
          {LOCKED.map((l) => <li key={l}><LockIcon />{l}<span className="sr-only"> (locked, cannot be changed)</span></li>)}
        </ul>
      </Row>
    </Group>

    <div className="kb-row" style={{ gap: 12 }}>
      <button type="button" onClick={reset} disabled={busy}>Reset to level defaults</button>
      <span className="muted" style={{ fontSize: 13, flex: "1 1 260px" }}>Coming later, once each passes a replay: exit style presets, more stocks, your own themes, days off, news-room strictness.</span>
    </div>
  </>);
}

function Group({ title, sub, children }: { title: string; sub?: string; children: ReactNode }) {
  const id = `g-${title.toLowerCase().replace(/\W+/g, "-")}`;
  return (
    <section className="kb-sect" aria-labelledby={`${id}-h`} id={id}>
      <div style={{ padding: "0 4px" }}><h2 id={`${id}-h`} className="cap kb-cap">{title}</h2>{sub && <span className="muted" style={{ fontSize: 13 }}>{sub}</span>}</div>
      <div className="card kb-group">{children}</div>
    </section>
  );
}

/** An accordion row: a button with aria-expanded/aria-controls, and a region that is always in the DOM (hidden when closed). */
function Row({ id, label, value, changed, open, onToggle, icon, valueClass, children }: {
  id: string; label: string; value: string; changed: boolean; open: boolean; onToggle: () => void; icon?: ReactNode; valueClass?: string; children: ReactNode;
}) {
  const panel = `row-${id}`;
  return (
    <div className="kb-srow">
      <button type="button" id={`${panel}-btn`} aria-expanded={open} aria-controls={panel} onClick={onToggle} onKeyDown={rowKeys}>
        {icon}
        <span style={{ flex: 1 }}>{label}</span>
        {changed && <span className="kb-dot" role="img" aria-label="changed" />}
        <span className={`num ${valueClass ?? ""}`} style={{ fontWeight: 600 }}>{value}</span>
        <svg className={`kb-chev${open ? " open" : ""}`} width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--stale)" strokeWidth="2" aria-hidden="true"><path d="M9 6 L15 12 L9 18" strokeLinecap="round" strokeLinejoin="round" /></svg>
      </button>
      <div id={panel} className="kb-expand" role="region" aria-labelledby={`${panel}-btn`} hidden={!open}>{open && children}</div>
    </div>
  );
}

function FocusEditor({ body, edit, ctx }: { body: ConfigBody; edit: (b: ConfigBody) => void; ctx: Ctx }) {
  const { themes, universe } = ctx;
  const [add, setAdd] = useState("");
  const set = focusSet(body, themes);
  const all = body.focus.mode === "all";
  const full = body.focus.exclude.length >= MAX_EXCLUDED;
  const addable = universe.filter((s) => !set.includes(s) && !body.focus.exclude.includes(s));
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
          <span className="sr-only">Add a stock to your focus</span>
          <input type="text" list="kb-universe" value={add} placeholder="Add a stock, e.g. COST" autoComplete="off"
            onChange={(e) => setAdd(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); doAdd(); } }} />
          <datalist id="kb-universe">{addable.map((s) => <option key={s} value={s} />)}</datalist>
        </label>
        <button type="button" onClick={doAdd} disabled={!universe.includes(add.trim().toUpperCase())}>Add</button>
      </div>
      {all ? <span className="muted" style={{ fontSize: 13 }}>All {universe.length - body.focus.exclude.length} stocks. Pick themes or add stocks to set a focus.</span> : (<>
        <span className="cap">Your focus · {set.length} stocks</span>
        <ul className="kb-symlist" aria-label="Stocks in your focus">
          {set.map((s) => (
            <li key={s}><StockBadge symbol={s} size={22} /><b>{s}</b>
              <button type="button" aria-label={`Never trade ${s}`} title={full ? `Limit of ${MAX_EXCLUDED} reached` : "Never trade"} disabled={full}
                onClick={() => edit(excludeStock(body, s, universe))}>×</button></li>
          ))}
        </ul>
      </>)}
      <span className={tooFew ? "kb-warn" : "muted"} style={{ fontSize: 13 }} role={tooFew ? "alert" : undefined}>
        {tooFew ? `“Only these” needs at least ${MIN_FOCUS} stocks.` : body.focus.mode === "only" ? "Kaibot trades only these stocks."
          : body.focus.mode === "prefer" ? "Kaibot looks at your focus first, then the rest, when slots compete." : "No focus: every stock is equal."}
      </span>
      <ExcludeEditor body={body} edit={edit} universe={universe} />
    </section>
  );
}
