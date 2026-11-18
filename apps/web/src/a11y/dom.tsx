// #327: mounts a page or a component in jsdom with react-dom/client, so the tests can open a sheet, press Escape and
// read where focus went. The same providers as pages/testkit (seeded query cache, memory router, the shell's context).
// Not a test file itself.
import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { act, type ReactElement, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import type { RunCtx } from "../components/Shell";
import { ctxFor, paperRun, seededClient, type Seed } from "../pages/testkit";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
/** The query cache of the mounted page, so a test can let an answer arrive (`client.setQueryData`). */
export let client: QueryClient;

/** jsdom has no matchMedia. The tests ask for reduced motion, so a sheet closes at once instead of after its 240 ms
 *  slide (the slide itself is checked in the browser by scripts/mobile_check.py). */
export function reducedMotionOn() {
  window.matchMedia = ((query: string) => ({ matches: /prefers-reduced-motion/.test(query), media: query, onchange: null,
    addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent: () => false })) as typeof window.matchMedia;
}

export async function mount(ui: ReactElement, opts: { seeds?: Seed[]; ctx?: RunCtx; path?: string; route?: string; children?: ReactNode } = {}) {
  const { seeds = [], ctx = ctxFor(paperRun()), path = "/", route = "/", children } = opts;
  await unmount();
  document.body.innerHTML = '<main id="app"></main>';
  root = createRoot(document.getElementById("app")!);
  client = seededClient(seeds);
  await act(async () => {
    root!.render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route element={<Outlet context={ctx} />}>
              <Route path={route} element={ui}>{children}</Route>
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  return document.body;
}

export async function unmount() {
  if (root) { const r = root; root = null; await act(async () => { r.unmount(); }); }
  document.body.innerHTML = "";
}

/** Run something that changes the page (a click, a key, a timer) and let React finish. */
export const doing = (fn: () => void | Promise<void>) => act(async () => { await fn(); });

/** A key press on whatever has focus, as the browser sends it (bubbling). */
export const press = (key: string) => doing(() => {
  (document.activeElement ?? document.body).dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
});

/** Activate the focused control as Enter or Space does on a native button or link. jsdom sends the key but not the
 *  click a browser adds for it, so the test first proves the control is one the browser does that for. */
export async function activate(el: HTMLElement) {
  if (!el.matches("button:not([disabled]), a[href], summary")) throw new Error(`not a native control: ${el.outerHTML.slice(0, 120)}`);
  if (el.tabIndex < 0) throw new Error(`not in the Tab order: ${el.outerHTML.slice(0, 120)}`);
  el.focus();
  await doing(() => { el.click(); });
}

/** Let timers and promises that are due run (a sheet's close, a query's answer). */
export const settle = (ms = 5) => doing(() => new Promise<void>((r) => setTimeout(r, ms)));

/** What a screen reader is told without moving focus: the text of every polite live region on the page. */
export const said = () => all('[role="status"], [aria-live="polite"]').map((e) => (e.textContent ?? "").replace(/\s+/g, " ").trim()).filter(Boolean);

export const q = <T extends HTMLElement = HTMLElement>(sel: string) => document.querySelector<T>(sel);
export const all = <T extends HTMLElement = HTMLElement>(sel: string) => [...document.querySelectorAll<T>(sel)];
