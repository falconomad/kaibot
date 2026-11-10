# Kaibot v2 screen spec: UX
*UX lead · 10 Nov 2026 · implements 04_prd_v2.md with the 02_design.md visual system. There is no Trade approval screen and no AI rail.*

## 1. IA and navigation
- **Onboarding:** full screen, no shell. Runs on first login and for each New run.
- **Shell:** a 44px safety header and a left nav: **Live** (home) · **Trades** · **Report** · **Engine health**, with **Run & settings** below a divider.
- **Header:** PAPER, status (Running/Paused/Halted/Market closed), data health, LLM spend against $150/$190, day P&L against the stop, open risk, and drawdown against the run cap. Amber at 80% of a limit; crimson on a halt.
- **Currency and time:** $ first, with a € reference. CET, with ET in brackets.

## 2. Screens
**Onboarding:**
1. **Amount:** a $1k–100k stepper with the € reference.
2. **Level:** three cards (Cautious / Balanced / Aggressive). Each shows its *bad day* (the daily stop in $) and its *good day* (the replay's 90th-percentile day).
3. **Contract:** the §4 copy and one checkbox.
4. **Start:** "Starts Wed 11 Nov, 15:35 CET (09:35 ET)".

**Live** (top to bottom):
1. **Status banner.**
2. **Pot row:** today's P&L after costs against SPY and cash on one chart, using the worse of ledger and broker.
3. **Risk strip:** day P&L against the stop, open risk, positions and entries against their caps, drawdown.
4. **Columns:** open positions (60%) beside the last 10 tape rows with reason and verdict (40%).
5. **Footer:** the evidence counter and the expectations line.

**Live states:**
- **Running:** "Running · next scan 15:02:30".
- **Paused:** amber, "Paused. Stops stay at the broker; positions close by 21:55 CET" [Resume].
- **Halted:** flow b.
- **Outside market hours:** "Market closed · next session Wed 15:35 CET". It shows yesterday's daily card; live regions grey out.
- **Calibration week** (assumed: the first 5 sessions of a run or segment): a slate banner, "Calibration · day 2 of 5"; ratios read "Too few trades".

**Trades:**
- **Filters:** date, strategy, segment and outcome, plus *Vetoed* and *Shadow* toggles (dimmed rows, excluded from P&L).
- **Tape columns:** time, symbol, strategy, entry→exit, qty, P&L after costs, R, reason, verdict (Pass/Downgrade/Veto).
- **Detail:** a 50% right pane with a shareable URL. Tabs:
  - **Why** (the default): the reason codes in plain language, the calculated values, and a mini chart.
  - **Gate checks:** actual against limit for each rule.
  - **Agent room:** the Proposer, Critic and Judge turns with sources, plus the verdict. Labelled "AI analysis: can veto or downgrade, never size or price".
  - **Fills & costs:** broker against shadow.

**Report:**
- **Daily card** by 22:15 CET: net against SPY and cash, trades, halts, and vetoes with what each would have made.
- **Weekly attribution** with kill flags.
- **Evidence counter:** one row per strategy. Cells read Not yet/Met/Failed for: 900 trades, 3+ months, t>3, DSR>0, random percentile ≥95, Sharpe above SPY's, drawdown within the cap. The footer reads "Passing opens a discussion, not a switch". There is no live button.
- CSV export; gate report.

**Run & settings:**
- **Pause/Resume:** no confirmation.
- **Flatten-and-stop:** crimson outline.
- **New run:** archives the current run (read-only), then onboarding.
- **Next-session level:** "Applies from the next open as a new stats segment".

**Engine health:**
- **Data sources:** role (IN / LOGGED), freshness, status and fallback.
- **LLM spend:** today against $3 (above it, rules-only vetoes), the month, and per agent.
- **Halts log:** type, reason, duration.
- Watchdog and reconciliation status.

## 3. Flows
**a. Onboarding:** magic link → Amount → Level → Contract (the checkbox enables Start) → Live, showing "Market closed" with a countdown.

**b. Automatic halt:** the header goes crimson, and the banner reads:
- **3 losses:** "Paused 60 min after 3 losses. Stops stay at the broker. Resumes 17:42 CET".
- **Stale data:** "Halted: quotes stale > 5 s. Resumes when fresh", linking to Engine health.
- **Drift:** "Halted: ledger and broker disagree on AAPL (40 vs 38). No entries until reconciled".
- **3 stops in 10 days, or the drawdown cap:** "Run ended by rule", offering only New run.
- **$190 spend:** amber, "No new entries; exits still managed".

Each halt is logged and appears on the daily card.

**c. Flatten-and-stop:** one dialog. "Flatten and stop? Sells 4 positions at market now. Nothing trades until you resume at the next open or start a New run." [Cancel] [Flatten and stop]. Progress counts "Closing 4 → 0" (within 30 s), then "Stopped by you, 16:12 CET"; fills are tagged `user_flatten`.

**d. Why, in 2 clicks:** a tape row (Live or Trades) opens Why; then Agent room or Gate checks.

## 4. Honest-expectations copy
**Contract:**
> **What Kaibot does:** trades liquid US stocks with paper money, on its own, ending every day flat. No overnight positions, shorts or leverage.
>
> **What to expect:** success means beating SPY and cash *after costs*, by about 0.02–0.10% a day. Expect 4–5 losing days in 10, and some red weeks.
>
> **What it won't do:** promise profit, or go live on its own. That takes 900+ trades over 3+ months passing a pre-registered test, then a conversation.
>
> ☐ I understand this is an experiment and losing days are part of it. [Start]

**Live footer:** "Today is one data point. The question is whether 900 trades beat SPY and cash. So far: 212."

**Live, red day:** "Down 0.4% after costs, within the −1.5% daily stop. Normal variance, not a signal."
