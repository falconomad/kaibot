// @vitest-environment jsdom
// Settings with minimal or missing data, mounted in jsdom so its queries answer (the SSR tests in Settings.test.tsx
// never let a query settle, which is why they missed this). The design audit's mock had no kb_invite_list, its
// catch-all answer ({ ok, queued }) reached the invite card and `invites.length` threw: the whole page went blank.
// Every answer below must leave the page up, with its cards, and nothing like "undefined" or "NaN" shown.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mount, reducedMotionOn, settle, unmount } from "../a11y/dom";
import { Settings } from "./Settings";
import { ctxFor, emptyOverview, paperRun, replayRun } from "./testkit";
import { fixInviteList } from "../lib/invites";

const rec = vi.hoisted(() => ({ list: vi.fn<() => Promise<unknown>>() }));
vi.mock("../lib/api", async () => {
  const kit = await import("./testkit-api");
  const never = () => new Promise<never>(() => {});
  return { ...kit, api: new Proxy({}, { get: (_t, k) => (k === "inviteList" ? rec.list : never) }) };
});

const mem = new Map<string, string>();
vi.stubGlobal("localStorage", { getItem: (k: string) => mem.get(k) ?? null, setItem: (k: string, v: string) => { mem.set(k, v); },
  removeItem: (k: string) => { mem.delete(k); } });

const page = () => (document.body.textContent ?? "").replace(/\s+/g, " ").trim();
const glitches = () => page().match(/\b(?:NaN|undefined|null|Infinity|\[object Object\])\b/g) ?? [];

async function show(answer: unknown, ctx = ctxFor(paperRun())) {
  rec.list.mockResolvedValue(answer);
  const errors: unknown[] = [];
  const onError = (e: ErrorEvent) => { errors.push(e.error); e.preventDefault(); };
  window.addEventListener("error", onError);
  const spy = vi.spyOn(console, "error").mockImplementation((...a) => { errors.push(a[0]); });
  try {
    await mount(<Settings />, { ctx });
    await settle(20);
  } finally {
    window.removeEventListener("error", onError);
    spy.mockRestore();
  }
  return errors;
}

beforeEach(() => { reducedMotionOn(); mem.clear(); rec.list.mockReset(); });
afterEach(async () => { await unmount(); });

describe("Settings with minimal or missing data", () => {
  // [name, answer, the places-left line it should show (null: none, as a count is missing)]
  const shapes: [string, unknown, string | null][] = [
    ["the audit mock's old catch-all answer", { ok: true, queued: true }, null],
    ["an empty object", {}, null],
    ["invites not a list", { cap: 5, taken: 1, invites: null }, "4 of 5 places left."],
    ["counts missing", { invites: [] }, null],
    ["a bare list", [], null],
    ["a string", "oops", null],
    ["a row that is null", { cap: 5, taken: 0, invites: [null] }, "5 of 5 places left."],
  ];
  it.each(shapes)("%s: the page renders, every card included, without an error", async (_name, answer, left) => {
    const errors = await show(answer);
    expect(errors).toEqual([]);
    const t = page();
    for (const h of ["This run", "The levels", "All runs", "Pause animations", "Invite someone", "Account"]) expect(t).toContain(h);
    if (left) expect(t).toContain(left); else expect(t).not.toContain("places left");
    expect(document.querySelector('table[aria-label="Invites"]')).toBeNull();
    expect(glitches()).toEqual([]);
  });

  it("kb_invite_list not there yet (null): the card says so and the page stays up", async () => {
    const errors = await show(null);
    expect(errors).toEqual([]);
    expect(page()).toContain("Invites need the database update");
    expect(page()).toContain("Account");
  });

  it("the live shape (0046): the places left and the invites table", async () => {
    const errors = await show({ cap: 5, taken: 1, invites: [{ id: 1, email: "a@example.com", created_at: "2026-11-24T10:00:00Z",
      expires_at: "2026-12-01T10:00:00Z", used_at: null, status: "open", code: "ABCD", mine: true, can_revoke: true }] });
    expect(errors).toEqual([]);
    expect(page()).toContain("4 of 5 places left.");
    expect(document.querySelector('table[aria-label="Invites"]')?.textContent).toContain("a@example.com");
  });

  it("a replay run with no overview and a single run: still renders", async () => {
    const r = replayRun({ label: null, started_on: null, ended_on: null });
    const errors = await show({}, ctxFor(r, { overview: undefined, runs: [r] }));
    expect(errors).toEqual([]);
    expect(page()).toContain("All runs");
    expect(glitches()).toEqual([]);
  });

  it("a paper run whose overview is empty", async () => {
    const r = paperRun({ level: null });
    const errors = await show({ ok: true }, ctxFor(r, { overview: emptyOverview(r) }));
    expect(errors).toEqual([]);
    expect(page()).toContain("This run");
  });
});

describe("fixInviteList", () => {
  it("keeps null (not applied), and makes any other answer a list with numeric or null counts", () => {
    expect(fixInviteList(null)).toBeNull();
    expect(fixInviteList(undefined)).toBeNull();
    expect(fixInviteList({ ok: true })).toEqual({ cap: null, taken: null, invites: [] });
    expect(fixInviteList({ cap: "5", taken: 2, invites: [] })).toEqual({ cap: 5, taken: 2, invites: [] });
    const once = fixInviteList({ cap: 3, taken: 1, invites: [{ id: 1 }] });
    expect(fixInviteList(once)).toEqual(once);
  });
});
