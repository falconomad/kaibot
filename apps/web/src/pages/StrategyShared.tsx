// M5 (decision 12): what /strategy and /strategy/fine-tune share. The Editor in Strategy.tsx owns the one draft and
// the outcome/review bar; both pages read and edit it through this context, so moving between them keeps every edit.
// No network here: the Editor is the only place that calls the config RPCs.
import { createContext, useContext, useId, useMemo, useState } from "react";
import type { Level } from "../lib/api";
import {
  MAX_EXCLUDED, SESSION_OPEN, canExclude, excludeStock, includeStock, normalizeBounds, toMin,
  type Bounds, type ConfigBody, type ConfigResp, type Themes, dollars,
} from "../lib/config";
import { type EstimatorData, estimateTrades } from "../lib/derive";
import { LEVELS } from "../lib/model";
import estimatorJson from "../data/estimator.json";
import themesJson from "../data/themes.json";

export const EST = estimatorJson as unknown as EstimatorData;
export const UNIVERSE = Object.keys(EST.dip_per_month);
export const usd0 = (v: number) => "$" + Math.round(v).toLocaleString("en-US");
export const LOCKED = [
  "News veto: no entry on company news in the last 24 h",
  "Sell everything at 15:55 ET, every day",
  "No shorts, no leverage, no overnight holds",
  "Broker-side stop and target on every trade, fresh quotes only",
];
export const reducedMotion = () => typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

export interface Ctx { b: Bounds; themes: Themes; universe: string[]; level: Level; levelName: string }

export function useCtx(resp: ConfigResp): Ctx {
  return useMemo(() => {
    const level = (resp.level ?? "balanced") as Level;
    const themes = resp.themes && Object.keys(resp.themes).length ? resp.themes : (themesJson as Themes);
    const universe = Array.isArray(resp.universe) && resp.universe.length ? resp.universe : UNIVERSE;
    return { b: normalizeBounds(resp.bounds, level), themes, universe, level, levelName: LEVELS[level]?.name ?? level };
  }, [resp]);
}

export interface StrategyState {
  body: ConfigBody; edit: (b: ConfigBody) => void; ctx: Ctx; baseline: ConfigBody; est: ReturnType<typeof estimateTrades>;
  usd: ReturnType<typeof dollars>; pot: number; reset: () => void; busy: boolean;
}
const StrategyContext = createContext<StrategyState | null>(null);
export const StrategyProvider = StrategyContext.Provider;
export function useStrategy(): StrategyState {
  const v = useContext(StrategyContext);
  if (!v) throw new Error("useStrategy needs the Strategy editor around it");
  return v;
}

export function DayBar({ body, wide }: { body: ConfigBody; wide?: boolean }) {
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

export const LockIcon = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--gain)" strokeWidth="2" aria-hidden="true"><rect x="5" y="11" width="14" height="10" rx="2" /><path d="M8 11 V8 a4 4 0 0 1 8 0 V11" /></svg>
);

/** The "Never traded" list with a put-back button per stock. Renders nothing when there are no exclusions. */
export function NeverTraded({ body, edit }: { body: ConfigBody; edit: (b: ConfigBody) => void }) {
  if (!body.focus.exclude.length) return null;
  return (
    <div className="kb-never">
      <span className="cap">Never traded · {body.focus.exclude.length} of {MAX_EXCLUDED}</span>
      <ul className="kb-symlist out" aria-label="Stocks never traded">
        {body.focus.exclude.map((s) => (
          <li key={s}><b>{s}</b><button type="button" aria-label={`Put ${s} back`} title="Put back" onClick={() => edit(includeStock(body, s))}>↺</button></li>
        ))}
      </ul>
    </div>
  );
}

/** Exclude single stocks, in any focus mode (All stocks included). Only the 59-stock universe is offered, so
 * reference symbols (SPY, sector ETFs) can't be excluded. At the limit the input is disabled with a plain message. */
export function ExcludeEditor({ body, edit, universe }: { body: ConfigBody; edit: (b: ConfigBody) => void; universe: string[] }) {
  const [text, setText] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const listId = useId();
  const full = body.focus.exclude.length >= MAX_EXCLUDED;
  const sym = text.trim().toUpperCase();
  const options = universe.filter((s) => !body.focus.exclude.includes(s));
  const add = () => {
    if (!sym) return;
    if (!universe.includes(sym)) { setMsg(`${sym} isn't one of the ${universe.length} stocks Kaibot trades, so there is nothing to exclude.`); return; }
    if (body.focus.exclude.includes(sym)) { setMsg(`${sym} is already never traded.`); return; }
    if (!canExclude(body, sym, universe)) return;
    edit(excludeStock(body, sym, universe));
    setText(""); setMsg(null);
  };
  return (
    <div className="kb-exclude" id="exclude">
      <span className="cap" id={`${listId}-h`}>Never trade a stock</span>
      <div className="kb-row">
        <label className="kb-search">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="M5 5 L19 19" strokeLinecap="round" /></svg>
          <span className="sr-only">Stock to never trade</span>
          <input type="text" list={listId} value={text} placeholder={full ? `Limit of ${MAX_EXCLUDED} reached` : "Exclude a stock, e.g. INTC"} autoComplete="off"
            disabled={full} aria-describedby={`${listId}-m`}
            onChange={(e) => { setText(e.target.value); setMsg(null); }} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(); } }} />
          <datalist id={listId}>{options.map((s) => <option key={s} value={s} />)}</datalist>
        </label>
        <button type="button" onClick={add} disabled={full || !sym}>Exclude</button>
      </div>
      <span id={`${listId}-m`} className={full || msg ? "kb-warn" : "muted"} style={{ fontSize: 13 }} role="status">
        {full ? `You can exclude up to ${MAX_EXCLUDED} stocks. Put one back to exclude another.`
          : msg ?? `Excluded stocks are never traded, whatever the focus. Up to ${MAX_EXCLUDED}.`}
      </span>
      <NeverTraded body={body} edit={edit} />
    </div>
  );
}
