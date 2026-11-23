// #378 (M8 S104): "New on the market" and "Big day" on Home (docs/design/m8_market_notices.md). One calm row each (a
// line that wraps to a second on a narrow phone, never cut),
// details in a sheet. They inform only: no link, no order, no setting, no price, never a warning colour or a live
// region. Lazy (pages/Live.tsx, through components/Lines.tsx): this file, lib/notices.ts and notices.css are in that
// chunk, not first load.
import { useQuery } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { MIN, poll } from "../lib/poll";
import {
  BIG_DAY_WHY, HOW_OFTEN, KIND_LABEL, LISTING_WHY, ONLY_INFORMS, type Notice, type Notices, clock, dayName, eligibleRows,
  fetchNotices, homeNotices, merged, noticeLine, untilText, when,
} from "../lib/notices";
import { Sheet } from "./Sheet";
import { NAME } from "../brand/brand";
import "./notices.css";

const MAX_ROWS = 2;

const Icon = ({ kind }: { kind: Notice["kind"] }) => (
  <svg className="nt-ico" width="20" height="20" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d={kind === "new_listing" ? "M5 21V4m0 0h11l-2 4 2 4H5"
      : "M7 3v3m10-3v3M4 8h16M5 5h14a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z"} />
  </svg>
);
const Chev = () => <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M9 6 L15 12 L9 18" /></svg>;

/** Home's notices: nothing while loading, on a day without notices or before migration 0035; a quiet line on error. */
export default function MarketNotices({ tz }: { tz?: string }) {
  const q = useQuery({ queryKey: ["market-notices"], queryFn: fetchNotices, staleTime: 10 * MIN, ...poll(30 * MIN) });
  if (q.isError) return (
    <p className="nt-err">Market notices could not load. <button type="button" className="link" onClick={() => { void q.refetch(); }}>Try again</button></p>
  );
  if (!q.data) return null;
  return <NoticeRows data={q.data} tz={tz} />;
}

export function NoticeRows({ data, tz }: { data: Notices; tz?: string }) {
  const rows = homeNotices(data);
  if (!rows.length) return null;
  const shown = rows.slice(0, MAX_ROWS), rest = rows.length - shown.length;
  return (
    <div className="nt-rows" aria-label="Market notices">
      {shown.map((x) => <NoticeRow key={`${x.kind}-${x.flag}-${x.session}-${x.symbol ?? ""}`} n={x} data={data} tz={tz} />)}
      {rest > 0 && <MoreRow data={data} tz={tz} count={rest} />}
    </div>
  );
}

function NoticeRow({ n, data, tz }: { n: Notice; data: Notices; tz?: string }) {
  const [open, setOpen] = useState(false);
  const line = noticeLine(n, data, { tz });
  return (
    <>
      <button type="button" className="nt-row" aria-haspopup="dialog" aria-label={`${KIND_LABEL[n.kind]}: ${line}`} onClick={() => setOpen(true)}>
        <Icon kind={n.kind} />
        <span className="nt-text"><span className="nt-kind">{KIND_LABEL[n.kind]} · </span>{line}</span>
        <Chev />
      </button>
      <Sheet open={open} onClose={() => setOpen(false)} title={noticeLine(n, data, { tz, full: true })}>
        <NoticeSheetBody n={n} data={data} tz={tz} />
      </Sheet>
    </>
  );
}

function MoreRow({ data, tz, count }: { data: Notices; tz?: string; count: number }) {
  const [open, setOpen] = useState(false);
  const label = `${count} more market notice${count === 1 ? "" : "s"}`;
  return (
    <>
      <button type="button" className="nt-row more" aria-haspopup="dialog" onClick={() => setOpen(true)}>
        <Icon kind="big_day" /><span className="nt-text">{label}</span><Chev />
      </button>
      <Sheet open={open} onClose={() => setOpen(false)} title="Market notices">
        <NoticeList data={data} tz={tz} />
        <p className="nt-only">{ONLY_INFORMS}</p>
      </Sheet>
    </>
  );
}

/** Every notice of the window that is not past, one line each with its day. */
export function NoticeList({ data, tz, except }: { data: Notices; tz?: string; except?: Notice }) {
  const items = merged(data).filter((x) => x.session >= data.today || (x.kind === "new_listing" && x.session >= data.from))
    .filter((x) => !except || !(x.kind === except.kind && x.flag === except.flag && x.session === except.session && x.symbol === except.symbol));
  if (!items.length) return null;
  return (
    <ul className="nt-list">
      {items.map((x) => (
        <li key={`${x.kind}-${x.flag}-${x.session}-${x.symbol ?? ""}`}><span className="muted">{dayName(x.session)}</span>
          <span>{noticeLine(x, data, { tz, list: true })}</span></li>
      ))}
    </ul>
  );
}

export function NoticeSheetBody({ n, data, tz }: { n: Notice; data: Notices; tz?: string }) {
  const others = <NoticeList data={data} tz={tz} except={n} />;
  const listing = n.kind === "new_listing";
  const ny = n.starts ? clock(n.starts, "America/New_York") : null;
  return (
    <div className="nt-sheet">
      <p className="nt-kick"><Icon kind={n.kind} />{KIND_LABEL[n.kind]}</p>
      {listing ? (<>
        <p>{LISTING_WHY}</p>
        {n.first_eligible && <p>Its rules could first look at {n.symbol} {untilText(n.session, n.first_eligible)}.</p>}
        <div className="kv nt-kv">
          <span>First day of trading</span><span className="v">{dayName(n.session)}{when(n, data) === "next" || when(n, data) === "later" ? " (expected)" : ""}</span>
          {eligibleRows(n).map(([k, v]) => <Fragment key={k}><span>{k}</span><span className="v">{v}</span></Fragment>)}
        </div>
        <p className="muted">The earliest dates, not a forecast. Trend momentum looks only at S&amp;P 500 companies, and a new company can join the index after
          12 months of trading at the earliest.</p>
        {n.alsoBigDay && <p className="muted">{NAME} is also recording how the rest of the market moves {when(n, data) === "today" ? "today" : "on its first day"}.</p>}
      </>) : (<>
        <p>{BIG_DAY_WHY}</p>
        {HOW_OFTEN[n.flag] && <p className="muted">{HOW_OFTEN[n.flag]}</p>}
        <div className="kv nt-kv">
          <span>When</span><span className="v">{dayName(n.session)}{n.starts ? ` · ${clock(n.starts, tz)} your time` : ""}</span>
          {ny && <><span>In New York</span><span className="v">{ny} ET</span></>}
        </div>
      </>)}
      {others && <><h3 className="nt-h">{listing ? "Also this week" : "Coming up"}</h3>{others}</>}
      <p className="nt-only">{ONLY_INFORMS}</p>
    </div>
  );
}
