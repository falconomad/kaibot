// Brand system v1 (docs/design/brand/brand_system_v1.md, #265): the colours derive from the Rising k logo, blue is
// kept for the logo, the one action, links, focus and the live point, and the experiment is a neutral lab note.
import { describe, expect, it } from "vitest";
import css from "./app.css?raw";
import tokens from "./tokens.css?raw";

// Design's file lives outside apps/web; read it from disk (this project has no Node types, so the reader is typed by hand).
const { readFileSync } = (await import(/* @vite-ignore */ ["node", "fs"].join(":"))) as { readFileSync(path: URL, enc: "utf8"): string };
const designTokens = readFileSync(new URL("../../../docs/design/brand/tokens_v1.css", import.meta.url), "utf8");

const block = (src: string, head: string) => src.slice(src.indexOf(head)).split("}")[0];
const values = (b: string) => Object.fromEntries([...b.matchAll(/(--[a-z0-9-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].replace(/\s*\/\*.*$/, "").trim()]));

describe("brand system v1", () => {
  it("the app's colour tokens are Design's, dark and light, value for value", () => {
    for (const head of [':root, :root[data-theme="dark"] {', ':root[data-theme="light"] {']) {
      const want = values(block(designTokens, head)), have = values(block(tokens, head));
      for (const [name, v] of Object.entries(want)) if (!name.startsWith("--font")) expect(have[name], `${head} ${name}`).toBe(v);
      expect(Object.keys(want).length).toBeGreaterThan(40);
    }
  });
  it("the light theme is defined but nothing switches it on", () => {
    const sources = import.meta.glob("./**/*.{ts,tsx}", { query: "?raw", import: "default", eager: true }) as Record<string, string>;
    expect(Object.entries(sources).filter(([f, s]) => !/\.test\./.test(f) && /data-theme|dataset\.theme/.test(s)).map(([f]) => f)).toEqual([]);
  });
  it("no colour is typed into the stylesheet: every one is a token", () => {
    expect(css.match(/#[0-9A-Fa-f]{6}\b/g) ?? []).toEqual([]);
  });
  it("the one action is brand blue; a selected filter is ink; a destructive button is never blue", () => {
    expect(css).toContain("button.primary { background: var(--sea); border-color: var(--sea); color: var(--on-sea); }");
    expect(css).toContain(".chip-btn[aria-pressed=true] { border-color: var(--ink); background: var(--ink); color: var(--paper); }");
    expect(css).toContain("button.danger { background: transparent; border-color: var(--loss-line); color: var(--loss); }");
  });
  it("controls have a border that can be seen, and one focus ring", () => {
    expect(css).toMatch(/button, \.btn \{[^}]*border: 1px solid var\(--control\)/);
    expect(css).toMatch(/input\[type=email\][^{]*\{[^}]*border: 1px solid var\(--control\)/);
    expect(css).not.toContain("outline: 2px solid var(--sea)");
    expect(css.match(/outline: 2px solid var\(--focus\)/g)!.length).toBeGreaterThan(5);
  });
  it("the experiment is a lab note: neutral, dashed, with the flask; other notes are neutral and nothing honest is blue", () => {
    expect(css).toMatch(/\.status-row\.exp \{ color: var\(--exp-text\); border: 1px dashed var\(--exp-line\);/);
    expect(css).toMatch(/\.exp-tag, \.exp-pill, \.row-tag \{ color: var\(--exp-text\); background: var\(--exp-bg\); border: 1px dashed var\(--exp-line\); \}/);
    expect(css).toMatch(/\.status-row\.exp \.status-text::before \{[^}]*mask:/);
    expect(css).toContain(".status-row.info { background: var(--surface-2); border-color: var(--rule-strong); color: var(--text); }");
    expect(css).toContain(".tag.info { color: var(--slate); background: var(--surface-2); }");
    // halts keep their red, with the stronger edge
    expect(css).toContain(".status-row.breach { background: var(--down-tint); border-color: var(--loss-line); }");
  });
  it("a gain or loss figure carries its arrow as well as its sign; the active tab carries the point", () => {
    expect(css).toMatch(/\.num \.gain::before, \.num\.gain::before \{ content: "▲/);
    expect(css).toMatch(/\.num \.loss::before, \.num\.loss::before \{ content: "▼/);
    expect(css).toMatch(/\.tabbar a\.active::before \{[^}]*width: 6px; height: 6px;[^}]*background: var\(--live\)/);
  });
});
