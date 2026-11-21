"""Re-score every existing rule report under gate v3 WITHOUT re-running any rule (M11 ticket 2.5, issue #456).

.venv/bin/python scripts/gate_v3_rescore.py                      reads data/reports, writes the table
.venv/bin/python scripts/gate_v3_rescore.py --data data --out docs/research/results

Reads only what earlier runs wrote: the report JSON files and daily CSVs under <data>/reports, the trial registry
<data>/trials.jsonl and the Treasury-bill file <data>/inputs/fred_dtb3.json (scripts/fetch_tbill.py). It runs
no simulator, loads no price bar and logs no trial. Writes <out>/gate_v3_rescore.md and .json.

What it does, per rule and kind of run (the newest report of each):
1. cuts the account's daily returns to gate v3's research window (2017-05-01 to 2024-12-31). Sessions in the
   hold-out are dropped before any number is computed, so no hold-out value enters the table;
2. scores the sleeve marks S1 to S6 of decision 16 (kaibot.evaluation.gate_v3);
3. applies Holm's correction across the rule families, using the best-evidenced run of each rule for its family's
   p-value (the engine with SimBroker before the research harness, the point-in-time universe before today's list).

A re-score is information, not a verdict: every rule here was pre-registered before decision 16, so none can
`pass`. A mark that the existing report cannot support is shown as unknown with the reason (for example a control
that drew from the whole universe for a rule that picks among companies with a headline). The book test has
nothing to score yet: no combined book has been run.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kaibot.evaluation import gate_v3 as g3  # noqa: E402
from kaibot.evaluation import prereg, results, tbill, trials  # noqa: E402

EVIDENCE = {"engine_core": 0, "spy_engine": 0, "core": 1, "oneshots": 1, "spy_trend": 1, "spy": 2, "multiday": 3,
            "walkforward": 4}


def evidence_rank(row: results.Row) -> tuple:
    """Lower is better evidence: the engine first, then the point-in-time universe, then cost model v2."""
    cm = row.meta.get("cost_model_row") or row.meta.get("cost_model")
    return (EVIDENCE.get(row.set, 5), 0 if row.meta.get("universe_mode") == "point_in_time" else 1,
            0 if cm == "v2" else 1, row.label)


def gate_rule(gate: g3.GateV3, row: results.Row) -> str:
    """The name the rule has in config/gate_v3.yaml: its own, else the rule of the report it is a version of."""
    if gate.family_of(row.rule) or not row.meta.get("report_rule"):
        return row.rule
    return row.meta["report_rule"] if gate.family_of(row.meta["report_rule"]) else row.rule


def account(row: results.Row, spy) -> g3.Account | None:
    d = results.daily(row)
    if d is None or spy is None:
        return None
    return g3.Account(d["ret"], spy[0], d.get("exposure"), d.get("cost"), row.control,
                      row.meta.get("universe_mode") or "today")


def source_of(row: results.Row, spy_kind: str | None) -> dict:
    cm = row.meta.get("cost_model_row") or row.meta.get("cost_model")
    return {"engine": row.meta.get("engine"), "cost_model": cm or "not stated",
            "data_repairs": bool(row.meta.get("universe_mode") == "point_in_time"
                                 and row.meta.get("bars") in (None, "store")),
            "benchmark": spy_kind or "not stated", "report": row.report, "run_at": row.meta.get("run_at"),
            "code_sha": row.meta.get("code_sha")}


def first_run(registry, rule: str) -> datetime | None:
    stamps = sorted(str(r["ts"]) for r in registry.rows if r.get("strategy") == rule and r.get("ts"))
    try:
        return datetime.fromisoformat(stamps[0].replace("Z", "+00:00")) if stamps else None
    except ValueError:
        return None


def rescore(data: Path, root: Path = ROOT, as_of: datetime | None = None) -> dict:
    gate = g3.GateV3.load(root)
    folder = data / "reports"
    found = results.reports(folder)
    every = [r for f, rep in found for r in results.rows(f, rep)]
    by_file = dict((f.name, (f, rep)) for f, rep in found)
    pool = results.spy_pool(folder)
    bills = tbill.find(data)
    registry = trials.load(None, data / "trials.jsonl")
    base = results.newest_per_rule(every)
    neighbours = {}
    for r in every:
        if r.role == "neighbour" and r.neighbour and r.cost in (None, 1.0, "0.5 bp") and r.meta.get("fill") in (
                None, "base"):
            neighbours.setdefault((r.report, r.rule), {})[r.neighbour] = r

    # pass 1: one p-value per rule, from its best-evidenced run with a usable control
    rule_p: dict[str, float | None] = {}
    for row in sorted(base.values(), key=evidence_rank):
        name = gate_rule(gate, row)
        entry = gate.rule(name)
        n = g3.control_numbers(row.control)
        if name in rule_p or entry is None or n is None or n["p_value"] is None or not n["matched"]:
            continue
        timing = entry["kind"] == "timing" and str(n.get("kind") or "").startswith("random_timing")
        if n["runs"] >= gate.cfg["sleeve"]["s3_control"]["random_runs_min"] and (
                timing or entry.get("existing_control") in g3.SCORABLE_CONTROLS):
            rule_p[name] = n["p_value"]
    fam_p = g3.family_p(gate, rule_p)

    # pass 2: the marks
    out, skipped = [], []
    for (rule, kind), row in sorted(base.items(), key=lambda kv: (kv[0][0], evidence_rank(kv[1]))):
        f, rep = by_file[row.report]
        spy = results.spy(f, rep, pool)
        acc = account(row, spy)
        name = gate_rule(gate, row)
        if acc is None or acc.exposure is None:
            why = "no daily file" if row.daily_file is None else (
                "no SPY series for its sessions" if spy is None else "the daily file has no exposure column")
            skipped.append({"rule": rule, "run": kind, "report": row.report, "why": why, "summary": row.summary})
            continue
        nb = {k: a for k, r in neighbours.get((row.report, row.rule), {}).items()
              if (a := account(r, spy)) is not None}
        try:
            reg = prereg.load(name, root)
        except prereg.PreregError:
            reg = None
        fam = gate.family_of(name)
        try:
            rep3 = g3.evaluate_sleeve(gate, rule=name, account=acc, neighbours=nb, prereg=reg,
                                      source=source_of(row, spy[1]), registry=registry,
                                      first_run_at=first_run(registry, name), tbill=bills,
                                      other_family_p={k: v for k, v in fam_p.items() if k != fam}, as_of=as_of)
        except ValueError as e:
            skipped.append({"rule": rule, "run": kind, "report": row.report, "why": str(e), "summary": row.summary})
            continue
        v2 = rep.get("gate_b")
        out.append({"rule": rule, "scored_as": name, "run": kind, "report": row.report, "label": row.label,
                    "evidence_rank": list(evidence_rank(row))[:3], "gate_v2": v2 if isinstance(v2, list | dict)
                    else None, "gate_v3": rep3})
    return {"generated_at": (as_of or datetime.now(UTC)).isoformat(timespec="seconds"), "gate_sha": gate.sha,
            "window": [str(gate.window[0].date()), str(gate.window[1].date())], "level": gate.level,
            "families": {f: {"rules": list(b["rules"]), "p": fam_p[f]} for f, b in gate.families.items()},
            "holm": g3.holm(fam_p, gate.level), "rule_p": rule_p, "tbill": bills is not None,
            "registry": registry.summary(), "reports_read": len(found), "other_files": results.other_files(folder),
            "rows": out, "skipped": skipped}


def _cell(mark: dict) -> str:
    return {True: "pass", False: "FAIL", None: "?"}[mark["met"]]


def _num(x, fmt="{:.2%}") -> str:
    return "n/a" if x is None else fmt.format(x)


def render(res: dict) -> str:
    out = ["# Gate v3 re-score of the existing rule reports", "",
           f"Generated {res['generated_at']} by `scripts/gate_v3_rescore.py` from {res['reports_read']} reports. No "
           "rule was re-run. Marks are those of decision 16 (`docs/decisions/16_gate_v3.md`), judged on "
           f"{res['window'][0]} to {res['window'][1]}; later sessions were dropped before any number was computed.",
           "", "**This is a re-score, not a verdict.** Every rule below was pre-registered before decision 16, so "
           "none can pass gate v3; `provisional` means every mark that could be scored holds. Gate v2's verdicts "
           "stand as they were given.", "",
           "Idle cash: " + ("Treasury bills on both sides" if res["tbill"] else
                            "0% on both sides (no bill-rate file: run scripts/fetch_tbill.py)")
           + f". Trial registry: {res['registry']['configurations']:,} configurations in all.", "",
           "| Rule | Run | Exposure | Active return a year | 90% interval | t | At 2x costs | Control p | "
           "Holm-adjusted | Deflated Sharpe (N) | S1 | S2 | S3 | S4 | S5 | S6 | Gate v3 |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in res["rows"]:
        g, m = r["gate_v3"], r["gate_v3"]["marks"]
        n1, n2, n3, n4 = (m[k]["numbers"] for k in ("s1", "s2", "s3", "s4"))
        out.append(
            f"| {r['rule']} | {r['run']} | {_num(g['statistics']['mean_exposure'], '{:.0%}')} | "
            f"{_num(n1['annualised'])} | {_num(n1['interval'][0])} to {_num(n1['interval'][1])} | {n1['t']:.2f} | "
            f"{_num((n2.get('active_return_at') or {}).get('2x'))} | {_num(n3.get('p_value'), '{:.4f}')} | "
            f"{_num(n3.get('holm_adjusted'), '{:.4f}')} | {_num(n4.get('dsr'), '{:.3f}')}"
            f"{' (' + format(n4['configurations'], ',') + ')' if n4.get('configurations') else ''} | "
            + " | ".join(_cell(m[k]) for k in ("s1", "s2", "s3", "s4", "s5", "s6")) + f" | {g['verdict']} |")
    out += ["", "## Why a mark is unknown or failed", ""]
    for r in res["rows"]:
        why = [f"{k.upper()}: {v['why']}" for k, v in r["gate_v3"]["marks"].items()
               if v["met"] is not True and v["why"]]
        if why:
            out.append(f"- **{r['rule']}** ({r['run']}): " + "; ".join(why) + ".")
    out += ["", "## Rule families and the correction", "",
            f"Holm's step-down at a family-wise level of {res['level']:g} over {len(res['families'])} families. A "
            "family's p-value is the smallest control p-value of its rules times the number of rules it lists; a "
            "family without a usable control counts as 1.", "",
            "| Family | Rules | Family p | Holm-adjusted | Clears the level |", "|---|---|---|---|---|"]
    for f, body in res["families"].items():
        h = res["holm"][f]
        out.append(f"| {f} | {', '.join(body['rules'])} | {_num(body['p'], '{:.4f}')} | "
                   f"{_num(h['adjusted'] if h['known'] else None, '{:.4f}')} | {'yes' if h['rejected'] else 'no'} |")
    if res["skipped"]:
        out += ["", "## Runs that could not be scored", "", "| Rule | Run | Report | Why |", "|---|---|---|---|"]
        out += [f"| {s['rule']} | {s['run']} | `{s['report']}` | {s['why']} |" for s in res["skipped"]]
    out += ["", f"gate_v3.yaml sha256 `{res['gate_sha']}`."]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--out", default=str(ROOT / "docs" / "research" / "results"))
    ap.add_argument("--root", default=str(ROOT), help=argparse.SUPPRESS)  # tests: another repository root
    a = ap.parse_args(argv)
    if not (Path(a.data) / "reports").is_dir():
        sys.exit(f"no {Path(a.data) / 'reports'}: this script reads the reports of earlier runs (the research Mac)")
    res = rescore(Path(a.data), Path(a.root))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "gate_v3_rescore.json").write_text(json.dumps(res, indent=1, default=str))
    text = render(res)
    (out / "gate_v3_rescore.md").write_text(text)
    print(text)
    print(f"wrote {out / 'gate_v3_rescore.md'} and .json: {len(res['rows'])} runs scored, "
          f"{len(res['skipped'])} could not be scored", file=sys.stderr)
    return res


if __name__ == "__main__":
    main()
