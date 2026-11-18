// @vitest-environment jsdom
// #327: logo tiles and screen readers. A tile beside its ticker is decoration and is hidden; a tile that stands alone
// (`label`) is an image named by its ticker.
import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { StockBadge, StockCell, SystemTile } from "../components/StockBadge";
import { render } from "../pages/testkit";
import { audit, lines } from "./axe";
import { PAGES, loadedCtx } from "./fixtures";

vi.mock("../lib/api", () => import("../pages/testkit-api"));
vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });

const dom = (html: string) => { document.body.innerHTML = html; return document.body.firstElementChild as HTMLElement; };
// The tile's three drawings: a company logo (AAPL is in our list), the initials (not a ticker, so no image is asked
// for), and the soft tile while the company's domain is looked up (a ticker we have never seen).
const DRAWINGS = [["a logo", "AAPL"], ["the initials", "1234"], ["the tile while the logo is looked up", "QQZZ"]] as const;

describe("a tile beside its ticker is decoration", () => {
  for (const [what, symbol] of DRAWINGS) it(`${what}: hidden from screen readers, with no name of its own`, () => {
    const tile = dom(renderToStaticMarkup(<StockBadge symbol={symbol} />));
    expect(tile.classList.contains("badge")).toBe(true);
    expect(tile.getAttribute("aria-hidden")).toBe("true");
    expect(tile.hasAttribute("role")).toBe(false);
    expect(tile.hasAttribute("aria-label")).toBe(false);
    for (const img of tile.querySelectorAll("img")) expect(img.getAttribute("alt")).toBe("");
  });
  it("the drawings are what the test says they are", () => {
    expect(dom(renderToStaticMarkup(<StockBadge symbol="AAPL" />)).querySelector("img")).not.toBeNull();
    expect(dom(renderToStaticMarkup(<StockBadge symbol="1234" />)).textContent).toBe("1234");
    const pending = dom(renderToStaticMarkup(<StockBadge symbol="QQZZ" />));
    expect([pending.querySelector("img"), pending.textContent]).toEqual([null, ""]);
  });
  it("the icon tile of a row with no company is hidden too", () => {
    expect(dom(renderToStaticMarkup(<SystemTile><i /></SystemTile>)).getAttribute("aria-hidden")).toBe("true");
  });
  it("logo, ticker and name in a table cell: the ticker is read once, from the text", () => {
    const cell = dom(renderToStaticMarkup(<StockCell symbol="AAPL" />));
    expect(cell.querySelector(".badge")!.getAttribute("aria-hidden")).toBe("true");
    expect(cell.querySelector("b")!.textContent).toBe("AAPL");
  });
});

describe("a tile that is the only label", () => {
  for (const [what, symbol] of DRAWINGS) it(`${what}: an image named by its ticker`, async () => {
    const html = renderToStaticMarkup(<StockBadge symbol={symbol} label />);
    const tile = dom(html);
    expect(tile.getAttribute("role")).toBe("img");
    expect(tile.getAttribute("aria-label")).toBe(symbol);
    expect(tile.hasAttribute("aria-hidden")).toBe(false);
    for (const img of tile.querySelectorAll("img")) expect(img.getAttribute("alt")).toBe("");   // named once, by the tile
    expect(lines(await audit(`<ul><li>${html}</li></ul>`))).toEqual([]);
    expect(lines(await audit(`<button type="button">${html}</button>`))).toEqual([]);            // it names a control it stands alone in
  });
});

describe("every tile on every page", () => {
  for (const p of PAGES) it(p.name, () => {
    document.body.innerHTML = render(p.el, { path: p.path, route: p.route, children: p.children, seeds: p.loaded, ctx: loadedCtx() });
    for (const tile of document.querySelectorAll<HTMLElement>(".badge")) {
      const named = tile.getAttribute("role") === "img" && !!tile.getAttribute("aria-label");
      const hidden = !!tile.closest('[aria-hidden="true"]');
      expect(named !== hidden, tile.outerHTML.slice(0, 160)).toBe(true);    // one or the other, never both, never neither
      if (!hidden || tile.classList.contains("sys")) continue;
      // A hidden company tile is never the only label: what it sits in (a row, a button, a cell, a heading line) has
      // words a screen reader reads, or a name of its own.
      const home = tile.closest<HTMLElement>("button, a, li, td, .stockcell") ?? tile.parentElement!;
      const words = [...home.querySelectorAll<HTMLElement>("*")].filter((e) => !e.closest('[aria-hidden="true"]') && !e.children.length).map((e) => e.textContent ?? "").join(" ").trim();
      const name = home.closest("[aria-label]")?.getAttribute("aria-label") ?? "";
      expect((words || name).length, home.outerHTML.slice(0, 200)).toBeGreaterThan(0);
    }
  });
  it("the pages above do draw tiles", () => {
    const count = (name: string) => {
      const p = PAGES.find((x) => x.name === name)!;
      document.body.innerHTML = render(p.el, { path: p.path, route: p.route, children: p.children, seeds: p.loaded, ctx: loadedCtx() });
      return document.querySelectorAll(".badge").length;
    };
    for (const name of ["Home", "Positions", "Trade", "Activity", "Report", "Strategy (themes)", "Strategy (My lists)"]) expect(count(name), name).toBeGreaterThan(0);
  });
});
