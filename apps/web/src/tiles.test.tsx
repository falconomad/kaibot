// Company tiles (stakeholder, 17 Nov 2026): an iOS-style squircle, the logo on its own sampled colour, the initials as
// the fallback, and a tile on every Activity row.
import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import css from "./app.css?raw";
import symbols from "./data/symbols.json";
import { StockBadge, SystemTile, TILES } from "./components/StockBadge";
import { Tape } from "./pages/Tape";
import { ctxFor, paperRun, render } from "./pages/testkit";

vi.mock("./lib/api", () => import("./pages/testkit-api"));

describe("the tile", () => {
  it("is a squircle mask with a hairline on the same curve, for logos and initials alike; never a white disc", () => {
    expect(css).toMatch(/:root \{ --squircle: url\("data:image\/svg\+xml,/);
    expect(css).toMatch(/\.badge \{[^}]*mask: var\(--squircle\) center \/ 100% 100% no-repeat;/);
    expect(css).toMatch(/\.badge::after \{[^}]*background: var\(--squircle-line\)/);
    expect(decodeURIComponent(css.match(/--squircle-line: url\("data:image\/svg\+xml,([^"]+)"\)/)![1])).toContain("stroke='white' stroke-opacity='.08'");
    expect(css).not.toMatch(/\.badge\.logo \{ background: #fff; \}/);
    expect(css).not.toMatch(/\.badge \{[^}]*border-radius: 999px/);
  });
  it("the curve is a superellipse: its corner sits well inside a square's and well outside a circle's", () => {
    const d = decodeURIComponent(css.match(/--squircle: url\("data:image\/svg\+xml,([^"]+)"\)/)![1]).match(/d='([^']+)'/)![1];
    const pts = d.replace(/[MZ]/g, "").split("L").map((p) => p.trim().split(" ").map(Number));
    const corner = Math.max(...pts.map(([x, y]) => Math.hypot(x - 50, y - 50)));
    expect(corner).toBeGreaterThan(Math.hypot(50, 50) * 0.8);  // a circle reaches 50, a square 70.7
    expect(corner).toBeLessThan(Math.hypot(50, 50) * 0.95);
  });
  it("every symbol in symbols.json has its colours entry", () => {
    expect(Object.keys(TILES).sort()).toEqual(Object.keys(symbols).sort());
    for (const [s, t] of Object.entries(TILES)) {
      expect(typeof t.sampled, s).toBe("boolean");
      expect(t.pad === 0 || t.pad === 12, s).toBe(true);
      if (t.sampled) expect(t.bg, s).toMatch(/^#[0-9A-F]{6}$/);
    }
  });
  it("a sampled logo sits on its own colour (full-bleed when it has a solid brand background); the image is reserved and fades in", () => {
    TILES.TESTA = { bg: "#C8102E", pad: 0, sampled: true };
    (symbols as Record<string, unknown>).TESTA = { name: "Test A", logo: "https://example.com/a.png", industry: null };
    const html = renderToStaticMarkup(<StockBadge symbol="TESTA" size={36} />);
    expect(html).toMatch(/^<span class="badge logo" style="width:36px;height:36px;flex:none;--tile-bg:#C8102E;--tile-pad:0%"><img src="https:\/\/example.com\/a.png" alt="" width="36" height="36"/);
    expect(html).not.toContain('class="loaded"');
    expect(css).toMatch(/\.badge\.logo img \{[^}]*opacity: 0; transition: opacity/);
    delete TILES.TESTA; delete (symbols as Record<string, unknown>).TESTA;
  });
  it("not sampled yet: a soft white tile; unreadable or no logo: the initials", () => {
    const was = TILES.AAPL;
    TILES.AAPL = { bg: null, pad: 12, sampled: false };
    try {
      expect(renderToStaticMarkup(<StockBadge symbol="AAPL" />)).toMatch(/--tile-bg:var\(--grey-50\);--tile-pad:12%/);
      TILES.AAPL = { ...was, unreadable: true, sampled: false };
      expect(renderToStaticMarkup(<StockBadge symbol="AAPL" size={48} />)).toMatch(/^<span class="badge" style="[^"]*background:#[0-9A-F]{6}[^"]*" aria-hidden="true">AAPL<\/span>$/);
    } finally { TILES.AAPL = was; }
    expect(renderToStaticMarkup(<StockBadge symbol="ZZZZ" size={48} />)).toContain(">ZZZZ</span>");
    expect(renderToStaticMarkup(<StockBadge symbol="ZZZZ" size={40} />)).toContain(">ZZZ</span>");
    expect(renderToStaticMarkup(<StockBadge symbol="ZZZZ" size={36} />)).toContain(">ZZZ</span>");
    expect(renderToStaticMarkup(<StockBadge symbol="BRK.B" size={36} />)).toContain(">BRK</span>");
    expect(renderToStaticMarkup(<SystemTile><i /></SystemTile>)).toBe('<span class="badge sys" style="width:36px;height:36px;flex:none" aria-hidden="true"><i></i></span>');
  });
});

describe("Activity", () => {
  it("every row has a tile in the first column: the company's, or a neutral one", () => {
    const fill = { kind: "fill", ts: "2026-11-17T15:00:00Z", side: "buy", qty: 3, venue_px: 100, shadow_px: 100, fees: 0, tag: null, symbol: "AAPL", strategy: "trend_momentum", trade_id: 1, exit_reason: null, pnl_usd: null };
    const run = paperRun();
    const ov = { run, daily: [], heartbeat: [], pending_commands: [], ledger: null, today: null, halts_today: [{ kind: "losses", reason: "3 losses", ts: "2026-11-17T16:00:00Z" }] };
    const html = render(<Tape />, { ctx: ctxFor(run, { overview: ov as never }), seeds: [{ key: ["tape", 1], data: [fill], infinite: true }] });
    const rows = html.split('<button type="button" class="row').slice(1);
    expect(rows).toHaveLength(2);
    for (const r of rows) expect(r).toMatch(/^[^>]*>(<span class="badge(?: logo| sys[^"]*)?")/);
    expect(html).toContain('class="badge logo"');
    expect(html).toContain('class="badge sys halt"');
  });
});
