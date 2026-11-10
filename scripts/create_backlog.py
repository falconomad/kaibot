"""Create labels, milestones and issues on GitHub from docs/backlog.json (idempotent by title).

Usage: GH repo must exist. `python scripts/create_backlog.py falconomad/kaibot` (needs `gh` authenticated).
"""

import json
import subprocess
import sys

REPO = sys.argv[1] if len(sys.argv) > 1 else "falconomad/kaibot"
MILESTONES = {
    "M0": "Foundation & replay (weeks 1-6)",
    "M1": "Paper engine live (weeks 7-12) - evidence clock starts",
    "M2": "Onboarding, dashboard & reporting (weeks 13-17)",
    "M3": "Agent room A/B & alt-data features (weeks 18-23)",
    "M4": "Evaluation gate (week 24+)",
}
TEAM_COLORS = {"design": "c5def5", "frontend": "1d76db", "backend-quant": "0e8a16", "data": "fbca04",
               "agents-ai": "5319e7", "qa-ops": "b60205"}


def gh(*args, input_=None):
    r = subprocess.run(["gh", "api", *args], input=input_, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr)
    return json.loads(r.stdout or "null")


def main():
    with open("docs/backlog.json") as fh:
        items = json.load(fh)
    existing_labels = {lbl["name"] for lbl in gh(f"repos/{REPO}/labels", "--paginate")}
    labels = {f"team:{t}": c for t, c in TEAM_COLORS.items()}
    labels |= {f"epic:{i['epic'].split()[0]}": "ededed" for i in items}
    labels["story"] = "d4c5f9"
    for name, color in labels.items():
        if name not in existing_labels:
            gh(f"repos/{REPO}/labels", "-f", f"name={name}", "-f", f"color={color}")
    milestones = gh(f"repos/{REPO}/milestones", "-f", "state=all", "-X", "GET")
    ms_ids = {m["title"].split(" ")[0]: m["number"] for m in milestones}
    for key, desc in MILESTONES.items():
        if key not in ms_ids:
            ms_ids[key] = gh(f"repos/{REPO}/milestones", "-f", f"title={key} {desc.split(' (')[0]}",
                             "-f", f"description={desc}")["number"]
    open_titles = {i["title"] for i in gh(f"repos/{REPO}/issues", "-X", "GET", "-f", "state=all", "-f", "per_page=100")}
    for it in items:
        title = f"[{it['milestone']}·S{it['n']:02d}] {it['title']}"
        if title in open_titles:
            continue
        body = (f"**Epic:** {it['epic']}\n**Teams:** {', '.join(it['teams'])}\n**Milestone:** {it['milestone']}\n\n"
                f"### Acceptance criteria\n- [ ] {it['ac']}\n\n"
                f"### Tasks\n- [ ] Design/approach note in the PR description\n- [ ] Implementation\n- [ ] Tests\n"
                f"- [ ] Docs updated\n\nSource: docs/decisions/04_prd_v2.md (story {it['n']})")
        payload = {"title": title, "body": body, "milestone": ms_ids[it["milestone"]],
                   "labels": ["story", f"epic:{it['epic'].split()[0]}", *[f"team:{t}" for t in it["teams"]]]}
        gh(f"repos/{REPO}/issues", "--input", "-", input_=json.dumps(payload))
        print("created", title)


if __name__ == "__main__":
    main()
