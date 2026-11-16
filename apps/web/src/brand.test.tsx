// The app's display name and logo (#254; stakeholder, 16 Nov 2026: "on the app UI the name should be kaibo").
// Three nets keep the earlier name from coming back where a person reads it:
// 1. every page-render test reads what it rendered (testkit `render` throws on the earlier name);
// 2. the text written in the source (string literals and JSX text, never identifiers or comments) is read here, for
//    the states no test renders: closed sheets, error branches;
// 3. text that comes from outside apps/web (engine reasons, the shared presets file) passes through brandText.
// Code identifiers, routes, storage keys, RPC names and env vars are not display names and are not checked.
import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import ts from "typescript";
import { BRAND, NAME, REPO_DIR, brandText } from "./brand/brand";
import { ICONS, MANIFEST_PATH, backgroundOf, brandHtml, manifest, manifestJson } from "./brand/assets";
import lock from "./brand/icons.lock.json";
import { Logo } from "./components/Logo";
import { Shell } from "./components/Shell";
import { NotOwner, SignIn } from "./pages/SignIn";
import { Onboarding } from "./pages/Onboarding";
import { PRESETS, SPEC } from "./lib/config";
import { applyBlockers, itemFromCheck } from "./lib/tickers";
import indexHtml from "../index.html?raw";
import css from "./app.css?raw";
import { earlierName, emptyOverview, paperRun, render, text } from "./pages/testkit";

vi.mock("./lib/api", () => import("./pages/testkit-api"));
vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });

describe("the display name is written once", () => {
  it("kaibo: lowercase as the wordmark, capitalised in a sentence", () => {
    expect(BRAND).toBe("kaibo");
    expect(NAME).toBe("Kaibo");
  });

  it("the source writes the name through BRAND or NAME: no string or JSX text spells the earlier one", () => {
    const sources = import.meta.glob("./**/*.{ts,tsx}", { query: "?raw", import: "default", eager: true }) as Record<string, string>;
    // brand.ts is the one module that knows the earlier name (brandText) and the repo's folder (REPO_DIR).
    const files = Object.keys(sources).filter((f) => !/\.test\.tsx?$/.test(f) && !/\/testkit[^/]*$/.test(f) && f !== "./brand/brand.ts");
    expect(files.length).toBeGreaterThan(40);
    const found: string[] = [];
    for (const f of files) {
      const sf = ts.createSourceFile(f, sources[f], ts.ScriptTarget.Latest, true, f.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
      const visit = (n: ts.Node) => {
        const written = ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n) || ts.isTemplateHead(n) || ts.isTemplateMiddle(n)
          || ts.isTemplateTail(n) || ts.isJsxText(n);
        const importPath = ts.isStringLiteral(n) && (ts.isImportDeclaration(n.parent) || ts.isExportDeclaration(n.parent));
        if (written && !importPath && /kaibot/i.test(n.text))
          found.push(`${f}:${sf.getLineAndCharacterOfPosition(n.getStart(sf)).line + 1} "${n.text.trim().slice(0, 60)}"`);
        ts.forEachChild(n, visit);
      };
      visit(sf);
    }
    expect(found, "A string or JSX text spells the earlier name: write NAME or BRAND from src/brand/brand.ts. If it is never shown to a person "
      + "(a storage key, a schema name), it is not a display name: leave it out of this check and say why.").toEqual([]);
  });

  it("the reader of rendered pages finds the earlier name in text and in what a screen reader says, and only there", () => {
    expect(earlierName("<p>Kaibot trades on its own.</p>")).toEqual(["Kaibot trades on its own."]);
    expect(earlierName('<a aria-label="kaibot home" href="/"><img alt="" src="/x.svg"/></a>')).toEqual(["kaibot home"]);
    expect(earlierName('<img alt="Kaibot" src="/x.svg"/><input placeholder="Ask kaibot"/><span title="About Kaibot">i</span>')).toHaveLength(3);
    // not the name: a literal path, a link, a class, an env var in an engine message, and the new name itself
    expect(earlierName(`<p>Open Terminal in the <code class="num">${REPO_DIR}</code> folder.</p>`)).toEqual([]);
    expect(earlierName('<a class="kaibot-x" href="https://example.com/kaibot" data-app="kaibot">Docs</a>')).toEqual([]);
    expect(earlierName("<p>re-enabled by the stakeholder (KAIBOT_REENABLE)</p>")).toEqual([]);
    expect(earlierName(`<p>${NAME} trades on its own.</p><b>${BRAND}</b>`)).toEqual([]);
  });

  it("a page that shows the earlier name fails its render test", () => {
    expect(() => render(<p>Kaibot applies its limits by itself.</p>)).toThrow(/earlier name shows on this page: "Kaibot applies its limits/);
    expect(() => render(<p>{NAME} applies its limits by itself.</p>)).not.toThrow();
  });
});

describe("text from outside apps/web is shown with today's name", () => {
  it("brandText: the capitalised name in a sentence, nothing else", () => {
    expect(brandText("CRWV closed at $3.10; Kaibot only trades stocks above $5.")).toBe("CRWV closed at $3.10; Kaibo only trades stocks above $5.");
    expect(brandText("Kaibot's books")).toBe("Kaibo's books");
    // a module, a folder, a command or an env var is not the display name
    for (const s of ["python -m kaibot.engine", "~/kaibot/scripts/run_engine.sh", "KAIBOT_REENABLE", "Kaibots", "kaibot"]) expect(brandText(s)).toBe(s);
    expect(brandText(null)).toBeNull();
    expect(brandText(undefined)).toBeUndefined();
  });

  it("the engine's reason for refusing a ticker", () => {
    const reason = "SPY is a reference fund Kaibot compares stocks against, so it cannot be traded.";
    expect(itemFromCheck({ symbol: "SPY", status: "refused", reason }).reason).toBe("SPY is a reference fund Kaibo compares stocks against, so it cannot be traded.");
    expect(itemFromCheck({ symbol: "ZZZT", status: "refused", reason: null }).reason).toBe("Kaibo's safety filters refused this ticker.");
    expect(applyBlockers([{ symbol: "CRWV", etf: "XLK" }], [{ symbol: "CRWV", status: "refused", reason: "CRWV closed at $3.10; Kaibot only trades stocks above $5." }])[0].why)
      .toBe("CRWV closed at $3.10; Kaibo only trades stocks above $5.");
  });

  it("the shared presets file (config/presets.yaml, not ours to edit): its first preset reads 'Kaibo default'", () => {
    expect(SPEC.presets.default.name).toBe("Kaibot default");
    expect(PRESETS.find((p) => p.key === "default")?.name).toBe("Kaibo default");
    for (const p of PRESETS) expect(earlierName(`<p>${p.name} ${p.blurb}</p>`)).toEqual([]);
  });
});

describe("the logo: one component, one symbol file", () => {
  it("symbol and wordmark; the symbol is decoration next to the word", () => {
    // (react-dom/server also writes a preload hint for the image in front of the markup; the browser build does not)
    const html = renderToStaticMarkup(<Logo />).replace(/^<link rel="preload"[^>]*>/, "");
    expect(html).toMatch(/^<span class="logo"><img class="logo-symbol" src="[^"]*symbol\.svg[^"]*" alt="" width="26" height="26" style="width:26px;height:26px"\/><span class="logo-word">kaibo<\/span><\/span>$/);
  });
  it("symbol only: no wordmark, and the symbol carries the name", () => {
    const html = renderToStaticMarkup(<Logo symbolOnly size={40} />);
    expect(html).toContain('alt="kaibo" width="40" height="40"');
    expect(html).not.toContain("logo-word");
  });
  it("size: the wordmark scales with the symbol", () => {
    expect(renderToStaticMarkup(<Logo size={52} />)).toContain('<span class="logo" style="font-size:36px">');
    expect(css).toMatch(/\.logo \{[^}]*font-weight: 700; font-size: 18px; letter-spacing: -0\.02em;/);
  });
  it("the header, sign-in and New run all draw it, each once, and nothing else draws a logo", () => {
    const run = paperRun();
    const header = render(<Shell />, { route: "*", seeds: [{ key: ["runs"], data: [run] }, { key: ["overview", 1], data: emptyOverview(run) }] });
    expect(header).toMatch(/<a class="brand" aria-label="kaibo home"[^>]*><span class="logo"><img class="logo-symbol"[^>]*alt=""[^>]*\/><span class="logo-word">kaibo<\/span><\/span><\/a>/);
    const signIn = render(<SignIn />);
    const start = render(<Onboarding />);
    for (const html of [header, signIn, start]) expect(html.match(/class="logo"/g)).toHaveLength(1);
    expect(text(signIn)).toMatch(/^kaibo PAPER Sign in/);
    expect(text(start)).toMatch(/^kaibo PAPER 1 Amount/);
    const sources = import.meta.glob("./**/*.tsx", { query: "?raw", import: "default", eager: true }) as Record<string, string>;
    const drawsSymbol = Object.keys(sources).filter((f) => !/\.test\.tsx$/.test(f) && /symbol\.svg|M8 18 L12\.5 12\.5/.test(sources[f]));
    expect(drawsSymbol).toEqual(["./components/Logo.tsx"]);
  });
  it("signed in to someone else's dashboard: the name in the sentence", () => {
    const html = renderToStaticMarkup(<MemoryRouter><NotOwner failed={false} retry={() => {}} /></MemoryRouter>);
    expect(text(html)).toBe("Not your dashboard This Kaibo belongs to another account. Nothing here is visible to you. Sign out");
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
    expect(indexHtml).toContain('<link rel="icon" href="/src/brand/symbol.svg" type="image/svg+xml" />');
    expect(indexHtml).toContain('<link rel="icon" href="/favicon.ico" sizes="32x32" />');
    expect(indexHtml).toContain('<link rel="apple-touch-icon" href="/apple-touch-icon.png" />');
    expect(indexHtml).toContain(`<link rel="manifest" href="/${MANIFEST_PATH}" />`);
    expect(ICONS.map((i) => i.file)).toEqual(expect.arrayContaining(["favicon.ico", "apple-touch-icon.png"]));
  });
  it("the manifest: name and short_name from BRAND, opens in the browser, PNG icons with a maskable one", () => {
    const m = manifest();
    expect([m.name, m.short_name]).toEqual([BRAND, BRAND]);
    // "browser", not "standalone": the emailed sign-in link opens in the browser, and a standalone app would not see it
    expect(m.display).toBe("browser");
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

describe("the favicon and app icons come from the one symbol file", () => {
  const stale = "The icons in public/ do not match src/brand/symbol.svg and icons.json: run `npm run icons` and commit the result.";

  it("they were drawn from today's symbol.svg and icons.json", async () => {
    const symbol = bytes("./brand/symbol.svg"), spec = bytes("./brand/icons.json");
    const inputs = new Uint8Array(symbol.length + 1 + spec.length);
    inputs.set(symbol, 0); inputs[symbol.length] = 0x0a; inputs.set(spec, symbol.length + 1);
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

  it("the list covers a browser tab, an iPhone home screen and the manifest", () => {
    const by = (use: string) => ICONS.filter((i) => i.use === use);
    expect(by("favicon").map((i) => [i.file, i.sizes])).toEqual([["favicon.ico", [16, 32, 48]]]);
    expect(by("apple").map((i) => [i.file, i.sizes])).toEqual([["apple-touch-icon.png", [180]]]);
    expect(by("manifest").map((i) => i.sizes[0]).sort()).toEqual([192, 512, 512]);
    // an iPhone paints transparent pixels black, so every home-screen icon has a colour behind the symbol
    for (const i of [...by("apple"), ...by("manifest")]) expect(backgroundOf(i), i.file).toMatch(/^#[0-9A-F]{6}$/i);
    expect(backgroundOf(by("favicon")[0])).toBeNull();
  });
});
