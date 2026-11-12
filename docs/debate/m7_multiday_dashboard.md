# M7 debate: the dashboard for a multi-day book

Status: **proposal by Design, 12 Nov 2026**, for issue #129 (S89). Not approved. Product reviews it; the stakeholder approves the boards before Frontend builds them (S90).

Written by Design alone. The other teams have not argued it yet: where this note speaks for the engine or for Frontend, it quotes decision 15 or the current code, not a person.

## The ask

Until now Kaibot day-traded and was flat every night, so Home shows today's trades and realised profit. Engine v3 (decision 15) holds a small portfolio for weeks, decides once a day after the close and trades at the next close.

The owner wants to see at a glance:

1. that the engine is healthy;
2. what it holds;
3. how much is invested;
4. how the pot is doing against simply holding the S&P 500 (SPY);
5. what will happen next.

Earlier feedback from the same owner: screens were "too crowded", and a long scrolling list is "too much for lazy users". Simple first, detail on demand.

Inputs: `docs/design/research_m7_multiday_dashboard.md`; decision 15, sections 1, 3, 6 and 7; today's pages in `apps/web` (Home with its Controls card, Trades, Tape, Report, Go / no-go, Engine, Strategy, Run & settings).

## Three layouts

### A. One long Home

Everything on Home, top to bottom: engine state, pot and chart, invested share, the full holdings table, tonight's plan, the evidence. No new pages.

- For: nothing to learn, nothing hidden, no change to the navigation.
- Against: with the sample data Home is more than seven phone screens long. Twelve holdings with six facts each and seven planned orders with their reasons on one page is the "too crowded" screen again. "What happens next" lands far below the fold on every device. This is the tracker-dashboard model (Parqet in the research note).

### B. Home answers, three pages explain (recommended)

Home answers the five questions with one line or one picture each and fits one screen. Each answer links to the page that explains it: **Holdings**, **Next close**, **Evidence**. A fourth view shows one holding. On a phone: four tabs and "More".

- For: all five answers without scrolling, on desktop and on the first phone screen. Each page has one job, so none gets crowded. It matches how expert apps fold detail on phones (IBKR Mobile shows two numbers and a "Details" arrow) and gives "what happens next" a place of its own (as M1 does with "Upcoming Activity").
- Against: three pages and a detail view to build. The holdings list is one tap away from Home. The navigation changes: Trades, Tape and Go / no-go are absorbed (see "Smaller choices").

### C. Broker home: number, chart, then the list

As Trade Republic or Robinhood: the pot, the chart, and the holdings list straight below. The invested share and the next close shrink to two chips above the list. Evidence keeps its own page.

- For: familiar from the broker apps the stakeholder likes. Holdings are on Home.
- Against: the list grows to twenty rows, so Home is a long list again. A broker home is built for someone who acts on each position; Kaibot's owner cannot ("No stock-by-stock control"), so the layout invites checking every stock's gain each day for rules that work over weeks. The research note cites the experiment in which more frequent feedback led to worse results. Engine state and "what next" get the least room, although they are what the owner of an automated system needs first.

## Trade-offs

| | A. One long Home | B. Home answers, pages explain | C. Broker home |
|---|---|---|---|
| All five answers without scrolling, desktop | no: the plan and the evidence are far down | yes | yes, with two of them as chips |
| All five answers on the first phone screen | no | yes | no: pot, chart and the top of the list |
| Length of Home on a phone, sample data | more than 7 screens | under 2 screens | about 3.5 screens |
| Simple first, detail on demand | no | yes | partly |
| Fits "no stock-by-stock control" | neutral | yes | pulls the other way |
| Pages for Frontend | 1 very long page | 3 pages and 1 detail view | 2 pages and 1 detail view |
| Change to the navigation | none | Trades, Tape and Go / no-go are absorbed | Trades is absorbed |

The lengths are measured (B: 1,588 px for a 844 px phone screen) or added up from the same built components (A and C).

## Smaller choices made on the boards

Each of these had an alternative. Product can overrule any of them without touching the layout.

1. **The "exposure dial" is a straight bar**, from "All cash" to "Fully invested", with a white mark for the limit. A round gauge was considered and dropped: a length is read faster than an angle (research note), the bar lines up with the small bars per rule under it, and it fits a phone row.
2. **In every chart range both lines start at the same value**, and one sentence under the chart says who is ahead over that range. The alternative is to zoom into the since-start lines, so the end values never change. The first is fairer for "how did it do this month"; the second has one truth on screen. A question for the stakeholder.
3. **On a phone a holding is two lines and opens in place.** Considered: three-line rows (crowded) and a switch for the right-hand value (one more control to learn).
4. **Where the old pages go.** The list of closed trades becomes the "Sold" tab in Holdings. The tape of decisions becomes "Earlier closes", reached from Next close. Go / no-go becomes Evidence. Report, Engine, Strategy and Run & settings are untouched in this story; they need their own pass when the intraday rule is switched off.
5. **Alarms.** One banner under the header on every page, in plain words, plus the headline of the four checks on Home. A stop never sells the holdings; the banner says so.
6. **Rule names on screen**: "Steady risers" for `trend_momentum`, "12-month winners" for `mom_12_1_band`. Plain names, open to change.
7. **Times**: Berlin first, New York in brackets, as on the v3 boards. The app today prints New York first.
8. **"Flatten and stop" stays where it is today**: in the Controls card at the bottom of Home, with its two-step confirmation.

## Recommendation: B

- It is the only layout that answers all five questions on the first screen of both devices.
- Each page has one job, so the fix for "too crowded" is built into the structure and does not depend on trimming later.
- It suits an owner who watches and does not trade: Home shows state, not a list to act on.
- It gives "what happens next" its own place. A once-a-day engine raises that question more than any other, and today's dashboard has no answer to it.

What it costs: three pages and a detail view, and the holdings list is one tap from Home. To soften that, Home shows how many stocks are held, how many are up and down, and the best and the worst.

## Decisions still open that the screens depend on

These are shown on the boards with sample values. None blocks the review of the layout.

| Open decision | Where it shows | Sample on the boards |
|---|---|---|
| Level limits for multi-day books: the limit on the invested share and the run loss limit (decision 15, section 6; S92) | the white mark on the invested bar; the banner texts | limit 80% |
| The most one stock may be | the size bars in Holdings, the holding view | 10% of the pot |
| How far the safety stop sits | Holdings, the holding view | eight typical daily moves below the price paid |
| The paper pot size (S93). With whole shares only, a small pot cannot buy expensive stocks | "Not buying" on Next close | $5,000: a share above about $260 is skipped |
| Is the pot priced during the day or only at each close | the time printed under the pot, "Today" | priced during the day, time shown |
| Names of the rules | every screen | "Steady risers", "12-month winners" |

## Questions for the stakeholder when approving

1. Is the first screen of Home right: these five answers, in this order?
2. Chart ranges: should both lines start again at the same value in each range, or should a range only zoom in?
3. Are the names "Steady risers" and "12-month winners" clear enough?
4. Evidence: is one sentence per rule plus "Show the checks" the right depth?
5. On a phone the chart sits one swipe below the answers. Fine, or should the chart come first?
6. Should the paper pot be larger, so that fewer stocks are skipped for costing more than one slot?
