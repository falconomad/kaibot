// Home's quiet lines under the status rows, as ONE lazy chunk (pages/Live.tsx): the market notices (#378) and the
// headline line (migration 0043). One chunk on purpose: the first load names one file here, as it did for the notices
// alone, so adding the headlines costs the first load nothing (size-budget.json is nearly full).
import MarketNotices from "./Notices";
import Headlines from "./Headlines";
import type { Freshness } from "../lib/freshness";

/** `headlines`: show the headline line (a paper run that is not over). Only the owner reaches Home today (every read
 *  is owner-only), so the viewer is the owner; multi-user passes "other" and sees public (SEC) lines only. */
/** `fresh`: how fresh the news feed is (lib/freshness.ts newsFreshness), said on the headline card when not live. */
export default function Lines({ headlines, fresh }: { headlines: boolean; fresh?: Freshness | null }) {
  return (
    <>
      <MarketNotices />
      {headlines && <Headlines viewer="owner" fresh={fresh} />}
    </>
  );
}
