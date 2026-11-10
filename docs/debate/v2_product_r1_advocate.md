# v2 Round 1: User Advocate (autonomous paper day-trader)

## 1. What I need to see to trust it
- **The pot, live:** value against my start amount, today's P&L in $ and % (€ shown too), next to **SPY buy-and-hold and cash** for the same day. "More than we started with" means little if the market rose 2%.
- **Trade tape:** every fill with time, size, entry, exit and P&L after modelled slippage, plus a **one-line why** ("NVDA −3.1% on a tariff headline, fundamentals unchanged, rebound expected within 60 min") and its exit rule.
- **Agent room:** per trade, who argued for, who dissented, the deciding vote, and timestamped sources. Dissent stays visible.
- **Daily report card:** net result vs SPY, trades, win rate, average win and loss, biggest mistake, a **prediction scorecard** (did the "rebounds within the hour" calls actually rebound?), and what changes tomorrow.
- **When it's losing:** no softening and no hidden red. Show the loss against the cap ("−0.9% of −1.5%"), the losing streak, which strategy is bleeding, and whether the engine has scaled itself down.

## 2. Aggressiveness levels
- **Steady:** small sizes, few positions, high signal bar, daily cap −1%. "Fewer trades; most days within ±1%; losing days are still common."
- **Balanced:** moderate size and frequency, cap −2%. "Typical days within ±2%; expect losing streaks of 3–5 days."
- **Hunter:** larger sizes, more positions, lower signal bar, cap −3%. "Wide swings; some days hit the cap; a real chance of a losing month."

The ranges come from backtests and later from my own paper record. Until real data exists, show only the cap. Every level states it plainly: most intraday strategies don't beat buy-and-hold after costs.

## 3. Onboarding
1. **Amount** in USD, with a € reference at today's rate.
2. **Level**, each shown as a sample bad day and good day in money.
3. **Period** (day, week or month) and an optional target that's shown but never chased.
4. **The contract:** loss cap, hours, what it will never do (shorts, margin, live trading), how to pause. One confirmation.
5. **Start** at the next US open (15:30 CET); the first week is labelled "calibration".

## 4. Minimum controls
- **Pause** stops new entries; a separate **Flatten all** closes positions.
- **Aggressiveness changes** apply from the next session, never mid-day.
- **Reset or end the pot**, keeping the final report.
- **Alerts:** daily report, cap hit, engine paused itself. When the automatic kill switch fires, it says why.

## 5. Hills I'll fight on
1. **SPY and cash benchmarks on the main screen.** Otherwise a "profit" can still lose to doing nothing.
2. **"No control" means no approvals, not no brakes.** I can only trust what I can stop, so Pause and Flatten all stay.
3. **Honesty over hype.** P&L after slippage, the scorecard always visible, no confetti. Paper results only justify going live if they're honest.
