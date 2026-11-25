// M10 1.22 (#447): the dashboard's event list is generated from the engine's registry
// (src/kaibot/engine/event_names.py; tests/test_event_names.py fails when this file is stale). These checks keep the
// generated file whole on the dashboard's side: every health event has a severity, and every label is a health event.
import { describe, expect, it } from "vitest";
import { EVENT_LABELS, EVENT_SEVERITY, HEALTH_EVENTS, type EventSeverity } from "./lib/eventNames";

const KINDS: readonly EventSeverity[] = ["info", "warn", "critical"];

describe("generated event names", () => {
  it("gives every health event a known severity", () => {
    for (const name of HEALTH_EVENTS) expect(KINDS).toContain(EVENT_SEVERITY[name]);
    for (const sev of Object.values(EVENT_SEVERITY)) expect(KINDS).toContain(sev);
  });

  it("labels only health events, each listed once", () => {
    for (const name of Object.keys(EVENT_LABELS)) expect(HEALTH_EVENTS).toContain(name);
    expect(new Set(HEALTH_EVENTS).size).toBe(HEALTH_EVENTS.length);
  });

  it("keeps the Health page's text for the safety events", () => {
    expect(EVENT_LABELS.halt).toBe("Halt");
    expect(EVENT_LABELS.reconcile_blocked).toBe("Books differ from the broker: new buys stopped");
    expect(EVENT_SEVERITY.halt).toBe("critical");
    expect(EVENT_SEVERITY.engine_start).toBe("info");
  });
});
