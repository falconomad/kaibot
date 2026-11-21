// @vitest-environment jsdom
// M9 1.14 (#439): accepting a ledger/broker difference on the Engine page. The page is mounted in jsdom with
// kb_command replaced by a recorder, so the test can see: the exact difference before anything can be sent, the two
// things the owner must do first (a reason, a tick), what the command carries, and the states after it.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { auditNode, lines } from "./a11y/axe";
import { activate, all, client, doing, mount, q, reducedMotionOn, said, settle, unmount } from "./a11y/dom";
import type { Health as HealthResp } from "./lib/api";
import { Health } from "./pages/Health";
import { ctxFor, emptyOverview, paperRun } from "./pages/testkit";

const sent = vi.hoisted(() => ({ command: vi.fn<(kind: string, payload: unknown) => Promise<unknown>>() }));
vi.mock("./lib/api", async () => {
  const kit = await import("./pages/testkit-api");
  const never = () => new Promise<never>(() => {});
  return { ...kit, api: new Proxy({}, { get: (_t, k) => (k === "command" ? sent.command : never) }) };
});
vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });

const QTY = { kind: "quantity", symbol: "XOM", ledger: 10, broker: 8, offset: -2 };
const CASH = { kind: "cash", symbol: null, ledger_change: -1606.2, broker_change: -1608.95, receivable: 0, tolerance: 47.5, offset: -2.75 };
const run = paperRun();
const health = (reconcile: unknown, o: Partial<HealthResp> = {}, ageMs = 5_000): HealthResp => ({
  now: new Date().toISOString(), halts: [], events: [], pending_commands: [], deadman: { armed: true, jobs: [], runs: [] },
  heartbeat: [{ process: "engine", ts: new Date(Date.now() - ageMs).toISOString(), detail: { state: "managing", host: "mac", reconcile } as never }], ...o });
const DIFF = { ok: false, checked: true, ts: "2026-11-20T15:31:00+00:00", diffs: [QTY, CASH] };

const start = async (h: HealthResp) => {
  await mount(<Health />, { ctx: ctxFor(run, { overview: { ...emptyOverview(run), heartbeat: h.heartbeat } }), seeds: [{ key: ["health"], data: h }] });
};
const row = () => all<HTMLButtonElement>("button.row").find((b) => /Books and broker/.test(b.textContent ?? ""))!;
const sheet = () => q('[role="dialog"]')!;
const text = (el: Element | null) => (el?.textContent ?? "").replace(/\s+/g, " ").trim();
const button = () => all<HTMLButtonElement>('[role="dialog"] button[type="submit"]')[0];
const type = (el: HTMLInputElement, value: string) => doing(() => {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
});
const tick = (el: HTMLInputElement) => doing(() => { el.click(); });

beforeEach(() => { reducedMotionOn(); sent.command.mockReset(); });
afterEach(async () => { await unmount(); });

describe("Engine page: books and broker", () => {
  it("an engine that reports nothing shows no row; a clean check says 'Match'", async () => {
    await start(health(undefined));
    expect(row()).toBeUndefined();
    await start(health({ ok: true, checked: true, ts: "2026-11-20T15:31:00+00:00" }));
    expect(text(row())).toBe("Books and brokerMatchOK");
    await activate(row());
    expect(text(sheet())).toContain("kaibo's books and the broker's account agree");
    expect(button()).toBeUndefined();
  });

  it("the difference appears: the stock, both share counts and the cash gap, before anything can be sent", async () => {
    await start(health(DIFF));
    expect(text(row())).toBe("Books and broker2 differences · new buys stoppedProblem");
    await activate(row());
    const t = text(sheet());
    expect(t).toContain("kaibo's books and the broker don't agree, so new buys are stopped.");
    expect(t).toContain("XOMkaibo's books10 sharesThe broker8 sharesDifference2 shares fewer at the broker");
    expect(t).toContain("Cashkaibo's books: cash change since the run began-$1,606.20The broker: cash change since the run began-$1,608.95Difference$2.75 less at the broker");
    expect(t).toContain("starts buying again");
    expect(button().disabled).toBe(true);
    expect(lines(await auditNode(document.body))).toEqual([]);
  });

  it("the owner accepts: a reason and a tick first, then the command carries exactly what was shown", async () => {
    sent.command.mockResolvedValue({ queued: true });
    await start(health(DIFF));
    await activate(row());
    const reason = q<HTMLInputElement>('[role="dialog"] input[type="text"]')!, box = q<HTMLInputElement>('[role="dialog"] input[type="checkbox"]')!;
    await tick(box);
    expect(button().disabled).toBe(true); // no reason yet
    await type(reason, " x ");
    expect(button().disabled).toBe(true); // too short once trimmed
    await type(reason, "  a broker fee the books don't include ");
    expect(button().disabled).toBe(false);
    expect(text(button())).toBe("Accept the difference and resume buys");
    await tick(box);
    expect(button().disabled).toBe(true); // not ticked
    await tick(box);
    await activate(button());
    await settle();
    expect(sent.command).toHaveBeenCalledTimes(1);
    expect(sent.command).toHaveBeenCalledWith("accept_reconcile", { reason: "a broker fee the books don't include",
      shown: [{ kind: "quantity", symbol: "XOM", offset: -2 }, { kind: "cash", symbol: null, offset: -2.75 }] });
    expect(said().join(" ")).toContain("Sent. The engine reads the broker once more");
    expect(button().disabled).toBe(true); // one acceptance per look

    // buys resume: the engine's next heartbeat says the books match
    await doing(() => { client.setQueryData(["health"], health({ ok: true, checked: true, ts: "2026-11-20T15:32:00+00:00" })); });
    await settle();
    expect(text(row())).toBe("Books and brokerMatchOK");
    expect(text(sheet())).toContain("kaibo's books and the broker's account agree");
  });

  it("the engine refused (the numbers changed): said in words, with the new numbers, and it can be accepted again", async () => {
    await start(health({ ...DIFF, diffs: [{ ...QTY, broker: 7, offset: -3 }],
      refused: { ts: "2026-11-20T15:33:00+00:00", why: "nothing accepted: the difference has changed since it was shown: check again and accept the new value" } }));
    await activate(row());
    expect(text(q('[role="dialog"] [role="alert"]'))).toMatch(/^The numbers changed after you looked, so nothing was accepted/);
    expect(text(sheet())).toContain("3 shares fewer at the broker");
    expect(q('[role="dialog"] input[type="text"]')).not.toBeNull();
  });

  it("nothing to accept here: a failed read, an unknown fill, or a list cut short", async () => {
    await start(health({ ok: false, diffs: [{ kind: "unavailable", symbol: null, error: "RuntimeError: broker unreachable", offset: null }] }));
    await activate(row());
    expect(text(sheet())).toContain("couldn't compare its books with the broker");
    expect(button()).toBeUndefined();
    await start(health({ ...DIFF, more: 3 }));
    await activate(row());
    expect(text(sheet())).toContain("And 3 more that don't fit here, so the list can't be accepted from this page.");
    expect(button()).toBeUndefined();
  });

  it("the engine is not running: the difference shows, the button stays off and says why", async () => {
    await start(health(DIFF, {}, 10 * 60_000));
    await activate(row());
    await tick(q<HTMLInputElement>('[role="dialog"] input[type="checkbox"]')!);
    await type(q<HTMLInputElement>('[role="dialog"] input[type="text"]')!, "a broker fee");
    expect(button().disabled).toBe(true);
    expect(text(sheet())).toContain("The engine isn't running, so it can't accept anything now.");
  });

  it("a command already waiting for the engine cannot be sent twice", async () => {
    await start(health(DIFF, { pending_commands: [{ kind: "accept_reconcile", ts: new Date().toISOString() }] }));
    await activate(row());
    await tick(q<HTMLInputElement>('[role="dialog"] input[type="checkbox"]')!);
    await type(q<HTMLInputElement>('[role="dialog"] input[type="text"]')!, "a broker fee");
    expect(button().disabled).toBe(true);
    expect(said().join(" ")).toContain("Sent.");
  });

  it("the database update is not applied: nothing is sent, said in words", async () => {
    sent.command.mockRejectedValue(Object.assign(new Error("unknown command"), { code: "P0001" }));
    await start(health(DIFF));
    await activate(row());
    await tick(q<HTMLInputElement>('[role="dialog"] input[type="checkbox"]')!);
    await type(q<HTMLInputElement>('[role="dialog"] input[type="text"]')!, "a broker fee");
    await activate(button());
    await settle();
    expect(text(q('[role="dialog"] form [role="alert"]'))).toContain("database update that hasn't been applied yet (migration 0038)");
    expect(button().disabled).toBe(false); // may try again
  });
});
