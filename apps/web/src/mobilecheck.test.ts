// The mobile check (scripts/mobile_check.py, `npm run check:mobile`) must answer the same whenever it runs. It once
// read the wall clock: Home hides the daily note during market hours (lib/engineevents.showNote), so the check's
// "the note card is there" failed on every run between 09:30 and 16:00 New York and passed at night. Its clock is now
// one of two fixed instants, for the browser and for the mock. This guards that from the source, as routes.test.ts
// guards main.tsx: the check itself needs a browser and is not part of `vitest run`.
import { describe, expect, it } from "vitest";
import check from "../scripts/mobile_check.py?raw";
import mock from "../scripts/stress_mock.py?raw";
import { marketHours } from "./lib/engineclock";
import { isToday, showNote } from "./lib/engineevents";

const instant = (name: string) => {
  const m = new RegExp(`^${name} = dt\\.datetime\\((\\d+), (\\d+), (\\d+), (\\d+), (\\d+), tzinfo=dt\\.UTC\\)`, "m").exec(check);
  if (!m) throw new Error(`mobile_check.py has no fixed instant ${name}`);
  return new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]));
};
const etDay = (d: Date) => new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(d);

describe("the mobile check does not depend on when it runs", () => {
  it("never reads the wall clock", () => {
    expect(check).not.toMatch(/datetime\.now\(|\.today\(\)|time\.time\(/);
    expect(mock).not.toMatch(/datetime\.now\(|\.today\(\)|time\.time\(/);
  });

  it("every page's clock is pinned, and every mock is built at an instant", () => {
    // one place makes a browser context, and it sets the clock
    expect(check.match(/browser\.new_context\(/g)).toHaveLength(1);
    expect(check).toMatch(/ctx = browser\.new_context\(\*\*kw\)\n\s+ctx\.clock\.set_fixed_time\(at\)/);
    // a mock with no instant falls back to the wall clock (mock_data.Mock)
    expect(check).not.toMatch(/Mock\("running"\)/);
  });

  it("OPEN is in market hours and CLOSED after the close of the same New York day, by the app's own rules", () => {
    const open = instant("OPEN"), closed = instant("CLOSED");
    expect(marketHours(open)).toBe(true);
    expect(marketHours(closed)).toBe(false);
    expect(etDay(closed)).toBe(etDay(open));
    // the mock's "today" is the date five hours before UTC: the same day as New York's at both instants
    for (const d of [open, closed]) expect(new Date(d.getTime() - 5 * 3_600_000).toISOString().slice(0, 10)).toBe(etDay(d));
    // so tonight's note shows after the close as "Today", and never while the market is open
    const tonight = { session: etDay(closed), text: "x", source: "model" as const, why: null };
    expect(showNote(tonight, closed)).toBe(true);
    expect(isToday(tonight, closed)).toBe(true);
    expect(showNote(tonight, open)).toBe(false);
  });

  it("the stress run covers the note at its worst: long, the template with its reason, and text with nowhere to break", () => {
    for (const name of ["long", "template", "token"]) expect(mock).toMatch(new RegExp(`"${name}": \\{`));
    expect(check).toMatch(/def stress_notes\(/);
    expect(check).toMatch(/\n {4}stress_notes\(browser, dist, metrics, fails\)/);
  });
});
