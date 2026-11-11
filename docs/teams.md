# Kaibot teams and delegation

## How the stakeholder works with the teams

- **The stakeholder talks to Product only**, in the product chat or with a GitHub issue labelled `stakeholder`.
- **Product** turns asks into stories, runs the debate with the other teams, and brings back one recommendation.
- **Product delegates** every story to the team that owns it (`/delegate <issue>`). The stakeholder never has to pick a team.
- **Decisions that stay with the stakeholder:**
  - scope (new milestones, big changes);
  - design approval before Frontend builds;
  - anything irreversible or involving money: the agent room on, the cloud move, the go/no-go on real money.
- **Weekly update** (Monday): done, in progress, blocked, and anything waiting on the stakeholder.

## Teams and what they own

| Team | Owns | Signs off on |
|---|---|---|
| Product | `docs/decisions`, `docs/debate`, milestones, issues | scope, stories, acceptance criteria |
| Design | `docs/design`, the design canvas | screens before Frontend builds them |
| Frontend | `apps/web` | dashboard pages |
| Backend-quant | `src/kaibot/{engine,risk,strategies,replay,evaluation,features,broker}`, `migrations`, `config/profiles.yaml`, `config/gate.yaml` | engine logic, sizing, replays, trials, `kb_*` RPCs |
| Data | `src/kaibot/ingest`, `config/sources.yaml`, `config/universe_seed.yaml`, `config/macro_calendar.yaml`, fetch and collect scripts | free sources, budgets, history |
| Agents-AI | `src/kaibot/{agents,news}`, `engine/roomflow.py`, `engine/m3glue.py` | agent room, news desk, LLM budget |
| QA-Ops | `tests`, `.github`, `docs/ops`, Docker, run, drill and guard scripts | CI, drills, deploys; second key on risk, sizing and broker |

Each team's brief is in `.claude/agents/<team>.md`.

## Rules between teams

- **Two keys.** Changes to `risk/gate.py`, sizing or broker code need Backend-quant *and* QA-Ops.
- **Contracts first.** Where two teams meet (for example a new `kb_*` RPC), the owning team writes the interface in the story before either side builds.
- **Stay in your folders.** A story that needs another team's folder is split, not stretched.
- **Labels.** PRs are labelled by the folders they touch (`.github/labeler.yml`), so ownership is visible on every PR.

## Daily rhythm (set by the stakeholder on 11 Nov 2026)

- **Daily status report, about 18:00 Berlin, every day.** Product posts it as a GitHub issue labelled `stakeholder` and sends it as a notification. Per team: working on, progress, pending, next. Plus anything waiting on the stakeholder.
- **No team sits idle.** Product decides what each team does; it does not ask the stakeholder. A team with no milestone story takes the top item from its standing list below, as a normal story with a `team:*` label and the `idle-work` label.
- **Night shift, weekdays after the US close.** Teams build their top story, open PRs, and Product merges what is safe (see below).

### Standing lists for free time

| Team | In free time |
|---|---|
| Product | Groom the backlog, turn the research report's actions into stories, track competitors and user requests, keep `docs/decisions` current |
| Design | Designs for upcoming features, critique and tighten existing screens (debate first), phone layouts, a design-system page, a possible mobile app |
| Frontend | Page speed and bundle size, accessibility, empty/error/loading states, test coverage |
| Backend-quant | Engine speed (cache daily features), research-report experiments as replays (each logs a trial), follow-ups in the project index |
| Data | New free data sources and tools (check limits first, add to `config/sources.yaml`), data-quality checks, collectors in shadow mode |
| Agents-AI | Room prompt and evidence quality (offline evals only), LLM cost reduction, news clustering quality |
| QA-Ops | Flaky or missing tests, CI speed, drills, runbooks, the cross-machine engine lease |

### What may be merged without the stakeholder

- **Yes, when checks are green and it is outside 09:30-16:00 ET:** docs, designs, tests, CI, frontend, scripts, shadow-only collectors, and engine changes that are behaviour-neutral (speed, logging) with a replay diff of zero.
- **Never without the stakeholder:** anything that changes live trading behaviour (strategy rules or parameters, `risk/gate.py`, sizing, broker code, `config/profiles.yaml`), production database migrations, new paid services, switching the agent room on, the cloud move, real money. These wait as open PRs and are listed under "Waiting on you".
- Before starting a story, check for an open PR or branch on it, so no story is built twice.
- **Draft milestones are not built.** Stories in a milestone whose title ends with "(draft)" (for example M6) are never picked up by a team, the night shift or `/delegate` until the stakeholder approves the milestone.
