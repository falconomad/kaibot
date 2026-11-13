import { describe, expect, it, vi } from "vitest";
import { Route } from "react-router-dom";
import type { ConfigResp } from "../lib/config";
import { FineTune } from "./FineTune";
import { Strategy, StrategyHome } from "./Strategy";
import { boom, ctxFor, paperRun, glitches, hasAlert, hasStatus, render, replayRun, text, zeros, type Seed } from "./testkit";

vi.mock("../lib/api", () => import("./testkit-api"));

// /strategy and /strategy/fine-tune share one layout route (Strategy): it owns the config query, so its loading and
// error states are the same for both pages (FineTune.test.tsx covers /strategy/fine-tune the same way).
const key = ["config", 1];
/** A run that never saved or applied a setting: the page starts from the level's defaults. */
const fresh = { level: "balanced", bounds: {}, themes: {}, universe: [], applied: null, pending: null, draft: null, versions: [] } as unknown as ConfigResp;

const at = (path: string, seeds: Seed[] = [], ctx = ctxFor(paperRun())) => render(<Strategy />, {
  path, route: "/strategy", seeds, ctx,
  children: <><Route index element={<StrategyHome />} /><Route path="fine-tune" element={<FineTune />} /></>,
});

for (const [name, path, heading] of [["Strategy", "/strategy", "Strategy"]] as const) {
  describe(`${name} page states`, () => {
    it("loading: the shaped skeleton, no numbers", () => {
      const html = at(path);
      expect(hasStatus(html)).toBe(true);
      expect(html).toContain('aria-label="Loading your setup"');
      expect(zeros(html)).toEqual([]);
      expect(text(html)).not.toMatch(/trades\/mo|\$/);
    });

    it("empty: nothing saved yet, so the level's defaults show with nothing to apply", () => {
      const html = at(path, [{ key, data: fresh }]);
      const t = text(html);
      expect(html).toMatch(new RegExp(`<h1[^>]*>${heading}</h1>`));
      expect(t).toContain("Balanced");
      expect(t).toContain("Same as now");
      // Fine-tune's exclude editor has a role="status" live region, so "not loading" means no busy skeleton here.
      expect(html).not.toContain('aria-busy="true"');
      expect(glitches(html)).toEqual([]);
    });

    it("error: says the settings aren't available, with the server's message, and that trading goes on", () => {
      const html = at(path, [{ key, error: boom("kb_get_config does not exist") }]);
      const t = text(html);
      expect(hasAlert(html)).toBe(true);
      expect(t).toContain("Strategy settings aren't available");
      expect(t).toContain("kb_get_config does not exist");
      expect(t).toContain("Kaibot keeps trading on its level's defaults meanwhile.");
    });

    it("error: an empty answer from the server is treated as an error, not as defaults", () => {
      const html = at(path, [{ key, data: null }]);
      expect(hasAlert(html)).toBe(true);
      expect(text(html)).toContain("no data");
    });

    it("empty: a replay has nothing to tune", () => {
      const html = at(path, [], ctxFor(replayRun({ id: 1 })));
      expect(text(html)).toContain("This is a replay on past data, so there is nothing to tune.");
      expect(hasStatus(html)).toBe(false);
    });
  });
}
