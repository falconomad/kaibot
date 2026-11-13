import { describe, expect, it, vi } from "vitest";
import type { GateResp } from "../lib/api";
import { Gate } from "./Gate";
import { boom, glitches, hasAlert, hasStatus, render, text, zeros } from "./testkit";

vi.mock("../lib/api", () => import("./testkit-api"));

const key = ["gate", 1];
const none: GateResp = { latest: null, history: [], random_days: 0 };
const judgedNothing: GateResp = {
  latest: { id: 1, ts: "2026-11-11T21:05:00Z", session: "2026-11-11", bar_sha: "abcdef0123456789", fingerprint: "f00dfeed12345678", overall: "keep collecting",
    report: { generated_at: "2026-11-11T21:05:00Z", bar_sha: "abcdef0123456789", fingerprint: "f00dfeed12345678", frozen_since: null, overall: "keep collecting",
      bar: {}, kill: {}, strategies: {} } },
  history: [], random_days: 0,
};

describe("Go / no-go page states", () => {
  it("loading: a skeleton", () => {
    const html = render(<Gate />);
    expect(hasStatus(html)).toBe(true);
    expect(zeros(html)).toEqual([]);
  });

  it("empty: no report yet, said in words", () => {
    const html = render(<Gate />, { seeds: [{ key, data: none }] });
    expect(text(html)).toContain("No gate report yet.");
    expect(text(html)).toContain("There is no go-live button.");
    expect(glitches(html)).toEqual([]);
  });

  it("empty: a report that judged no strategy", () => {
    const html = render(<Gate />, { seeds: [{ key, data: judgedNothing }] });
    expect(text(html)).toContain("The latest report judged no strategy");
    expect(glitches(html)).toEqual([]);
  });

  it("error: a plain sentence with the server's message and a retry", () => {
    const html = render(<Gate />, { seeds: [{ key, error: boom() }] });
    expect(hasAlert(html)).toBe(true);
    expect(text(html)).toContain("The go / no-go review couldn't load.");
    expect(text(html)).toContain("Try again");
  });
});
