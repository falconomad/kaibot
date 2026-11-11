# Decision 12: Configuration panel (M5)

- **Status:** proposed by a stakeholder on 2026-11-11. **Designs approved by the stakeholder on 2026-11-11** (canvas boards 09, 09m, 09b). **Product owner asked for it to be implemented on 2026-11-11** (after M4 shipped), which counts as sign-off.
- **Scheduled:** after M4 (the evaluation gate). Nothing in M0–M4 changes.
- **Debate:** [`docs/debate/m5_config_panel.md`](../debate/m5_config_panel.md)

## Decision

Add a **Strategy** page. On it, the signed-in owner can tune the autonomous engine within the chosen level. The engine still trades by itself; the user only shapes what it may do.

Principle: **the user can make Kaibot pickier or smaller, never looser or different.** Every setting is a subset or tightening of the level policy. The server validates every setting and the engine validates it again (the existing `validate_policy`). If a check fails, the engine refuses the session (fail closed).

## MVP controls

| Section | Control | UI | Range / rule |
|---|---|---|---|
| Focus | Theme presets: Hyperscalers, Semiconductors, Big Tech, Software & IT, Communication & media, Consumer, Healthcare, Financials, Industrials & defence, Energy, Defensives | multi-select chips | union of the chosen themes |
| Focus | Custom stocks | searchable logo grid (59 names) | added to the themes |
| Focus | Mode | segmented control: *Only these* / *Prefer these* | *Only* needs at least 5 symbols |
| Focus | Trades-per-month estimate | read-only meter | from replay per-symbol candidate rates |
| Risk | Risk per trade | slider | 0.10% up to the level's value |
| Risk | Max open positions | stepper | 1 up to the level's value |
| Risk | Max position size | slider | 5% up to the level's value |
| Risk | Max new trades per day | slider | 1 up to the level's value |
| Risk | Daily stop | slider | 0.25% up to the level's value |
| Risk | End the run at drawdown | slider | 2% up to the level's value |
| Strategies | Dip reversal / SPY late momentum | toggles | at least one on; SPY only where the level includes it |
| Strategies | ORB, News continuation | read-only rows marked "under test" | shadow only |
| Timing & safety | Entry window | two-handle range | inside 09:35–15:45 |
| Timing & safety | Pause after a losing streak | stepper + slider | 2–3 losses, 60–240 min |
| Timing & safety | No new trades when VIX is above | slider | 20–35 (35 is the crisis gate) |
| Timing & safety | Fed / CPI quiet window | slider | ±30–90 min; always on |
| Timing & safety | News veto, 15:55 flatten, no shorts / leverage / overnight | locked rows | cannot be changed |
| Advanced | Min price, min daily $ volume, max spread | sliders | tighten only |

## Applying changes

- Drafts are saved while the user edits. A sticky **Review changes** bar shows:
  - the diff;
  - the impact (≈ trades/month, max loss per trade in dollars);
  - the note "Starts a new chapter in your report".
- **Apply** creates config version *n+1*, which takes effect at the **next session open**. Open positions keep the rules they were opened under.
- At most one applied version per session.
- *Pause now* and *Flatten and stop* stay instant, as today.
- Each applied version logs a `trial` row (config hash) and queues a replay of that config on the design window (CLAUDE.md: every parameter change runs a replay). The replay result appears on the page when it finishes.
- Every trade carries `config_version`. The Report and pot chart split results into chapters. Chapters with fewer than 30 trades show "too early to judge".
- The go-live gate (M4) judges one frozen configuration only.

## Engineering outline

1. **Storage.** Migration `kaibot.config(version, owner, body jsonb, created_at, applies_from, status)`, plus three RPCs:
   - `kb_get_config` (current, draft and level bounds);
   - `kb_save_config(body)`, which validates tighten-only in SQL;
   - `kb_apply_config`.
2. **Themes.** Add a `themes:` block to `config/universe_seed.yaml` that maps theme → symbols. It is version-controlled, with no runtime vendor call. Reference symbols (SPY, sector ETFs) are always loaded.
3. **Engine.** `engine/userconfig.py`:
   - `tighten(level_policy, cfg)` is pure and property-tested, and never exceeds the level or `HARD_CEILINGS`;
   - the engine reads the config in `preparing`;
   - it filters or ranks candidates by focus;
   - it adds `config_version` to the trade and run rows.
4. **Estimator.** Per-symbol candidate and trade rates from replay 002 are shipped as a static JSON. Estimate = Σ symbol rate × strategy share.
5. **Web.** `/strategy` page, using v3 components (chip, slider, range, stepper, toggle, locked row, review bar or sheet, skeleton).

## Not in the MVP

- **Later**, each with a replay first: exit presets, catalogue expansion, user-defined themes, day-of-week schedule, agent-room strictness (after M3).
- **Never:** shorts, leverage, overnight holds, any limit above the level, turning off the flatten / brackets / quote freshness / blackout / news veto, and editing strategy entry thresholds.

## Known trade-off

Narrow focus slows the evidence a lot. On replay 002's rate (about 45 trades/month on 59 symbols), *Hyperscalers only* gives about 4 trades a month, and *all tech* about 11. The page shows this before Apply. It does not hide it.

## UI direction (update 2026-11-11, after research)

See [`docs/design/research_m5_config_ui.md`](../design/research_m5_config_ui.md). The panel has two layers.

1. **Strategy page (one screen):**
   - "Start from" preset cards: Kaibot default / Big tech / Calm / Custom. Each shows its trades a month and max loss.
   - Focus theme cards (Prefer / Only, Exclude a stock).
   - One 5-step **Boldness** bar that sets all six risk limits within the level.
   - Trading-hours shapes on a day bar.
   - Three "extra caution" pill switches.
   - A pinned outcome bar with Review & apply.
2. **Fine-tune page:** every MVP control from the table above, one expandable row each.

A preset or Boldness step is only a shortcut that writes the same config fields. Nothing new reaches the engine.
