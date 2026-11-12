import { describe, expect, it, vi } from "vitest";
import type { Attribution, Evidence, Report as ReportResp, RoomAB } from "../lib/api";
import { Report } from "./Report";
import { boom, ctxFor, glitches, hasAlert, hasStatus, render, replayRun, text, zeros, type Seed } from "./testkit";

vi.mock("../lib/api", () => import("./testkit-api"));

const evidence: Evidence = { n: 0, pnl: 0, mean_r: null, sd_r: null, t: null, ci_lo: null, ci_hi: null, sessions: 0, first_session: null, by_strategy: [] };
const report: ReportResp = { session: null, sessions: [] };
const attribution: Attribution = { since: "2026-10-15T00:00:00Z", weeks: 4, n: 0, groups: [], weekly: [] };
const roomAb = { since: "2026-08-13T00:00:00Z", weeks: 13, n: 0, by_verdict: {}, by_status: {} } as unknown as RoomAB;

const keys = { evidence: ["evidence", 1], chapters: ["chapters", 1], report: ["report", 1, undefined], roomAb: ["room_ab", 1, 13], attribution: ["attribution", 1, 4] };
const empty: Seed[] = [
  { key: keys.evidence, data: evidence }, { key: keys.chapters, data: [] }, { key: keys.report, data: report },
  { key: keys.roomAb, data: roomAb }, { key: keys.attribution, data: attribution },
];
const failing = (msg: string): Seed[] => Object.values(keys).map((key) => ({ key, error: boom(msg) }));

describe("Report page states", () => {
  it("loading: every card shows a placeholder, and the evidence counter shows no zeros or verdict yet", () => {
    const html = render(<Report />);
    expect((html.match(/role="status"/g) ?? []).length).toBeGreaterThanOrEqual(5);
    expect(zeros(html)).toEqual([]);
    expect(text(html)).not.toContain("No closed trades yet.");
  });

  it("empty: each card says what is missing and when it will appear", () => {
    const html = render(<Report />, { seeds: empty });
    const t = text(html);
    expect(t).toContain("No closed trades yet.");
    expect(t).toContain("No chapters yet.");
    expect(t).toContain("No finished session yet.");
    expect(t).toContain("No agent-room sessions in this period.");
    expect(t).toContain("No closed trades in this period.");
    expect(hasStatus(html)).toBe(false);
    expect(glitches(html)).toEqual([]);
  });

  it("empty: a replay shows no chapters or room card, only its own cards", () => {
    const r = replayRun();
    const html = render(<Report />, { ctx: ctxFor(r), seeds: [
      { key: ["evidence", r.id], data: evidence }, { key: ["report", r.id, undefined], data: report }, { key: ["attribution", r.id, 4], data: attribution }] });
    expect(text(html)).toContain("Replay.");
    expect(text(html)).not.toContain("Chapters");
    expect(text(html)).not.toContain("Room vs rules-only");
    expect(hasStatus(html)).toBe(false);
  });

  it("error: each card says it couldn't load, with the server's message; no zeros stand in for the counter", () => {
    const html = render(<Report />, { seeds: failing("rpc kb_report failed") });
    const t = text(html);
    expect(hasAlert(html)).toBe(true);
    for (const what of ["The evidence counter", "The daily report card", "Room vs rules-only", "Attribution"]) expect(t).toContain(`${what} couldn't load.`);
    expect(t).toContain("Chapters aren't available yet: rpc kb_report failed");
    expect(t).not.toContain("No closed trades yet.");
    expect(zeros(html)).toEqual([]);
  });
});
