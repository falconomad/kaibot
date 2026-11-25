// @vitest-environment jsdom
// #738: one render error must not blank the app. Error boundaries (components/ErrorBoundary.tsx) around each page and
// each independent card on Home.
// - A throwing child shows the short fallback (role="alert", Retry, a link to Engine), never an empty page, and the
//   error is logged to the console with the boundary's name.
// - Retry re-mounts the children: a child that throws once renders on the second try, with fresh state.
// - A page boundary keeps the layout around it (the header and navigation) and tries again on a new path.
// - On Home, a throwing Holdings card leaves the pot and its chart; a throwing notices row leaves the headlines.
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, NavLink, Outlet, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BOUNDARY_TEXT, ErrorBoundary, PageBoundary } from "./components/ErrorBoundary";
import { doing, mount, unmount } from "./a11y/dom";
import type { Overview } from "./lib/api";
import { Live } from "./pages/Live";
import { ctxFor, emptyOverview, paperRun } from "./pages/testkit";

vi.mock("./lib/api", () => import("./pages/testkit-api"));
vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });

const fail = vi.hoisted(() => ({ holdings: false, notices: false }));
vi.mock("./components/Home", async (orig) => {
  const m = await orig<typeof import("./components/Home")>();
  return { ...m, TopHoldings: (p: Parameters<typeof m.TopHoldings>[0]) => {
    if (fail.holdings) throw new Error("holdings broke");
    return m.TopHoldings(p);
  } };
});
vi.mock("./components/Notices", () => ({ default: () => {
  if (fail.notices) throw new Error("notices broke");
  return <p>Notices here</p>;
} }));
vi.mock("./components/Headlines", () => ({ default: () => <p>Headlines here</p> }));

let errors: ReturnType<typeof vi.spyOn>;
beforeEach(() => { errors = vi.spyOn(console, "error").mockImplementation(() => {}); fail.holdings = false; fail.notices = false; });
afterEach(async () => { await unmount(); errors.mockRestore(); });

const button = (label: string) => [...document.querySelectorAll("button")].find((b) => b.textContent === label)!;
const logged = (name: string) => errors.mock.calls.some((c: unknown[]) => String(c[0]).includes(`${name} failed to render`));

function Boom({ when = true }: { when?: boolean }): never | null {
  if (when) throw new Error("boom");
  return null;
}

describe("ErrorBoundary", () => {
  it("a throwing child shows the fallback, not a blank page, and logs the name", async () => {
    const body = await mount(<ErrorBoundary name="Pot"><p>before</p><Boom /></ErrorBoundary>);
    const alert = body.querySelector('[role="alert"]')!;
    expect(alert).not.toBeNull();
    expect(alert.getAttribute("data-part")).toBe("Pot");
    expect(alert.textContent).toContain(BOUNDARY_TEXT);
    expect(alert.textContent).toContain("Reload, or open the Engine page.");
    expect(alert.querySelector('a[href="/health"]')).not.toBeNull();
    expect(button("Retry")).toBeTruthy();
    expect(body.textContent).not.toContain("before");
    expect(logged("Pot")).toBe(true);
  });

  it("a falsy throw (0) still shows the fallback, without looping", async () => {
    function Zero(): never { throw 0; }
    const body = await mount(<ErrorBoundary name="Zero"><Zero /></ErrorBoundary>);
    expect(body.querySelector('[role="alert"]')?.getAttribute("data-part")).toBe("Zero");
  });

  it("Retry re-mounts the children with fresh state", async () => {
    let broken = true, mounts = 0;
    function Flaky() {
      const [id] = useState(() => ++mounts);
      if (broken) throw new Error("still broken");
      return <p>ok {id}</p>;
    }
    const body = await mount(<ErrorBoundary name="Flaky"><Flaky /></ErrorBoundary>);
    expect(body.textContent).toContain(BOUNDARY_TEXT);
    // still broken: Retry tries again and shows the fallback again
    await doing(() => button("Retry").click());
    expect(body.textContent).toContain(BOUNDARY_TEXT);
    broken = false;
    const before = mounts;
    await doing(() => button("Retry").click());
    expect(body.textContent).not.toContain(BOUNDARY_TEXT);
    expect(body.querySelector('[role="alert"]')).toBeNull();
    expect(mounts).toBe(before + 1); // a fresh instance, not the old state
    expect(body.textContent).toBe(`ok ${mounts}`);
  });

  it("a broken page keeps the header and navigation; a new path tries again", async () => {
    document.body.innerHTML = '<div id="r"></div>';
    const root: Root = createRoot(document.getElementById("r")!);
    const Layout = () => (
      <><header><nav aria-label="Main"><NavLink to="/">Home</NavLink> <NavLink to="/report">Report</NavLink></nav></header>
        <main><Outlet /></main></>
    );
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={["/"]}>
          <Routes><Route element={<Layout />}>
            <Route index element={<PageBoundary key="Home" name="Home"><Boom /></PageBoundary>} />
            <Route path="report" element={<PageBoundary key="Report" name="Report"><p>Report page</p></PageBoundary>} />
          </Route></Routes>
        </MemoryRouter>,
      );
    });
    expect(document.querySelector("nav")?.textContent).toContain("Report");
    expect(document.querySelector("main")?.textContent).toContain(BOUNDARY_TEXT);
    await act(async () => { document.querySelector<HTMLAnchorElement>('a[href="/report"]')!.click(); });
    expect(document.querySelector("main")?.textContent).toContain("Report page");
    await act(async () => { root.unmount(); });
  });
});

describe("Home cards (#738)", () => {
  const run = paperRun();
  const ov = (): Overview => ({ ...emptyOverview(run), heartbeat: [{ process: "engine", ts: new Date().toISOString(), detail: { state: "trading" } }] });
  const home = () => mount(<Live />, { ctx: { ...ctxFor(run, { overview: ov() }), meters: { dayPct: 0, dd: 0, replay: false } },
    seeds: [{ key: ["trades-all", 1], data: { total: 0, rows: [] } }] });
  const settle = () => act(async () => { for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0)); });

  it("renders every card when nothing throws", async () => {
    const body = await home();
    await settle();
    expect(body.textContent).toContain("Paper pot");
    expect(body.querySelector('[role="alert"][data-part]')).toBeNull();
  });

  it("a throwing Holdings card leaves the pot, the chart and the lines rendered", async () => {
    fail.holdings = true;
    const body = await home();
    await settle();
    const parts = [...body.querySelectorAll('[role="alert"][data-part]')].map((e) => e.getAttribute("data-part"));
    expect(parts).toEqual(["Holdings"]);
    expect(body.querySelector("#pot-h")?.textContent).toBe("Paper pot");
    expect(body.querySelector(".home-chart")).not.toBeNull();
    expect(body.textContent).toContain("Notices here");
    expect(body.textContent).toContain("Headlines here");
    expect(logged("Holdings")).toBe(true);
  });

  it("a throwing notices row leaves the headlines and the pot rendered", async () => {
    fail.notices = true;
    const body = await home();
    await settle();
    const parts = [...body.querySelectorAll('[role="alert"][data-part]')].map((e) => e.getAttribute("data-part"));
    expect(parts).toEqual(["Market notices"]);
    expect(body.textContent).toContain("Headlines here");
    expect(body.querySelector("#pot-h")?.textContent).toBe("Paper pot");
  });
});
