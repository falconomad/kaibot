# Strategy config (M5)

The owner tunes the autonomous engine on the dashboard's **Strategy** page. The rule is that it can only make Kaibot
**pickier or smaller**, never looser and never different. The contract is in `docs/m5/contract.md` and the
decision in `docs/decisions/12_configuration_panel.md`.

## Life of a change
1. **Draft.** While you edit, the page saves a draft (`kb_save_config`). The database validates every save
   (`kaibot.config_check`); a value above the level, below its floor, or of the wrong shape is refused.
2. **Apply.** `kb_apply_config` validates again and creates version *n+1* with `applies_from` = the next 09:30 ET
   open. If an applied version hadn't started yet, the new one supersedes it, so at most one version is applied per
   session. It also queues a replay (`kaibot.replay_job`). An applied version can never be edited or deleted; only
   applied → superseded is allowed.
3. **Session open.** In `_start_day` the engine loads the latest non-superseded version created before today's open
   and runs it through `engine/userconfig.tighten()` against the level and `HARD_CEILINGS`.
   - Valid: the day's `Rules` govern the policy, strategies, focus, entry window, pause, VIX limit, event window
     and price/volume/spread filters, and a `config_applied` event is logged.
   - Invalid (it should never happen, because the server checked it): **entries are refused for that session
     only**, with an `engine_error` event and a `config` halt row. Stops, management and the 15:55 flatten still
     run, and the next session re-checks.
4. **Records.** Every trade and daily result carries `config_version` (0 = the level's own defaults). The Report's
   chapters (`kb_chapters`) group results by version; fewer than 30 trades reads "too early to judge". The go-live
   gate (M4) counts only trades made under the current version.
5. **Replay.** `python scripts/run_config_replays.py` replays each queued version on the design window
   (2025-02..2026-06) and logs a trial row. Until the minute-bar history is downloaded (M0 #3), jobs stay
   `waiting_for_data`; nothing is faked.

## What each setting does in the engine
| Setting | Effect |
|---|---|
| Risk sliders | Replace the level's values in `RiskPolicy`. Open risk follows the daily stop. |
| Strategies | Only the ones left on run. SPY momentum exists only on levels that include it. |
| Focus: Only | Candidates outside the focus set are dropped before any signal is logged. |
| Focus: Prefer | Focus names are tried first when several candidates compete in the same minute. |
| Entry window | Narrows 09:35–15:45. |
| Pause | 2–3 losses in a row trigger a pause of 60–240 minutes. |
| VIX limit | No new dip or SPY entries while the prior VIX close is above it. The crisis gate stays at 35. |
| Event window | Widens the ±30 min Fed/CPI blackout. |
| Min price / ADV / spread | A veto per candidate; the ADV floor is raised in the scanner. |

These are locked and never configurable: the news veto, brackets, quote freshness, the 15:55 flatten, no shorts /
leverage / overnight, and the strategy entry thresholds.

## Two checks that keep both sides honest
- `tests/test_userconfig.py`: property tests over random configs × every level. A config is either rejected or
  at least as strict as the level everywhere.
- `tests/test_config_sql.py`:
  - checks that the SQL seed data matches the YAML;
  - checks that the SQL and Python validators agree on random configs. This part needs `KAIBOT_TEST_PG` pointing
    at a scratch Postgres with 0011 applied; on 11 Nov they agreed on all 2,120 cases.
