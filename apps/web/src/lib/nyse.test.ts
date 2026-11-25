import { describe, expect, it } from "vitest";
import { COVERED_UNTIL, EARLY_CLOSES, HOLIDAYS, closeMin, hm, isTradingDay, sendFromMin, sendUntilMin, tradingDayFrom } from "./nyse";

describe("lib/nyse: the NYSE calendar of 2026-2027", () => {
  it("Thanksgiving, 26 Nov 2026, is closed; the next session is Fri 27 Nov", () => {
    expect(isTradingDay("2026-11-26")).toBe(false);
    expect(tradingDayFrom("2026-11-26")).toBe("2026-11-27");
  });
  it("Fri 27 Nov 2026 is an early close: orders go out at 12:50 ET, the window ends 12:57, the market closes at 13:00", () => {
    expect(isTradingDay("2026-11-27")).toBe(true);
    expect(hm(closeMin("2026-11-27"))).toBe("13:00");
    expect(hm(sendFromMin("2026-11-27"))).toBe("12:50");
    expect(hm(sendUntilMin("2026-11-27"))).toBe("12:57");
  });
  it("Christmas Eve 2026 closes at 13:00 ET; Christmas Day is closed; the next session is Mon 28 Dec", () => {
    expect(isTradingDay("2026-12-24")).toBe(true);
    expect(hm(sendFromMin("2026-12-24"))).toBe("12:50");
    expect(isTradingDay("2026-12-25")).toBe(false);
    expect(tradingDayFrom("2026-12-25")).toBe("2026-12-28");
  });
  it("an ordinary day: 15:50 ET send, 16:00 close; weekends are closed", () => {
    expect(hm(sendFromMin("2026-11-25"))).toBe("15:50");
    expect(hm(closeMin("2026-11-25"))).toBe("16:00");
    expect(isTradingDay("2026-11-28")).toBe(false);
    expect(tradingDayFrom("2026-11-28")).toBe("2026-11-30");
  });
  it("2027: Christmas observed Fri 24 Dec, Independence Day Mon 5 Jul, early close Fri 26 Nov", () => {
    expect(isTradingDay("2027-12-24")).toBe(false);
    expect(isTradingDay("2027-07-05")).toBe(false);
    expect(isTradingDay("2027-12-31")).toBe(true);
    expect(hm(closeMin("2027-11-26"))).toBe("13:00");
  });
  it("every listed day is a weekday inside the covered years", () => {
    for (const d of [...HOLIDAYS, ...Object.keys(EARLY_CLOSES)]) {
      expect([0, 6]).not.toContain(new Date(`${d}T12:00:00Z`).getUTCDay());
      expect(d <= COVERED_UNTIL).toBe(true);
    }
  });
  it("the table still covers the next month (extend it with next year's NYSE dates when this fails)", () => {
    const inAMonth = new Date(Date.now() + 31 * 86_400_000).toISOString().slice(0, 10);
    expect(inAMonth <= COVERED_UNTIL).toBe(true);
  });
});
