// The app's display name and logo (#254; stakeholder, 16 Nov 2026: "on the app UI the name should be kaibo", and
// "dont capitalize": it is lowercase everywhere, as the first word of a sentence too).
// Three nets keep a wrong name (the earlier one, or today's with a capital) from showing where a person reads it:
// 1. every page-render test reads what it rendered (testkit `render` throws on a wrong name);
// 2. the text written in the source (string literals and JSX text, never identifiers or comments) is read here, for
//    the states no test renders: closed sheets, error branches. No text may spell the name itself; it comes from BRAND;
// 3. text that comes from outside apps/web (engine reasons, the shared presets file) passes through brandText.
// Code identifiers, routes, storage keys, RPC names and env vars are not display names and are not checked.
import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import ts from "typescript";
import { BRAND, NAME, REPO_DIR, brandText } from "./brand/brand";
import { APP_COLOUR, ICONS, MANIFEST_PATH, backgroundOf, brandHtml, manifest, manifestJson } from "./brand/assets";
import lock from "./brand/icons.lock.json";
import { IDLE, LARGE, Logo, ROLES, TIMING, mark, resetLogoMotion } from "./components/Logo";
import { capsule, circle, drawnFrom } from "./brand/mark";
import wordDark from "./brand/logo/wordmark-on-dark.svg?raw";
import wordLight from "./brand/logo/wordmark-on-light.svg?raw";
import symbolDark from "./brand/logo/symbol-on-dark.svg?raw";
import iconDark from "./brand/logo/icon-dark.svg?raw";
import tokens from "./v4tokens.css?raw";
import { Shell } from "./components/Shell";
import { NotOwner, SignIn } from "./pages/SignIn";
import { Register } from "./pages/Register";
import { Onboarding } from "./pages/Onboarding";
import { PRESETS, SPEC } from "./lib/config";
import { applyBlockers, itemFromCheck } from "./lib/tickers";
import indexHtml from "../index.html?raw";
import css from "./app.css?raw";
import { ExperimentSheetBody } from "./components/Experiment";
import { riskStatus } from "./lib/derive";
import { roomOutcome } from "./lib/roomview";
import { emptyOverview, paperRun, render, text, wrongName } from "./pages/testkit";

vi.mock("./lib/api", () => import("./pages/testkit-api"));
vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });

describe("the display name is written once, in lowercase", () => {
  it("kaibo: one lowercase form, for the wordmark and for a sentence", () => {
    expect(BRAND).toBe("kaibo");
    expect(BRAND).toBe(BRAND.toLowerCase());
    expect(NAME).toBe(BRAND);
  });

  it("the source never spells the name: no string or JSX text writes it, in either form or any case", () => {
    const sources = import.meta.glob("./**/*.{ts,tsx}", { query: "?raw", import: "default", eager: true }) as Record<string, string>;
    // brand.ts is the one module that writes the name, knows the earlier one (brandText) and the repo's folder (REPO_DIR).
    const files = Object.keys(sources).filter((f) => !/\.test\.tsx?$/.test(f) && !/\/testkit[^/]*$/.test(f) && f !== "./brand/brand.ts");
    expect(files.length).toBeGreaterThan(40);
    const found: string[] = [];
    for (const f of files) {
      const sf = ts.createSourceFile(f, sources[f], ts.ScriptTarget.Latest, true, f.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
      const visit = (n: ts.Node) => {
        const written = ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n) || ts.isTemplateHead(n) || ts.isTemplateMiddle(n)
          || ts.isTemplateTail(n) || ts.isJsxText(n);
        const importPath = ts.isStringLiteral(n) && (ts.isImportDeclaration(n.parent) || ts.isExportDeclaration(n.parent));
        if (written && !importPath && /kaibot?/i.test(n.text))
          found.push(`${f}:${sf.getLineAndCharacterOfPosition(n.getStart(sf)).line + 1} "${n.text.trim().slice(0, 60)}"`);
        ts.forEachChild(n, visit);
      };
      visit(sf);
    }
    expect(found, "A string or JSX text spells the app's name: write NAME or BRAND from src/brand/brand.ts, so it stays lowercase and in one "
      + "place. If the text is never shown to a person (a storage key, a schema name), it is not a display name: leave it out of this check "
      + "and say why.").toEqual([]);
  });

  it("the reader of rendered pages finds the earlier name in text and in what a screen reader says", () => {
    expect(wrongName("<p>Kaibot trades on its own.</p>")).toEqual(["Kaibot trades on its own."]);
    expect(wrongName('<a aria-label="kaibot home" href="/"><img alt="" src="/x.svg"/></a>')).toEqual(["kaibot home"]);
    expect(wrongName('<img alt="Kaibot" src="/x.svg"/><input placeholder="Ask kaibot"/><span title="About Kaibot">i</span>')).toHaveLength(3);
  });

  it("and today's name with a capital, wherever it stands: it is lowercase as the first word of a sentence too", () => {
    expect(wrongName("<p>Kaibo manages each trade itself.</p>")).toEqual([expect.stringMatching(/^Kaibo manages each trade/)]);
    expect(wrongName("<p>Buying stops. Kaibo's books and the broker disagree.</p>")).toEqual([expect.stringMatching(/^Buying stops\. Kaibo's books/)]);
    expect(wrongName("<b>KAIBO</b>")).toEqual(["KAIBO"]);
    expect(wrongName('<a aria-label="Kaibo home" href="/"><img alt="Kaibo" src="/x.svg"/></a>')).toEqual(["Kaibo home", "Kaibo"]);
    // the right form next to a wrong one: only the wrong one is reported
    expect(wrongName("<p>kaibo is the name, not Kaibo.</p>")).toEqual(["kaibo is the name, not Kaibo."]);
    expect(wrongName("<p>kaibo manages each trade itself.</p><p>kaibo's books and the broker disagree.</p><b>kaibo</b>")).toEqual([]);
    expect(wrongName(`<p>${NAME} trades on its own.</p><b>${BRAND}</b>`)).toEqual([]);
  });

  it("not the name: a literal path, a link, a class, an env var in an engine message", () => {
    expect(wrongName(`<p>Open Terminal in the <code class="num">${REPO_DIR}</code> folder.</p>`)).toEqual([]);
    expect(wrongName('<a class="kaibot-x Kaibo-y" href="https://example.com/Kaibo" data-app="kaibot">Docs</a>')).toEqual([]);
    expect(wrongName("<p>re-enabled by the stakeholder (KAIBOT_REENABLE)</p>")).toEqual([]);
  });

  it("a page that shows the earlier name, or a capitalised one, fails its render test", () => {
    expect(() => render(<p>Kaibot applies its limits by itself.</p>)).toThrow(/name is shown wrongly on this page: "Kaibot applies its limits/);
    expect(() => render(<p>Kaibo applies its limits by itself.</p>)).toThrow(/name is shown wrongly on this page: "Kaibo applies its limits.*always lowercase "kaibo"/);
    expect(() => render(<p>{NAME} applies its limits by itself.</p>)).not.toThrow();
  });

  it("sentences that start with the name start lowercase", () => {
    expect(text(renderToStaticMarkup(<ExperimentSheetBody e={{ strategy: "trend_momentum", label: "Experiment: failed the ten-year test",
      evidence: "failed_gate_b", experiment: true, realMoney: false, level: null, capPct: null, retirePct: null }} />)))
      .toContain("There is no trade-by-trade control: kaibo decides on its own within these limits.");
    expect(riskStatus(paperRun(), { ...emptyOverview(paperRun()), heartbeat: [{ process: "engine", ts: new Date().toISOString(), detail: { account_clean: false } }] }, 0, 0).items)
      .toEqual(["Paper account has positions or orders kaibo didn't place"]);
    expect(roomOutcome("error", null).text).toBe("No trade: the room call failed, and kaibo fails closed.");
  });
});

describe("text from outside apps/web is shown with the name in lowercase", () => {
  it("brandText: the capitalised name in a sentence, the earlier one or today's; nothing else", () => {
    expect(brandText("CRWV closed at $3.10; Kaibot only trades stocks above $5.")).toBe("CRWV closed at $3.10; kaibo only trades stocks above $5.");
    expect(brandText("Kaibot's books")).toBe("kaibo's books");
    expect(brandText("Kaibo default")).toBe("kaibo default");
    expect(brandText("Kaibo needs more than $20M a day. Kaibot checks again at the open.")).toBe("kaibo needs more than $20M a day. kaibo checks again at the open.");
    // a module, a folder, a command or an env var is not the display name
    for (const s of ["python -m kaibot.engine", "~/kaibot/scripts/run_engine.sh", "KAIBOT_REENABLE", "Kaibots", "kaibot", "kaibo"]) expect(brandText(s)).toBe(s);
    expect(brandText(null)).toBeNull();
    expect(brandText(undefined)).toBeUndefined();
  });

  it("the engine's reason for refusing a ticker", () => {
    const reason = "SPY is a reference fund Kaibot compares stocks against, so it cannot be traded.";
    expect(itemFromCheck({ symbol: "SPY", status: "refused", reason }).reason).toBe("SPY is a reference fund kaibo compares stocks against, so it cannot be traded.");
    expect(itemFromCheck({ symbol: "ZZZT", status: "refused", reason: null }).reason).toBe("kaibo's safety filters refused this ticker.");
    expect(applyBlockers([{ symbol: "CRWV", etf: "XLK" }], [{ symbol: "CRWV", status: "refused", reason: "CRWV closed at $3.10; Kaibot only trades stocks above $5." }])[0].why)
      .toBe("CRWV closed at $3.10; kaibo only trades stocks above $5.");
  });

  it("the shared presets file (config/presets.yaml, not ours to edit): its first preset reads 'kaibo default'", () => {
    expect(SPEC.presets.default.name).toBe("Kaibot default");
    expect(PRESETS.find((p) => p.key === "default")?.name).toBe("kaibo default");
    for (const p of PRESETS) expect(wrongName(`<p>${p.name} ${p.blurb}</p>`)).toEqual([]);
  });
});

const ds = (html: string) => [...html.matchAll(/<path[^>]*\bd="([^"]+)"/g)].map((m) => m[1]);
const fileDs = (svg: string) => [...svg.matchAll(/<path[^>]*\bd="([^"]+)"/g)].map((m) => m[1]);
const still = (el: React.ReactElement) => renderToStaticMarkup(el);

describe("the logo: Design's Rising k, drawn from the delivered files", () => {
  it("the files have the shape the logo expects: the k's arm, leg and stem, the point, then the other letters", () => {
    const w = mark("word", "dark"), k = mark("symbol", "dark");
    expect(w.parts).toHaveLength(10);
    expect(k.parts).toHaveLength(4);
    expect(w.parts.map((p) => p.fill)).toEqual(["#4F95F0", "#F4F5F7", "#F4F5F7", "#F4F5F7", "#F4F5F7", "#F4F5F7", "#4F95F0", "#F4F5F7", "#F4F5F7", "#F4F5F7"]);
    expect(k.parts.map((p) => p.fill)).toEqual(["#4F95F0", "#4F95F0", "#F4F5F7", "#F4F5F7"]);
    for (const [kind, m] of [["word", w], ["symbol", k]] as const) {
      for (const part of ["arm", "leg", "stem"] as const) expect(capsule(m.parts[ROLES[kind][part]].d), `${kind} ${part}`).not.toBeNull();
      expect(circle(m.parts[ROLES[kind].point].d)?.r).toBe(9);
      // the same k in both files: stem 15 wide and 88 tall, standing on the baseline
      expect(capsule(m.parts[ROLES[kind].stem].d)).toEqual({ ends: [[7.5, -80.5], [7.5, -7.5]], r: 7.5 });
    }
    // on light: the same paths, in Design's two light-background colours
    expect(mark("word", "light").parts.map((p) => p.d)).toEqual(w.parts.map((p) => p.d));
    expect([...new Set(mark("word", "light").parts.map((p) => p.fill))]).toEqual(["#2F6FDE", "#0A0B0D"]);
    expect([...new Set(mark("symbol", "light").parts.map((p) => p.fill))]).toEqual(["#2F6FDE", "#0A0B0D"]);
  });

  it("the box hugs the letters: Design's wordmark file less its 14 units of clear space; the k out of its 120 square", () => {
    expect(mark("word", "dark").box).toEqual([0, -88, 301.55, 88]);
    expect(/viewBox="([^"]+)"/.exec(wordDark)?.[1]).toBe("-14 -102 329.55 116");
    expect(mark("symbol", "dark").box).toEqual([21.57, 16, 76.86, 88]);
    expect(mark("symbol", "dark").shift).toEqual([21.57, 104]);
  });

  it("the wordmark alone, with Design's paths as delivered: no tile beside it, no font, and it says its name", () => {
    const html = still(<Logo />);
    expect(html).toMatch(/^<svg class="logo logo-word" viewBox="0 -88 301.55 88" role="img" aria-label="kaibo" style="aspect-ratio:301.55 \/ 88">/);
    expect(ds(html)).toEqual(fileDs(wordDark));
    expect(html).not.toMatch(/<img|<text|logo-symbol|logo-moving|clip-path|animation/);
    expect(text(html)).toBe("");
    expect(ds(still(<Logo on="light" />))).toEqual(fileDs(wordLight));
    expect(still(<Logo on="light" />)).toContain('fill="#2F6FDE"');
  });

  it("symbolOnly draws the k with its point; size is the height of the letters", () => {
    const html = still(<Logo symbolOnly size={40} />);
    expect(html).toMatch(/^<svg class="logo" viewBox="21.57 16 76.86 88" role="img" aria-label="kaibo" style="aspect-ratio:76.86 \/ 88;height:40px"><g transform="translate\(21.57 104\)">/);
    expect(ds(html)).toEqual(fileDs(symbolDark));
    expect(still(<Logo size={18} />)).toContain("height:18px");
  });

  it("the header, sign-in (and its sibling, registration) and New run all draw it, each once, and nothing else draws a logo", () => {
    const run = paperRun();
    const header = render(<Shell />, { route: "*", seeds: [{ key: ["runs"], data: [run] }, { key: ["overview", 1], data: emptyOverview(run) }] });
    expect(header).toMatch(/<a class="brand" aria-label="kaibo home"[^>]*><svg class="logo logo-word[^"]*" viewBox="0 -88 301.55 88" role="img" aria-label="kaibo"/);
    const signIn = render(<SignIn />);
    const start = render(<Onboarding />);
    for (const html of [header, signIn, start]) expect(html.match(/class="logo /g)).toHaveLength(1);
    expect(text(signIn)).toMatch(/^PAPER Sign in/);
    const sources = import.meta.glob("./**/*.tsx", { query: "?raw", import: "default", eager: true }) as Record<string, string>;
    const draws = Object.keys(sources).filter((f) => !/\.test\.tsx$/.test(f) && /brand\/logo\/|<Logo\b/.test(sources[f])).sort();
    expect(draws).toEqual(["./components/Logo.tsx", "./components/Shell.tsx", "./pages/Onboarding.tsx", "./pages/Register.tsx",
      "./pages/SignIn.tsx"]);
  });

  it("the size comes from the stylesheet: 26 px on phones, 28 px above, in a 64 px and a 72 px header", () => {
    expect(css).toMatch(/\.logo \{ display: block; flex: none; height: 28px;/);
    expect(css).toMatch(/@media \(max-width: 760px\) \{[^@]*\.logo \{ height: 26px; \}/);
    expect(css).toMatch(/header\.top \{[^}]*min-height: 72px;/);
    expect(css).toMatch(/header\.top \{ flex-wrap: nowrap; min-height: var\(--topbar\);/);
    expect(tokens).toMatch(/--topbar: 64px;/);
    expect(css).toMatch(/\.haltbar-wrap \{[^}]*top: var\(--topbar\)/);
    expect(css).toMatch(/header\.top \.pill \{[^}]*min-height: 36px/);
    expect(css).toMatch(/header\.top \.engine-chip::before \{[^}]*inset: 4px 0;/);
    expect(css).toMatch(/\.brand \{[^}]*min-height: 44px/);
  });
});

describe("the logo's motion on load", () => {
  it("stem, leg, arm, then the point with one pulse: each stroke slides out inside its own outline", () => {
    resetLogoMotion();
    const html = still(<Logo animate />);
    expect(html).toContain('class="logo logo-word logo-moving"');
    expect(html.match(/<clipPath /g)).toHaveLength(3);
    expect(html.match(/class="logo-draw"/g)).toHaveLength(3);
    // the stem starts its whole height below; the leg and the arm start behind the point where they meet
    expect(html).toContain('class="logo-draw" style="--fx:0px;--fy:88px;animation-delay:0s;animation-duration:0.2s"');
    expect(html).toContain("--fx:-41.24px;--fy:-25.61px;animation-delay:0.12s;animation-duration:0.24s");
    expect(html).toContain("--fx:-35.74px;--fy:38.26px;animation-delay:0.26s;animation-duration:0.3s");
    expect(html).toMatch(/class="logo-pop" style="animation-delay:0.56s;animation-duration:0.18s"/);
    expect(html).toMatch(/<circle class="logo-pulse" cx="150.65" cy="-76.5" r="9" fill="none" stroke="#4F95F0" stroke-width="2.5" style="animation-delay:0.66s;animation-duration:0.3s"/);
    // the paths themselves are still Design's, and the box is the same: nothing around the logo moves
    expect([...new Set(ds(html))].sort()).toEqual([...fileDs(wordDark)].sort());
    expect(html).toContain('viewBox="0 -88 301.55 88"');
    const k = still(<Logo animate symbolOnly />);
    expect(k).toMatch(/<circle class="logo-pulse" cx="67.86" cy="-68.94" r="9"/);
  });

  it("under one second in all; position and opacity only", () => {
    const end = Math.max(...Object.values(TIMING).map(([at, lasts]) => at + lasts));
    expect(end).toBeGreaterThan(0.85);
    expect(end).toBeLessThan(1);
    for (const name of ["kb-logo-draw", "kb-logo-pop", "kb-logo-pulse"]) {
      const body = new RegExp(`@keyframes ${name} \\{([^@]*?)\\}\\s*\\}`).exec(css)?.[1] ?? "";
      const props = [...body.matchAll(/([a-z-]+)\s*:/g)].map((m) => m[1]);
      expect(props.length, name).toBeGreaterThan(1);
      expect(props.every((p) => p === "transform" || p === "opacity"), name).toBe(true);
    }
    expect(drawnFrom(mark("word", "dark").parts[ROLES.word.stem].d, "bottom")).toEqual([0, 88]);
  });

  it("only where asked, and with reduced motion the finished mark simply shows", () => {
    expect(still(<Logo />)).not.toContain("logo-moving");
    vi.stubGlobal("window", { matchMedia: (q: string) => ({ matches: q === "(prefers-reduced-motion: reduce)" }) });
    try {
      const html = still(<Logo animate />);
      expect(html).not.toMatch(/logo-moving|logo-draw|logo-pop|logo-pulse|clipPath/);
      expect(ds(html)).toEqual(fileDs(wordDark));
    } finally { vi.unstubAllGlobals(); vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} }); }
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\) \{\s*\*, \*::before, \*::after \{ animation: none !important;/);
    expect(css).toMatch(/\.logo-pulse \{[^}]*opacity: 0;/);
  });
});

describe("the logo's idle hop (stakeholder, 24 Nov 2026)", () => {
  // The hops of a cycle, from its keyframes: each hop is a rest stop, a lift and a landing; returns [start %, end %].
  const hops = (name: string) => {
    const body = new RegExp(`@keyframes ${name} \\{(.*)\\}`).exec(css)?.[1] ?? "";
    const stops = (sel: RegExp) => (sel.exec(body)?.[1] ?? "").split(",").map((v) => parseFloat(v));
    const rest = stops(/([\d.%, ]+)\{ transform: none; \}/);
    return [0, 2, 4].map((i) => [rest[i], rest[i + 1]]);
  };
  const rule = (sel: string) => new RegExp(`(?:^|\\n)${sel.replace(/\./g, "\\.")} \\{([^}]*)\\}`).exec(css)?.[1] ?? "";

  it("about 600 ms each, 6 s after the intro, on the brand easing, transform only", () => {
    expect(rule(".logo-hop")).toBe(` position: absolute; will-change: transform; transform-origin: 50% 100%; animation: kb-logo-hop ${IDLE.often}s var(--ease) ${IDLE.after}s infinite; `);
    expect(rule(".logo-hop.rare")).toBe(` animation-name: kb-logo-hop-rare; animation-duration: ${IDLE.rare}s; `);
    for (const [name, cycle] of [["kb-logo-hop", IDLE.often], ["kb-logo-hop-rare", IDLE.rare]] as const) {
      for (const [a, b] of hops(name)) expect(Math.abs(((b - a) / 100) * cycle - 0.6), name).toBeLessThan(0.02);
      const body = new RegExp(`@keyframes ${name} \\{(.*)\\}`).exec(css)?.[1] ?? "";
      expect([...body.matchAll(/([a-z-]+)\s*:/g)].every((m) => m[1] === "transform"), name).toBe(true);
      expect(body).toContain("translateY(-60%) scale(.94, 1.06)"); // up 60% of the point: 3.1-3.8 px at 26-30.8 px
      expect(hops(name)[0][0], "the first hop comes right at the end of the delay").toBe(0);
    }
  });

  it("every 8-10 s on sign-in, uneven; the header's variant much rarer, about every 30 s", () => {
    const gaps = (name: string, cycle: number) => {
      const starts = hops(name).map(([a]) => (a / 100) * cycle);
      return starts.map((t, i) => Math.round(((i + 1 < starts.length ? starts[i + 1] : cycle) - t) * 100) / 100);
    };
    const often = gaps("kb-logo-hop", IDLE.often), rare = gaps("kb-logo-hop-rare", IDLE.rare);
    expect(often).toEqual([8.69, 9.4, 8.91]);
    expect(rare).toEqual([28.98, 31.32, 29.7]);
    for (const g of often) expect(g >= 8 && g <= 10).toBe(true);
    for (const g of rare) expect(g >= 28 && g <= 32).toBe(true);
    expect(new Set(often).size).toBe(3); // jitter: no two gaps alike
    expect(Math.min(...rare)).toBeGreaterThan(3 * Math.max(...often));
  });

  it("where: sign-in and /register often, the app's header (and New run's) rare", () => {
    const sources = import.meta.glob("./**/*.tsx", { query: "?raw", import: "default", eager: true }) as Record<string, string>;
    for (const f of ["./pages/SignIn.tsx", "./pages/Register.tsx"]) expect(sources[f], f).toContain('<Logo animate idle="often" large />');
    for (const f of ["./components/Shell.tsx", "./pages/Onboarding.tsx"]) expect(sources[f], f).toContain('<Logo animate idle="rare" />');
    expect(still(<Logo idle="rare" />)).toMatch(/<span class="logo-box"><svg class="logo logo-word"[^]*<svg class="logo-hop rare"/);
    expect(still(<Logo idle="often" />)).toMatch(/<svg class="logo-hop"/);
    expect(still(<Logo />)).not.toMatch(/logo-hop|logo-box/);
  });

  it("with reduced motion: no hop at all, and the global rule would stop any that slipped through", () => {
    vi.stubGlobal("window", { matchMedia: (q: string) => ({ matches: q === "(prefers-reduced-motion: reduce)" }) });
    try {
      for (const idle of ["often", "rare"] as const) {
        const html = still(<Logo idle={idle} />);
        expect(html).not.toMatch(/logo-hop|logo-box/);
        expect(ds(html)).toEqual(fileDs(wordDark));
      }
    } finally { vi.unstubAllGlobals(); vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} }); }
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\) \{\s*\*, \*::before, \*::after \{ animation: none !important;/);
  });

  it("the sign-in (and /register) logo is 1.1 times the header's, on phones too", () => {
    expect(LARGE).toBe(1.1);
    const big = Number(/\n\.logo-lg \{ height: ([\d.]+)px; \}/.exec(css)?.[1]);
    const bigPhone = Number(/@media \(max-width: 760px\) \{[^@]*\.logo-lg \{ height: ([\d.]+)px; \}/.exec(css)?.[1]);
    expect(Math.round((big / 28) * 1000) / 1000).toBe(LARGE);
    expect(Math.round((bigPhone / 26) * 1000) / 1000).toBe(LARGE);
    for (const html of [render(<SignIn />), render(<Register />)]) expect(html).toContain('class="logo logo-word logo-lg');
    const run = paperRun();
    const header = render(<Shell />, { route: "*", seeds: [{ key: ["runs"], data: [run] }, { key: ["overview", 1], data: emptyOverview(run) }] });
    expect(header).not.toContain("logo-lg");
  });
});

describe("the name in sentences next to the logo", () => {
  it("signed in to someone else's dashboard: the name in the sentence", () => {
    const html = renderToStaticMarkup(<MemoryRouter><NotOwner failed={false} retry={() => {}} /></MemoryRouter>);
    expect(text(html)).toBe("Not your dashboard This kaibo belongs to another account. Nothing here is visible to you. Sign out");
    expect(text(renderToStaticMarkup(<NotOwner failed retry={() => {}} />))).toContain("Try again");
  });
});

describe("the browser tab, the home-screen label and the manifest read the same name", () => {
  it("index.html never spells the name: the build fills %BRAND% in", () => {
    expect(indexHtml).not.toMatch(/kaibo/i);
    const html = brandHtml(indexHtml);
    expect(html).toContain("<title>kaibo · paper</title>");
    expect(html).toContain('<meta name="application-name" content="kaibo" />');
    expect(html).toContain('<meta name="apple-mobile-web-app-title" content="kaibo" />');
    expect(html).not.toContain("%BRAND%");
  });
  it("index.html links the favicon to the logo's own file, and the icons and manifest the build serves", () => {
    expect(indexHtml).toContain('<link rel="icon" href="/src/brand/logo/favicon.svg" type="image/svg+xml" />');
    expect(indexHtml).toContain('<link rel="icon" href="/favicon.ico?v=k1" sizes="32x32" />');
    expect(indexHtml).toContain('<link rel="apple-touch-icon" href="/apple-touch-icon.png?v=k1" />');
    expect(indexHtml).toContain(`<link rel="manifest" href="/${MANIFEST_PATH}?v=k2" />`);
    expect(ICONS.map((i) => i.file)).toEqual(expect.arrayContaining(["favicon.ico", "apple-touch-icon.png"]));
  });
  it("the manifest: name and short_name from BRAND, opens in its own window from the home screen, PNG icons with a maskable one", () => {
    const m = manifest();
    expect([m.name, m.short_name]).toEqual([BRAND, BRAND]);
    // #481: installable. "standalone" needs the installed app's own sign-in (pwa/CodeEntry.tsx), since it never sees
    // the browser's; id and scope pin it to the site's root
    expect([m.display, m.id, m.start_url, m.scope]).toEqual(["standalone", "/", "/", "/"]);
    // the colour behind the starting app and of the status bar is the page's: the manifest, index.html and the tokens agree
    expect([m.background_color, m.theme_color]).toEqual([APP_COLOUR, APP_COLOUR]);
    expect(indexHtml).toContain(`<meta name="theme-color" content="${APP_COLOUR}" />`);
    expect(indexHtml).toContain('<meta name="apple-mobile-web-app-capable" content="yes" />');
    expect(indexHtml).toContain('<meta name="mobile-web-app-capable" content="yes" />');
    expect(indexHtml).toContain('<meta name="apple-mobile-web-app-status-bar-style" content="black" />');
    expect(m.icons).toEqual([
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/icons/icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ]);
    expect(JSON.parse(manifestJson())).toEqual(m);
    expect(manifestJson()).not.toMatch(/kaibot/i);
  });
});

// The icons are files in public/, drawn from src/brand/symbol.svg by `npm run icons`. This project has no Node types
// (tsconfig "types": ["vite/client"]), so the file reader is typed by hand.
const { readFileSync } = (await import(/* @vite-ignore */ ["node", "fs"].join(":"))) as { readFileSync(path: URL): Uint8Array };
const bytes = (rel: string) => readFileSync(new URL(rel, import.meta.url));
const sha = async (data: Uint8Array) =>
  [...new Uint8Array(await crypto.subtle.digest("SHA-256", new Uint8Array(data)))].map((b) => b.toString(16).padStart(2, "0")).join("");
const u32 = (b: Uint8Array, at: number, littleEndian = false) => new DataView(b.buffer, b.byteOffset, b.byteLength).getUint32(at, littleEndian);
const isPng = (b: Uint8Array) => [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a].every((v, i) => b[i] === v);
/** Width and height of each image in the file: one for a PNG, one per entry for an .ico (whose images are PNGs). */
function sizes(b: Uint8Array): number[][] {
  if (isPng(b)) return [[u32(b, 16), u32(b, 20)]];
  const n = b[4] | (b[5] << 8);
  return Array.from({ length: n }, (_, i) => {
    const at = u32(b, 6 + 16 * i + 12, true);
    const png = b.subarray(at, at + u32(b, 6 + 16 * i + 8, true));
    expect(isPng(png)).toBe(true);
    return [u32(png, 16), u32(png, 20)];
  });
}

describe("the favicon and app icons come from Design's files", () => {
  const stale = "The icons in public/ do not match src/brand/logo/ and icons.json: run `npm run icons` and commit the result.";

  it("they were drawn from today's logo files and icons.json", async () => {
    const names = [...new Set(ICONS.map((i) => i.source))].sort();
    expect(names).toEqual(["favicon.svg", "icon-dark.svg"]);
    const chunks = [...names.flatMap((n) => [bytes(`./brand/logo/${n}`), new Uint8Array([0x0a])]), bytes("./brand/icons.json")];
    const inputs = new Uint8Array(chunks.reduce((n, c) => n + c.length, 0));
    let at = 0;
    for (const c of chunks) { inputs.set(c, at); at += c.length; }
    expect(await sha(inputs), stale).toBe(lock.inputs);
  });

  it("every icon in the list is in public/, unchanged since it was drawn, at the size the list says", async () => {
    expect(Object.keys(lock.files).sort()).toEqual(ICONS.map((i) => i.file).sort());
    for (const icon of ICONS) {
      const file = bytes(`../public/${icon.file}`);
      expect(await sha(file), `${icon.file}: ${stale}`).toBe((lock.files as Record<string, string>)[icon.file]);
      expect(sizes(file), icon.file).toEqual(icon.sizes.map((s) => [s, s]));
    }
  });

  it("the tab uses Design's small-size favicon; the home screen and the manifest use the dark icon", () => {
    const by = (use: string) => ICONS.filter((i) => i.use === use);
    expect(by("favicon").map((i) => [i.file, i.source, i.sizes])).toEqual([["favicon.ico", "favicon.svg", [16, 32]]]);
    expect(by("apple").map((i) => [i.file, i.source, i.sizes])).toEqual([["apple-touch-icon.png", "icon-dark.svg", [180]]]);
    expect(by("manifest").map((i) => [i.source, i.sizes[0], i.purpose])).toEqual([["icon-dark.svg", 192, "any"], ["icon-dark.svg", 512, "any"], ["icon-dark.svg", 512, "maskable"]]);
    // an iPhone paints transparent pixels black and a phone may crop a maskable icon to a circle: both are filled to
    // the edge with the dark icon's own tile colour (the generator refuses a maskable icon that leaves the safe zone)
    const tile = /<path fill="(#[0-9A-F]{6})"/i.exec(iconDark)?.[1];
    expect(tile).toBe("#14171C");
    for (const i of [...by("apple"), ...by("manifest").filter((m) => m.purpose === "maskable")]) expect(backgroundOf(i), i.file).toBe(tile);
    expect(backgroundOf(by("favicon")[0])).toBeNull();
  });
});
