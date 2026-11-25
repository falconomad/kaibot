// Home's quiet lines under the status rows, as ONE lazy chunk (pages/Live.tsx): the market notices (#378) and the
// headline line (migration 0043). One chunk on purpose: the first load names one file here, as it did for the notices
// alone, so adding the headlines costs the first load nothing (size-budget.json is nearly full).
import MarketNotices from "./Notices";
import Headlines from "./Headlines";
import { ErrorBoundary } from "./ErrorBoundary";
import type { Freshness } from "../lib/freshness";
import type { Viewer } from "../lib/headlines";

/** `viewer`: who is looking. Only the owner reaches Home today (main.tsx sends an invited member to their own page and
 *  every read here is owner-only in the database), and the page says the same rule again (#634): the market notices
 *  are for the owner alone, and anyone else would get the public (SEC) headlines only (lib/headlines.ts visibleTo).
 *  `headlines`: show the headline line (a paper run that is not over).
 *  `fresh`: how fresh the news feed is (lib/freshness.ts newsFreshness), said on the headline card when not live. */
export default function Lines({ viewer, headlines, fresh }: { viewer: Viewer; headlines: boolean; fresh?: Freshness | null }) {
  return (
    <>
      {/* #738: one boundary each, so a broken notice row does not take the headlines (or Home) with it */}
      {viewer === "owner" && <ErrorBoundary name="Market notices"><MarketNotices /></ErrorBoundary>}
      {headlines && <ErrorBoundary name="Headlines"><Headlines viewer={viewer} fresh={fresh} /></ErrorBoundary>}
    </>
  );
}
