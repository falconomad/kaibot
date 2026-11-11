---
description: Hand a GitHub story to the team that owns it (e.g. /delegate 56)
---

Delegate GitHub issue #$ARGUMENTS in falconomad/kaibot.

1. Read the issue with `gh api repos/falconomad/kaibot/issues/$ARGUMENTS` (title, body, labels, milestone).
2. Check it is ready: it has acceptance criteria, exactly the team:* labels it needs, its milestone is approved, and any design it needs is approved. If not, stop and say what is missing (Product fixes it).
3. One team label: start that team's agent (`.claude/agents/<team>.md`) in its own worktree with the issue text and the link to docs/decisions/. Several team labels: split into sub-tasks, contracts first (e.g. Backend-quant writes the kb_* interface before Frontend uses it), and run them in that order.
4. Comment on the issue: which team took it, the branch, and later the PR link.
5. Report to the stakeholder (through Product) only: done / PR link / anything that needs their decision.
