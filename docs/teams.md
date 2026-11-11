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
