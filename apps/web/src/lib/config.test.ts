import { describe, expect, it } from "vitest";
import estimatorJson from "../data/estimator.json";
import themesJson from "../data/themes.json";
import seedYaml from "../../../../config/universe_seed.yaml?raw";
import presetsJson from "../data/presets.json";
import strategyPageSrc from "../pages/Strategy.tsx?raw";
import fineTuneSrc from "../pages/FineTune.tsx?raw";
import sharedSrc from "../pages/StrategyShared.tsx?raw";
import mainSrc from "../main.tsx?raw";
import type { Level } from "./api";
import {
  ADV_KEYS, BOLD_STEPS, CAUTION, HOURS, MAX_EXCLUDED, NUM_KEYS, PRESETS, canExclude, excludeStock, includeStock, RISK_KEYS, TIMING_KEYS, boldnessRisk, boldnessSteps, cautionOn, clampBody, diffConfig, diffSentence,
  dollars, focusSet, hoursShape, hoursWindow, levelDefaults, matchBoldness, matchPreset, nextOpenEt, normalizeBounds, presetBody,
  setBoldness, setCaution, toggleTheme, validateBody, type Bounds, type ConfigBody, type NumKey, type Themes,
} from "./config";
import { estimateTrades, paceText, windowShare, type EstimatorData } from "./derive";

const strategySrc = strategyPageSrc + fineTuneSrc + sharedSrc;
const EST = estimatorJson as unknown as EstimatorData;
const THEMES = themesJson as Themes;
const UNI = Object.keys(EST.dip_per_month);
const LEVELS: Level[] = ["conservative", "balanced", "aggressive"];
const B = (l: Level = "aggressive") => normalizeBounds(null, l);
const val = (body: ConfigBody, k: NumKey) =>
  (RISK_KEYS as string[]).includes(k) ? body.risk[k as never] : (ADV_KEYS as string[]).includes(k) ? body.advanced[k as never] : body.timing[k as never];
const inBounds = (body: ConfigBody, b: Bounds) => NUM_KEYS.every((k) => val(body, k) >= b[k].min && val(body, k) <= b[k].max);

/** Tiny parser for the `themes:` block of config/universe_seed.yaml (flow-style one-liners). */
function parseThemes(yaml: string): Themes {
  const out: Themes = {};
  const block = yaml.split(/^themes:\s*$/m)[1] ?? "";
  for (const line of block.split("\n")) {
    const m = line.match(/^\s+(\w+):\s*\{label:\s*([^,]+?),\s*symbols:\s*\[([^\]]*)\]\}/);
    if (m) out[m[1]] = { label: m[2].trim(), symbols: m[3].split(",").map((s) => s.trim()).filter(Boolean) };
    else if (/^\S/.test(line)) break;
  }
  return out;
}

describe("themes.json mirrors config/universe_seed.yaml", () => {
  it("has the same keys, labels and symbols", () => {
    const y = parseThemes(seedYaml);
    expect(Object.keys(y).length).toBeGreaterThan(5);
    expect(THEMES).toEqual(y);
  });
  it("every theme symbol is in the estimator universe", () => {
    for (const t of Object.values(THEMES)) for (const s of t.symbols) expect(UNI).toContain(s);
    expect(UNI.length).toBe(59);
  });
});

describe("estimateTrades (S45)", () => {
  const d = clampBody(null, B(), THEMES, UNI);
  it("the full window is exactly 1", () => expect(windowShare(5, 375, EST)).toBeCloseTo(1, 9));
  it("level defaults ≈ the replay's total (~44/month), normal pace", () => {
    const e = estimateTrades(d, EST, THEMES);
    const total = Object.values(EST.dip_per_month).reduce((a, b) => a + b, 0);
    expect(e.perMonth).toBeCloseTo(total, 6);
    expect(e.perMonth).toBeGreaterThan(40);
    expect(e.pace).toBe("normal");
    expect(e.monthsTo900).toBeCloseTo(900 / total, 6);
  });
  it("Only hyperscalers ≈ 1.8/month, very slow", () => {
    const b = { ...d, focus: { themes: ["hyperscalers"], symbols: [], exclude: [], mode: "only" as const } };
    const e = estimateTrades(b, EST, THEMES);
    expect(e.perMonth).toBeCloseTo(5 * 0.364, 3);
    expect(e.pace).toBe("very slow evidence");
    expect(paceText(e)).toMatch(/years/);
  });
  it("Prefer still trades all 59 (it only ranks)", () => {
    const b = { ...d, focus: { themes: ["hyperscalers"], symbols: [], exclude: [], mode: "prefer" as const } };
    expect(estimateTrades(b, EST, THEMES).perMonth).toBeCloseTo(estimateTrades(d, EST, THEMES).perMonth, 9);
  });
  it("exclusions shrink an Only set", () => {
    const b = { ...d, focus: { themes: ["semiconductors"], symbols: [], exclude: ["TXN"], mode: "only" as const } };
    expect(estimateTrades(b, EST, THEMES).perMonth).toBeCloseTo(0.364 * 4 + 0.606, 3);
  });
  it("skipping the open removes most of the first half-hour", () => {
    const skip = { ...d, timing: { ...d.timing, ...hoursWindow("skip", B()) } };
    const r = estimateTrades(skip, EST, THEMES).perMonth / estimateTrades(d, EST, THEMES).perMonth;
    expect(r).toBeCloseTo(1 - 0.2246, 3);
  });
  it("mornings + afternoons ≈ all day", () => {
    const w = (k: string) => { const x = hoursWindow(k, B()); return windowShare(Number(x.entry_start.slice(0, 2)) * 60 + Number(x.entry_start.slice(3)) - 570, Number(x.entry_end.slice(0, 2)) * 60 + Number(x.entry_end.slice(3)) - 570, EST); };
    expect(w("am") + w("pm")).toBeCloseTo(1, 9);
  });
  it("dip off gives 0 and says SPY is not estimated", () => {
    const e = estimateTrades({ ...d, strategies: { dip_reversal: false, spy_late_momentum: true } }, EST, THEMES);
    expect(e.perMonth).toBe(0);
    expect(e.monthsTo900).toBeNull();
    expect(paceText(e)).toMatch(/not estimated/);
  });
  it("pace thresholds", () => {
    const mk = (n: number) => estimateTrades({ ...d, focus: { themes: [], symbols: UNI.slice(0, n), exclude: [], mode: "only" } }, EST, THEMES).pace;
    expect(mk(5)).toBe("very slow evidence");
    expect(mk(59)).toBe("normal");
  });
});

describe("Boldness (5 steps, clamped to the level)", () => {
  it("Hunter gets the design's table exactly", () => {
    for (let i = 0; i < 5; i++) expect(boldnessRisk(i, B())).toEqual(BOLD_STEPS[i].risk);
  });
  for (const l of LEVELS) {
    it(`${l}: every step is inside the bounds, monotone, and Max = the level`, () => {
      const b = B(l);
      let prev: Record<string, number> | null = null;
      for (let i = 0; i < 5; i++) {
        const r = boldnessRisk(i, b);
        for (const k of RISK_KEYS) {
          expect(r[k]).toBeGreaterThanOrEqual(b[k].min);
          expect(r[k]).toBeLessThanOrEqual(b[k].max);
          if (prev) expect(r[k]).toBeGreaterThanOrEqual(prev[k]);
        }
        prev = r;
      }
      for (const k of RISK_KEYS) expect(boldnessRisk(4, b)[k]).toBe(b[k].max);
      expect(matchBoldness(levelDefaults(b).risk, b)).not.toBeNull();
    });
  }
  it("below Hunter the upper steps collapse and are disabled", () => {
    const bal = boldnessSteps(B("balanced")).map((s) => s.enabled);
    expect(bal).toEqual([true, true, true, false, false]);
    expect(boldnessRisk(3, B("balanced"))).toEqual(boldnessRisk(4, B("balanced")));
    expect(boldnessSteps(B("aggressive")).every((s) => s.enabled)).toBe(true);
  });
  it("a fine-tuned risk set matches no step", () => {
    const b = B();
    const x = { ...boldnessRisk(2, b), max_positions: 7 };
    expect(matchBoldness(x, b)).toBeNull();
  });
  it("setBoldness writes the risk fields and marks custom", () => {
    const x = setBoldness(levelDefaults(B()), 1, B());
    expect(x.risk.risk_per_trade_pct).toBe(0.35);
    expect(x.preset).toBe("custom");
    expect(x.boldness).toBe(1);
  });
});

describe("presets → body", () => {
  for (const l of LEVELS) for (const p of PRESETS) {
    it(`${l} / ${p.key} is valid, bounded and recognised`, () => {
      const b = B(l);
      const x = presetBody(p.key, b, THEMES, UNI);
      expect(inBounds(x, b)).toBe(true);
      expect(validateBody(x, b, THEMES)).toEqual([]);
      expect(matchPreset(x, b, THEMES, UNI)).toBe(p.key);
    });
  }
  it("default = level defaults, no diff", () => {
    expect(diffConfig(levelDefaults(B()), presetBody("default", B(), THEMES, UNI), THEMES)).toEqual([]);
  });
  it("big tech: Only, 3 themes, Moderate, skips the open", () => {
    const x = presetBody("big_tech", B(), THEMES, UNI);
    expect(x.focus.mode).toBe("only");
    expect(x.focus.themes).toEqual(["big_tech", "hyperscalers", "semiconductors"]);
    expect(x.risk.risk_per_trade_pct).toBe(0.5);
    expect(hoursShape(x, B())).toBe("skip");
    expect(focusSet(x, THEMES).length).toBe(13);
  });
  it("calm: Careful and all three caution pills on", () => {
    const x = presetBody("calm", B(), THEMES, UNI);
    expect(x.risk.risk_per_trade_pct).toBe(0.25);
    expect(cautionOn(x, B())).toEqual({ vix: true, pause: true, quiet: true });
    expect(x.timing.vix_max).toBe(25);
    expect(x.timing.pause_losses).toBe(2);
    expect(x.timing.pause_minutes).toBe(120);
    expect(x.timing.event_window_min).toBe(60);
  });
  it("any edit after a preset is no longer that preset", () => {
    const x = setCaution(presetBody("big_tech", B(), THEMES, UNI), "vix", true, B());
    expect(matchPreset(x, B(), THEMES, UNI)).toBe("custom");
  });
  it("caution off returns to the level", () => {
    const b = B();
    let x = presetBody("calm", b, THEMES, UNI);
    for (const c of ["vix", "pause", "quiet"] as const) x = setCaution(x, c, false, b);
    expect(cautionOn(x, b)).toEqual({ vix: false, pause: false, quiet: false });
    for (const k of TIMING_KEYS) expect(x.timing[k]).toBe(levelDefaults(b).timing[k]);
  });
});

describe("diff text", () => {
  const b = B();
  it("is empty for the same body and ignores preset/boldness", () => {
    const d = levelDefaults(b);
    expect(diffConfig(d, { ...d, preset: "custom", boldness: 1 }, THEMES)).toEqual([]);
  });
  it("speaks plain words", () => {
    const rows = diffConfig(levelDefaults(b), presetBody("big_tech", b, THEMES, UNI), THEMES).map(diffSentence);
    expect(rows).toContain("Risk per trade (of the pot): 1.00% → 0.50%");
    expect(rows).toContain("New trades between: 09:35–15:45 ET → 10:00–15:45 ET");
    expect(rows.find((r) => r.startsWith("Focus"))).toBe("Focus: All 59 stocks → Only Big Tech, Hyperscalers, Semiconductors (13 stocks)");
    expect(rows).toContain("Open positions at once: 8 → 5");
  });
  it("names exclusions and strategy switches", () => {
    const d = levelDefaults(b);
    const x = { ...toggleTheme(d, "energy"), strategies: { dip_reversal: true, spy_late_momentum: false } };
    x.focus = { ...x.focus, exclude: ["XOM"] };
    const s = diffConfig(d, x, THEMES).map(diffSentence);
    expect(s).toContain("Never traded: none → XOM");
    expect(s).toContain("SPY end-of-day momentum: on → off");
    expect(s).toContain("Focus: All 59 stocks → Prefer Energy (2 stocks)");
  });
});

describe("body validation mirror: never emit a value outside the bounds", () => {
  it("clamps wild values for every level", () => {
    for (const l of LEVELS) {
      const b = B(l);
      const wild = {
        risk: Object.fromEntries(RISK_KEYS.map((k) => [k, 999])), timing: { entry_start: "04:00", entry_end: "23:59", pause_losses: -5, pause_minutes: 1e9, vix_max: 99, event_window_min: 0 },
        advanced: { min_price_usd: 0, min_adv_usd: -1, max_spread_bp: 50 }, strategies: { dip_reversal: true, spy_late_momentum: true },
        focus: { themes: ["nope", "energy"], symbols: ["ZZZZ", "CRM", "CRM"], exclude: ["SPY"], mode: "weird" },
      } as unknown as Partial<ConfigBody>;
      const x = clampBody(wild, b, THEMES, UNI);
      expect(inBounds(x, b)).toBe(true);
      expect(x.timing.entry_start).toBe(b.entry.start);
      expect(x.timing.entry_end).toBe(b.entry.end);
      expect(x.focus).toEqual({ themes: ["energy"], symbols: ["CRM"], exclude: [], mode: "all" });
      if (l === "conservative") expect(x.strategies.spy_late_momentum).toBe(false);
    }
  });
  it("missing fields mean the level's value", () => {
    const b = B("balanced");
    const x = clampBody({ risk: { risk_per_trade_pct: 0.3 } } as Partial<ConfigBody>, b, THEMES, UNI);
    expect(x.risk.risk_per_trade_pct).toBe(0.3);
    expect(x.risk.max_positions).toBe(5);
    expect(x.timing.pause_minutes).toBe(60);
    expect(x.strategies).toEqual({ dip_reversal: true, spy_late_momentum: true });
  });
  it("random bodies always clamp into bounds", () => {
    let seed = 7;
    const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
    for (let i = 0; i < 300; i++) {
      const b = B(LEVELS[i % 3]);
      const n = () => (rnd() - 0.3) * 1e3;
      const x = clampBody({ risk: Object.fromEntries(RISK_KEYS.map((k) => [k, n()])), timing: Object.fromEntries(TIMING_KEYS.map((k) => [k, n()])),
        advanced: Object.fromEntries(ADV_KEYS.map((k) => [k, n() * 1e6])) } as unknown as Partial<ConfigBody>, b, THEMES, UNI);
      expect(inBounds(x, b)).toBe(true);
    }
  });
  it("flags the non-range rules", () => {
    const b = B();
    const d = levelDefaults(b);
    expect(validateBody({ ...d, focus: { themes: ["energy"], symbols: [], exclude: [], mode: "only" } }, b, THEMES).join()).toMatch(/at least 5/);
    expect(validateBody({ ...d, strategies: { dip_reversal: false, spy_late_momentum: false } }, b, THEMES).join()).toMatch(/one strategy/);
    expect(validateBody({ ...d, timing: { ...d.timing, entry_start: "12:00", entry_end: "12:15" } }, b, THEMES).join()).toMatch(/30 minutes/);
    expect(validateBody(d, b, THEMES)).toEqual([]);
  });
  it("tolerates partial server bounds", () => {
    const b = normalizeBounds({ risk_per_trade_pct: { min: 0.1, max: 0.4 } } as Partial<Bounds>, "balanced");
    expect(b.risk_per_trade_pct.max).toBe(0.4);
    expect(b.max_positions.max).toBe(5);
    expect(b.strategies).toContain("spy_late_momentum");
  });
});

describe("dollars and dates", () => {
  it("dollar equivalents on the pot", () => {
    const x = dollars(levelDefaults(B()), 5000);
    expect(x.maxLoss).toBe(50);
    expect(x.position).toBe(1000);
    expect(x.dailyStop).toBe(150);
  });
  it("next open skips weekends and the current session", () => {
    expect(nextOpenEt(new Date("2026-11-11T13:00:00Z"))).toBe("2026-11-11"); // Wed 08:00 ET
    expect(nextOpenEt(new Date("2026-11-11T17:00:00Z"))).toBe("2026-11-12"); // Wed 12:00 ET
    expect(nextOpenEt(new Date("2026-11-13T22:00:00Z"))).toBe("2026-11-16"); // Fri evening → Mon
    expect(nextOpenEt(new Date("2026-11-15T12:00:00Z"))).toBe("2026-11-16"); // Sunday
  });
});

describe("the Strategy page has no live-trading control", () => {
  it("never calls the command or new-run RPCs and offers no go-live", () => {
    expect(strategySrc).not.toMatch(/api\.(command|newRun)\b/);
    expect(strategySrc).not.toMatch(/go[- ]?live\s*(button|switch)|live trading (on|switch)|live (keys|base url)/i);
    expect(strategySrc).not.toMatch(/dangerouslySetInnerHTML/);
    const rpcs = [...strategySrc.matchAll(/api\.(\w+)\(/g)].map((m) => m[1]);
    for (const r of rpcs) expect(["getConfig", "saveConfig", "applyConfig", "resetConfig"]).toContain(r);
  });
  it("the config body has no field outside the contract", () => {
    expect(Object.keys(levelDefaults(B())).sort()).toEqual(["advanced", "boldness", "focus", "preset", "risk", "schema", "strategies", "timing"]);
  });
});

describe("exclude means never traded", () => {
  it("drops excluded stocks from the estimate in every mode", () => {
    const d = levelDefaults(B());
    const base = estimateTrades(d, EST, THEMES).perMonth;
    const ex = estimateTrades({ ...d, focus: { ...d.focus, exclude: ["DUK"] } }, EST, THEMES).perMonth;
    expect(ex).toBeLessThan(base);
  });
});

describe("S51: presets and Boldness come from the shared spec (presets.json)", () => {
  const spec = presetsJson as unknown as {
    boldness: Record<string, number | string>[]; hours: { key: string }[]; caution: Record<string, Record<string, number | string>>;
    presets: Record<string, { boldness: number; hours: string; caution: string[] }>; limits: { max_excluded: number };
  };
  it("the exported tables are the spec, nothing hard-coded", () => {
    expect(BOLD_STEPS.map((s) => s.name)).toEqual(spec.boldness.map((s) => s.name));
    for (let i = 0; i < 5; i++) for (const k of RISK_KEYS) expect(BOLD_STEPS[i].risk[k]).toBe(spec.boldness[i][k]);
    expect(HOURS).toEqual(spec.hours);
    expect(CAUTION.map((c) => c.key)).toEqual(Object.keys(spec.caution));
    expect(PRESETS.map((p) => p.key)).toEqual(Object.keys(spec.presets));
    expect(MAX_EXCLUDED).toBe(spec.limits.max_excluded);
    expect(MAX_EXCLUDED).toBe(10);
  });
  for (const l of LEVELS) {
    it(`${l}: every step, hours shape, caution pill and preset, clamped, stays inside the bounds`, () => {
      const b = B(l);
      const d = levelDefaults(b);
      for (let i = 0; i < spec.boldness.length; i++) {
        const x = setBoldness(d, i, b);
        expect(inBounds(x, b)).toBe(true);
        expect(clampBody(x, b, THEMES, UNI).risk).toEqual(x.risk); // already clamped: clampBody changes nothing
      }
      for (const h of spec.hours) {
        const x = { ...d, timing: { ...d.timing, ...hoursWindow(h.key, b) } };
        expect(validateBody(x, b, THEMES)).toEqual([]);
      }
      for (const c of CAUTION) {
        const x = setCaution(d, c.key, true, b);
        expect(inBounds(x, b)).toBe(true);
        expect(cautionOn(x, b)[c.key]).toBe(true);
      }
      for (const p of PRESETS) {
        const x = presetBody(p.key, b, THEMES, UNI);
        expect(inBounds(x, b)).toBe(true);
        expect(validateBody(x, b, THEMES)).toEqual([]);
        expect(x.risk).toEqual(boldnessRisk(spec.presets[p.key].boldness, b));
        expect(hoursShape(x, b)).toBe(spec.presets[p.key].hours);
        for (const c of CAUTION) expect(cautionOn(x, b)[c.key]).toBe(spec.presets[p.key].caution.includes(c.key));
      }
    });
  }
  it("any manual edit after a preset or step switches to Custom", () => {
    const b = B("balanced");
    const x = presetBody("calm", b, THEMES, UNI);
    expect(matchPreset({ ...x, risk: { ...x.risk, max_positions: x.risk.max_positions - 1 } }, b, THEMES, UNI)).toBe("custom");
    expect(matchPreset(excludeStock(x, "INTC", UNI), b, THEMES, UNI)).toBe("custom");
    expect(setBoldness(x, 1, b).preset).toBe("custom");
  });
});

describe("S53: exclusions, in every mode", () => {
  const b = B();
  const d = levelDefaults(b);
  it("excluding works in All mode and keeps the mode", () => {
    const x = excludeStock(excludeStock(d, "INTC", UNI), "XOM", UNI);
    expect(x.focus.mode).toBe("all");
    expect(x.focus.exclude).toEqual(["INTC", "XOM"]);
    expect(includeStock(x, "INTC").focus.exclude).toEqual(["XOM"]);
  });
  it("an excluded extra symbol leaves the focus set", () => {
    const x = { ...d, focus: { themes: ["semiconductors"], symbols: ["CRM"], exclude: [], mode: "prefer" as const } };
    const y = excludeStock(x, "CRM", UNI);
    expect(y.focus.symbols).toEqual([]);
    expect(focusSet(y, THEMES)).not.toContain("CRM");
  });
  it("choosing All stocks keeps the exclusions", () => {
    const x = excludeStock(toggleTheme(d, "energy"), "XOM", UNI);
    expect(toggleTheme(x, null).focus).toEqual({ themes: [], symbols: [], exclude: ["XOM"], mode: "all" });
  });
  it("at most MAX_EXCLUDED: the 11th is refused and clampBody trims", () => {
    let x = d;
    for (const s of UNI.slice(0, MAX_EXCLUDED)) x = excludeStock(x, s, UNI);
    expect(x.focus.exclude.length).toBe(MAX_EXCLUDED);
    expect(canExclude(x, UNI[MAX_EXCLUDED], UNI)).toBe(false);
    expect(excludeStock(x, UNI[MAX_EXCLUDED], UNI)).toBe(x);
    const wild = clampBody({ focus: { themes: [], symbols: [], exclude: UNI.slice(0, 15), mode: "all" } } as Partial<ConfigBody>, b, THEMES, UNI);
    expect(wild.focus.exclude.length).toBe(MAX_EXCLUDED);
  });
  it("reference symbols (SPY, sector ETFs) aren't in the universe, so they can't be excluded", () => {
    for (const s of ["SPY", "XLK", "XLE", "XLF", "SMH", "QQQ"]) {
      expect(UNI).not.toContain(s);
      expect(canExclude(d, s, UNI)).toBe(false);
      expect(excludeStock(d, s, UNI).focus.exclude).toEqual([]);
    }
  });
  for (const mode of ["all", "prefer", "only"] as const) {
    it(`the estimator drops excluded stocks in ${mode} mode`, () => {
      const x = { ...d, focus: { themes: ["semiconductors", "big_tech"], symbols: [], exclude: [], mode } };
      const sym = focusSet(x, THEMES)[0];
      const before = estimateTrades(x, EST, THEMES).perMonth;
      const after = estimateTrades(excludeStock(x, sym, UNI), EST, THEMES).perMonth;
      expect(before - after).toBeCloseTo(EST.dip_per_month[sym], 9);
    });
  }
});

describe("S52: Fine-tune is a real route that shares the Strategy draft", () => {
  it("/strategy/fine-tune is a lazy child route of /strategy", () => {
    expect(mainSrc).toMatch(/<Route path="strategy" element={<Strategy \/>}>[\s\S]*<Route path="fine-tune" element={<FineTune \/>} \/>/);
    expect(mainSrc).toMatch(/const FineTune = lazy\(/);
  });
  it("Fine-tune reads the shared draft and calls no RPC itself", () => {
    expect(fineTuneSrc).toMatch(/useStrategy\(\)/);
    expect(fineTuneSrc).not.toMatch(/api\./);
    expect(fineTuneSrc).toMatch(/to="\/strategy"/);
  });
  it("the accordion rows are buttons with aria-expanded and aria-controls", () => {
    expect(fineTuneSrc).toMatch(/aria-expanded={open} aria-controls={panel}/);
  });
});
