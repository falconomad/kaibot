// #378 (M8 S104), follow-up: the market notices on Home keep their rows when a later ask fails and say their age with
// the one freshness cue (#604/#650, components/Fresh.tsx); the list is the owner's alone (#634).
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import MarketNotices, { NoticeRows } from "./components/Notices";
import Lines from "./components/Lines";
import linesSrc from "./components/Lines.tsx?raw";
import liveSrc from "./pages/Live.tsx?raw";
import { NOTICES_EVERY, type Notices, noticesFreshness, parseNotices } from "./lib/notices";
import { RECENT_MS, liveFor } from "./lib/freshness";
import { text } from "./pages/testkit";

vi.mock("./lib/api", () => import("./pages/testkit-api"));

const ANT = { kind: "new_listing", flag: "ipo", tier: "major", session: "2026-11-19", symbol: "ANT", title: "Anthropic (ANT) starts trading",
  starts: null, first_eligible: "2027-11-19", source: "publisher", detail: { name: "Anthropic" } };
const NOV19 = parseNotices({ today: "2026-11-19", from: "2026-11-13", to: "2026-11-27",
  sessions: ["2026-11-19", "2026-11-20", "2026-11-23"], notices: [ANT] }) as Notices;
const NOW = Date.parse("2026-11-19T16:00:00Z");
const H = 3_600_000;

/** A cache whose notices query holds `data` from `updatedAt`, and whose latest ask failed when `failed`. */
function client(data: Notices | null | undefined, updatedAt: number, failed: boolean) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, retryOnMount: false, staleTime: Infinity, gcTime: Infinity } } });
  const err = new Error("boom");
  qc.getQueryCache().build(qc, { queryKey: ["market-notices"] }).setState({
    status: failed ? "error" : "success", data, dataUpdatedAt: updatedAt,
    error: failed ? err : null, errorUpdatedAt: failed ? NOW : 0, fetchStatus: "idle", fetchFailureCount: failed ? 1 : 0,
    fetchFailureReason: failed ? err : null,
  } as never);
  return qc;
}
const show = (qc: QueryClient, el = <MarketNotices tz="Europe/Berlin" />) =>
  renderToStaticMarkup(<QueryClientProvider client={qc}>{el}</QueryClientProvider>);

describe("the notices' freshness, by the one rule", () => {
  it("nothing while asks succeed, however old; when an ask fails, the rule of lib/freshness.ts", () => {
    expect(noticesFreshness(NOW - 5 * H, false, NOW)).toBeNull();
    expect(noticesFreshness(0, true, NOW)).toBeNull();
    expect(noticesFreshness(NOW - 10 * 60_000, true, NOW)).toEqual({ state: "live", text: null });
    const lim = liveFor(NOTICES_EVERY);
    expect(noticesFreshness(NOW - lim - 60_000, true, NOW)).toEqual({ state: "recent", text: "A few minutes old" });
    expect(noticesFreshness(NOW - lim - RECENT_MS - 60_000, true, NOW)?.state).toBe("stale");
    expect(noticesFreshness(NOW - 3 * H, true, NOW)).toEqual({ state: "stale", text: "Stale · 3 h old" });
  });

  it("the poll and the rule use the same interval", () => {
    expect(NOTICES_EVERY).toBe(30 * 60_000);
  });
});

describe("a failed ask keeps the rows", () => {
  it("rows from before a failed ask stay, with the age in the shared cue, not an error line", () => {
    vi.useFakeTimers({ now: NOW });
    try {
      const html = show(client(NOV19, NOW - 3 * H, true));
      expect(text(html)).toContain("Anthropic (ANT) started trading today");
      expect(text(html)).toContain("Stale · 3 h old");
      expect(html).toContain('class="fresh stale"');
      expect(text(html)).not.toContain("could not load");
      const fresh = show(client(NOV19, NOW - 10 * 60_000, true));
      expect(text(fresh)).toContain("Anthropic (ANT) started trading today");
      expect(fresh).not.toContain('class="fresh');
    } finally { vi.useRealTimers(); }
  });

  it("a first ask that fails is still the quiet line; a success says no age", () => {
    expect(text(show(client(undefined, 0, true)))).toBe("Market notices could not load. Try again");
    vi.useFakeTimers({ now: NOW });
    try {
      const ok = show(client(NOV19, NOW - 5 * H, false));
      expect(text(ok)).toContain("Anthropic (ANT) started trading today");
      expect(ok).not.toContain('class="fresh');
    } finally { vi.useRealTimers(); }
  });

  it("the cue sits inside the one notice list and only when there are rows", () => {
    const html = renderToStaticMarkup(<NoticeRows data={NOV19} tz="Europe/Berlin" fresh={{ state: "stale", text: "Stale · 3 h old" }} />);
    expect(html).toMatch(/^<div class="nt-rows" aria-label="Market notices">.*<p class="fresh stale" role="status"><span class="sr-only">Market notices: <\/span>Stale · 3 h old<\/p><\/div>$/);
    const none = parseNotices({ today: "2026-12-07", sessions: [], notices: [] }) as Notices;
    expect(renderToStaticMarkup(<NoticeRows data={none} fresh={{ state: "stale", text: "Stale · 3 h old" }} />)).toBe("");
  });
});

describe("owner only", () => {
  it("the market notices render for the owner and never for anyone else", () => {
    const qc = client(NOV19, Date.now(), false);
    expect(text(show(qc, <Lines viewer="owner" headlines={false} />))).toContain("Anthropic (ANT)");
    expect(show(client(NOV19, Date.now(), false), <Lines viewer="other" headlines={false} />)).toBe("");
  });

  it("Home names its viewer, and Lines takes no default", () => {
    expect(liveSrc).toMatch(/<Lines viewer="owner" headlines=\{!ended\} fresh=\{newsFresh\} \/>/);
    expect(linesSrc).toMatch(/\{ viewer: Viewer; headlines: boolean/);
    expect(linesSrc).toMatch(/viewer === "owner" && <MarketNotices \/>/);
    expect(linesSrc).toMatch(/<Headlines viewer=\{viewer\}/);
  });
});
