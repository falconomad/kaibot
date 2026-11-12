import { describe, expect, it } from "vitest";
import { engineVersion, parseFingerprint } from "./engine";

describe("parseFingerprint", () => {
  it("reads the per-rule parts the engine logs", () => {
    const f = parseFingerprint("0123456789abcdef;core=aaaaaaaaaaaaaaaa;dip_reversal=bbbbbbbbbbbbbbbb;spy_late_momentum=cccccccccccccccc")!;
    expect(f.all).toBe("0123456789abcdef");
    expect(f.core).toBe("aaaaaaaaaaaaaaaa");
    expect(f.rules).toEqual([{ key: "dip_reversal", hash: "bbbbbbbbbbbbbbbb" }, { key: "spy_late_momentum", hash: "cccccccccccccccc" }]);
  });
  it("keeps an old single hash, marks unknown, ignores junk", () => {
    expect(parseFingerprint("0123456789abcdef")).toMatchObject({ all: "0123456789abcdef", core: null, rules: [] });
    expect(parseFingerprint("unknown")).toMatchObject({ unknown: true, all: null });
    expect(parseFingerprint(null)).toBeNull();
    expect(parseFingerprint(42)).toBeNull();
    expect(parseFingerprint("abc;=x;core=;nope")?.rules).toEqual([]);
  });
});

describe("engineVersion", () => {
  const ev = (ts: string, code_sha: string, fingerprint = "f;core=c") => ({ type: "engine_start", ts, run_id: 1, payload: { code_sha, fingerprint } });
  it("takes the code version from the heartbeat and the fingerprint from the latest start", () => {
    const v = engineVersion({
      heartbeat: [{ process: "engine", ts: "2026-11-12T18:00:00Z", detail: { state: "trading", code_sha: "3e5c09c" } }],
      events: [ev("2026-11-11T13:00:00Z", "old1234"), ev("2026-11-12T13:00:00Z", "3e5c09c", "f;core=c;dip_reversal=d"),
        { type: "session_start", ts: "2026-11-12T14:30:00Z", run_id: 1, payload: {} }],
    });
    expect(v.running).toBe("3e5c09c");
    expect(v.start?.codeSha).toBe("3e5c09c");
    expect(v.start?.fingerprint?.rules).toEqual([{ key: "dip_reversal", hash: "d" }]);
    expect(v.mismatch).toBe(false);
  });
  it("flags a running version that differs from the last logged start, and copes with nothing", () => {
    expect(engineVersion({ heartbeat: [{ process: "engine", ts: "x", detail: { code_sha: "new" } }], events: [ev("2026-11-12T13:00:00Z", "old")] }).mismatch).toBe(true);
    expect(engineVersion({ heartbeat: [], events: [] })).toEqual({ running: null, start: null, mismatch: false });
  });
});
