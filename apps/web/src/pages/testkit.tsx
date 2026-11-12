// Shared harness for the page state tests (#166). No DOM library: a page is rendered with react-dom/server inside a
// real QueryClient whose cache is seeded with stubbed query data, so each test picks the loading, empty or error state.
// Pages read the database only through lib/api; the tests replace that module with ./testkit-api, so nothing here
// can reach the network. Not a test file itself (vitest only runs *.test.tsx).
import { QueryClient, QueryClientProvider, type QueryKey } from "@tanstack/react-query";
import type { ReactElement, ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import type { RunCtx } from "../components/Shell";
import type { Overview, Run } from "../lib/api";

/** One cached query. No `data` and no `error` is left out of the cache, so the page sees it pending (loading). */
export type Seed = { key: QueryKey; data?: unknown; error?: unknown; infinite?: boolean };

export const boom = (msg = "permission denied for function") => Object.assign(new Error(msg), { code: "42501" });

export function seededClient(seeds: Seed[] = []) {
  // retryOnMount: false keeps an errored query in its error state on render (otherwise it would show as pending again).
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, retryOnMount: false, staleTime: Infinity, gcTime: Infinity } } });
  for (const s of seeds) {
    if (s.error !== undefined) {
      qc.getQueryCache().build(qc, { queryKey: s.key }).setState({
        status: "error", error: s.error, errorUpdatedAt: 1, fetchStatus: "idle", fetchFailureCount: 1, fetchFailureReason: s.error, data: undefined,
      } as never);
    } else qc.setQueryData(s.key, s.infinite ? { pages: [s.data], pageParams: [undefined] } : s.data);
  }
  return qc;
}

export const paperRun = (over: Partial<Run> = {}): Run => ({
  id: 1, created_at: "2026-11-02T14:00:00Z", amount_usd: 10_000, status: "running", end_reason: null, peak_equity: null,
  kind: "paper", label: "Paper 1", level: "balanced", started_on: "2026-11-02", ended_on: null, ...over,
});
export const replayRun = (over: Partial<Run> = {}): Run =>
  paperRun({ id: 2, kind: "replay", label: "Replay A", status: "ended", started_on: "2026-01-02", ended_on: "2026-06-30", ...over });

/** An overview with nothing in it yet: no sessions, no heartbeat, no ledger. */
export const emptyOverview = (run: Run): Overview => ({ run, daily: [], heartbeat: [], pending_commands: [], ledger: null, today: null });

export function ctxFor(run: Run, over: Partial<RunCtx> = {}): RunCtx {
  return { run, runs: [run], overview: emptyOverview(run), retryOverview: () => {}, ...over };
}

/** Render `page` at `path` (matched by `route`) inside the shell's outlet context, as the dashboard does. */
export function render(page: ReactElement, opts: { seeds?: Seed[]; ctx?: RunCtx; path?: string; route?: string; children?: ReactNode } = {}) {
  const { seeds = [], ctx = ctxFor(paperRun()), path = "/", route = "/", children } = opts;
  return renderToStaticMarkup(
    <QueryClientProvider client={seededClient(seeds)}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route element={<Outlet context={ctx} />}>
            <Route path={route} element={page}>{children}</Route>
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** Visible text: tags dropped, entities decoded, whitespace collapsed. */
export const text = (html: string) => html.replace(/<[^>]*>/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">")
  .replace(/&quot;/g, '"').replace(/&#x27;/g, "'").replace(/\s+/g, " ").trim();

/** A loading skeleton or placeholder announced to screen readers. */
export const hasStatus = (html: string) => /role="status"/.test(html);
export const hasAlert = (html: string) => /role="alert"/.test(html);

/** Numbers a reader could take as data: a bare 0, $0 or 0% that is not part of a time or a longer number. */
export const zeros = (html: string) => text(html).match(/(?<![\d.,:])[$−-]?0(?:\.0+)?%?(?![\d.,:])/g) ?? [];

/** Rendering glitches that must never reach the page. */
export const glitches = (html: string) => text(html).match(/\b(?:NaN|undefined|null|Infinity|\[object Object\])\b/g) ?? [];
