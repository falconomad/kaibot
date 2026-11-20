# Dashboard notices: "New on the market" and "Big day"

Issue #378 (M8 · S104). Date: 21 Nov 2026. **Status: proposal.** It needs the stakeholder's design approval (through
Product) before Frontend's build ships. Design for M8: `docs/research/event_driven/design.md`, sections 3.5 and 4.
Brand: `docs/design/brand/brand_system_v1.md` (the name is always lowercase **kaibo**).

## Why

On 19 Nov 2026 the stakeholder should have seen four things and saw none: that a very large company started trading
(Anthropic, ANT), that kaibo knew, what kaibo did about it (nothing, and why), and when its rules could first
consider the stock. The same goes for scheduled days that can move the whole market, such as the Fed's decision.

Two small notices say it in plain words. **They inform only.** They never suggest buying, never show a price, and
nothing in them leads to an order, a setting or a promise of a trade.

## The two notices

| Notice | When | Line on Home (phone) | Sheet |
|---|---|---|---|
| **New on the market** | a new listing (an IPO or a direct listing) found by the listing watch (S102) | "Anthropic (ANT) started trading today" | "kaibo does not buy brand-new listings: they have no price history to judge, and on average they have done worse than the market in their first years. Its rules could first look at ANT in about 12 months." Then the first day of trading, the earliest date for the rule on paper and for the owner's own ticker list, and one line on why those are earliest dates, not a forecast |
| **Big day** | a flag of the market-event alarm (S103): the Fed's decision, the inflation report, several of the largest companies reporting, an index change at the close | "Fed decision at 20:00 your time" | "kaibo is not changing anything today; it is recording what happens on days like this." Then when (in the viewer's time and in New York) and how often days like this come |

Every sheet ends with the same line: **"A notice only informs. kaibo changes nothing because of it."** It has one
button, Close, and no link.

### The words, per kind

The phone line is short so it fits on one line at 390 px; the sheet's title is the full sentence. Times are the
viewer's own (the browser's time zone), with "your time" when a time is shown; New York time is in the sheet.

| Kind | Phone line (today) | Before / after its day | Sheet title |
|---|---|---|---|
| Listing | `{Name} ({SYM}) started trading today` | before: `Tomorrow: {Name} ({SYM}) starts trading`; after: `{Name} ({SYM}) started trading on {Wed, Nov 19}` | the same, as a sentence |
| Fed | `Fed decision at {20:00} your time` | `Tomorrow: Fed decision, {20:00}` | `The Fed announces its decision at {20:00} your time` |
| Inflation | `Inflation report at {14:30} your time` | `Tomorrow: inflation report, {14:30}` | `The US inflation report comes out at {14:30} your time` |
| Largest companies | `{NVDA, AAPL} report results` (at most three names, then "and N more") | `Tomorrow: ...` | `{NVDA, AAPL and MSFT} report their results` |
| Index change | `Index change at the close ({S&P 500})` | `Tomorrow: ...` | `Index funds rebalance at today's close ({S&P 500})` |

Plain-word rules (`design:ux-copy` review): no jargon ("IPO", "FOMC", "CPI", "rebalance" are left out of the phone
line), no exclamation marks, no colour words, no "alert", "warning", "risk" or "opportunity", no verb that asks for an
action. "kaibo does not buy" is the only sentence that names buying, and it says no.

A very large listing is also a big day for the rest of the market (alarm flag `mega_listing`). When both notices are
about the same symbol and session, the dashboard shows **one** row, the listing, and its sheet adds: "kaibo is also
recording how the rest of the market moves today."

## Where they live

**Home, under the status rows and above the pot** (phone and desktop). The order of the rows above the pot is
unchanged: a halt, a caution, the engine, the experiment; the notices come last, because they are the least urgent.

- **One line each.** 48 px tall (a status row is 56), card surface `--surface`, hairline edge `--rule`, an outline
  icon in `--slate`, the line in `--ink` at 15 px / 500, a chevron. On a phone the kind is carried by the icon (a flag
  for a listing, a calendar for a big day) and is in the row's accessible name; on desktop it is also written before
  the line in `--slate` ("New on the market · Anthropic (ANT) started trading today"). A line too long for its row
  ends in an ellipsis; the sheet always has the whole sentence.
- **Never a warning, never an action.** No tint of meaning (not amber, red, green, violet or blue), no dot, no
  pulse, no badge with a count, no `role="alert"` or `"status"`: a screen reader reads it in the page's order and is
  not interrupted. Not the shape of a button (no fill, no pill). The chevron says "more to read", as on every row.
- **Two rows at most.** A third and more fold into one quiet row, "N more market notices", which opens the list.
- **Details on tap:** a bottom sheet on a phone, the same sheet centred on desktop (the app's `Sheet`). Every sheet
  also lists the other notices of the next five sessions ("Coming up", or "Also this week" for listings).
- **Nowhere else.** No notice on Positions, Activity or the stock pages, and no notification: the notices are not
  about the account.

### Which notices are on Home, and when they go

| Notice | Shown on Home | Gone |
|---|---|---|
| Big day | on its session (New York date) | the next day |
| Big day, `major` (the Fed; a very large listing; three or more of the largest companies) | also the session before, as "Tomorrow: ..." | the day after |
| New listing, `major` (named a very large listing by the alarm, or at least $10 billion raised at its offer price) | from the session before, then its first session and the four after it ("started trading on Wed, Nov 19") | after its fifth session |
| New listing, ordinary | not on Home; in the "Also this week" list of the other sheets | after its fifth session |

They go by themselves. There is no dismiss button: it would be one more control, and a notice that is gone by
tomorrow does not need one.

## States

| State | What shows | Why |
|---|---|---|
| Loading | nothing | most days have no notice; a placeholder would flash and vanish and move the pot down and up |
| Nothing in the window | nothing | the normal day |
| Migration 0034 not applied yet (the function does not exist) | nothing | the same as a day without notices; nothing to fix for the stakeholder |
| Could not load | one muted line, "Market notices could not load." and a text button "Try again" | honest, but quiet: it is not about the account |
| One or two notices | one row each | |
| Three or more | two rows and "N more market notices" | |

Board: `mockups/v4/notices/states.png`.

## Mockups

| File | What |
|---|---|
| `mockups/v4/notices/home-listing.png` | Home on a phone, 19 Nov 2026: the ANT row under the experiment |
| `mockups/v4/notices/home-bigday.png` | Home on a phone, 9 Dec 2026: the Fed's decision |
| `mockups/v4/notices/sheet-listing.png` | the ANT sheet |
| `mockups/v4/notices/sheet-bigday.png` | the Fed sheet, with "Coming up" |
| `mockups/v4/notices/states.png` | the states above |
| `mockups/v4/notices/desktop.png` | Home on desktop, with the kind written before the line |

`python docs/design/mockups/v4/notices/gen.py --shot` draws them (brand v1 tokens and Outfit; the pot and holdings
are the brand kit's invented numbers; times as a viewer in Berlin sees them). Each page was checked for horizontal
overflow at its width: none.

## Colour and contrast (dark theme, the one switched on)

| Pair | Use | Ratio (needs) |
|---|---|---|
| `--ink` `#F4F5F7` on `--surface` `#131519` | the line, the sheet's text | 16.8 (4.5) |
| `--slate` `#C0C5CD` on `--surface` | the kind on desktop, the icon, "Coming up" dates, the error line | 10.5 (4.5; 3 for the icon) |
| `--stale` `#A7ADB8` on `--surface` | the chevron, section labels in the sheet | 8.1 (3 for the chevron) |
| `--sea-ink` on `--paper` | "Try again" | 9.2 (4.5) |
| `--rule` `#262A31` on `--paper` | the row's edge | decoration only; the row is also told apart by its surface and its text |

The frontend's colour check (`apps/web/src/notices.test.tsx`) reads these pairs from `tokens.css` and the notices'
stylesheet, so a later token change that breaks them fails the build.

## Accessibility

- Each row is a `button` with the full sentence as its name ("New on the market: Anthropic (ANT) started trading
  today") and `aria-haspopup="dialog"`; the sheet is the app's dialog (focus moves in, Escape closes, focus returns).
- Tap target: 48 px tall, full width. Text never under 13 px.
- Times: `Intl.DateTimeFormat` with the browser's zone, 24-hour where the browser's locale uses it.

## The data (contract for Backend-quant and Frontend)

Read-only, owner-only: `public.kb_market_notices(p_today date default null)` (migration **0034**), returning

```json
{"today": "2026-11-19", "from": "2026-11-13", "to": "2026-11-27",
 "sessions": ["2026-11-19", "2026-11-20", "2026-11-23", "2026-11-24", "2026-11-25", "2026-11-27"],
 "notices": [{"kind": "new_listing", "flag": "ipo", "tier": "major", "session": "2026-11-19", "symbol": "ANT",
              "title": "Anthropic (ANT) starts trading", "starts": null, "first_eligible": "2027-11-19",
              "detail": {"name": "Anthropic", "rules": [{"rule": "trend_momentum", "state": "on paper",
                         "first_eligible": "2027-11-19", "needs_sp500": true}, "..."]}, "source": "publisher"},
             {"kind": "big_day", "flag": "mega_listing", "tier": "major", "session": "2026-11-19", "symbol": "ANT",
              "title": "A very large company starts trading", "starts": null, "first_eligible": null,
              "detail": {"what": ["ANT"]}, "source": "engine"}]}
```

`kind`, `tier`, `symbol`, `title`, `starts` and `first_eligible` are the issue's fields; `flag`, `session`, `detail`
and `source` are added. The window is four sessions back to five sessions ahead (six sessions from today), so the
dashboard can show "started trading on ..." and "Coming up". No price is in it.

Where the rows come from, all on the Mac, none on the trading path:

1. **Listings:** `scripts/publish_notices.py` reads the listing watch's local store (S102) and writes
   `kaibot.market_notice` with the service key. Off unless `KAIBOT_NOTICES=1`; then the nightly daily job runs it
   (03:15 and 07:45 Berlin, after the 00:45 collection). It never fails the daily job.
2. **Big days:** the same script, for today and the next five sessions, from the alarm's pure function (S103),
   once PR #403 is merged; until then it publishes listings only.
3. **Today's big day, as the engine logged it:** the engine's `market_alarm` event (S103, only with
   `KAIBOT_MARKET_ALARM=on`), read by `kb_market_notices` from `kaibot.event`. When the event and a published row are
   the same notice, the event wins.

The engine neither reads nor writes `kaibot.market_notice`; nothing that trades imports the publisher (tests).

## Questions for the stakeholder

1. Is the place right: under the status rows, above the pot? (The alternative was under the holdings, where it
   would be missed on a phone.)
2. Should ordinary new listings ever be on Home, or only very large ones, as proposed?
3. Is "Tomorrow: ..." for major notices wanted, or only notices on their own day?
