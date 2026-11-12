import { describe, expect, it } from "vitest";
import type { Overview, Run } from "./api";
import { overviewPollMs, poll } from "./poll";
import mainSrc from "../main.tsx?raw";
import shellSrc from "../components/Shell.tsx?raw";

const run = (over: Partial<Run> = {}): Run => ({ id: 1, created_at: "", amount_usd: 5000, status: "running", end_reason: null, peak_equity: null,
  kind: "paper", label: null, level: "balanced", started_on: null, ended_on: null, ...over });
const now = Date.parse("2026-11-12T18:00:00Z");
const ov = (state: string, ageS = 5) => ({ heartbeat: [{ process: "engine", ts: new Date(now - ageS * 1000).toISOString(), detail: { state } }] }) as unknown as Overview;

describe("polling (no refetch storms)", () => {
  it("polls the overview every 5 s only while the engine is live in a session", () => {
    expect(overviewPollMs(run(), ov("trading"), now)).toBe(5_000);
    expect(overviewPollMs(run(), ov("managing"), now)).toBe(5_000);
    expect(overviewPollMs(run(), ov("market_closed"), now)).toBe(30_000);
    expect(overviewPollMs(run(), ov("trading", 300), now)).toBe(30_000);
    expect(overviewPollMs(run(), undefined, now)).toBe(30_000);
    expect(overviewPollMs(run({ kind: "replay" }), ov("trading"), now)).toBe(false);
    expect(overviewPollMs(run({ status: "ended" }), ov("trading"), now)).toBe(300_000);
    expect(overviewPollMs(undefined, undefined, now)).toBe(false);
  });
  it("never polls in a hidden tab", () => {
    expect(poll(15_000)).toEqual({ refetchInterval: 15_000, refetchIntervalInBackground: false });
    expect(mainSrc).toMatch(/refetchIntervalInBackground: false/);
    expect(shellSrc).toMatch(/overviewPollMs\(/);
  });
  it("leaves no bare refetchInterval number in a page (every poll goes through poll())", async () => {
    const pages = import.meta.glob("../pages/*.tsx", { query: "?raw", import: "default", eager: true }) as Record<string, string>;
    for (const [f, src] of Object.entries(pages)) expect(src, f).not.toMatch(/refetchInterval:\s*[\d(]/);
  });
});
