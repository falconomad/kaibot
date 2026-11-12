// M5 (decision 12, docs/m5/contract.md): the config body, its bounds and the pure helpers behind the Strategy page.
// No React, no network: unit-tested in config.test.ts. The UI only ever emits a body that passes clampBody, so the
// server's 22023 check should never fire. Presets and Boldness are shortcuts that write these same fields.
import type { Level } from "./api";
import { LEVELS } from "./model";
import presetsJson from "../data/presets.json";

export type FocusMode = "all" | "prefer" | "only";
export type PresetKey = "default" | "big_tech" | "calm" | "custom";
export type RiskKey = "risk_per_trade_pct" | "max_positions" | "max_position_pct" | "max_entries_per_day" | "daily_stop_pct" | "run_drawdown_end_pct";
export type TimingNumKey = "pause_losses" | "pause_minutes" | "vix_max" | "event_window_min";
export type AdvKey = "min_price_usd" | "min_adv_usd" | "max_spread_bp";
export type NumKey = RiskKey | TimingNumKey | AdvKey;
export type StrategyKey = "dip_reversal" | "spy_late_momentum";

export interface ConfigBody {
  schema: 1;
  preset: PresetKey;
  boldness: number | null;
  /** `custom` (S68/S69): the owner's own accepted tickers, stored by value. Only ever present with mode "only". */
  focus: { themes: string[]; symbols: string[]; exclude: string[]; mode: FocusMode; custom?: CustomList };
  risk: Record<RiskKey, number>;
  strategies: Record<StrategyKey, boolean>;
  timing: { entry_start: string; entry_end: string } & Record<TimingNumKey, number>;
  advanced: Record<AdvKey, number>;
}
export interface CustomTicker { symbol: string; etf: string }
export interface CustomList { name: string; tickers: CustomTicker[] }
export interface Range { min: number; max: number }
export type Bounds = Record<NumKey, Range> & { entry: { start: string; end: string }; strategies: StrategyKey[] };
export type Themes = Record<string, { label: string; symbols: string[] }>;
export interface ConfigVersion { version: number; body: Partial<ConfigBody>; applies_from: string; created_at: string }
export interface ConfigResp {
  level: Level; bounds: Bounds; themes: Themes; universe: string[];
  applied: ConfigVersion | null; pending: ConfigVersion | null;
  draft: { body: Partial<ConfigBody>; updated_at: string } | null;
  versions: { version: number; status: "applied" | "superseded"; applies_from: string; created_at: string }[];
}
export interface Chapter { version: number; from: string; to: string | null; n_trades: number; pnl_usd: number; mean_r: number | null;
  t_stat: number | null; too_early: boolean }

export const RISK_KEYS: RiskKey[] = ["risk_per_trade_pct", "max_positions", "max_position_pct", "max_entries_per_day", "daily_stop_pct", "run_drawdown_end_pct"];
export const TIMING_KEYS: TimingNumKey[] = ["pause_losses", "pause_minutes", "vix_max", "event_window_min"];
export const ADV_KEYS: AdvKey[] = ["min_price_usd", "min_adv_usd", "max_spread_bp"];
export const NUM_KEYS: NumKey[] = [...RISK_KEYS, ...TIMING_KEYS, ...ADV_KEYS];
export const STRATEGY_KEYS: StrategyKey[] = ["dip_reversal", "spy_late_momentum"];

/** Fields where a higher value is pickier: the level's value is the bound's min, not its max. */
export const UP_IS_TIGHTER = new Set<NumKey>(["pause_minutes", "event_window_min", "min_price_usd", "min_adv_usd"]);
/** UI step per control (the contract gives none). */
export const STEP: Record<NumKey, number> = {
  risk_per_trade_pct: 0.05, max_positions: 1, max_position_pct: 1, max_entries_per_day: 1, daily_stop_pct: 0.25, run_drawdown_end_pct: 0.5,
  pause_losses: 1, pause_minutes: 30, vix_max: 1, event_window_min: 15, min_price_usd: 5, min_adv_usd: 50_000_000, max_spread_bp: 1,
};
export const MIN_FOCUS = 5;
export const MIN_WINDOW_MIN = 30;
export const SESSION_OPEN = 9 * 60 + 30;

const round = (v: number) => Math.round(v * 10000) / 10000;
export const clamp = (v: number, r: Range) => round(Math.min(r.max, Math.max(r.min, Number.isFinite(v) ? v : r.max)));
export const toMin = (hhmm: string) => { const [h, m] = hhmm.split(":").map(Number); return h * 60 + m; };
export const toHhmm = (m: number) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;

/** Fill any bound the server left out from the level table and the contract's fixed ranges (tolerates a partial backend). */
export function normalizeBounds(raw: Partial<Bounds> | null | undefined, level: Level): Bounds {
  const lv = LEVELS[level];
  const fb: Record<NumKey, Range> = {
    risk_per_trade_pct: { min: 0.1, max: lv.risk }, max_positions: { min: 1, max: lv.maxPositions }, max_position_pct: { min: 5, max: lv.pos },
    max_entries_per_day: { min: 1, max: lv.entries }, daily_stop_pct: { min: 0.25, max: lv.stop }, run_drawdown_end_pct: { min: 2, max: lv.runDd },
    pause_losses: { min: 2, max: 3 }, pause_minutes: { min: 60, max: 240 }, vix_max: { min: 20, max: 35 }, event_window_min: { min: 30, max: 90 },
    min_price_usd: { min: 5, max: 50 }, min_adv_usd: { min: 50_000_000, max: 500_000_000 }, max_spread_bp: { min: 1, max: 5 },
  };
  const out = {} as Record<NumKey, Range>;
  for (const k of NUM_KEYS) {
    const r = (raw as Record<string, Partial<Range> | undefined> | null | undefined)?.[k];
    const min = Number(r?.min ?? fb[k].min), max = Number(r?.max ?? fb[k].max);
    out[k] = Number.isFinite(min) && Number.isFinite(max) && min <= max ? { min, max } : fb[k];
  }
  const strategies = Array.isArray(raw?.strategies) && raw.strategies.length ? raw.strategies.filter((s) => STRATEGY_KEYS.includes(s))
    : (level === "conservative" ? ["dip_reversal"] : ["dip_reversal", "spy_late_momentum"]) as StrategyKey[];
  return { ...out, entry: { start: raw?.entry?.start ?? "09:35", end: raw?.entry?.end ?? "15:45" }, strategies };
}

export const levelValue = (k: NumKey, b: Bounds) => (UP_IS_TIGHTER.has(k) ? b[k].min : b[k].max);

/** The body that means "everything as the level sets it". */
export function levelDefaults(b: Bounds): ConfigBody {
  const num = <K extends NumKey>(ks: K[]) => Object.fromEntries(ks.map((k) => [k, levelValue(k, b)])) as Record<K, number>;
  return {
    schema: 1, preset: "default", boldness: 4,
    focus: { themes: [], symbols: [], exclude: [], mode: "all" },
    risk: num(RISK_KEYS),
    strategies: { dip_reversal: b.strategies.includes("dip_reversal"), spy_late_momentum: b.strategies.includes("spy_late_momentum") },
    timing: { entry_start: b.entry.start, entry_end: b.entry.end, ...num(TIMING_KEYS) },
    advanced: num(ADV_KEYS),
  };
}

/** focus.custom, cleaned: only with Only mode, at most 40, no duplicates, no catalogue stocks, each with a sector fund.
 * The server checks each ticker's acceptance again on save and apply; this only keeps the shape right. */
export function cleanCustom(raw: unknown, mode: FocusMode, uni: Set<string>): CustomList | undefined {
  if (mode !== "only" || !raw || typeof raw !== "object") return undefined;
  const r = raw as { name?: unknown; tickers?: unknown };
  const seen = new Set<string>();
  const tickers: CustomTicker[] = [];
  for (const t of Array.isArray(r.tickers) ? r.tickers : []) {
    const symbol = String((t as CustomTicker)?.symbol ?? "").toUpperCase(), etf = String((t as CustomTicker)?.etf ?? "");
    if (!/^[A-Z][A-Z.-]{0,9}$/.test(symbol) || !etf || uni.has(symbol) || seen.has(symbol)) continue;
    seen.add(symbol);
    tickers.push({ symbol, etf });
  }
  if (!tickers.length) return undefined;
  return { name: String(r.name ?? "").slice(0, 40), tickers: tickers.slice(0, 40) };
}

const uniq = (xs: unknown, allowed: Set<string>) => [...new Set(Array.isArray(xs) ? xs.map(String) : [])].filter((x) => allowed.has(x));

/** Merge a (possibly partial or stale) body onto the level defaults and force every value inside the bounds.
 * Missing means "the level's value" (contract). This never returns a number outside its bound. */
export function clampBody(partial: Partial<ConfigBody> | null | undefined, b: Bounds, themes: Themes, universe: string[]): ConfigBody {
  const d = levelDefaults(b);
  const p = (partial ?? {}) as Partial<ConfigBody>;
  const uni = new Set(universe);
  const f = p.focus ?? d.focus;
  const mode: FocusMode = f.mode === "only" || f.mode === "prefer" ? f.mode : "all";
  const pick = <K extends NumKey>(ks: K[], src: Partial<Record<K, unknown>> | undefined) =>
    Object.fromEntries(ks.map((k) => [k, clamp(src?.[k] === undefined || src?.[k] === null ? levelValue(k, b) : Number(src[k]), b[k])])) as Record<K, number>;
  const lo = toMin(b.entry.start), hi = toMin(b.entry.end);
  const tm = (v: unknown, dflt: number) => {
    const m = typeof v === "string" && /^\d{2}:\d{2}$/.test(v) ? toMin(v) : dflt;
    return Math.min(hi, Math.max(lo, m));
  };
  const s = p.strategies ?? d.strategies;
  const custom = cleanCustom(f.custom, mode, uni);
  return {
    schema: 1,
    preset: (["default", "big_tech", "calm", "custom"] as const).includes(p.preset as PresetKey) ? (p.preset as PresetKey) : "custom",
    boldness: typeof p.boldness === "number" && p.boldness >= 0 && p.boldness <= 4 ? Math.round(p.boldness) : null,
    focus: { themes: uniq(f.themes, new Set(Object.keys(themes))), symbols: uniq(f.symbols, uni), exclude: uniq(f.exclude, uni).slice(0, MAX_EXCLUDED), mode,
      ...(custom ? { custom } : {}) },
    risk: pick(RISK_KEYS, p.risk),
    strategies: Object.fromEntries(STRATEGY_KEYS.map((k) => [k, (s[k] === undefined || s[k] === null ? d.strategies[k] : !!s[k]) && b.strategies.includes(k)])) as Record<StrategyKey, boolean>,
    timing: { entry_start: toHhmm(tm(p.timing?.entry_start, lo)), entry_end: toHhmm(tm(p.timing?.entry_end, hi)), ...pick(TIMING_KEYS, p.timing) },
    advanced: pick(ADV_KEYS, p.advanced),
  };
}

/** The focus set: union of the themes and extra symbols, minus the exclusions. */
export function focusSet(body: ConfigBody, themes: Themes): string[] {
  const out: string[] = [];
  for (const t of body.focus.themes) for (const s of themes[t]?.symbols ?? []) if (!out.includes(s)) out.push(s);
  for (const s of body.focus.symbols) if (!out.includes(s)) out.push(s);
  if (body.focus.mode === "only") for (const t of body.focus.custom?.tickers ?? []) if (!out.includes(t.symbol)) out.push(t.symbol);
  return out.filter((s) => !body.focus.exclude.includes(s));
}

/** Mirrors the server's non-range rules. An empty list means the body can be saved and applied. */
export function validateBody(body: ConfigBody, b: Bounds, themes: Themes): string[] {
  const err: string[] = [];
  for (const k of NUM_KEYS) {
    const v = k in body.risk ? body.risk[k as RiskKey] : k in body.advanced ? body.advanced[k as AdvKey] : body.timing[k as TimingNumKey];
    if (!(v >= b[k].min && v <= b[k].max)) err.push(`${LABEL[k]} must be between ${fmtNum(k, b[k].min)} and ${fmtNum(k, b[k].max)}.`);
  }
  if (body.focus.mode === "only" && focusSet(body, themes).length < MIN_FOCUS)
    err.push(`“Only these” needs at least ${MIN_FOCUS} stocks. Add a theme or switch to Prefer.`);
  if (body.focus.custom && body.focus.mode !== "only") err.push("A custom list can only be used with 'Only these'. Choose 'Only these' or remove the list.");
  if (body.focus.custom && !body.focus.custom.name.trim()) err.push("Give your list a name first.");
  if (body.focus.mode !== "all" && focusSet(body, themes).length === 0) err.push("Pick at least one theme or stock, or choose All stocks.");
  if (!body.strategies.dip_reversal && !body.strategies.spy_late_momentum) err.push("Keep at least one strategy on.");
  if (body.strategies.spy_late_momentum && !b.strategies.includes("spy_late_momentum")) err.push("The SPY late-day rule isn't part of this level.");
  const s = toMin(body.timing.entry_start), e = toMin(body.timing.entry_end);
  if (s < toMin(b.entry.start) || e > toMin(b.entry.end)) err.push(`Trading hours must stay inside ${b.entry.start}–${b.entry.end} ET.`);
  if (e - s < MIN_WINDOW_MIN) err.push(`Leave at least ${MIN_WINDOW_MIN} minutes between the first and last new trade.`);
  return err;
}

// ---------------------------------------------------------------- the shared spec (S51)
// config/presets.yaml is the one source for the presets, the 5 Boldness steps, the hours shapes, the caution pills and
// the exclusion limit; src/data/presets.json is its generated copy (scripts/gen_presets_json.py, kept in sync by
// tests/test_presets.py). The spec is in absolute numbers (Careful = the Steady level's limits, Max = Hunter); every
// value is clamped to the run's level here, so below Hunter the upper steps collapse onto the level's own limits.
export type Caution = "vix" | "pause" | "quiet";
type SpecPresetKey = Exclude<PresetKey, "custom">;
interface PresetSpec {
  boldness: ({ name: string } & Record<RiskKey, number>)[];
  hours: { key: string; name: string; from: number; to: number }[];
  caution: Record<Caution, { name: string } & Partial<Record<TimingNumKey, number>>>;
  presets: Record<SpecPresetKey, { name: string; icon: string; blurb: string; focus: { mode: FocusMode; themes: string[] }; boldness: number;
    hours: string; caution: Caution[] }>;
  limits: { max_excluded: number };
}
export const SPEC = presetsJson as unknown as PresetSpec;
/** Most stocks that can be excluded ("never traded"). The server rejects more. */
export const MAX_EXCLUDED = SPEC.limits.max_excluded;

// ---------------------------------------------------------------- Boldness (5 steps)
export const BOLD_STEPS: { name: string; risk: Record<RiskKey, number> }[] = SPEC.boldness.map((s) => ({
  name: s.name, risk: Object.fromEntries(RISK_KEYS.map((k) => [k, Number(s[k])])) as Record<RiskKey, number>,
}));

export function boldnessRisk(step: number, b: Bounds): Record<RiskKey, number> {
  const s = BOLD_STEPS[Math.max(0, Math.min(4, Math.round(step)))];
  return Object.fromEntries(RISK_KEYS.map((k) => [k, step >= 4 ? b[k].max : clamp(s.risk[k], b[k])])) as Record<RiskKey, number>;
}
const sameRisk = (a: Record<RiskKey, number>, c: Record<RiskKey, number>) => RISK_KEYS.every((k) => Math.abs(a[k] - c[k]) < 1e-9);
/** Steps that give a distinct set of limits on this level (the rest collapse onto the level's own limits). */
export function boldnessSteps(b: Bounds): { name: string; enabled: boolean }[] {
  return BOLD_STEPS.map((s, i) => ({ name: s.name, enabled: i === 0 || !sameRisk(boldnessRisk(i, b), boldnessRisk(i - 1, b)) }));
}
/** The lowest (distinct, enabled) step whose limits equal these, or null if they were fine-tuned. */
export function matchBoldness(risk: Record<RiskKey, number>, b: Bounds): number | null {
  let hit: number | null = null;
  for (let i = 0; i < 5; i++) if (sameRisk(boldnessRisk(i, b), risk) && hit === null) hit = i;
  return hit;
}
export function setBoldness(body: ConfigBody, step: number, b: Bounds): ConfigBody {
  return { ...body, preset: "custom", boldness: Math.max(0, Math.min(4, Math.round(step))), risk: boldnessRisk(step, b) };
}

// ---------------------------------------------------------------- trading-hours shapes (minutes after 09:30)
export const HOURS: { key: string; name: string; from: number; to: number }[] = SPEC.hours;
export function hoursWindow(key: string, b: Bounds) {
  const h = HOURS.find((x) => x.key === key) ?? HOURS[0];
  const lo = toMin(b.entry.start), hi = toMin(b.entry.end);
  const f = Math.min(hi, Math.max(lo, SESSION_OPEN + h.from)), t = Math.min(hi, Math.max(lo, SESSION_OPEN + h.to));
  return { entry_start: toHhmm(f), entry_end: toHhmm(t) };
}
export function hoursShape(body: ConfigBody, b: Bounds): string | null {
  return HOURS.find((h) => { const w = hoursWindow(h.key, b); return w.entry_start === body.timing.entry_start && w.entry_end === body.timing.entry_end; })?.key ?? null;
}

// ---------------------------------------------------------------- extra caution pills
export const CAUTION: { key: Caution; name: string }[] = (Object.keys(SPEC.caution) as Caution[]).map((key) => ({ key, name: SPEC.caution[key].name }));
const cautionFields = (key: Caution) => (Object.keys(SPEC.caution[key]) as (TimingNumKey | "name")[]).filter((k): k is TimingNumKey => k !== "name");
/** A pill is on when any field it writes is stricter than the level's value. */
export function cautionOn(body: ConfigBody, b: Bounds): Record<Caution, boolean> {
  return Object.fromEntries(CAUTION.map(({ key }) => [key, cautionFields(key).some((k) => body.timing[k] !== levelValue(k, b))])) as Record<Caution, boolean>;
}
/** On writes the spec's values (clamped to the level); off returns those fields to the level's value. */
export function setCaution(body: ConfigBody, key: Caution, on: boolean, b: Bounds): ConfigBody {
  const t = { ...body.timing };
  for (const k of cautionFields(key)) t[k] = on ? clamp(Number(SPEC.caution[key][k]), b[k]) : levelValue(k, b);
  return { ...body, preset: "custom", timing: t };
}

// ---------------------------------------------------------------- presets
export const PRESETS: { key: SpecPresetKey; name: string; icon: string; blurb: string }[] = (Object.keys(SPEC.presets) as SpecPresetKey[])
  .map((key) => ({ key, name: SPEC.presets[key].name, icon: SPEC.presets[key].icon, blurb: SPEC.presets[key].blurb }));
export function presetBody(key: SpecPresetKey, b: Bounds, themes: Themes, universe: string[]): ConfigBody {
  const p = SPEC.presets[key];
  let x = levelDefaults(b);
  x = { ...x, focus: { themes: p.focus.themes.filter((t) => t in themes), symbols: [], exclude: [], mode: p.focus.mode },
    timing: { ...x.timing, ...hoursWindow(p.hours, b) } };
  x = setBoldness(x, p.boldness, b);
  for (const c of p.caution) x = setCaution(x, c, true, b);
  return clampBody({ ...x, preset: key }, b, themes, universe);
}
/** Which preset card these settings match (presets are only shortcuts, so we compare the fields they write). */
export function matchPreset(body: ConfigBody, b: Bounds, themes: Themes, universe: string[]): PresetKey {
  for (const p of PRESETS) if (diffConfig(presetBody(p.key, b, themes, universe), body, themes).length === 0) return p.key;
  return "custom";
}

// ---------------------------------------------------------------- focus edits
export function toggleTheme(body: ConfigBody, key: string | null): ConfigBody {
  // Exclusions are kept: they mean "never traded" in every mode, including All stocks.
  if (key === null) return { ...body, preset: "custom", focus: { themes: [], symbols: [], exclude: body.focus.exclude, mode: "all" } };
  const on = body.focus.themes.includes(key);
  const th = on ? body.focus.themes.filter((t) => t !== key) : [...body.focus.themes, key];
  const empty = th.length === 0 && body.focus.symbols.length === 0;
  return { ...body, preset: "custom", focus: { ...body.focus, themes: th, mode: empty ? "all" : body.focus.mode === "all" ? "prefer" : body.focus.mode } };
}

// ---------------------------------------------------------------- exclusions (S53): never traded, in every mode
/** Can another stock be excluded? Only universe stocks (reference symbols like SPY or sector ETFs aren't in it). */
export const canExclude = (body: ConfigBody, sym: string, universe: string[]) =>
  universe.includes(sym) && !body.focus.exclude.includes(sym) && body.focus.exclude.length < MAX_EXCLUDED;
/** Exclude a stock: it also leaves the focus set (an extra symbol is dropped). The mode is unchanged. */
export function excludeStock(body: ConfigBody, sym: string, universe: string[]): ConfigBody {
  if (!canExclude(body, sym, universe)) return body;
  const f = body.focus;
  return { ...body, preset: "custom", focus: { ...f, exclude: [...f.exclude, sym], symbols: f.symbols.filter((x) => x !== sym) } };
}
export function includeStock(body: ConfigBody, sym: string): ConfigBody {
  return { ...body, preset: "custom", focus: { ...body.focus, exclude: body.focus.exclude.filter((x) => x !== sym) } };
}

// ---------------------------------------------------------------- plain-words diff
export const LABEL: Record<NumKey, string> = {
  risk_per_trade_pct: "Risk per trade (of the pot)", max_positions: "Open positions at once", max_position_pct: "Largest position (of the pot)",
  max_entries_per_day: "New trades a day", daily_stop_pct: "Daily stop", run_drawdown_end_pct: "End the run at drawdown",
  pause_losses: "Pause after losses in a row", pause_minutes: "Pause length", vix_max: "No new trades when VIX is above",
  event_window_min: "Fed/CPI quiet window", min_price_usd: "Min share price", min_adv_usd: "Min daily $ volume", max_spread_bp: "Max bid-ask spread",
};
export function fmtNum(k: NumKey, v: number): string {
  switch (k) {
    case "risk_per_trade_pct": return `${v.toFixed(2)}%`;
    case "daily_stop_pct": return `−${v.toFixed(2)}%`;
    case "run_drawdown_end_pct": return `−${v}%`;
    case "max_position_pct": return `${v}%`;
    case "pause_losses": return `${v} losses`;
    case "pause_minutes": return `${v} min`;
    case "event_window_min": return `±${v} min`;
    case "min_price_usd": return `$${v}`;
    case "min_adv_usd": return `$${Math.round(v / 1e6)}M`;
    case "max_spread_bp": return `${v} bp`;
    default: return String(v);
  }
}
// Plain names, the same as everywhere else (lib/explain.ts RULES). Both rules are retired (decision 15): systems test only.
export const STRATEGY_NAME: Record<StrategyKey, string> = { dip_reversal: "Dip rule", spy_late_momentum: "SPY late-day rule" };

export function focusText(body: ConfigBody, themes: Themes): string {
  const set = focusSet(body, themes);
  if (body.focus.mode === "all") return "All 59 stocks";
  const c = body.focus.mode === "only" ? body.focus.custom : undefined;
  const names = c ? [`“${c.name}”`] : [...body.focus.themes.map((t) => themes[t]?.label ?? t), ...body.focus.symbols];
  const list = names.length > 3 ? `${names.slice(0, 3).join(", ")} +${names.length - 3}` : names.join(", ");
  return `${body.focus.mode === "only" ? "Only" : "Prefer"} ${list} (${set.length} stock${set.length === 1 ? "" : "s"})`;
}

export interface DiffRow { key: string; label: string; from: string; to: string }
/** What changes between two bodies, in plain words. UI-only fields (preset, boldness) are not changes. */
export function diffConfig(a: ConfigBody, c: ConfigBody, themes: Themes): DiffRow[] {
  const rows: DiffRow[] = [];
  const add = (key: string, label: string, from: string, to: string) => { if (from !== to) rows.push({ key, label, from, to }); };
  add("focus", "Focus", focusText(a, themes), focusText(c, themes));
  const ex = (x: ConfigBody) => (!x.focus.exclude.length ? "none" : [...x.focus.exclude].sort().join(", "));
  add("exclude", "Never traded", ex(a), ex(c));
  const own = (x: ConfigBody) => (x.focus.custom?.tickers.length ? x.focus.custom.tickers.map((t) => t.symbol).sort().join(", ") : "none");
  add("custom", "Your own tickers", own(a), own(c));
  for (const k of RISK_KEYS) add(k, LABEL[k], fmtNum(k, a.risk[k]), fmtNum(k, c.risk[k]));
  for (const s of STRATEGY_KEYS) add(s, STRATEGY_NAME[s], a.strategies[s] ? "on" : "off", c.strategies[s] ? "on" : "off");
  add("hours", "New trades between", `${a.timing.entry_start}–${a.timing.entry_end} ET`, `${c.timing.entry_start}–${c.timing.entry_end} ET`);
  for (const k of TIMING_KEYS) add(k, LABEL[k], fmtNum(k, a.timing[k]), fmtNum(k, c.timing[k]));
  for (const k of ADV_KEYS) add(k, LABEL[k], fmtNum(k, a.advanced[k]), fmtNum(k, c.advanced[k]));
  return rows;
}
export const diffSentence = (r: DiffRow) => `${r.label}: ${r.from} → ${r.to}`;

// ---------------------------------------------------------------- dollars on the current pot
export function dollars(body: ConfigBody, pot: number) {
  return {
    maxLoss: (body.risk.risk_per_trade_pct / 100) * pot,
    position: (body.risk.max_position_pct / 100) * pot,
    dailyStop: (body.risk.daily_stop_pct / 100) * pot,
    runEndsNear: pot * (1 - body.risk.run_drawdown_end_pct / 100),
  };
}

// ---------------------------------------------------------------- next session open (09:30 ET, weekdays)
/** The next 09:30 ET open strictly after `now`, as YYYY-MM-DD. Market holidays are not known here; the engine simply
 * applies a version at the next real open, so the UI says "next open" and shows this date as the likely one. */
export function nextOpenEt(now: Date = new Date()): string {
  const parts = (d: Date) => Object.fromEntries(new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", year: "numeric", month: "2-digit",
    day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false, weekday: "short" }).formatToParts(d).map((p) => [p.type, p.value]));
  const p = parts(now);
  const mins = (Number(p.hour) % 24) * 60 + Number(p.minute);
  let d = new Date(Date.UTC(Number(p.year), Number(p.month) - 1, Number(p.day)));
  const wk = (x: Date) => x.getUTCDay();
  if (!(wk(d) >= 1 && wk(d) <= 5 && mins < SESSION_OPEN)) {
    do d = new Date(d.getTime() + 86_400_000); while (wk(d) === 0 || wk(d) === 6);
  }
  return d.toISOString().slice(0, 10);
}
/** "Thu 12 Nov 09:30 ET" from a date or an ISO timestamp. */
export function openLabel(isoOrDate: string): string {
  const d = isoOrDate.length === 10 ? new Date(isoOrDate + "T12:00:00Z") : new Date(isoOrDate);
  const tz = isoOrDate.length === 10 ? "UTC" : "America/New_York";
  const f = new Intl.DateTimeFormat("en-GB", { timeZone: tz, weekday: "short", day: "numeric", month: "short" }).format(d).replace(",", "");
  return `${f} 09:30 ET`;
}
