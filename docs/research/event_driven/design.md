# Event-driven trading, new listings and the market-event alarm (design for M8)

- **Date:** 19 Nov 2026. **Authors:** Backend-quant with Data. **For:** Product.
- **Status:** a design. Nothing here changes the engine, a rule, a setting or the database.
- **Asked by the stakeholder on 19 Nov 2026:** buying on a fixed weekday "doesn't sound good"; decisions should
  depend on company news and other factors; the engine "missed" the Anthropic listing (ANT), which may also have
  moved other stocks.
- **Milestone:** M8 Event-driven trading (GitHub milestone 9). Stories S95-S106, issues #369-#380 (section 6).
- **Rules this design keeps, unchanged:** paper only. No LLM output sets price, size, stop or exit; a model may
  only veto or halve. One decision core for test and live; go/no-go numbers come only from the engine with
  `SimBroker`. A pre-registration in `docs/prereg/` before a rule's first run, and a trial row for every run.
  Features read only data with `available_ts <= decision_ts`. Free data only, inside `config/sources.yaml`.

---

## Summary for the stakeholder (one page)

**What you said.** Buying on a fixed weekday does not sound right; decisions should depend on company news; and
kaibo missed the Anthropic listing.

**What is true today.** The one rule that trades on paper looks once a week and buys stocks that have risen
steadily for about four months. The weekday is only *when it looks*; it reads no news. Two other rules react to
news, but they only take notes. kaibo's safety rules keep every rule away from a stock with no trading history,
so nothing could have bought Anthropic today, and that part is right. What was wrong: kaibo did not know the
listing was happening and told you nothing.

**What we will try first.** A rule that acts the day after a company reports its results: it buys the companies
whose share price reacted best and holds them for one week. Of the news ideas it has the most recent support in
studies, and it is already written. You run one command on the Mac; about an hour later we have a first honest
answer. Second, a rule that buys after a large jump on heavy trading with real company news behind it, also for
one week. That needs about a week of work first.

**How long until there is a real answer.**

| Step | When |
|---|---|
| First answer on the results rule ("no", or "worth the full test") | the day you run the command |
| First answer on the news-jump rule | about a week later |
| Ten-year verdict through the real engine, for a rule that got that far | one to two weeks after that |
| Proof on paper, for a rule that passes | at least three more months |

The earliest "this works" is about March 2027. The most likely outcome, said plainly: these effects have become
small in big, heavily traded companies. I put the chance that one of the two rules passes at about one in five
(my judgement, not a measurement). If neither passes, we will say so.

**What we add in any case. It changes no trade.** A watch for new listings: kaibo knows about each one, shows it
to you on the day, and says when its rules could first consider it. And a "big day" notice (Fed and inflation
days, the largest companies' results, a very large listing, index reshuffles). At first it only shows a notice
and keeps a record, so we can measure whether such days matter before anything may act on it.

**What we will not do, and why.**

- **Buy new listings early.** Records of more than 9,000 US listings over 45 years: bought at the end of the
  first day, they earned about 20 points less than the market over three years. Large companies with real sales
  did about as well as the market, not better. Anthropic rose 34% between its offer price and its first public
  trade; that gain went to investors who were given shares at the offer price, which kaibo cannot get.
- **Let an AI model pick stocks from headlines.** A model may only block or halve a trade, never start one. It
  also "remembers" how past events ended, so a test on the past would flatter it. If a rule passes, a model may
  watch it for some months, and we measure whether its vetoes would have helped.
- **Switch on a news rule without the ten-year test, or pay for data.**

**What we need from you.** Run one command on the Mac (section 6). Say yes to milestone M8. Tell us whether you
accept "no early buying of new listings". The other decisions are in section 7.

---

## 1. Where we stand today (checked in the code on 19 Nov 2026)

| Fact | Where it is in the repository |
|---|---|
| `trend_momentum` is the only rule that sends paper orders. It decides on the first session of each week that falls on Wednesday or later. Its score is the slope of a **90-session** regression of the price (about four months), times how straight the line is | `strategies/momentum.py`, `docs/prereg/trend_momentum.md` |
| It is a labelled experiment: it failed the ten-year test (Gate B) and can never go to real money | decision 15, section 11 |
| Its universe is the S&P 500 as it was on each day, with price >= $5 and 20-day average dollar volume >= $50M | `engine/live_daily.py` (`mode="pit"`), `engine/daily.py` |
| The twelve-month rule `mom_12_1_band` is **off** | `config/profiles.yaml` |
| Two news-type rules run in shadow, intraday only, on the live list (about 75 symbols by #355; Q2 in section 5 prints the exact number): `news_continuation` and `orb_stocks_in_play`. They write `signal` rows and one `shadow_session_end` event a day. No orders | `engine/shadow.py`, `strategies/news_continuation.py`, `strategies/orb.py` |
| The shadow news rule never needed the empty news table: it asks Alpaca directly whether a headline exists | `engine/shadow.py` `_news_flags`, `engine/alpaca.py` `news_count` |
| `kaibot.news_item` was empty until today; PR #355 (open) adds the poller. `available_ts` is the ingest time | #354, #355, `docs/ops/news.md` |
| The research store on the Mac holds Benzinga headlines for today's 288 research stocks (not for former index members) | `ingest/alpaca_history.py`, `docs/prereg/news_continuation.md` |
| Earnings dates exist point in time: every 8-K with Item 2.02 since 2016 for every company that was in the S&P 500, with the SEC acceptance time to the second. Research store only | `docs/ops/earnings_dates.md` (S85) |
| A one-week post-earnings rule, `post_earnings_week`, is **already pre-registered and built** (13 Nov 2026). No result is recorded in the repository or in any issue | `docs/prereg/post_earnings_week.md`, #112, #191 |
| S&P 500 changes 2016-2026 are stored with their effective dates, not their announcement dates | `ingest/sp500/changes.csv` |
| Fed and inflation dates are stored and already block intraday entries around the Fed statement | `config/macro_calendar.yaml` |
| Nothing collects listing dates. No rule has an event feed | - |

**One correction to how the rule is usually described.** `trend_momentum` is a four-month trend rule, not a
twelve-month one. A stock needs 100 sessions of closes for it (the 100-day average), no single-day move above 15%
in the last 90 returns, and membership of the S&P 500. The twelve-month wait for a new listing comes from the
last condition: S&P requires a company to have traded for 12 months before it can join (section 3.3).

**What "fixed weekday" really is.** The engine asks every rule after every session's close. `trend_momentum`
answers only once a week because the published rule it copies says so: checking daily makes a slow rule trade
more, not better. The weekday is not a belief that Wednesday is a good day to buy. An event rule answers on
whatever day its event happens. PR #366 (open, for #363) will also carry the weekly rule's unfilled buys to the
following sessions.

---

## 2. Part A: event-driven rules

### How an event rule plugs into the engine that exists

Nothing in the engine loop has to change.

```
after each session's close  ->  the rule is asked (Event.AFTER_CLOSE, every session)
                            ->  it looks for events with available_ts <= decision time
                            ->  no event and no holding due to leave: it answers nothing
                            ->  else: a TargetList (what the book should hold after the next fill)
                            ->  portfolio builder (sizes, caps) -> risk gate -> orders near the next close
```

- **The trigger is the event.** The rule is asked every day and acts only when something happened: an earnings
  filing was accepted, a headline-confirmed jump closed, a filing of a given type arrived.
- **The fill is the tested one.** Decide after the close of day T, trade near the close of day T+1 (decision 15;
  since #337 a near-close market order at 15:50 ET).
- **What is new** is a point-in-time event table that the daily context can read. In research it exists for
  earnings today. For live trading it needs a collector with its own `available_ts`; that is built only for a
  rule that has passed (S99).

| Event data | Time of the event | `available_ts` in research | `available_ts` live |
|---|---|---|---|
| 8-K filings (earnings and other items) | SEC acceptance time, to the second | the same | when Kaibot's poller stored it |
| Headlines (Benzinga through Alpaca) | `created_at` | `created_at`; we require it to be at least 15 minutes old | `news_item.available_ts` (ingest) |
| Daily bars | the close | the close plus 15 minutes (free consolidated data is delayed) | the same |
| Listings | first bar in the store | first bar | first sighting by the collector (S102) |

### The candidates, ranked

| # | Candidate | My prior that it survives a ten-year test in large stocks | Cost of the first test | Verdict of this design |
|---|---|---|---|---|
| 2.1 | Post-earnings drift, one-week window | low to moderate: the only window with recent support | **zero**: pre-registered and built | **first** (S96) |
| 2.5 | Large jump on news, one week | low to moderate: good evidence, but mostly in small stocks and on bad news | small: one pre-registration, one rule in the existing harness | **second** (S98) |
| 2.3 | 8-K filing types | moderate for bad-news filings, which a long-only book can only avoid | medium: a data story, then one study | a logged do-not-buy flag at most (S100, S101) |
| 2.2 | Guidance changes | unknown as a separate signal: it arrives with the results | small, as deterministic tags | a tag and a logged feature (S105), not a rule |
| 2.4 | Index additions | very low: the effect has mostly disappeared | medium (announcement dates are not stored) | **not tested**; a calendar entry for the alarm only |

Not assessed here because they were not asked for: analyst revisions, insider buying (Form 4 is already
collected), buyback announcements.

How sure I am of each source is marked: **[checked]** = read on 19 Nov 2026 (section 8), **[repo]** = sourced in
`docs/research/engine_v3/` on 12 Nov 2026 and not re-read today, **[memory]** = from my memory of the literature,
not checked against the paper.

### 2.1 Post-earnings drift

**What it does.** After a company reports results, buy the stocks whose price reacted best and hold them for a
short, fixed time. The rule that exists, `post_earnings_week`: the reaction is the stock's return minus SPY's on
the first session that trades on the report (day 0). If it is positive and in the top fifth of all reactions of
the last 252 sessions, decide after that close, buy at the close of day +1, sell at the close of day +6. At most
10 names at 10% of equity each.

**Evidence.**
- The drift after earnings surprises is one of the oldest anomalies (Bernard and Thomas 1989) **[memory]**.
- It survived costs on paper through 2012: about 0.26 to 0.35% a month, long-short (Novy-Marx and Velikov)
  **[repo]**.
- It has faded since. Martineau: none in large stocks since 2006. Subrahmanyam (2025): not significant once the
  smallest stocks are removed, 2001-2024 **[repo]**.
- The one part that has *strengthened*: trading days +2 to +6 after the report (Kettell, McInnis and Zhao 2022,
  1974-2020, t = 3.92), while the later window declined **[repo]**. The pre-registered rule trades exactly that
  window.
- Ranking on the price reaction instead of the accounting surprise needs no analyst data (Brandt and co-authors)
  **[repo]**.

**Usual failure modes.**
- The profit sits in small, illiquid stocks that Kaibot's liquidity filter excludes, and on the short side.
- "Surprise" against analyst forecasts needs paid data. Free substitutes are late (the accounts arrive weeks
  after the release) or shallow (free calendars give a few quarters). The price reaction is the only timely free
  measure, and it is partly just "the stock went up".
- The big move is the gap on day 0. We enter after it and get only what is left.
- Reports cluster: banks in one week, technology in another. The 40% sector cap and the ten slots then decide
  which trades are taken, so the ranking carries more weight than it seems.

**Free data, and from when.** SEC EDGAR 8-K Item 2.02 acceptance times, from 2016, for every company that was in
the S&P 500 (built, S85). Daily bars from 2016 (built, S84). The point-in-time S&P 500 (built, S83). For live
trading: the SEC current-filings feed (`sec_edgar_atom`, already budgeted) and the nightly Finnhub calendar
snapshots, collected since Nov 2026.

**How it is tested.**
1. **S96:** the existing one-shot (`./scripts/run_experiments.sh oneshots`), research harness, provisional. It
   can fail the rule; it cannot pass it.
2. **S99, only if it did not fail:** the same rule as an engine `Strategy`, through `SimBroker` with cost model
   v2 on the point-in-time universe: Gate B, all six marks.

**What could go wrong.**
- Day 0 is wrong for a report filed during the session, or for a second 2.02 filing. The `report` flag and the
  `session` column of S85 handle the known cases.
- The rule buys what `trend_momentum` sells: the core sells a holding after a one-day move above 15%, which a
  very good report can cause. Two rules, one symbol, opposite orders on the same close (S99 nets them).
- The rule is in cash more than half the time, which matters for how it is judged (section 2.8).

**What would make us drop it.** On the point-in-time S&P 500, any of: mark 1 fails (not positive overall, or
positive in fewer than 6 of 10 years); mark 3 fails (below the 90th percentile of 500 matched random entries);
either fails at 2x costs. Then earnings drift is dropped for large stocks. No second definition of surprise, no
other holding period, no re-run. This reading was written into S96 before anyone saw a result.

### 2.2 Guidance changes

**What it would do.** Buy after a company raises its own forecast; stay out after it cuts.

**Evidence.** Forecasts by management move prices, and the market's response to guidance is well documented
(Anilowski, Feng and Skinner 2007) **[memory]**. Most guidance is now issued together with the quarterly results
("bundled", Rogers and Van Buskirk 2013) **[memory]**. I know of no long-only, large-stock, after-cost test of a
guidance rule, and I did not search for one today.

**Usual failure modes.** Because it is bundled, guidance is not a separate event: the day-0 reaction of 2.1
already contains it. Companies also guide low on purpose so that they can beat the number later.

**Free data, and from when.** There is no free structured history of guidance. It can be read deterministically
from Benzinga headlines (Alpaca news, from 2015), whose wording for guidance is formulaic ("raises FY guidance",
"cuts outlook") **[memory of the format; S105 measures it]**.

**How it is tested.** Not as a rule. S105 writes the keyword tags and measures their error rate on 300
hand-labelled headlines. If 2.1 survives S99, "the report raised guidance" may be **one** pre-declared neighbour
of that rule. Otherwise it stays a logged feature.

**What could go wrong.** A headline is edited after publication and keeps its first timestamp. The 15-minute
minimum age and after-close decisions bound this; the live `news_item` rows remove it going forward.

**What would make us drop it.** Tag precision below 95% on the labelled sample, or 2.1 failing.

### 2.3 8-K event types

**What it would do.** Use the type of an SEC filing as a deterministic event flag. An 8-K says under numbered
items what happened: 2.02 results, 1.01 a material agreement, 5.02 a director or senior officer leaves or joins,
4.01 the auditor changes, 4.02 earlier accounts can no longer be relied on, 2.05 and 2.06 restructuring and
write-downs.

**Evidence.** The market reacts to 8-K items and part of the reaction arrives late (Lerman and Livnat 2010)
**[memory]**. What I remember as best documented is the bad-news side: restatements, auditor changes, late
filings. I did not re-read these papers today and I give no numbers.

**Usual failure modes.** The item numbers are coarse (5.02 covers a chief executive and a junior director alike;
8.01 is "other"). A filing may arrive up to four business days after the event, when the news is already in the
price. And the tradable side of bad news is short, which Kaibot does not do.

**Free data, and from when.** EDGAR lists every 8-K with its items and acceptance time. The files S85 already
downloads contain them; only Item 2.02 rows are kept today. S100 keeps them all: about 2,000 requests at 5 a
second, 2016 onward, former index members included.

**How it is tested.** S101: one pre-declared event study (six groups of items, two windows, twelve looks) on the
point-in-time universe. No simulator: under decision 15 a fast screen may rank ideas, and it decides nothing. A
group that counts becomes a logged "do not buy" feature. Using it to veto would be a new, pre-registered variant
of a rule that already passed.

**What could go wrong.** Twelve looks will show something by chance; hence |t| >= 3.5 and the same sign in 6 of
10 years, declared beforehand. S&P 500 companies rarely restate, so some groups will have too few cases to say
anything.

**What would make us drop it.** No group counts.

### 2.4 Index additions

**What it would do.** Buy a stock when its addition to the S&P 500 is announced, sell when index funds have
bought.

**Evidence.** The average abnormal return of S&P 500 additions fell from 3.4% in the 1980s and 7.6% in the 1990s
to 0.8% in 2010-2020; deletions averaged -0.6% in 2010-2020 (Greenwood and Sammon, "The Disappearing Index
Effect") **[checked]**. Earlier work that found the effect: Shleifer 1986; Harris and Gurel 1986; Chen, Noronha
and Singal 2004 **[memory]**.

**Usual failure modes.** The announcement comes after the close and the price jumps before the next session
opens, where Kaibot does not trade. Specialised desks trade it at scale: Reuters reported on 12 Nov 2026 that two
teams at one hedge fund were said to have made $3.7 billion in June 2026 trading index changes (a Bloomberg
report the fund did not confirm) **[checked]**. What is left for a buyer at the next close is about nothing.

**Free data, and from when.** Effective dates 2016-2026 are stored. Announcement dates are not; they are in S&P's
press releases and would have to be collected by hand.

**New in 2026, and why it does not change the verdict.** Index providers rewrote their rules for very large new
listings. Nasdaq's fast-entry rule lets one join the Nasdaq-100 after 15 trading days if it ranks in the top 40.
ANT is reported to be on track to join at the open on 21 Dec 2026, with the announcement expected after the close
on 11 Dec **[checked]**. S&P decided on 4 June 2026 **not** to change its rules: 12 months of trading and a
public float of at least 10% still apply **[checked]**. Two cases (SpaceX in June, Anthropic now) cannot support a
rule.

**How it would be tested.** A one-shot like the others. **Not planned.**

**What would make us revisit it.** Nothing in sight. The dates go into the alarm's calendar (section 4).

### 2.5 Large jump on news, one week (`news_gap_week`, proposed)

**What it does.** The daily sibling of the shadow rule `news_continuation`, and the candidate closest to what the
stakeholder asked for: the trigger is company news, on whatever day it comes. A stock closes sharply up, on heavy
volume, with a company headline behind the move. Decide after that close, buy at the close of the next session,
hold five sessions.

**Evidence.**
- Large price moves that come with information are followed by drift in the same direction; large moves without
  information reverse (Savor 2012, Journal of Financial Economics; his measure of information is analyst reports)
  **[checked: abstract only, no magnitudes]**.
- Stocks with headlines continue, stocks that moved without headlines reverse (Chan 2003). News days are followed
  by less reversal (Tetlock 2010). Reactions to stale news reverse (Tetlock 2011). News-driven returns measured
  at high frequency continue for days (Jiang, Li and Wang 2021) **[memory]**.
- In all of these, as I remember them, the effect is larger in small and thinly traded stocks and after bad
  news. I know of no long-only, large-stock, after-cost test. The repository's own note says stock-level
  intraday continuation is weak outside the first minutes **[repo]**.

**Usual failure modes.**
- A large company has a headline almost every day, so "has news" may be true nearly always and carry no
  information. The rule therefore needs the jump *and* the volume first.
- It buys a stock just after its volatility rose. Sizing on the 20-session range lags.
- Earnings days are the biggest news jumps. Counted here, the rule would be 2.1 under another name.

**Proposed frozen settings (to be fixed in the pre-registration, S98). No number is new:** each comes from a rule
that is already frozen.

| Setting | Value | Borrowed from |
|---|---|---|
| Universe | point-in-time S&P 500, close >= $5, 20-day average dollar volume >= $50M | every multi-day rule |
| Jump | the stock's close-to-close return minus SPY's >= 1.0 x its 20-session daily sigma | `news_continuation` (move), `post_earnings_week` (minus SPY) |
| Volume | >= 3 x its 20-session average | `news_continuation` |
| News | at least one headline tagged to the company, `created_at` between the previous close and 15 minutes before this close | `news_continuation` |
| Not an earnings day | day 0 of an 8-K Item 2.02 report is excluded | keeps 2.1 and 2.5 separate |
| Trade | decide after the close of the event day, buy at the close of day +1, sell at the close of day +6, no stop | `post_earnings_week` |
| Book | at most 10 names at 10% of equity, largest jump first; 3% cash reserve; level Balanced | `post_earnings_week` |
| Neighbours (4) | `move2` (2.0 sigma), `relvol2` (2 x volume), `hold10` (10 sessions), `info8k` (news = an 8-K other than 2.02 accepted in the window) | - |
| Control (1) | `nonews`: the same jumps with no headline and no 8-K. The rule must clearly beat it | Savor's and Chan's comparison |

**Free data, and from when.** Daily bars from 2016 (built). Benzinga headlines through Alpaca: the archive goes
back to 2015 and the free plan allows 200 requests a minute **[checked]**. The Mac's store has them for today's
288 research stocks only. Former index members have none, so the headline version carries survivorship bias and
cannot meet mark 5. The `info8k` neighbour uses SEC filings, which exist for every former member (after S100),
and is the version that can run on the full point-in-time universe. Whether Alpaca returns old headlines for
delisted tickers is not known; S98 tries five before anyone fetches hundreds.

**How it is tested.** S98: pre-registration first, then one frozen run in the existing one-shot harness (no new
simulator), with the four neighbours and the control. Then S99 if it did not fail.

**What could go wrong.** Alpaca called its news API a "limited-time beta" when it launched and said prices and
limits may change **[checked]**: a rule built on it depends on a free service staying free. The test window
contains 2020 and 2021, when news-driven jumps in large technology stocks were unusually persistent; the
6-of-10-years mark guards against one such period carrying the result.

**What would make us drop it.** The same reading as 2.1 (marks 1, 3 and 4), or a result no better than the
`nonews` control: then the headline adds nothing and the rule is only "buy what jumped".

### 2.6 Classifying news: deterministic first; where an LLM could fit

**Decision of this design: every event rule classifies with numbers, filing types and keyword rules.**

| Deterministic input | Used for |
|---|---|
| The SEC form and item (8-K 2.02, 4.02, ...) and its acceptance time | what happened and when, with no text at all |
| The price reaction and the volume on the day | whether it mattered, in the market's own judgement |
| The count of company headlines in a time window | "there is news behind this move" |
| Keyword tags on headlines (S105: results, guidance up or down, analyst up or down, deal, offering) | the kind of news, with a measured error rate |

**Is an LLM classifier worth it? Not now. Possibly later, as a veto, measured forward only.**

How it would stay inside "veto or halve only":

1. The deterministic rule chooses the names, the sizes and the exits. The model sees only that list.
2. It answers with a fixed contract: `pass`, `halve` or `veto`, a reason code and the evidence it read. The
   contract has no field for a price, a size, a stop or an exit, like `agents/contracts.py` today.
3. A fixed table turns the answer into the action. A property test proves that, whatever the model says, every
   position is at most what the rule alone would hold and no new name appears.
4. Every answer is stored with the model id, the prompt's hash and the time. A replay reads an answer only if it
   existed at the decision time, so test and live stay one code path.
5. When no answer arrives, the rule's own trade goes ahead unchanged: that is the rule that was tested.

How it could be tested without look-ahead. **A model trained after an event may know how the event ended. That is
a real leak, and a backtest cannot remove it.** So:

| Evidence | What it is worth |
|---|---|
| **Forward-only shadow log**, from the day the model version and prompt are frozen: its answer on every candidate of the rule, acted on by nobody | The only clean evidence. Events after the freeze are after the model's training by construction. The cost is time: at 10 to 20 candidates a week, 300 candidates take four to seven months |
| A run over history | **An upper bound, labelled "leaky" in every table.** It can end the idea (no value even with the leak). It can never justify switching the veto on |
| Bounding the leak, 1: the same headlines with company names, tickers and dates removed | Glasserman and Lin (2023) compared named and anonymised headlines. Inside the model's training window the anonymised ones did *better*, which they read as the model's general knowledge of a company distorting its reading of the text more than look-ahead helps it **[checked: abstract]**. So the gap between the two runs measures both effects together, not the leak alone |
| Bounding the leak, 2: events before the model's training cut-off against events after it | If the veto "works" only before the cut-off, it is memory. Only the part after the cut-off is unbiased |
| Bounding the leak, 3: a memory probe | Ask the model directly whether the stock rose in the following week, for events before the cut-off. Accuracy above chance is recall, and its size is the size of the problem |

Other work I rely on from memory: Sarkar and Vafa (2024) show such leakage in pretrained models; Lopez-Lira and
Tang (2023) is the well-known "headlines scored by a chat model predict returns" result that raised the question;
He, Lv, Manela and Wu (2025) train models only on text up to a date, which would be the clean tool if a free one
of useful quality exists **[memory, none checked today]**.

A local sentiment model with fixed weights (the FinBERT entry in `config/sources.yaml`) is treated like an LLM
here: veto or halve only. Its training data are older than most of the test window, which helps with look-ahead,
but it is still a learned language model and not a rule one can read.

S106 holds this plan and is **parked** until an event rule has passed Gate B.

### 2.7 Sizing, exits and the book caps for an event rule

| Topic | What applies | Note |
|---|---|---|
| Size of a position | as pre-registered for each rule. `post_earnings_week` and `news_gap_week` use fixed slots (10% of equity, at most 10 names). The core's risk sizing (shares = equity x 0.1% / 20-session range) would be a different rule under a different name | S99 stops and reports if the portfolio builder cannot express the pre-registered sizing |
| Per-name cap | the level's cap: 10, 15 or 20% of equity, by level. It must hold for the **symbol across all rules**, not per rule | acceptance criterion of S99 |
| Gross and overnight exposure | the level's cap for multi-day books (30, 60 or 100% of equity for the experiment, by level). An event sleeve gets its own share of it, pre-registered. The level limits of multi-day books are the stakeholder's (decision 15, section 6) | section 7, decision 5 |
| Sector cap | 40% of equity per sector, across rules | binds in earnings season; the pre-registration fixes the order (largest reaction first) |
| Number of names | at most 30 in the multi-day book; the event sleeve at most 10 | |
| Cash | the 3% reserve of the builder on the 1% hard floor | unchanged |
| Gap scenario | max(10%, 3 x the 20-session range) on every overnight position, book limit 15% | this is what limits a freshly volatile stock |
| Exits | by rule, at a fixed session (close of day +6). No tight stop (decision 15: tight stops subtract value). The wide protective stop rests at the broker as insurance, as for the core | |
| Earnings while holding | a five-session hold never spans the company's next report | |
| Daily stop, run drawdown end, pause, "Flatten and stop" | unchanged; they act on the whole run | |
| Two rules, one symbol | one net order per symbol and close. Alpaca refuses opposite orders in a symbol | acceptance criterion of S99 |
| Market filter | none pre-registered for the event rules. The core's "no new buys while SPY is below its 200-day average" is the core's own setting | a neighbour at most, never added after a run |

### 2.8 How a part-time rule is judged (an open question for Product)

Gate B judges a rule as a whole account against SPY: mark 2 asks for a yearly return within two points of SPY's
with a much smaller drawdown, or one point above it. An event rule holds cash whenever nothing has happened: I
estimate `post_earnings_week` is invested well under half of the time (S96 reports the real figure). SPY returned
roughly 12 to 13% a year in the test window (+209% by decision 15; my arithmetic). A rule that is in cash most of
the time fails mark 2 however good its signal is.

That is not a reason to loosen the gate. It means the pre-registration must say **what the idle cash does**:

| Option | What it means | Consequence |
|---|---|---|
| A. Cash stays cash (as `post_earnings_week` is registered today) | the rule is its own account | fails mark 2 almost by construction. Could only ever trade as a labelled experiment, like `trend_momentum` |
| B. Idle cash is held in SPY; each event trade swaps a slice of SPY for the stock for a week | the book is always invested, and the rule's whole contribution is "the stock against SPY that week" | a new rule name and pre-registration. SPY must be exempt from the per-name cap inside this book: a risk-rule change (two keys) and a level limit for the stakeholder. All six marks apply unchanged. To pass, the swaps must add about one point a year to SPY after costs |
| C. The event rule shares the book with `trend_momentum` | it uses the cash the core leaves idle | the core failed Gate B; the pair inherits that |

**Recommendation: B**, decided by Product before S99 starts. For S96 and S98, the one-shots stay as registered
(option A) and are read on the marks that do not depend on the cash: 1, 3 and 4. Mark 3 compares the rule with
random entries of the same exposure and holding time, so it is the fair test of the signal itself.

---

## 3. Part B: new listings (IPO watch)

### 3.1 Where a listing date can come from (all free)

| Source | What it gives | When | Limits (checked 19 Nov 2026 unless marked) |
|---|---|---|---|
| **Alpaca assets** (`/v2/assets`) | the symbol, once Alpaca has set it up. For ANT: 05:00 ET on the listing day. No listing-date field that I could find, so the first sighting is our date | the morning of the listing | 200 requests a minute on the free plan; we need one a night |
| **SEC EDGAR** | S-1 and S-1/A (the plan and the price range), 8-A12B (exchange registration, shortly before listing), 424B4 (final prospectus after pricing: offer price, shares, lock-up terms). The daily form index lists them with the filing time | weeks ahead (S-1); the night after pricing (424B4) | at most 10 requests a second, with a declared User-Agent; the daily index is rebuilt nightly from about 22:00 ET. Quarterly indexes go back decades: that is the history |
| **Nasdaq Trader** notices and symbol directory | the symbol and the first trading date. For ANT the notice was dated 11 Nov for a 19 Nov start: eight days ahead | days ahead | static files; one request a second is ample. NYSE publishes similar notices **[memory]** |
| The first daily bar in our own store | the listing date for history | after the fact | already there |

**Recommendation.** For history: EDGAR's quarterly indexes (424B4) joined to the first daily bar. Live: the Alpaca
list compared night by night, EDGAR's daily index for the advance notice and the offer price, Nasdaq's notice as
a cross-check. S102 builds it as a shadow collector that the engine never reads.

Things that look like a new listing and are not: ticker changes, spin-offs, direct listings (no offer price),
funds, units, warrants, rights, preferred shares, and a ticker reused by a different company. S102 must classify
them; an acceptance criterion counts false alarms over 30 nights.

### 3.2 The history each rule needs (from the code)

| Rule | What a stock needs before the rule can consider it |
|---|---|
| `trend_momentum` (on paper) | S&P 500 membership on the day; 100 closes (100-day average; the score uses 90); no one-day move above 15% in the last 90 returns; close >= $5; 20-day average dollar volume >= $50M (at least 15 sessions) |
| `mom_12_1_band` (off) | S&P 500 membership; 273 sessions (twelve months plus one) |
| `news_continuation` (shadow) | in the live list (the seed universe or the owner's own tickers); 21 sessions of minute bars |
| `orb_stocks_in_play` (shadow) | in the live list; 15 sessions; average volume >= 1,000,000 shares; a 14-session range above $0.50 |
| The owner's own tickers | 60 sessions (the beta and volatility windows), dollar volume >= $50M, price >= $5 (`engine/tickercheck.py`) |
| `post_earnings_week`, `news_gap_week` | S&P 500 membership; then the first qualifying event |

So the gate that matters for the two multi-day rules is **index membership**, not the count of sessions.

### 3.3 When ANT is first eligible for what

Session dates come from the repository's own NYSE calendar. Facts about the listing are from the sources in
section 8.

| Date | What |
|---|---|
| 11 Nov 2026 | Nasdaq publishes the listing date. Kaibot has no collector for it |
| 18 Nov 2026 | priced at $83 a share, above the $70-78 range |
| 19 Nov 2026 | first trade at 11:48 ET at $111.50, 34% above the offer price and 2 hours 18 minutes after the opening bell. Before that first trade Alpaca accepts only whole-share limit orders |
| about 23 Nov 2026 | options expected to start (Alpaca: about two business days after) |
| 10 Dec 2026 | 15th session: enough for the shadow opening-range rule, if ANT were in the live list |
| 18 Dec 2026 | 21st session: enough for the shadow news rule, if ANT were in the live list |
| 21 Dec 2026 | reported entry into the Nasdaq-100 at the open: funds that track the index must buy around that date |
| 17 Feb 2027 | 60th session: ANT could pass the check for the owner's own ticker list |
| Feb-March 2027 (date unknown) | first results as a listed company. This would be the first event for an event rule **only if that rule's universe included ANT**. As pre-registered (S&P 500 members) it does not |
| 15 Apr 2027 | 100th session: `trend_momentum`'s own arithmetic is satisfied. Index membership is not |
| about 18 May 2027 | 180 days: most existing shares leave their lock-up from here, in stages (365 days for the founders and some large holders) |
| 19 Nov 2027 | 252nd session and 12 months of trading: the **earliest** day S&P's rules allow ANT into the S&P 500, and so into `trend_momentum`'s universe. S&P also asks for a public float of at least 10%; the offering sold about 7% of the shares. So later is likely |
| 21 Dec 2027 | 273rd session: enough for the twelve-month rule, which is off |

About the size. The brief I was given says a market value of about $2 trillion. The sources I read give about
$1.1 trillion at the offer price and, by my arithmetic (about 13.2 billion shares x $111.50), about $1.5 trillion
at the first trade. I did not find a closing price. The difference changes nothing in this design.

**Short answer.** An event rule could consider ANT sooner than `trend_momentum` only if its universe were wider
than the S&P 500. With the universe both rules are registered on, both wait until ANT is in the index: November
2027 at the earliest. Widening a universe is a rule change: a new name, a new pre-registration, new trials. It is
not proposed here.

### 3.4 A separate "seasoned IPO" rule, against the evidence

The idea: buy a new listing after N sessions if conditions hold (for example it is above its first-day close, or
its lock-up has passed).

**The evidence is against trading IPOs early, and I do not find support for a "buy after N sessions" rule.**

Jay Ritter's tables, updated in 2026, for 9,253 US IPOs from 1980 to 2024 **[checked]**:

| Three-year return, measured from the first day's close | IPOs | Against the market | Against similar companies |
|---|---|---|---|
| All IPOs | +19.1% | **-20.5 points** | -8.9 points |
| Companies with sales under $100 million | +5.3% | -34.3 points | -17.9 points |
| Companies with sales of $1 billion and more | +32.4% | -2.1 points | +0.6 points |

| Period after the listing (first-day return left out) | IPOs | Companies matched on size and book-to-market |
|---|---|---|
| First six months | +4.9% | +4.3% (about level) |
| Second six months | 0.0% | +4.0% (**the gap opens here**) |
| First year | +5.6% | +8.4% |
| Second year | +5.8% | +12.6% |

What this says:
- The average first-day return of 18.9% is earned between the offer price and the first close. A buyer in the
  market does not get it. Measured from the offer price, IPOs roughly match the market over three years; measured
  from the first close they lag it by 20 points.
- The damage is in months 7 to 24 and in small companies with little revenue. Large companies with real sales
  are about level with the market: **no edge in either direction.**
- When lock-ups end, the price falls on average: a three-day abnormal return of about -1.5% over 1,948 lock-ups,
  larger for venture-backed companies, with trading volume up about 40% for good (Field and Hanka 2001)
  **[checked: abstract]**. For a long-only book that is a reason to be out, not a way in.
- The one positive pattern I remember: a rise when the banks' analysts start coverage, about 25 days after the
  listing (Bradley, Jordan and Ritter 2003) **[memory]**. It is old, short, and belongs to the first weeks when
  nothing can be sized responsibly.
- Trillion-dollar listings: there are two (SpaceX in June 2026, Anthropic now). Reuters reported that SpaceX
  rose sharply on its third day, when options began trading, and drifted lower after its first 15 days
  **[checked]**. Two cases are an anecdote.

Other reasons not to trade the first weeks:
- **No history.** No range, no volatility, no average volume: the numbers Kaibot sizes every position with do not
  exist. The first session of ANT began at 11:48.
- **The broker.** Alpaca's page for ANT says limit orders only before the first trade, no margin for 30 days and
  no short selling at launch **[checked]**. That page is about live accounts; it does not say how the paper
  simulation treats a first day. Paper already filled Kaibot's closing orders badly (#333).
- **Our own gate.** Three or four large listings a year cannot fill a ten-year test.

**Verdict: no IPO rule.** If the stakeholder wants the number for large listings anyway, one small study would do
it (IPOs since 2016 worth $10 billion or more, return against SPY from session 60 to session 252, one trial).
The published table says the answer will be "about the same as the market". It is not in the plan.

### 3.5 What we build instead

1. **Know.** A point-in-time listing calendar (S102): planned, priced, first session.
2. **Show.** A notice on the day, in plain words, with the date from which each rule could consider the stock
   (S104). For ANT: "kaibo's rules could first look at it in about 12 months".
3. **Record.** `listing_age_sessions` as a feature, so that any later rule can exclude young listings by rule.
4. **Use it for the market.** A very large listing is a "big day" for everything else (section 4).

**Free data, and from when.** Section 3.1. EDGAR's quarterly indexes reach back to the 1990s **[memory]**; our
daily bars start in 2016, so the listing history is built from 2016.

**How it is tested.** There is no rule here, so nothing goes through the engine. The collector has its own
point-in-time test (deleting every later input never changes what it reports for a date) and an isolation test
(the engine cannot import it). If a rule on young listings were ever wanted, it would be a daily rule with
`listing_age_sessions` as its trigger: a pre-registration, the one-shot harness, then the engine with
`SimBroker`, like every other rule.

**What could go wrong.** False alarms from ticker changes and funds. A listing that slips (the notice gives an
expected date; ANT was first expected as early as 16 Nov). Offer prices that cannot be read reliably from a prospectus:
the collector leaves them empty and never guesses.

**What would make us drop it.** More than a handful of false "new listings" a month after the classification is
tuned on the first 30 nights: then the Alpaca comparison is dropped and only EDGAR and the exchange notices stay.

---

## 4. Part C: the market-event alarm

### What it does

A read-only daily signal for sessions on which one very large, scheduled event can move a sector or the whole
index. At first it does **nothing to orders**. It produces:

1. one `market_alarm` event per session, written before the open, with the flags and the inputs behind them;
2. a logged feature on every decision of that session, in live and in the replay;
3. a notice on the dashboard (S104).

### The flags (deterministic, each known before the session opens)

| Flag | Set when | Data | Roughly how often |
|---|---|---|---|
| `fed_day` | a scheduled Fed statement is due in the session | `config/macro_calendar.yaml` (exists; the engine already blocks intraday entries around it) | 8 a year |
| `cpi_day` | the inflation report is due before the open | the same file | 12 a year |
| `megacap_report` | one of the ten largest S&P 500 companies has reported since the previous close, or is due before the next open | SEC 8-K Item 2.02 times for history; the nightly calendar snapshot live | up to 40 sessions a year, clustered |
| `index_reshuffle` | an S&P quarterly rebalance, the Russell reconstitution or the Nasdaq-100 annual change takes effect at this session's close | a dated list kept like the Fed dates | 5 or 6 a year |
| `mega_listing` | the first session of a listing worth at least $100 billion at its offer price, and its dated follow-ups: options start, index entry, lock-up end | S102 | two or three in the last ten years |

Two tiers. `major`: `fed_day`, `mega_listing`, or three or more megacaps reporting into the same session.
Everything else is `notice`. The thresholds are frozen in a pre-registration (S103) before the history is looked
at.

**Free data, and from when.** Fed and inflation dates: in the repository from 2016 to 2027. Earnings times: SEC
filings from 2016 (S85). Index dates: to be collected for 2016 to 2027 from the index providers' own schedules
(S103). Listings: from 2016 (S102). Share counts for "ten largest": SEC filings, point in time.

**How it runs through the engine.** The flag is a pure function of dated inputs. The live engine and the
`SimBroker` replay call the same function and log the same feature, and a test proves that adding it changes no
order in any replay (a diff of zero). Its value is then read from the replay, as below.

For 19 Nov 2026 the alarm would have said, from inputs dated 11 Nov: **major: `mega_listing` ANT.** And for
21 Dec, as a follow-up of the same flag: ANT enters the Nasdaq-100 (as reported).

### Can a very large listing move other stocks?

Plausibly, through three channels. Index funds must make room: Reuters cited an estimate that index-tracking funds
would take about 28% of the Anthropic shares on offer, about $19 billion **[checked]**, paid for by selling a
little of everything else they hold. Investors sell similar companies to buy the new one; studies find that
competitors lag after a large IPO in their industry (Hsu, Reed and Rocholl 2010) and that large IPOs weigh on the
prices of correlated stocks (Braun and Larrain 2009, in emerging markets) **[memory]**. And listed companies that
own a stake are re-priced on the new market value: Amazon and Google were reported to hold about 15% and 14% of
Anthropic **[checked]**.

How large any of this was on 19 Nov I do not know: I found no measurement, and one day cannot separate it from
everything else that moved prices. Hence: record first.

### How its value is measured before it may gate anything (S103)

On ten years of history, with flags rebuilt point in time, flagged sessions against all others:

1. the size of SPY's daily move;
2. the daily profit and loss of the `trend_momentum` ten-year `SimBroker` run.

Four flags (`fed_day`, `cpi_day`, `megacap_report`, `index_reshuffle`) times two measures: eight comparisons,
declared beforehand, each logged as a trial. `mega_listing` has two or three cases and is shown as a list.

Only if a comparison is clear (|t| >= 3) may someone **propose** a gate. A gate is a rule change: a new variant
under a new name ("no new buys on major days"), its own pre-registration, its own Gate B. One caution from the
literature, from memory: average market returns have been *higher* on days with scheduled macro announcements
(Savor and Wilson 2013) and ahead of Fed statements (Lucca and Moench 2015). A gate that stays out on those days
may give up return for calm.

The other possible route is a tighten-only owner switch ("no new buys on big days"), which needs no proof of
profit because it only reduces risk. It would make the paper experiment differ from its tested replay, so it is
the stakeholder's call and it is not proposed now.

### What could go wrong

- **Too many alarms.** As drafted, about one session in four carries some flag. That is why there are two tiers,
  and why S103 tightens `major` before reading any result if it covers more than one session in ten.
- **Hindsight.** Calling a day "big" after seeing it. Every flag is computed from inputs dated before the open
  and written before the session; the test deletes later inputs and checks the answer does not change.
- **A stale calendar.** The index dates are a hand-kept list. A stale list loses notices and blocks nothing.
- **"Ten largest" needs share counts.** They come from SEC cover pages, point in time. If that cannot be built,
  the fallback (the ten highest dollar volumes) is declared before the run.

### What would make us drop it

No comparison reaches |t| >= 3. Then the notice may stay if the stakeholder finds it useful, and no gate is ever
proposed from it.

---

## 5. Evidence we already have: the shadow rules

I queried nothing in production. Product runs the four read-only queries below (story S97, #371) and posts the
output. They are plain `SELECT`s. I checked their syntax and logic on a local Postgres 16 with made-up rows shaped
like the engine's: a restart that writes a second summary, a signal the gate sized to zero, a run with a
different pot.

What the engine writes (`engine/shadow.py`): at the end of each session one `kaibot.event` row with
`type = 'shadow_session_end'` and the payload

```json
{"day": "2026-11-18",
 "orb_stocks_in_play": {"signals": 4, "sized": 3, "pnl_usd": 12.3, "avg_r": 0.21},
 "news_continuation":  {"signals": 1, "sized": 1, "pnl_usd": -4.0, "avg_r": -0.35},
 "cost_ms": {"setup": 900.1, "p50": 3.2, "max": 40.0, "symbols": 75}}
```

and, per would-be trade, one `kaibot.signal` row with `mode = 'shadow'` whose `features.counterfactual` holds the
result of that trade (`r_multiple`, `pnl_usd`, `reason`).

### Q1. One row per rule: sessions, would-be trades, mean R, daily result

```sql
with ends as (            -- one summary per session; if the engine restarted, the latest one wins
  select distinct on ((payload->>'day')::date)
         (payload->>'day')::date as day, id, run_id, payload
  from kaibot.event
  where type = 'shadow_session_end'
  order by (payload->>'day')::date, id desc
),
per_rule as (
  select e.day, e.run_id, r.amount_usd, s.rule,
         coalesce((e.payload->s.rule->>'signals')::int, 0)     as signals,
         coalesce((e.payload->s.rule->>'sized')::int, 0)       as sized,
         coalesce((e.payload->s.rule->>'pnl_usd')::numeric, 0) as pnl_usd,
         (e.payload->s.rule->>'avg_r')::numeric                as avg_r
  from ends e
  left join kaibot.run r on r.id = e.run_id
  cross join (values ('news_continuation'), ('orb_stocks_in_play')) as s(rule)
  where e.payload ? s.rule
)
select rule,
       count(*)                                             as sessions,
       min(day)                                             as first_day,
       max(day)                                             as last_day,
       sum(signals)                                         as signals,
       sum(sized)                                           as sized_trades,
       count(*) filter (where sized > 0)                    as sessions_with_a_trade,
       round(sum(sized)::numeric / count(*), 2)             as trades_per_session,
       round(sum(avg_r * sized) / nullif(sum(sized), 0), 3) as mean_r_per_trade,
       count(*) filter (where pnl_usd > 0)                  as up_sessions,
       count(*) filter (where pnl_usd < 0)                  as down_sessions,
       round(avg(10000 * pnl_usd / nullif(amount_usd, 0)), 2)         as mean_daily_bp_of_pot,
       round(stddev_samp(10000 * pnl_usd / nullif(amount_usd, 0)), 2) as sd_daily_bp_of_pot,
       round(avg(pnl_usd / nullif(amount_usd, 0))
             / nullif(stddev_samp(pnl_usd / nullif(amount_usd, 0)), 0)
             * sqrt(count(*))::numeric, 2)                  as t_daily,
       case when sum(sized) > 0
            then ceil(900.0 * count(*) / sum(sized)) end    as sessions_for_900_trades_at_this_rate
from per_rule
group by rule
order by rule;
```

### Q2. The same data day by day (gaps, restarts, the change of pot between runs)

```sql
with ends as (
  select distinct on ((payload->>'day')::date)
         (payload->>'day')::date as day, id, ts, run_id, payload
  from kaibot.event
  where type = 'shadow_session_end'
  order by (payload->>'day')::date, id desc
)
select e.day, e.run_id, r.amount_usd as pot_usd, s.rule,
       (e.payload->s.rule->>'signals')::int     as signals,
       (e.payload->s.rule->>'sized')::int       as sized,
       (e.payload->s.rule->>'pnl_usd')::numeric as pnl_usd,
       (e.payload->s.rule->>'avg_r')::numeric   as avg_r,
       (e.payload->'cost_ms'->>'symbols')::int  as symbols_watched
from ends e
left join kaibot.run r on r.id = e.run_id
cross join (values ('news_continuation'), ('orb_stocks_in_play')) as s(rule)
where e.payload ? s.rule
order by e.day, s.rule;
```

### Q3. Per would-be trade (the exact R of each trade, not the daily average)

```sql
with sig as (             -- one signal per rule, symbol and session; after a restart the latest row wins
  select distinct on (strategy, symbol, (ts at time zone 'America/New_York')::date)
         strategy as rule, symbol, (ts at time zone 'America/New_York')::date as day, features
  from kaibot.signal
  where mode = 'shadow'
    and strategy in ('news_continuation', 'orb_stocks_in_play')
  order by strategy, symbol, (ts at time zone 'America/New_York')::date, id desc
),
t as (
  select rule, symbol, day,
         (features->'counterfactual'->>'r_multiple')::numeric as r,
         features->'counterfactual'->>'reason'               as exit_reason
  from sig
  where jsonb_typeof(features->'counterfactual') = 'object'
    and features->'counterfactual'->>'r_multiple' is not null
),
ranked as (
  select t.*, row_number() over (partition by rule order by r desc) as best_rank
  from t
)
select rule,
       count(*)                                             as trades,
       count(distinct day)                                  as sessions_with_a_trade,
       count(distinct symbol)                               as symbols,
       round(avg(r), 3)                                     as mean_r,
       round(stddev_samp(r), 3)                             as sd_r,
       round(avg(r) / nullif(stddev_samp(r), 0) * sqrt(count(*))::numeric, 2) as t_per_trade,
       round(100.0 * count(*) filter (where r > 0) / count(*), 1)             as win_pct,
       count(*) filter (where exit_reason = 'stop')         as stopped_out,
       count(*) filter (where exit_reason = 'time')         as held_to_1555,
       round(max(r), 2)                                     as best_r,
       round(min(r), 2)                                     as worst_r,
       round(sum(r), 2)                                     as total_r,
       round(coalesce(sum(r) filter (where best_rank > 5), 0), 2) as total_r_without_best_5,
       case when avg(r) <> 0 and count(*) > 1
            then ceil(power(3 * stddev_samp(r) / abs(avg(r)), 2)) end as trades_for_t3_if_this_mean_were_true
from ranked
group by rule
order by rule;
```

### Q4. Health of the record: sessions without a shadow summary, and shadow errors

```sql
with started as (
  select distinct (ts at time zone 'America/New_York')::date as day
  from kaibot.event where type = 'session_start'
),
ended as (
  select distinct (payload->>'day')::date as day
  from kaibot.event where type = 'shadow_session_end'
)
select 'session without a shadow summary' as finding, s.day, null::text as detail
from started s left join ended e using (day)
where e.day is null
union all
select 'shadow error', (ts at time zone 'America/New_York')::date,
       (payload->>'where') || ': ' || (payload->>'error')
from kaibot.event
where type = 'shadow_error'
order by 2, 1;
```

### How many sessions before the numbers mean anything

The paper engine has run for about a week or two. Honest thresholds, written before anyone has seen the output:

| What the output shows | What it means |
|---|---|
| Fewer than **60 sessions**, or fewer than **50 sized trades** for a rule | Nothing about an edge, for or against. It shows the plumbing works: signals are found, sized by the risk gate, and closed out |
| 50 trades or more | The rule can be **killed** (t at or below -2: `config/gate.yaml`, "50 trades can only kill"). It still cannot pass |
| **900 trades** over at least 3 months **on unchanged code**, t above 3, above 95% of 1,000 random entries, daily Sharpe above SPY's | The intraday bar of `config/gate.yaml`. Q1 prints how many sessions that takes at the present rate |

Why 900. With a spread of about 1 R per trade, an average of +0.1 R needs (3 x 1 / 0.1)² = 900 trades to stand
three standard errors clear of zero. A smaller edge needs far more: +0.05 R needs 3,600. Q3 prints this number
for the mean it finds, which is a reading aid and not a forecast.

My estimate of the wait, until Q1 gives the real rate: on about 75 symbols the opening-range rule should size a
few trades a session, so 900 trades take roughly one to two years. The news rule asks for a rare combination
(fresh news, a move of one sigma, three times the usual volume, a pullback that holds) and may size less than one
trade a session: several years.

Three more reasons the live log cannot be the verdict:

- **Trades on one day move together.** `t_per_trade` treats them as independent and flatters. `t_daily` uses one
  number per session and is the honest one. On daily numbers even a strong rule (a yearly Sharpe of 2) needs
  about a year to reach t = 2, and an ordinary one (Sharpe 1) about four.
- **The clock keeps restarting.** The gate counts a rule's trades only on unchanged code, and the fingerprint
  covers `src/kaibot/engine/*.py`. With engine merges almost daily, the 900-trade count has barely begun.
- **Dollars do not add up across runs.** The pot changed between runs, so dollar sums across them mean little.
  Read R and basis points. `mean_daily_bp_of_pot` divides by the run's starting amount; the shadow book is sized
  on the day's starting equity, so it is close, not exact.

**So the verdict on the two shadow rules comes from their ten-year tests (S96), which are pre-registered and
built. The live shadow log is a check that live behaves like the test.**

---

## 6. Staged plan

Ordered so that the cheapest test of the most promising idea comes first. Teams work in parallel: Data starts
S100 and S102 the same night Backend-quant starts S96 to S98.

| Order | Story | Issue | Team | What it answers | New code | Pre-registration | New trials |
|---|---|---|---|---|---|---|---|
| 0 | S95 this design | #369 | backend-quant, data | - | none | - | 0 |
| 1 | **S96 run the pre-registered post-earnings week (and the shadow rules' ten-year tests)** | #370 | backend-quant | is there anything left of earnings drift in large stocks? keep or delete the shadow rules | **none** | exists (13 Nov 2026) | 0 new (declared in M7) |
| 2 | S97 shadow results to date | #371 | product, backend-quant | does the shadow plumbing work; how long would 900 trades take | none | - | 0 |
| 3 | S98 `news_gap_week` | #372 | backend-quant, data | does a headline behind a jump matter in large stocks | one rule in the existing harness | **to write:** `docs/prereg/news_gap_week.md` | 6 |
| 4 | S99 Gate B through the engine with `SimBroker` | #373 | backend-quant, qa-ops | the go/no-go for a rule that did not fail | the rule as an engine strategy; an event feed for research | **to write:** one per rule that goes on (`<rule>_on_spy` if option B) | up to 6 per rule, at most 12 |
| 5 | S100 every 8-K with its items | #374 | data | - (data) | extends the SEC extract | - | 0 |
| 6 | S102 new-listing watch | #376 | data | - (data); kaibo knows about listings | a shadow collector | - | 0 |
| 7 | S103 market-event alarm, read-only | #377 | data, backend-quant | do "big days" differ, measured on ten years | a pure flag function, a log line, a report | **to write:** `docs/prereg/market_alarm_study.md` | 8 |
| 8 | S101 8-K filing flags | #375 | backend-quant | is any filing type worth a do-not-buy flag | one study script | **to write:** `docs/prereg/filing_flags_study.md` | 12 |
| 9 | S105 deterministic headline tags | #379 | agents-ai | can guidance and the kind of news be read by rule, and how often wrongly | a pure function and a labelled sample | - | 0 |
| 10 | S104 dashboard notices | #378 | design, frontend, backend-quant | the stakeholder sees listings and big days | design, one read function, one migration | - | 0 |
| 11 | S106 LLM veto, forward only (**parked**) | #380 | agents-ai, backend-quant | would a model's veto have helped | only after a rule passes | pre-registered comparison before the first verdict | 1 (2 with the leaky historical run) |

### What the stakeholder runs on the Mac

Exact commands are in each issue. The one that exists today:

```bash
cd ~/Projects/kaibot && git pull
.venv/bin/python scripts/trial_registry.py count      # the trial count before
./scripts/run_experiments.sh oneshots                 # S96: finished rules are skipped; 1 to 1.5 hours if none has run
.venv/bin/python scripts/trial_registry.py count      # the trial count after
cat data/reports/experiments_oneshots_summary.md
```

Run it outside 15:30-22:00 Berlin. The engine can stay on: the command reads only the history under `data/`.

Commands that the stories themselves deliver (they do not exist yet):

| Story | Command | About |
|---|---|---|
| S98 | `./scripts/run_experiments.sh oneshots` (the new rule joins the list) | a few minutes |
| S99 | `./scripts/run_experiments.sh events_engine` | measured and printed by the run |
| S100 | `./scripts/fetch_earnings_dates.sh --all-items` | 10-15 minutes, about 2,000 SEC requests |
| S101 | `.venv/bin/python scripts/filing_flags_study.py --start 2017-05-01` | minutes |
| S102 | `.venv/bin/python scripts/fetch_listings.py --since 2016-01-01`, then nightly `scripts/collect_alt.py --env ~/Projects/kaibot-secrets/.env --source listings` | a few minutes; one extra source in the nightly job |
| S103 | `.venv/bin/python scripts/market_alarm_report.py --start 2016-01-01`, and an engine restart after the merge | minutes |

Waiting on the stakeholder besides the runs: design approval and "apply" for the one migration of S104.

### Trial-count cost under "every trial is counted"

| Story | Configurations added to the registry |
|---|---|
| S96 | 0 new. Its configurations were declared in M7 (S72). If they have never run they are logged now, as declared: for `post_earnings_week` the base, two exposure caps and four neighbours |
| S98 | 6 (base, four neighbours, the no-news control) |
| S99 | 1 base and at most 5 neighbours per rule that goes on: at most 12 |
| S101 | 12 (six groups x two windows) |
| S103 | 8 (four flags x two measures) |
| S106 | 1, or 2 with the historical run |
| **M8 in all** | **at most about 40** |

Three honest notes:

- The cells of S101 and S103 are comparisons, not rule configurations. Decision 15 counts configurations. I log
  them anyway, one row each, so that any later proposal built on them carries the cost of the search. Product may
  rule that they need not count; the conservative reading is that they do.
- Gate v2 shows the deflated Sharpe and does not use it as a pass mark, and the registry already holds thousands
  of configurations from the retired dip rule (8,286 by decision 15's count). Forty more move that number very
  little. The discipline that matters is the other one: **one frozen run per idea, at most five neighbours, and
  no second attempt under another definition.**
- I could not read the current count: the registry lives on the Mac. S96 posts it before and after.

---

## 7. Decisions needed

| # | Decision | Who | Recommendation |
|---|---|---|---|
| 1 | Approve milestone M8 as scoped here | stakeholder (new milestones are scope) | yes |
| 2 | Run the S96 command on the Mac | stakeholder | this week |
| 3 | "No early buying of new listings; watch and notify instead" | stakeholder | accept. The evidence is against it (section 3.4) |
| 4 | How a part-time event rule is judged: what the idle cash does (section 2.8) | Product, before S99 | option B: idle cash in SPY, as a new pre-registered rule; Gate B unchanged |
| 5 | The exposure share of an event sleeve in the multi-day book, and SPY as its cash holding | stakeholder (level limits of multi-day books) | only when a rule has passed Gate B |
| 6 | Whether S101's and S103's comparisons count as trials | Product | count them |
| 7 | Keep event rules on the S&P 500 universe, or widen it so that a large new listing can enter after about a year without waiting for the index | Product, then stakeholder | keep it. Widening is a new rule |
| 8 | Whether the alarm may ever become a tighten-only switch ("no new buys on big days") | stakeholder | not now; measure first |
| 9 | Design of the two notices; "apply" for the migration of S104 | stakeholder | when S104 is ready |
| 10 | API credit for a model veto | stakeholder | none now. S106 is parked until a rule passes |

---

## 8. Sources, and how sure I am

### Checked on 19 Nov 2026 (read today)

| Claim | Source |
|---|---|
| Alpaca news: history back to 2015, Benzinga only, 200 calls a minute on the free plan, launched as a "limited-time beta" whose terms may change | [Alpaca, Introducing News API](https://alpaca.markets/blog/introducing-news-api-for-real-time-fiancial-news) |
| Alpaca free plan: 200 historical requests a minute; recent consolidated data delayed 15 minutes | [Alpaca, About Market Data API](https://docs.alpaca.markets/us/docs/about-market-data-api.md) |
| A reported gap in Alpaca's 2015-2016 news turned out to be the user's paging error | [Alpaca forum](https://forum.alpaca.markets/t/historical-news-data-missing-for-2016/13576) |
| SEC EDGAR: at most 10 requests a second, a declared User-Agent, daily indexes rebuilt nightly from about 22:00 ET, quarterly indexes | [SEC, Accessing EDGAR Data](https://www.sec.gov/os/accessing-edgar-data) |
| Nasdaq's notice of 11 Nov 2026: Anthropic Class A, symbol ANT, first trading day 19 Nov 2026 | [Nasdaq Data Technical News 2026-28](https://nasdaqtrader.com/TraderNews.aspx?id=DTN2026-28) |
| ANT at Alpaca: symbol set up at 05:00 ET on the listing day; limit orders only before the first trade; most new listings open between 10:00 and 12:00 ET; no margin for 30 days; no short selling at launch; options about two business days later | [Alpaca, Anthropic Trading Availability and FAQs](https://docs.alpaca.markets/us/docs/anthropic-trading-availability-and-faqs) |
| ANT priced at $83; first trade $111.50 at 11:48 ET; 980 million shares sold; lock-up terms; about $1.1 trillion at the offer price | [StockTitan live page, read at its 12:06 ET update](https://www.stocktitan.net/articles/anthropic-ipo-live-ant-first-trading-day-2026) |
| Nasdaq-100 fast entry (15 trading days, top 40); ANT expected to join at the open on 21 Dec 2026, announced after the close on 11 Dec | [StockAnalysis, 17 Nov 2026, citing Bloomberg](https://stockanalysis.com/news/anthropic-nasdaq-100-funds-december-21/) |
| Index funds about 28% of the offered shares (about $19 billion, an estimate); SpaceX's third-day rise and later drift; the reported $3.7 billion from index trades | [Reuters via Khaleej Times, 12 Nov 2026](https://www.khaleejtimes.com/business/from-spacex-to-anthropic-markets-have-a-new-playbook-for-mega-share-debuts) |
| S&P kept the 12-month wait and the 10% float rule (4 June 2026) | [Axios](https://www.axios.com/2026/06/04/musk-spacex-ipo-sp-investors) |
| Amazon about 15% and Google about 14% of Anthropic after the IPO (Bloomberg, as reported) | [PitchBook live updates](https://pitchbook.com/anthropic-ipo-live-updates) |
| IPO returns 1980-2024 (all figures in section 3.4) | [Jay Ritter, long-run returns on IPOs, tables updated 2026](https://site.warrington.ufl.edu/ritter/files/IPOs-long-run-returns-on-IPOs.pdf) |
| S&P 500 additions: 3.4%, 7.6%, then 0.8%; deletions -0.6% | [Greenwood and Sammon, The Disappearing Index Effect](https://www.hks.harvard.edu/centers/mrcbg/programs/growthpolicy/disappearing-index-effect) (abstract) |
| Named against anonymised headlines; look-ahead and "distraction" | [Glasserman and Lin, arXiv 2309.17322](https://arxiv.org/abs/2309.17322) (abstract) |
| Drift after price shocks with information, reversal without | [Savor 2012](https://ideas.repec.org/a/eee/jfinec/v106y2012i3p635-659.html) (abstract; no magnitudes) |
| Lock-up expiry: -1.5% over three days, 1,948 lock-ups, volume up about 40% | [Field and Hanka 2001](https://ideas.repec.org/a/bla/jfinan/v56y2001i2p471-500.html) (abstract) |

Most of these pages were read through a tool that summarises them, so a figure may be off in a detail. Anything
that becomes a setting of a rule is checked again when its pre-registration is written.

### From the repository's earlier research (12 Nov 2026, with its own sources; not re-read today)

Post-earnings drift: Novy-Marx and Velikov; Martineau; Subrahmanyam (2025); Kettell, McInnis and Zhao (2022);
Brandt and co-authors. See `docs/research/engine_v3/notes_multi_day_strategies.md`, section 3. Intraday news
continuation: `docs/research/engine_v3/notes_intraday_overnight_strategies.md`, section 4.

### From memory, not checked against the paper today

Bernard and Thomas (1989); Chan (2003); Tetlock (2010, 2011); Jiang, Li and Wang (2021); Lerman and Livnat
(2010); Anilowski, Feng and Skinner (2007); Rogers and Van Buskirk (2013); Shleifer (1986); Harris and Gurel
(1986); Chen, Noronha and Singal (2004); Bradley, Jordan and Ritter (2003); Hsu, Reed and Rocholl (2010); Braun
and Larrain (2009); Savor and Wilson (2013); Lucca and Moench (2015); Sarkar and Vafa (2024); Lopez-Lira and Tang
(2023); He, Lv, Manela and Wu (2025). I am confident these papers exist and say roughly what I attribute to
them. I give no numbers from them, and none of them is a reason to skip a test.

### What I do not know

- Whether the one-shot tests of M7 have ever run on the Mac. Nothing in the repository or the issues records a
  result. S96 settles it.
- The current trial count. It lives on the Mac.
- How many shadow sessions and signals exist. S97 answers it.
- ANT's closing price and market value on its first day, and what the listing did to other stocks.
- Whether Alpaca's paper simulation handles a stock on its first day. No rule would try.
- Whether Alpaca's news archive returns headlines for delisted tickers. S98 tries five.
- The size of the one-week drift after earnings in large stocks today. The published result is a trend with a
  t-statistic, not a return I could size a rule on. That is what S96 measures.

---

## Results

Empty on 19 Nov 2026. S96 (#370) and S97 (#371) add their numbers here, each with its date, the code version and
the pre-registration's sha256.

### S97 (#371): the shadow rules to date

How to get the numbers: `DATABASE_URL=... ./scripts/shadow_results.sh` runs section 5's four queries
(`scripts/shadow_results.sql`, the same text, kept the same by `tests/test_shadow_results_sql.py`) in a session the
server keeps read-only, and prints Q1-Q4 in order. They can also be pasted one by one into the Supabase SQL editor.
The test runs every query on a scratch Postgres with rows shaped like `engine/shadow.py`'s and checks each number
against a hand calculation.

**19 Nov 2026, Product (a read-only query on production, one summary per session, latest wins; posted on #371):**

| Rule | Sessions | First | Last | Would-be trades | Result on paper | Up days | Down days |
|---|---|---|---|---|---|---|---|
| news_continuation | 4 | 13 Nov | 18 Nov | 1 | -$321.69 | 0 | 1 |
| orb_stocks_in_play | 4 | 13 Nov | 18 Nov | 17 | +$523.50 | 1 | 2 |

Backend-quant, 21 Nov 2026, reading that output: four sessions for each rule. `news_continuation` had 1 would-be
trade, or 0.25 per session; `orb_stocks_in_play` had 17, or 4.25 per session. At these rates, 900 trades on unchanged
code (the intraday bar of `config/gate.yaml`) take about 3,600 sessions for `news_continuation` (some 14 years) and
about 212 sessions for `orb_stocks_in_play` (some 10 months). Reaching 50 trades, from which a rule can be killed but
not passed, takes about 196 more sessions for `news_continuation` and about 8 more for `orb_stocks_in_play`. Both
counts restart with each engine change, and engine merges are daily at present. Q2 to Q4 have not been run yet, so I
cannot say whether there are sessions without a summary or shadow errors; Product's table shows one summary for each
of the four sessions. Nothing here says anything about an edge, for or against: both rules are far below every
threshold in section 5. Their verdict comes from S96's ten-year tests. The dollar results are left unread on purpose:
the pot changed between runs (section 5).

Next: Product runs `./scripts/shadow_results.sh` and posts the four outputs unchanged on #371; they go in here with
their date and the engine's `code_sha`.
