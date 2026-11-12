# Research: how brokers, robo-advisers and portfolio apps show holdings, how much is invested and performance against an index (M7 dashboard)

Date: 2026-11-12. For issue #129 (S89), Engine v3 (decision 15).

Question: Kaibot moves from day trades to a small portfolio held for weeks. How do established products show what is held, how much is invested, how the money is doing against an index, and what an automated system will do next, on desktop and on a phone, for people who do not want to study a screen?

Method: public help pages, product pages and reviews, read on 12 Nov 2026. No accounts were opened, so screens are described from the vendors' own help texts and from reviews. Where the source is a review and not the vendor, the table says so. Links are at the end.

## What they do

| Product | Pattern | What we take |
|---|---|---|
| **Robinhood** (broker app) | A "Position" block on each stock page with six facts: shares, market value, average cost, "portfolio diversity" (the share of the portfolio in this stock), today's return, total return. Chart ranges from 1 day to 5 years. Press and hold to read one point. [1] | Six facts is the right size for one holding. We keep size, share of the pot, today and total, and add the two only an engine has: why it was bought and what would sell it. Press and hold becomes one read-out line above the chart. |
| **Robinhood Strategies** (managed portfolios) | Three chart views behind icons: overall return (a green line after fees, with a grey line for the money paid in), an allocation pie, a return breakdown. "Compare" shows time-weighted returns against a benchmark blended from the S&P 500 and a bond index in the same proportions as the portfolio. "Insights" sit just below the chart: returns, why the positioning changed, market updates, written to "anticipate questions". [2] [3] | Explanations sit next to the numbers, as sentences. We put the reason on every planned order and one sentence under the chart. The blended benchmark is noted, not copied (see "Patterns to avoid"). |
| **Trade Republic** (reviews, not the vendor) | 2022: the portfolio as a pie, by asset class or by position, sortable by size or by gain in % or €. [4] 2023 redesign: everything on screen got bigger. A review calls it an improvement that made things worse; commenters say important figures are now hidden, uninvested cash left the headline total, the list sorts by one value only, and charts lost their labels. [5] 2026: for an ETF, a "Position" section under the chart lists each purchase with its value and gain. [6] | The stakeholder likes this style: one big number, one chart, a list. The complaints are our checklist: cash is part of the pot total, the chart keeps a labelled axis, the list can be sorted. |
| **Interactive Brokers** (IBKR Mobile, PortfolioAnalyst) | The phone portfolio tab shows two numbers on top (daily P&L, market value). Net liquidity, margin figures and realised and unrealised P&L sit behind a "Details" arrow. Holdings sort by any column, and "Manage Columns" picks the columns. [7] [8] PortfolioAnalyst compares against more than 300 benchmarks and shows current against target allocation. [9] | Even the most expert broker shows two numbers first on a phone and folds the rest. We do the same. We do not copy the column picker: Kaibot's owner should not have to configure a table. |
| **Wealthfront** | The dashboard shows a time-weighted return (the one it recommends) and a money-weighted return, and refuses to show a simple return. [10] Its blog argues that the S&P 500 is a misleading yardstick for a diversified portfolio: "apples to oranges". [11] | Say on the screen what the comparison is. Kaibot's pot has no deposits or withdrawals during a run, so "same start, same amount", in dollars, is both simple and correct. |
| **Betterment** | Performance, then "Portfolio returns", then "Add comparison", where the user picks a stock and bond mix as the benchmark. Help texts ask for fair comparisons (same risk, same time frame) and warn against judging on a few months. [12] [13] | A comparison needs its time frame stated. We print one sentence under the chart for the chosen range. We show the invested share beside the comparison, because a pot that is half in cash moves about half as much as the index. |
| **Composer** (rule-based strategies; the closest product to Kaibot) | Trades once a day in a window just before the close (3:45 to 4:00 PM New York). Its help says that trading nearer the close tracks its tests more closely, because the tests use end-of-day prices. [14] [15] A strategy page has a line chart against chosen benchmarks (SPY, QQQ) with range chips, a table of statistics (cumulative and annual return, Sharpe, alpha, beta) and a marker for the "OOS start date", the day the strategy was last edited. [16] [17] | The same rhythm as Engine v3, so the same need: nothing happens during the day, and the screen says when the next trade time is. Their page leads with a table of statistics; ours leads with a sentence. The marker between tested history and live results is an idea for the Report page later. |
| **M1 Finance** | Orders run in scheduled "trade windows", not instantly. [18] A queued sale shows as a pending trade under "Upcoming Activity" until the next window, and can be cancelled before it. [19] | A named place for "what happens at the next window". Ours is "Next close". Kaibot is autonomous, so there is no button per order; the two controls it has today (pause, flatten and stop) stay. |
| **Public.com** (news report) | Web portfolio: sort by name, price, daily change, all-time change or share of holdings; a comparison with index ETFs for a chosen period; an allocation view. [20] | Three sort orders are enough for 10 to 20 holdings: biggest, best gain, longest held. |
| **eToro** (press release) | 2025 portfolio tools: breakdown by asset type and sector, which holdings raise the risk score, expected dividends, comparison with a benchmark such as the S&P 500. [21] | Confirms that "against the S&P 500" is now standard in retail apps. |
| **Parqet, Finanzfluss Copilot, Portfolio Performance** (portfolio trackers) | Parqet's dashboard opens with value, performance, allocation, a chart of the worst falls, dividends and benchmark charts, then return figures, activity, best and worst holdings and the full list below. [22] Copilot adds the benchmark as a second line in another colour; hovering shows a guide line and one box with the date and both values; its help says both lines must start at the same point. [23] Portfolio Performance sets both series to zero at the start of the chosen period. [24] | A second line, the same start, one read-out for both values: that is our chart. Parqet's dashboard is the "everything on one page" model the debate note argues against. |

## What design and accessibility guidance adds

- **A dashboard is read at a glance.** Nielsen Norman Group: one view, little interaction; lengths and positions are read most accurately; gauges are harder to read than straight bars; pies and donuts are best avoided. [25] So the "exposure dial" is a straight bar with a mark for the limit.
- **Few things first, the rest one step away.** Progressive disclosure works when the first screen holds the right few items and the way to the rest is obvious. [26]
- **Tables on phones.** Keep columns legible without zooming, use accordions for grouped data, and cut the data itself. [27] So a holding on a phone is two lines and opens in place.
- **Status needs more than colour.** Carbon asks for colour plus shape or symbol and warns that more than five or six indicators overwhelm; a group takes the colour of its most serious member. PatternFly: text, colour and an icon together. [28] [29] So the engine state is one headline over four checks, each with an icon and words.
- **Steps with words.** Government task lists label each step "Completed", "Incomplete" or "Cannot start yet". [30] So each gate says "Passed", "46 of 60 days" or "Not started".
- **WCAG.** Colour may not be the only carrier of meaning (1.4.1, level A). Pointer targets are at least 24 by 24 px (2.5.8, level AA); the v3 design system already asks for 44 on phones. [31] [32]
- **How often people look matters.** In a well-known experiment the investors who got the most frequent feedback took the least risk and earned the least. [33] A multi-week engine should not lead with today's wiggle. Today's change is on Home, as asked, but smaller than the figures since the start, and Home has no ticking price per stock.

## Patterns worth borrowing

1. One big number for the pot, a line chart under it, and the index as a second line from the same start (Trade Republic, Copilot, Portfolio Performance).
2. One sentence that states the comparison and its period (Betterment, Wealthfront).
3. Two or three numbers first, the rest behind one tap (IBKR Mobile).
4. A position block with a handful of fixed facts (Robinhood).
5. A named place for what happens at the next trade time (M1's "Upcoming Activity", Composer's trading period).
6. Reasons written as sentences next to the numbers (Robinhood Strategies insights).
7. Sorting by size, gain and time held (Public, Trade Republic).
8. Status as icon plus words, grouped under the most serious item (Carbon, PatternFly).

## Patterns to avoid

1. **Everything on one long page** (Parqet's dashboard). It is thorough and it is what the stakeholder called "too crowded".
2. **A total that leaves the cash out** (the Trade Republic complaint). The pot is cash plus stocks, always.
3. **Charts without axis labels** (same review).
4. **Round gauges and pies for one ratio** (NN/g). A straight bar reads faster and lines up with other bars.
5. **A table of statistics as the first thing** (Composer's strategy page: alpha, beta, Sharpe). Plain sentences first; statistics stay on the Report page.
6. **Column pickers and saved table layouts** (IBKR). That is expert tooling.
7. **A blended or self-assembled benchmark** (Robinhood Strategies, Betterment). It is the fair choice for a mixed portfolio, but the stakeholder asked for "against simply holding SPY", and a benchmark that needs explaining is not simple. We show the invested share next to the chart instead.
8. **Buttons per order** (M1's cancel). Kaibot has no stock-by-stock control and should not suggest it.
9. **Red and green alone.** Every gain or loss carries an arrow and a sign.
10. **Live prices ticking on the home screen.** It invites checking a weekly engine every minute.

## What we could not check

- Trade Republic's own help pages redirected to a general support page, so its screens are described from reviews [4] [5] [6].
- Composer's help pages do not say whether a list of the coming trades is shown before the trading period. We found no such preview and did not assume one.
- Nobody used the apps hands-on. Anything about feel or speed is out of scope.

## Sources

1. [Robinhood: viewing stock details](https://robinhood.com/us/en/support/articles/viewing-stock-detail-pages)
2. [Robinhood Strategies charts](https://robinhood.com/us/en/support/articles/strategies-charts)
3. [Robinhood Strategies insights](https://robinhood.com/us/en/support/articles/strategies-insights)
4. [broker-test.at: Trade Republic shows the portfolio as a pie chart (16 Nov 2022)](https://www.broker-test.at/news/trade-republic-zeigt-das-portfolio-nun-auch-als-kreisdiagramm/)
5. [mobiflip: "Verschlimmbessert", Trade Republic's new design (15 Sep 2023)](https://www.mobiflip.de/?p=441392)
6. [Finanztip: what is new in the Trade Republic app (21 Apr 2026)](https://finanztip.de/daily/neues-feature-bei-trade-republic-was-in-der-app-neu-ist/)
7. [IBKR guides: the Portfolio screen on a phone](https://ibkrguides.com/android/portfolio.htm)
8. [IBKR Campus: monitor your account and portfolio (IBKR Mobile)](https://www.interactivebrokers.com/campus/trading-lessons/monitor-your-account-and-portfolio/)
9. [Interactive Brokers: PortfolioAnalyst features](https://www.interactivebrokers.com/en/portfolioanalyst/features.php)
10. [Wealthfront: how the return on the dashboard is calculated](https://support.wealthfront.com/hc/en-us/articles/209353386-How-do-you-calculate-the-return-displayed-on-my-dashboard-and-account-pages)
11. [Wealthfront blog: how to benchmark a portfolio (2016)](https://www.wealthfront.com/blog/benchmark-investments-portfolio-performance/)
12. [Betterment: the pitfalls of comparing portfolio returns (updated 28 Jan 2026)](https://www.betterment.com/resources/sp-500-benchmark)
13. [Betterment: what are time-weighted returns?](https://betterment.com/help/time-weighted-returns)
14. [Composer: trading period](https://help.composer.trade/article/63-trading-period)
15. [Composer: how does Composer trade?](https://help.composer.trade/article/65-how-does-composer-trade)
16. [Composer: how symphonies work](https://help.composer.trade/article/214-6-how-composer-symphonies-work)
17. [Composer: a public strategy page ("SPY and Chill")](https://www.composer.trade/trading-strategies/spy-and-chill-OV3ohHkJ4tKA6HqqSfle)
18. [M1: how M1 trades](https://help.m1.com/en/articles/9331916-how-m1-trades)
19. [M1: how to cancel pending sells or account edits](https://help.m1.com/en/articles/9332124-how-to-cancel-pending-sells-or-account-edits)
20. [FX News Group: Public.com introduces more ways to build and manage a portfolio (10 Feb 2022)](https://fxnewsgroup.com/forex-news/platforms/public-com-introduces-more-ways-to-build-and-manage-ones-portfolio/)
21. [eToro press release: new portfolio tools (Jan 2025)](https://www.etoro.com/news-and-analysis/press-releases/etoro-unveils-range-of-new-portfolio-tools-helping-users-to-diversify-and-make-more-informed-investment-decisions/)
22. [justETF: Parqet review (28 Feb 2025)](https://www.justetf.com/de/academy/parqet-erfahrungsbericht.html)
23. [Finanzfluss Copilot help: the benchmark feature](https://www.finanzfluss.de/copilot/hilfe/benchmark-feature/)
24. [Portfolio Performance manual: benchmarking](https://help.portfolio-performance.info/en/how-to/benchmarking)
25. [Nielsen Norman Group: dashboards, making charts and graphs easier to understand (2017)](https://www.nngroup.com/articles/dashboards-preattentive/)
26. [Nielsen Norman Group: progressive disclosure (2006)](https://nngroup.com/articles/progressive-disclosure/)
27. [Nielsen Norman Group: mobile tables (2017)](https://www.nngroup.com/articles/mobile-tables/)
28. [Carbon Design System: status indicator pattern](https://carbondesignsystem.com/patterns/status-indicator-pattern)
29. [PatternFly: status and severity](https://patternfly.org/patterns/status-and-severity)
30. [NHS design system: task list (built on the GOV.UK pattern)](https://service-manual.nhs.uk/design-system/components/task-list)
31. [W3C: understanding WCAG 1.4.1, use of color](https://www.w3.org/WAI/WCAG21/Understanding/use-of-color)
32. [W3C: understanding WCAG 2.5.8, target size (minimum)](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html)
33. [Thaler, Tversky, Kahneman and Schwartz (1997), "The effect of myopia and loss aversion on risk taking", Quarterly Journal of Economics 112(2)](https://www.casact.org/abstract/effect-myopia-and-loss-aversion-risk-taking-experimental-test)
