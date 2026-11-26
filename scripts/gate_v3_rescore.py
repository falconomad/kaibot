"""Re-score every existing rule report under gate v3 WITHOUT re-running any rule (M11 ticket 2.5, issue #456).

.venv/bin/python scripts/gate_v3_rescore.py                      reads data/reports, writes the table
.venv/bin/python scripts/gate_v3_rescore.py --data data --out docs/research/results

Reads only what earlier runs wrote: the report JSON files and daily CSVs under <data>/reports, the trial registry
<data>/trials.jsonl and the Treasury-bill file <data>/inputs/fred_dtb3.json (scripts/fetch_tbill.py). It runs
no simulator, loads no price bar and logs no trial. Writes <out>/gate_v3_rescore.md and .json.

What it does, per rule and kind of run (the newest report of each):
1. cuts the account's daily returns to gate v3's research window (2017-05-01 to 2024-12-31). Sessions in the
   hold-out are dropped before any number is computed FROM THE DAILY FILES (S1, S2, S4, S5). One number cannot
   be cut: the control's p-value (S3 and the correction across families) is the one the earlier report stored,
   over that report's whole window, hold-out sessions included when the run went past 2024-12-31. Recomputing
   it needs a re-run. The table marks those rows;
2. scores the sleeve marks S1 to S6 of decision 16 (kaibot.evaluation.gate_v3);
3. applies Holm's correction across the rule families, using the best-evidenced run of each rule for its family's
   p-value (the engine with SimBroker before the research harness, the point-in-time universe before today's list).

A re-score is information, not a verdict: every rule here was pre-registered before decision 16, so none can
`pass`. A mark that the existing report cannot support is shown as unknown with the reason (for example a control
that drew from the whole universe for a rule that picks among companies with a headline).

Books (#456, decision 16 section 5; the index core #458 and the core with its sleeve #459): a report that holds a
book test (`gate_v3_book`, written by scripts/index_core.py and scripts/index_core_book.py) is NOT scored as a
sleeve. For each book, index cap and universe, the newest report is re-scored with `gate_v3.evaluate_book` from its
own daily files: the judged account (`base|1x` / `book|1x`), its pre-declared neighbours, the sleeve verdict its
run stored (`gate_v3_sleeve`, the book script's sleeve test) and the budgets the engine ran. SPY is the series the
run wrote beside its report (`spy_daily.csv`), else the longest total-return series of the folder. Nothing is
re-run. The table shows the verdict the run gave next to the re-score and says when they differ (for example a
report scored under an earlier `config/gate_v3.yaml`). A book report's rows never enter the sleeve table or the
family correction. Its sleeve test is the book script's own, shown with the book.

Gate v3.1 (decision 18, #701): each book row lists N, the configurations of its book idea in the registry, which
B2's chance test deflates by; and a verdict is printed with "hindsight" when an amendment of the pre-registration
was committed after the first logged run.
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
                      row.meta.get("universe_mode") or "today",
                      intraday_exposure=d.get("exposure_intraday"))  # #703: an intraday rule's time in SPY


def source_of(row: results.Row, spy_kind: str | None) -> dict:
    cm = row.meta.get("cost_model_row") or row.meta.get("cost_model")
    return {"engine": row.meta.get("engine"), "cost_model": cm or "not stated",
            "data_repairs": bool(row.meta.get("universe_mode") == "point_in_time"
                                 and row.meta.get("bars") in (None, "store")),
            "benchmark": spy_kind or "not stated", "report": row.report, "run_at": row.meta.get("run_at"),
            "code_sha": row.meta.get("code_sha")}


def holdout_sessions(gate: g3.GateV3, row: results.Row) -> int:
    """Sessions of the row's daily file after the research window (its control saw them)."""
    d = results.daily(row)
    return 0 if d is None else int((d.index > gate.window[1]).sum())


def holm_check(gate: g3.GateV3, name: str, row: results.Row, pool) -> tuple[float | None, list[str]]:
    """#704: the row's control p-value and why it may not enter Holm (empty: it may)."""
    f, rep = row_report(row)
    spy = results.spy(f, rep, pool) if f is not None else None
    return g3.holm_input(gate, name, row.control, source_of(row, spy[1] if spy else None),
                         holdout_sessions(gate, row))


_REPORTS: dict[str, tuple] = {}


def row_report(row: results.Row) -> tuple:
    return _REPORTS.get(row.report, (None, None))


def first_run(registry, rule: str) -> datetime | None:
    stamps = sorted(str(r["ts"]) for r in registry.rows if r.get("strategy") == rule and r.get("ts"))
    try:
        return datetime.fromisoformat(stamps[0].replace("Z", "+00:00")) if stamps else None
    except ValueError:
        return None


def is_book_report(gate: g3.GateV3, rep: dict) -> bool:
    """A report of a book test (#456): it holds `gate_v3_book`, or it is named after a book under `books:`."""
    return isinstance(rep.get("gate_v3_book"), dict) or any(
        isinstance(rep.get(k), str) and gate.book(rep[k]) is not None for k in ("book", "rule"))


BOOK_BASE = ("base|1x", "book|1x")  # the judged account of scripts/index_core.py / scripts/index_core_book.py
BOOK_NEIGHBOUR = ("neighbour|", "book_neighbour|")


def book_name(rep: dict) -> str:
    return str(rep.get("book") or rep.get("rule") or (rep.get("gate_v3_book") or {}).get("rule") or "?")


def book_key(rep: dict) -> tuple[str, str, str]:
    """(book, index cap at Hunter, universe). A report written before `--index-cap` existed (#458) records no cap: it
    ran at config/index_core.yaml's, 27% since #600 (23 Nov 2026, before any book run)."""
    cap = rep.get("index_cap_pct", 27.0)
    return (book_name(rep), f"{float(cap):g}%", str(rep.get("universe") or "?"))


def rescore_books(gate: g3.GateV3, found, pool, registry, bills, root: Path, as_of) -> tuple[list, list]:
    """(one re-scored row per book, index cap and universe: the newest report of each; the book reports that
    could not be scored). Reads only the reports' daily files; nothing is re-run."""
    newest: dict[tuple, tuple] = {}
    for f, rep in found:  # oldest first, so the newest report of a key wins
        newest[book_key(rep)] = (f, rep)
    out, skipped = [], []
    for key, (f, rep) in sorted(newest.items()):
        name = key[0]
        rows = {r.label: r for r in results.rows(f, rep)}
        base = next((rows[k] for k in BOOK_BASE if k in rows), None)
        spy = results.spy(f, rep, pool)
        d = results.daily(base) if base is not None else None
        if d is None or spy is None:
            why = "no judged account (base|1x / book|1x) with a daily file" if d is None else \
                "no SPY series for its sessions"
            skipped.append({"rule": name, "run": f"book, index cap {key[1]}, {key[2]}", "report": f.name,
                            "why": why, "summary": {}})
            continue
        uni = "point_in_time" if key[2] in ("pit", "point_in_time") else key[2]
        acc = g3.Account(d["ret"], spy[0], d.get("exposure"), d.get("cost"), None, uni)
        nb = {}
        for label, r in rows.items():
            if label.startswith(BOOK_NEIGHBOUR) and (nd := results.daily(r)) is not None:
                nb[label.split("|", 1)[1]] = nd["ret"]
        stored_sleeve = rep.get("gate_v3_sleeve") if isinstance(rep.get("gate_v3_sleeve"), dict) else None
        listed = gate.book(name) or {}
        if stored_sleeve is not None:
            sleeves = {str(stored_sleeve.get("rule")): str(stored_sleeve.get("verdict"))}
        elif not (listed.get("sleeves") or {}):
            sleeves = {}  # a book without a return sleeve: B6 holds with the empty mapping
        else:
            sleeves = None  # a listed sleeve without a stored verdict: B6 unknown
        ran = (rep.get("budgets") or {}).get("engine") if isinstance(rep.get("budgets"), dict) else None
        if ran is None and not (listed.get("sleeves") or {}):
            ran = {}
        try:
            reg = prereg.load(name, root)
        except prereg.PreregError:
            reg = None
        src = rep.get("source") if isinstance(rep.get("source"), dict) else {}
        try:
            rep3 = g3.evaluate_book(gate, book=name, account=acc, neighbours=nb, sleeves=sleeves, prereg=reg,
                                    source={**src, "report": f.name, "run_at": rep.get("run_at"),
                                            "code_sha": rep.get("code_sha")},
                                    registry=registry, first_run_at=first_run(registry, name), tbill=bills,
                                    as_of=as_of, ran_budgets=ran)
        except ValueError as e:
            skipped.append({"rule": name, "run": f"book, index cap {key[1]}, {key[2]}", "report": f.name,
                            "why": str(e), "summary": {}})
            continue
        stored = rep.get("gate_v3_book") if isinstance(rep.get("gate_v3_book"), dict) else {}
        out.append({"book": name, "index_cap": key[1], "universe": key[2], "report": f.name,
                    "run_at": rep.get("run_at"), "gate_v3": rep3, "spy": spy[1],
                    "sleeve_test": None if stored_sleeve is None else {
                        "rule": stored_sleeve.get("rule"), "verdict": stored_sleeve.get("verdict")},
                    "at_run": {"verdict": stored.get("verdict"), "gate_sha": stored.get("gate_sha"),
                               "marks": {k: (v or {}).get("met") for k, v in (stored.get("marks") or {}).items()}}})
    return out, skipped


def rescore(data: Path, root: Path = ROOT, as_of: datetime | None = None) -> dict:
    gate = g3.GateV3.load(root)
    folder = data / "reports"
    everything = results.reports(folder)
    if as_of is None and (newest := results.reports_as_of(everything)):
        as_of = datetime.fromisoformat(newest)  # #452: the newest report's time, so a rerun writes the same file
    found = [(f, rep) for f, rep in everything if not is_book_report(gate, rep)]
    book_found = [(f, rep) for f, rep in everything if is_book_report(gate, rep)]
    every = [r for f, rep in found for r in results.rows(f, rep)]
    by_file = dict((f.name, (f, rep)) for f, rep in found)
    _REPORTS.clear()
    _REPORTS.update(by_file)
    pool = results.spy_pool(folder)
    bills = tbill.find(data)
    registry = trials.load(None, data / "trials.jsonl")
    books, books_skipped = rescore_books(gate, book_found, pool, registry, bills, root, as_of)
    base = results.newest_per_rule(every)
    neighbours = {}
    for r in every:
        if r.role == "neighbour" and r.neighbour and r.cost in (None, 1.0, "0.5 bp") and r.meta.get("fill") in (
                None, "base"):
            neighbours.setdefault((r.report, r.rule), {})[r.neighbour] = r

    # pass 1: one p-value per rule, from its best-evidenced run whose p-value may enter Holm (#704, decision 18
    # s.8: final_requires met, enough runs, a matched control, no hold-out session). Every other p-value is kept
    # greyed out: shown, never in the correction (it counts as 1 there)
    rule_p: dict[str, float | None] = {}
    greyed: dict[str, dict] = {}
    for row in sorted(base.values(), key=evidence_rank):
        name = gate_rule(gate, row)
        if name in rule_p or gate.rule(name) is None:
            continue
        p, why = holm_check(gate, name, row, pool)
        if p is None:
            continue
        if not why:
            rule_p[name] = p
            greyed.pop(name, None)
        elif name not in greyed:
            greyed[name] = {"p": p, "why": why, "report": row.report, "label": row.label}
    fam_p = g3.family_p(gate, rule_p)
    fam_grey = {f: {r: greyed[r] for r in b["rules"] if r in greyed and r not in rule_p}
                for f, b in gate.families.items()}

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
        hp, hwhy = holm_check(gate, name, row, pool)
        out.append({"rule": rule, "scored_as": name, "run": kind, "report": row.report, "label": row.label,
                    "evidence_rank": list(evidence_rank(row))[:3], "gate_v2": v2 if isinstance(v2, list | dict)
                    else None, "gate_v3": rep3,
                    "holm_input": {"p": hp, "enters_holm": hp is not None and not hwhy, "why_not": hwhy}})
    return {"generated_at": (as_of or datetime.now(UTC)).isoformat(timespec="seconds"), "gate_sha": gate.sha,
            "window": [str(gate.window[0].date()), str(gate.window[1].date())], "level": gate.level,
            "families": {f: {"rules": list(b["rules"]), "p": fam_p[f], "greyed": fam_grey[f]}
                         for f, b in gate.families.items()},
            "holm": g3.holm(fam_p, gate.level), "rule_p": rule_p, "rule_p_greyed": greyed, "tbill": bills is not None,
            "registry": registry.summary(), "reports_read": len(everything),
            "other_files": results.other_files(folder), "rows": out, "books": books,
            "skipped": skipped + books_skipped}


DAGGER = "†"  # a control p-value from a report whose window ran into the hold-out


def _cell(mark: dict) -> str:
    return {True: "pass", False: "FAIL", None: "?"}[mark["met"]]


def _num(x, fmt="{:.2%}") -> str:
    return "n/a" if x is None else fmt.format(x)


def _pcell(r: dict, n3: dict) -> str:
    """The control p of a sleeve row; greyed out (italics) when it may not enter Holm (#704)."""
    p = n3.get("p_value")
    if p is None:
        return "n/a"
    cell = f"{p:.4f}{DAGGER if r['gate_v3'].get('sessions_in_holdout_dropped') else ''}"
    return cell if (r.get("holm_input") or {}).get("enters_holm") else f"_{cell} (greyed)_"


def _hcell(r: dict, n3: dict) -> str:
    if n3.get("holm_adjusted") is None:
        return "n/a"
    return _num(n3["holm_adjusted"], "{:.4f}") if (r.get("holm_input") or {}).get("enters_holm") else "_greyed_"


def render(res: dict) -> str:
    out = ["# Gate v3 re-score of the existing rule reports", "",
           f"Generated {res['generated_at']} by `scripts/gate_v3_rescore.py` from {res['reports_read']} reports. No "
           "rule was re-run. Marks are those of decision 16 (`docs/decisions/16_gate_v3.md`), judged on "
           f"{res['window'][0]} to {res['window'][1]}; later sessions were dropped before any number was computed "
           "from the daily files. The exception is the control's p-value (S3, the Holm column and the families "
           "table): it is the earlier report's own figure over that report's whole window. Where that window ran "
           "into the hold-out the p-value is marked with a dagger (†): it has seen hold-out sessions and is not a "
           "research-window number.",
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
            f"{_num((n2.get('active_return_at') or {}).get('2x'))} | {_pcell(r, n3)} | "
            f"{_hcell(r, n3)} | {_num(n4.get('dsr'), '{:.3f}')}"
            f"{' (' + format(n4['configurations'], ',') + ')' if n4.get('configurations') else ''} | "
            + " | ".join(_cell(m[k]) for k in ("s1", "s2", "s3", "s4", "s5", "s6"))
            + f" | {g.get('verdict_text') or g['verdict']} |")
    out += ["", "## Why a mark is unknown or failed", ""]
    for r in res["rows"]:
        why = [f"{k.upper()}: {v['why']}" for k, v in r["gate_v3"]["marks"].items()
               if v["met"] is not True and v["why"]]
        hi = r.get("holm_input") or {}
        if hi.get("p") is not None and not hi.get("enters_holm"):
            why.append("control p greyed out, not in Holm: " + ", ".join(hi.get("why_not") or []))
        if why:
            out.append(f"- **{r['rule']}** ({r['run']}): " + "; ".join(why) + ".")
    out += ["", "## Rule families and the correction", "",
            f"Holm's step-down at a family-wise level of {res['level']:g} over {len(res['families'])} families. A "
            "family's p-value is the smallest control p-value of its rules times the number of rules it lists; a "
            "family without a usable control counts as 1.", "",
            "Gate v3.1 (decision 18 section 8): only a p-value from a run that meets `final_requires` (the engine "
            "with SimBroker, cost model v2, data repairs, SPY total return), with enough control runs, a matched "
            "control (at most 0.5% of slots unfilled) and no hold-out session enters Holm. Every other p-value is "
            "shown greyed out, in italics with the reason, and counts as 1.", "",
            "| Family | Rules | Family p (in Holm) | Holm-adjusted | Clears the level | Greyed out (not in Holm) |",
            "|---|---|---|---|---|---|"]
    for f, body in res["families"].items():
        h = res["holm"][f]
        grey = "; ".join(f"_{r} p {g['p']:.4f}: {', '.join(g['why'])}_" for r, g in (body.get("greyed") or {}).items())
        out.append(f"| {f} | {', '.join(body['rules'])} | {_num(body['p'], '{:.4f}')} | "
                   f"{_num(h['adjusted'] if h['known'] else None, '{:.4f}')} | {'yes' if h['rejected'] else 'no'} | "
                   f"{grey or '-'} |")
    out += ["", "## Books (the book test, decision 16 section 5)", ""]
    if not res.get("books"):
        out.append("No book report was found.")
    else:
        out += ["Gate v3.1 (decision 18): B2 holds only when a branch holds and the chance test holds: the book's "
                "Sharpe over SPY's, deflated by N, the configurations logged for the book's idea. `hindsight` in the "
                "verdict: an amendment of the pre-registration was committed after the first logged run.", "",
                "Each book is re-scored from its own daily files (nothing re-run), judged at Hunter on the research "
                "window, with idle cash at 0% (the T-bill view is in the JSON). A report that records no index cap "
                "ran at the config's 27% (#600). `At run` is the verdict the run itself gave. A book's sleeve test "
                "is the one its script ran (`gate_v3_sleeve`); its rows are not "
                "in the sleeve table or in the family correction above.", "",
                "| Book | Index cap at Hunter | Universe | CAGR | SPY CAGR | Max drawdown | SPY max drawdown | "
                "N (book idea) | B2 chance (deflated) | B1 | B2 | B3 | B4 | B5 | B6 | Sleeve test | Gate v3 | At run |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for b in res["books"]:
            g, m = b["gate_v3"], b["gate_v3"]["marks"]
            n = m["b2"]["numbers"]
            same = b["at_run"]["verdict"] == g["verdict"] and all(
                b["at_run"]["marks"].get(k) == m[k]["met"] for k in m if k in b["at_run"]["marks"])
            at = b["at_run"]["verdict"] or "n/a"
            if not same:
                at += " (differs" + ("; scored under another gate_v3.yaml" if b["at_run"]["gate_sha"] not in (
                    None, g["gate_sha"]) else "") + ")"
            st = b["sleeve_test"]
            nb, ch = g.get("book_configurations") or {}, n.get("chance") or {}
            ncell = f"{nb['configurations']} ({nb['idea']})" if nb.get("configurations") is not None else \
                f"? ({nb.get('idea', b['book'])})"
            out.append(f"| {b['book']} | {b['index_cap']} | {b['universe']} | {_num(n.get('cagr'))} | "
                       f"{_num(n.get('spy_cagr'))} | {_num(n.get('max_drawdown'))} | "
                       f"{_num(n.get('spy_max_drawdown'))} | {ncell} | {_num(ch.get('dsr'), '{:.3f}')} | "
                       + " | ".join(_cell(m[k]) for k in ("b1", "b2", "b3", "b4", "b5", "b6"))
                       + f" | {'none' if st is None else st['rule'] + ': ' + str(st['verdict'])} | "
                       f"{g.get('verdict_text') or g['verdict']} | {at} |")
        out += ["", "Why a book mark is unknown or failed:", ""]
        for b in res["books"]:
            why = [f"{k.upper()}: {v['why']}" for k, v in b["gate_v3"]["marks"].items()
                   if v["met"] is not True and v["why"]]
            why += [f"provisional: {r}" for r in b["gate_v3"].get("provisional_reasons") or []]
            if why:
                out.append(f"- **{b['book']}** (index cap {b['index_cap']}, `{b['report']}`): " + "; ".join(why)
                           + ".")
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
    (out / "gate_v3_rescore.json").write_text(results.portable(json.dumps(res, indent=1, default=str), a.root,
                                                               a.data))
    text = results.portable(render(res), a.root, a.data)  # no path of this machine in the repository (#594, #452)
    (out / "gate_v3_rescore.md").write_text(text)
    print(text)
    print(f"wrote {out / 'gate_v3_rescore.md'} and .json: {len(res['rows'])} runs scored, "
          f"{len(res['skipped'])} could not be scored", file=sys.stderr)
    return res


if __name__ == "__main__":
    main()
