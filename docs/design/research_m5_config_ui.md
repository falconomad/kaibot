# Research: how automated-investing products let users tune a bot (M5 Strategy page)

Date: 2026-11-11.

Question: how do big robo-advisors, broker apps and trading-bot builders let lazy users configure an automated system without a long list of settings?

## What they do

| Product | Pattern | What we take |
|---|---|---|
| **Wealthfront** | A single risk score from 0.5 to 10, recommended for you, changed with + / − buttons. There is a "Switch to recommended risk" shortcut, and the allocation follows the score. Fine edits (per-asset sliders) sit behind an *Edit investments* toggle. | **One master dial** drives many underlying numbers. A way back to "recommended". Detail is behind a toggle. |
| **Betterment** | An interactive stocks/bonds slider with 5 named bands (Very conservative … Very aggressive). The projected range of outcomes moves as you drag. | **Named steps**, not raw numbers. **Live consequences** next to the control. |
| **Pionex** | Each bot offers *Use AI strategy* vs *Set myself*. AI parameters come from 7/30/180-day history, and the Martingale bot adds a risk-preference dropdown. Beginners are told to use AI first and fine-tune later. | **Start from a recommended preset.** Manual setup is an opt-in path, not the default screen. |
| **3Commas** | Public Presets were rebuilt as a *Strategy Gallery*: cards with real backtest metrics (ROI, max drawdown) and one-click launch. | **Preset cards that carry evidence.** One tap to adopt. |
| **Composer** | A no-code editor with the backtest preview next to it, so every change shows its effect. | **Show the result of a change before committing.** |
| **eToro Smart Portfolios** | Themes first ("pick a theme you believe in"), presented as cards (semiconductors, AI, 5G…). | **Themes as cards**, not tickers. |
| **Robinhood Strategies** | Managed for you. Customisation is narrow: restrict up to 3 stocks, update your investor profile. | **Exclusions** are a small, separate control, and a managed product keeps customisation light. |
| **UX literature (NN/g progressive disclosure, settings patterns)** | Defaults must work for most people. Advanced options stay one click away with specific labels ("Advanced liquidity floors", not "More…"). | **Two layers:** a one-screen tune page, plus a Fine-tune page holding every setting. |

## Design direction for Kaibot

1. **Start from…** preset cards. Each shows its trades a month and its max loss. One tap fills every setting:
   - **Kaibot default** — everything as the level sets it;
   - **Big tech** — focus on Big Tech, Hyperscalers and Semis, moderate boldness, skip the open;
   - **Calm** — all stocks, careful boldness, skip the open, sit out on fear spikes;
   - **Custom** — picked automatically as soon as you change anything.
2. **Focus** as theme cards with a stack of stock logos and each card's own trades-a-month, plus "Prefer / Only" and a small **Exclude a stock** link. All stocks is the default.
3. **Boldness** as one 5-step segmented bar, from Careful to Max (your level). Each step sets all six risk limits at once, within the level, and the dollar consequences sit underneath. Individual limits move to Fine-tune (Wealthfront / Betterment model).
4. **Trading hours** as tap-able shapes on the day bar: All day / Skip the open / Mornings / Afternoons. The bar redraws, the Fed/CPI quiet zone and the 15:55 sell-all are always drawn, and exact minutes are in Fine-tune.
5. **Safety** as three pill switches: sit out on fear spikes (VIX), longer pause after losses, wider Fed/CPI quiet window. A ✓ "4 rules always on" opens the locked list.
6. **Live outcome bar** at the bottom: ≈ trades a month, max loss a trade, worst day, plus Apply. It is always visible, and the result is shown before you commit (Composer / Betterment).
7. **Fine-tune all settings →** opens the full list (every slider and toggle), so no feature is lost.

A lazy user taps a preset and then Apply: two taps. A curious user adjusts the cards on one screen. A power user opens Fine-tune.

## Sources

- [Wealthfront: customize your portfolio](https://support.wealthfront.com/hc/en-us/articles/360059631751)
- [Wealthfront: how to change your risk score](https://support.wealthfront.com/hc/en-us/articles/360032689612-How-do-I-change-my-risk-score-)
- [Betterment: asset allocation methodology (risk slider, 5 bands)](https://www.betterment.com/resources/goals-advice-explained)
- [Pionex: grid trading bot / AI strategy](https://intercom.help/pionex/en/articles/14616349-grid-trading-bot)
- [Pionex: Martingale bot risk preference](https://intercom.help/pionexus/en/articles/14319693-martingale-dca-bot-standard-mode)
- [3Commas Strategy Gallery announcement](https://t.me/s/commas/2765)
- [Composer](https://composer.trade/ride)
- [eToro Smart Portfolios](https://go.etoro.com/en/smart-portfolios)
- [Robinhood Strategies FAQ (restrict stocks)](https://invest.robinhood.com/us/en/strategies/)
- [Progressive disclosure, Jakob Nielsen](https://jakobnielsenphd.substack.com/p/progressive-disclosure)
