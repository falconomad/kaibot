# M5 debate: the configuration panel

Status: **proposal**. A stakeholder brought it to the product owners on 2026-11-11, and it is not yet approved. It is scheduled after M0–M4.

Stakeholder ask: the engine keeps trading on its own, but the signed-in user can tune it with toggles and sliders. That includes which kinds of stock it targets (for example hyperscalers, tech, or a sub-sector of tech), and "each and everything should be configurable". Phase one covers only what the engine can already do.

Participants: Product (PM, user advocate), Quant, Risk, Engineering (backend, frontend) and Design.

## 0. Inventory: what the engine can actually adjust today

The inventory was read from `config/profiles.yaml`, `risk/gate.py` (`HARD_CEILINGS`, `validate_policy`), `engine/core.py`, `engine/events.py`, `engine/shadow.py`, `config/universe_seed.yaml` and `replay/features.candidates()`.

| # | Capability | Where it lives | Today | Class |
|---|---|---|---|---|
| 1 | Level (Steady / Balanced / Hunter) | profiles.yaml `levels` | user picks per run | **already exposed** |
| 2 | Tradable universe (59 stocks in 11 sectors) | universe_seed.yaml | fixed | **adjustable: filter only (subset)** |
| 3 | Risk per trade % | level `risk_per_trade_pct` | 0.25 / 0.5 / 1.0 | **adjustable: tighten only** |
| 4 | Max open positions | level `max_positions` | 3 / 5 / 8 | **adjustable: tighten only** |
| 5 | Max position % of pot | level `max_position_pct` | 10 / 15 / 20 | **adjustable: tighten only** |
| 6 | Max entries per day | level `max_entries_per_day` | 10 / 20 / 30 | **adjustable: tighten only** |
| 7 | Daily stop % | level `daily_stop_pct` | 0.75 / 1.5 / 3.0 | **adjustable: tighten only** |
| 8 | Run drawdown end % | level `run_drawdown_end_pct` | 4 / 8 / 12 | **adjustable: tighten only** |
| 9 | Dip reversal strategy | level `strategies` | on for all levels | **toggle (off allowed)** |
| 10 | SPY late momentum | level `strategies` | Balanced and Hunter | **toggle (off allowed; on only where the level allows it)** |
| 11 | ORB / news continuation | `shadow_strategies` | shadow only, never trades | **view only: "under test"** |
| 12 | Entry window | `all_levels` 09:35–15:45 | fixed | **adjustable: narrow only** |
| 13 | Loss-streak pause (3 losses → 60 min) | `all_levels.halts` | fixed | **adjustable: tighten only (2–3 losses, 60–240 min)** |
| 14 | VIX regime (> 35 no entries, > 25 half size) | core.py regime gate | fixed | **adjustable: lower the "no entries" threshold only (20–35)** |
| 15 | FOMC/CPI blackout ±30 min | events.py | always on | **locked on; window may widen (30–90 min)** |
| 16 | News 24 h veto | core.py | on | **locked on** |
| 17 | Min price / min ADV / max spread / min quality | profiles.yaml `universe` | 5 / 50M / 5 bp / 70 | **adjustable: tighten only (advanced)** |
| 18 | Exit rule: 1.5 ATR stop, 2R target, 1.5 ATR trail after +1R | core.py constants | fixed | **not adjustable in MVP (a strategy change, needs a replay)** |
| 19 | Dip-reversal entry thresholds (z ≤ −1.5, residual, turnover) | features.candidates | fixed | **never user-adjustable (strategy definition)** |
| 20 | Shorts, leverage, overnight holds, flatten 15:55, brackets at broker, quote age 5 s, hard ceilings | all_levels / gate | fixed | **never user-adjustable** |

Rule of thumb: the user may make the engine **pickier or smaller**, never **looser or different**. That keeps every user setting inside what `validate_policy` already enforces.

## Round 1: positions

**PM.** The headline feature is *Focus*. People think in themes, not tickers: "hyperscalers", "chips", "big tech", "banks", "defensives". Ship theme presets, a custom pick from the catalogue, and the risk dials we already have as sliders. One page, called **Strategy**, added to the nav.

**User advocate.** The user's mental model is "tell the bot what I care about, and how brave to be". Twenty controls on one screen would be paralysing. Use three plain sections: *What to trade*, *How much to risk*, *When to trade*. Hide the liquidity floors under Advanced. Each control should say what it does in one line, in money terms where possible ("max loss per trade ≈ $25").

**Quant.** I want two problems on the record before we design anything.

(a) **Narrow focus starves the evidence.** Replay 002 (variant A) made 750 trades in 353 sessions across 59 symbols, about 45 a month. Trades scale roughly with universe size:
- *Hyperscalers* (MSFT, AMZN, GOOGL, META, ORCL; 5 of 59) gives about 4 trades a month. The 900-trade go-live bar would take decades at that rate.
- *All tech* (about 15 names) gives about 11 a month.

(b) **Every applied change is a new trial.** CLAUDE.md already says so. If users can drag sliders daily, we can never attribute results to a configuration. My recommendation is that Focus be a *preference* (rank focus names first), not a hard filter.

**Risk.** I'm fine with all of it on two conditions: tighten-only, and validated on the server. A slider's maximum is the level's value, never `HARD_CEILINGS` directly. Nothing touches shorts, leverage, overnight holds, the 15:55 flatten, broker brackets or quote freshness. The event blackout and news veto stay locked on. Changes apply at the **next session open**, never mid-session, so that open positions keep the rules they were opened under. The exception is anything that only reduces exposure: *Pause now* and *Flatten and stop* already exist and stay instant.

**Backend.** This is cheap if we never add a code path.
- A versioned `kaibot.config` row (jsonb) is written by `kb_save_config`, which validates tighten-only in SQL and mirrors `validate_policy`.
- The engine reads the latest applied config in `preparing`, before the open. It merges it as `policy = tighten(level_policy, user_cfg)`, runs the existing `validate_policy`, and refuses to start the session if validation fails (fail closed).
- Every trade row gets `config_version`.
- The universe filter needs a stable theme map in `config/universe_seed.yaml` (`themes:`). We don't call Finnhub at runtime.

**Frontend.** The page follows the v3 patterns: chips, sliders, toggles, and a sticky **Review changes** bar that shows the diff and its impact before anything is saved. Drafts live in the browser. Nothing changes until the user presses Apply.

**Design.** One page, four cards on desktop:
1. Focus;
2. Risk;
3. Strategies;
4. Timing & safety.

Advanced is collapsed. On a phone, the same cards stack and the review bar becomes a bottom sheet. Locked rules are visible but greyed with a lock, so the user sees what protects them. Impact belongs right next to the control that causes it.

## Round 2: rebuttals

**PM → Quant.** "Preference" isn't what the stakeholder asked for. They said *focus on only that stock*. A hidden ranking tweak would feel like the setting does nothing.

**Quant → PM.** Then make the trade-off visible rather than silent. Offer both modes: **Only these** (hard filter) and **Prefer these** (rank first, still trade the rest). Show the estimated trades per month for the current selection. Show "evidence pace" in plain words ("about 4 trades a month; the go-live bar needs ~900"). I can live with a hard filter if the user sees that before they apply it.

**Risk → Quant.** Agreed. Also enforce a **minimum of 5 tradable symbols** for *Only these*. With fewer than that, one name's bad day dominates the run. Reference symbols (SPY and the sector ETFs used for residuals) are always fetched whatever the focus, because the features depend on them.

**Quant → everyone, on churn.** Applied configs are *segments*. The Report splits results by segment. A segment with fewer than 30 trades is shown greyed as "too early to judge". The go-live gate (M4) evaluates exactly one frozen configuration. Rate limit: at most one applied change per session. You can save drafts all day, but only the latest draft before the open is applied.

**User advocate → Quant.** No "trial" or "segment" jargon in the UI. Say "Changes start a new chapter in your report", and show the chapter markers on the pot chart.

**Backend → Risk.** Next-open application is also simpler for us: one read per session, no mid-session state change. Exits (#18) stay out of the MVP, since a different stop or target is a different strategy and needs its own replay row.

**PM → all.** "Each and everything should be configurable." What do we tell the stakeholder about exits and strategy thresholds?

**Quant.** Later, as **replay-backed presets** ("Exit: tighter / standard / looser"). Each one gets enabled only after its replay is logged. Never free sliders.

**Design → PM.** Show those items in the panel as a short "Coming later" list, so the stakeholder sees the roadmap without inactive controls cluttering the page.

## Resolution: MVP scope

**In the MVP (M5):**
1. **Focus.**
   - Theme presets: Hyperscalers, Semiconductors, Big Tech, Software & IT, Communication & media, Consumer, Healthcare, Financials, Industrials & defence, Energy, Defensives (staples + utilities + real estate).
   - Custom pick from the 59-stock catalogue.
   - Mode: *Only these* / *Prefer these*.
   - Minimum 5 symbols for *Only*.
   - Live "≈ trades per month" estimate.
2. **Risk sliders, bounded by the level:** risk per trade, max open positions, max position % of pot, entries per day, daily stop, run drawdown end. Each shows a money equivalent on the current pot.
3. **Strategies:** toggle dip reversal and SPY late momentum (where the level allows). At least one must stay on. Shadow strategies are listed read-only as "under test".
4. **Timing & safety:**
   - entry window range (narrow only);
   - skip the first N minutes;
   - loss-streak pause (2–3 losses, 60–240 min);
   - "no new trades when VIX above" (20–35);
   - event blackout width (30–90 min, always on);
   - news veto shown locked.
5. **Advanced (collapsed):** min price, min daily $ volume, max spread (tighten only).
6. **Review & apply:**
   - diff of changes;
   - impact (trades/month, max loss per trade in $);
   - "starts a new chapter in your report";
   - Apply at next open, plus *Reset to level defaults*.
7. **Engine and report:**
   - server-side tighten-only validation;
   - the engine applies the config at the session open and fails closed;
   - `config_version` on every trade;
   - Report and pot chart split by chapter.

**Not in the MVP (later, each needs a replay row first):** exit presets, catalogue expansion (more semis/software names, each must pass the universe filters), user-defined themes, scheduling (for example "Fridays off"), and agent-room strictness (after M3).

**Never:** shorts, leverage, overnight holds, raising any limit above the level, disabling the 15:55 flatten, broker brackets, quote freshness, event blackout or news veto, and editing strategy entry thresholds.
