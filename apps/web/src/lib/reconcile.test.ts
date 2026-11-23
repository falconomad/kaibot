// M9 1.14 (#439): the heartbeat's reconcile block, read defensively, and its differences in plain words.
import { describe, expect, it } from "vitest";
import { acceptable, diffView, queueErrorText, reconLine, reconcileFrom, refusedText, shares, shownOf, unbookedOf } from "./reconcile";

const QTY = { kind: "quantity", symbol: "XOM", ledger: 10, broker: 8, offset: -2 };
const CASH = { kind: "cash", symbol: null, ledger_change: -1606.2, broker_change: -1608.95, receivable: 0, tolerance: 47.5, offset: -2.75 };
const beat = (reconcile: unknown) => ({ state: "managing", reconcile });

describe("the heartbeat's reconcile block", () => {
  it("an engine that says nothing (before #439, or no check yet) is 'not reported', never a match", () => {
    for (const d of [null, undefined, {}, { reconcile: null }, { reconcile: "ok" }, { reconcile: { diffs: [] } }, { reconcile: { ok: "yes" } }])
      expect(reconcileFrom(d)).toBeNull();
  });

  it("a clean check", () => {
    expect(reconcileFrom(beat({ ok: true, checked: true, ts: "2026-11-20T14:31:00+00:00" })))
      .toEqual({ ok: true, ts: "2026-11-20T14:31:00+00:00", diffs: [], more: 0, refused: null });
  });

  it("shares and cash: the numbers of both sides and the offset that goes back with the command", () => {
    const s = reconcileFrom(beat({ ok: false, checked: true, ts: "2026-11-20T14:31:00+00:00", diffs: [QTY, CASH] }))!;
    expect(s.ok).toBe(false);
    expect(shownOf(s)).toEqual([{ kind: "quantity", symbol: "XOM", offset: -2 }, { kind: "cash", symbol: null, offset: -2.75 }]);
    expect(acceptable(s)).toBe(true);
    expect(reconLine(s)).toBe("2 differences · new buys stopped");
    const [q, c] = s.diffs.map(diffView);
    expect(q).toMatchObject({ head: "XOM", canAccept: true });
    expect(q.rows).toEqual([["kaibo's books", "10 shares"], ["The broker", "8 shares"], ["Difference", "2 shares fewer at the broker"]]);
    expect(c.head).toBe("Cash");
    expect(c.rows.map((r) => r[1])).toEqual(["-$1,606.20", "-$1,608.95", "$2.75 less at the broker"]);
  });

  it("a block that says 'ok' but lists a difference is not a match", () => {
    expect(reconcileFrom(beat({ ok: true, diffs: [QTY] }))!.ok).toBe(false);
  });

  it("dividends owed appear only when there are some; more at the broker reads 'more'", () => {
    const c = diffView(reconcileFrom(beat({ ok: false, diffs: [{ ...CASH, broker_change: -1500, receivable: 12.5, offset: 106.2 }] }))!.diffs[0]);
    expect(c.rows.map((r) => r[0])).toContain("Dividends owed, not paid yet (already left out)");
    expect(c.rows.at(-1)![1]).toBe("$106.20 more at the broker");
    const q = diffView(reconcileFrom(beat({ ok: false, diffs: [{ kind: "quantity", symbol: "KO", ledger: 0, broker: 1, offset: 1 }] }))!.diffs[0]);
    expect(q.rows.at(-1)![1]).toBe("1 share more at the broker");
    expect(shares(0.333333333)).toBe("0.333333 shares");
  });

  it("differences that only heal are shown, explained and never sent back", () => {
    const s = reconcileFrom(beat({ ok: false, diffs: [
      { kind: "foreign_fill", symbol: "KO", activity: "a1", order_id: "o1", qty: 5, side: "buy", offset: null },
      { kind: "unbooked", symbol: "PG", coid: "c1", venue: 7, booked: 4, offset: null },
      { kind: "unavailable", symbol: null, error: "RuntimeError: broker unreachable", offset: null },
      { kind: "something_new", symbol: "T", offset: 3 }] }))!;
    expect(shownOf(s)).toEqual([]);
    expect(acceptable(s)).toBe(false);
    const v = s.diffs.map(diffView);
    expect(v.every((x) => !x.canAccept && x.note.length > 20)).toBe(true);
    expect(v[0].rows).toEqual([["Filled at the broker", "bought 5 shares"]]);
    expect(v[1].rows).toEqual([["Filled, not in the books yet", "3 shares"]]);
    expect(reconLine(s)).toBe("4 differences · new buys stopped");
  });

  it("a quantity or cash row with a number missing cannot be accepted (nothing unseen is sent back)", () => {
    const s = reconcileFrom(beat({ ok: false, diffs: [{ kind: "quantity", symbol: "XOM", ledger: 10, offset: -2 }, { kind: "cash", offset: "x" }, "junk", { symbol: "A" }] }))!;
    expect(s.diffs).toHaveLength(2);
    expect(shownOf(s)).toEqual([]);
  });

  it("a list the heartbeat cut short cannot be accepted from here", () => {
    const s = reconcileFrom(beat({ ok: false, diffs: [QTY], more: 10 }))!;
    expect(acceptable(s)).toBe(false);
    expect(reconLine(s)).toBe("11 differences · new buys stopped");
  });

  it("a refusal and a failed send read in plain words", () => {
    const s = reconcileFrom(beat({ ok: false, diffs: [QTY], refused: { ts: "2026-11-20T14:40:00+00:00", why: "nothing accepted: the difference has changed since it was shown: check again and accept the new value" } }))!;
    expect(refusedText(s.refused!.why)).toMatch(/^The numbers changed after you looked/);
    expect(refusedText("nothing accepted: a difference that was shown has healed: check again")).toMatch(/cleared by itself/);
    expect(refusedText("Kaibot said no")).toBe("Nothing was accepted: kaibo said no");
    expect(queueErrorText(new Error("unknown command"))).toMatch(/database update that hasn't been applied yet \(migration 0037\)/);
    expect(queueErrorText(new Error("not authorised"))).toMatch(/Only the owner/);
    expect(queueErrorText(new Error("fetch failed"))).toMatch(/couldn't be sent, so nothing changed\. fetch failed/);
  });

  it("#529: an unbooked fill a day old can be booked or accepted, and then only it goes back with the command", () => {
    const UN = { kind: "unbooked", symbol: "PG", coid: "kb7-trend_momentum-PG-20260803-buy-1", venue: 7, booked: 4, aged: true, offset: null };
    const s = reconcileFrom(beat({ ok: false, diffs: [QTY, UN, CASH] }))!;
    expect(unbookedOf(s).map((d) => d.coid)).toEqual([UN.coid]);
    const v = diffView(s.diffs[1]);
    expect(v.canAccept).toBe(true);
    expect(v.rows).toEqual([["Filled at the broker", "7 shares"], ["In kaibo's books", "4 shares"], ["Never reported", "3 shares"]]);
    expect(acceptable(s)).toBe(true);
    expect(shownOf(s)).toEqual([{ kind: "unbooked", symbol: "PG", coid: UN.coid, venue: 7, booked: 4, action: "book" }]);
    expect(shownOf(s, { [UN.coid]: "accept" })[0]).toMatchObject({ action: "accept" });
    // the same row on its own day (its report is on its way), or with a number missing: not decided here
    for (const d of [{ ...UN, aged: false }, { ...UN, aged: "yes" }, { ...UN, coid: null }, { ...UN, venue: "x" }, { ...UN, booked: 7 }]) {
      const t = reconcileFrom(beat({ ok: false, diffs: [d] }))!;
      expect(unbookedOf(t)).toEqual([]);
      expect(diffView(t.diffs[0]).canAccept).toBe(false);
      expect(shownOf(t)).toEqual([]);
    }
    expect(queueErrorText(new Error("a difference shown is not one that can be accepted"))).toMatch(/migration 0040/);
  });
});
