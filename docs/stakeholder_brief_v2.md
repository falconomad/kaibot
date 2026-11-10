# Stakeholder brief v2 (Tue 10 Nov 2026) — supersedes the approval-based v1 scope

From the stakeholder (product owner, solo developer):

- Kaibot is an AUTONOMOUS PAPER day-trading app for US-listed stocks only. Paper trading only for now; a live broker connection (e.g. Alpaca) may come later, based on paper results.
- It does not have to use Alpaca. It needs trade signals plus a way to fund a paper account with simulated money.
- Onboarding: the user logs in, enters a paper-trading amount (e.g. $5,000 / €5,000) and chooses an aggressiveness level. Once approved, that amount is used and shown on the dashboard.
- After onboarding the user has NO control: the engine buys and sells in the background. Any number of trades is allowed. The dashboard updates and tracks what we made at the end of each day / period.
- The only objective is to end the day (or a set period) with more money than we started with, whether that's 0.5%, 1% or 2%.
- Strategy ideas: "loser hunter" — stocks with strong fundamentals that are down because of external factors (geopolitics, market-wide moves) and likely to rebound within the hour or day; whole-market context; sentiment.
- Data only from FREE endpoints, e.g. Finnhub, Hugging Face, Polymarket and other prediction markets, geopolitical sources, Twitter/X, anything that can move stocks or improve predictions. No paid services.
- Claude API credits can be used.
- Engine design: a "room" where different agents debate and decide, then hunt for stocks to buy and sell.
- Fresh GitHub repo named kaibot. The existing folders (bloodbath bot, Umbrella Android app) may or may not be reused.
- Wanted: research; product-team discussion with engineering; risks; decisions; delegation to design and dev; stories and tasks; a workflow and plan; designs; tools; technical documentation; how effective the prediction logic will be and how it works.
