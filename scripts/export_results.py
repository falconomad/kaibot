"""Export every research run's summary from data/reports into the repository (M11 tickets 2.1 and 2.2, issues
#452 and #453). It reads what earlier runs wrote and re-runs nothing.

.venv/bin/python scripts/export_results.py                       all rules -> docs/research/results/
.venv/bin/python scripts/export_results.py --rule trend_momentum  one rule's page only

Why: the ten-year results exist only on the research Mac, in data/ (which git ignores) and in issue comments.
The audit could not read several of them. This writes, for every pre-registration, one page and one JSON file
under docs/research/results/ plus an index (README.md), small enough to commit.

Reads: <data>/reports/*.json and the daily CSV of each account, <data>/trials.jsonl, <data>/inputs/fred_dtb3.json
(scripts/fetch_tbill.py), docs/prereg/*.md, config/gate_v3.yaml. Runs no simulator, reads no price bar, logs no
trial and writes nothing under <data>.

Per rule and per kind of run (set, universe, cost model, cost scenario), from the NEWEST report of that kind:
- what the run was: report file, date, code version, window, universe, engine, cost model, status;
- the account: CAGR, return, maximum drawdown, Sharpe, mean exposure, costs a year (from its daily file);
- SPY on the same sessions, and SPY held at the rule's own exposure;
- alpha against SPY with its Newey-West t, and beta;
- idle cash both ways (ticket 2.4): at 0% as simulated, and at the Treasury-bill rate with Sharpe and alpha
  above bills;
- the matched random control: percentile, runs, what it drew from;
- the gate v2 marks exactly as the report stored them (never recomputed here);
- the trial count: configurations logged for the rule and in the whole registry;
- its neighbours (ticket 2.2): each with the change it makes (from the pre-registration), exposure, return,
  drawdown, beta, alpha and the difference from the frozen rule.

Two windows are shown for every account: the run's own window, as it was run and already reported; and the
research window of gate v3 (2017-05-01 to 2024-12-31). The first includes sessions that are now in the hold-out
(config/holdout.yaml): those runs were made before the hold-out was frozen, and their numbers are history, not
new reads. A pre-registration with no report on this machine still gets a page that says so.

The one-table pages the run scripts wrote themselves (experiments_*.md, the two studies' reports) are copied
unchanged into docs/research/results/as_reported/.

Every run, not only the newest of its kind (#452): one small JSON per report in docs/research/results/runs/,
named after the report, with each base account's marks on the research window only (CAGR, drawdown, Sharpe,
exposure, alpha and its t, beta, SPY at the rule's exposure, idle cash in bills), its neighbours' headline and the
trial count; and, as the run stored them on its own window, the control's percentile and the gate v2 marks (never
recomputed). Numbers are rounded to six places.

Deterministic (#452): the time written on every page is the newest report's own time, not the clock, and no path
of this machine is written (the data folder becomes `data/`, the repository root `.`). The same reports give the
same files byte for byte.

The gate v3 marks are in docs/research/results/gate_v3_rescore.md (scripts/gate_v3_rescore.py).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kaibot.evaluation import gate_v3 as g3  # noqa: E402
from kaibot.evaluation import mtm, results, tbill, trials  # noqa: E402

OUT = Path("docs") / "research" / "results"
KW = {"periods": 252, "haircut": 0.5, "warn_above": 1.0}


# The one-table pages the run scripts write (and the two studies' reports): which rules each covers.
SUMMARIES = ("experiments_*.md", "filing_flags_study.md", "market_alarm_backfill.md", "index_cost_calibration.md")
SUMMARY_OF = {"trend_momentum": ("experiments_core",), "mom_12_1_band": ("experiments_core",),
              "spy_noise_band": ("experiments_spy_summary", "experiments_spy_engine", "experiments_spy_cost_rerun"),
              "spy_trend_filter": ("experiments_spy_trend",), "market_alarm_study": ("market_alarm_backfill",),
              **{r: ("experiments_multiday",) for r in ("trend_highs", "rel_strength", "winners_run", "overnight_spy")},
              **{r: ("experiments_oneshots",) for r in ("orb_stocks_in_play", "news_continuation", "pullback_uptrend",
                                                        "ath_breakout", "post_earnings_week", "weekly_fall_rebound",
                                                        "news_gap_week")}}


def as_reported(folder: Path) -> dict[str, Path]:
    """The Markdown pages the run scripts wrote under data/reports, by file name."""
    return {f.name: f for pat in SUMMARIES for f in sorted(folder.glob(pat))}


def prereg_rules(root: Path) -> list[str]:
    return sorted(p.stem for p in (root / "docs" / "prereg").glob("*.md") if p.stem != "TEMPLATE")


def neighbour_notes(root: Path, rule: str) -> dict[str, str]:
    """What each neighbour changes, from the table of the rule's pre-registration (`| \\`name\\` | change |`)."""
    f = root / "docs" / "prereg" / f"{rule}.md"
    if not f.is_file():
        return {}
    out = {}
    for m in re.finditer(r"^\|\s*`([A-Za-z0-9_.-]+)`\s*\|\s*([^|]+?)\s*\|\s*$", f.read_text(), flags=re.M):
        out.setdefault(m.group(1), m.group(2))
    return out


def owner(gate: g3.GateV3, row: results.Row, names: set[str]) -> str:
    """The pre-registration a row belongs to: its own name, else the rule of the report it is a version of."""
    if row.rule in names:
        return row.rule
    parent = row.meta.get("report_rule")
    return parent if parent in names else row.rule


def family_rules(gate: g3.GateV3, rule: str) -> tuple[str, ...]:
    fam = gate.family_of(rule)
    return tuple(gate.families[fam]["rules"]) if fam else ()


def _met(m: dict) -> str:
    return "pass" if m.get("met") else ("fail" if m.get("met") is False else "unknown")


def stats(net: pd.Series, spy: pd.Series | None, exposure: pd.Series | None, cost: pd.Series | None,
          bills: pd.Series | None) -> dict:
    """Everything the page shows for one account on one window."""
    out = {"window": [net.index[0].date().isoformat(), net.index[-1].date().isoformat()], "sessions": int(net.size),
           "rule": mtm.curve(net, **KW), "mean_exposure": None if exposure is None else float(exposure.mean()),
           "costs_a_year": None if cost is None else float(cost.mean() * KW["periods"]),
           "spy": None, "alpha_beta": None, "exposure_matched_spy": None, "active_return": None, "cash": None}
    if spy is None or net.size < 30:
        return out
    out["spy"] = mtm.curve(spy, **KW)
    out["alpha_beta"] = asdict(mtm.alpha_beta(net, spy, periods=KW["periods"]))
    if exposure is not None:
        bench = pd.Series(mtm.exposure_matched(spy, exposure), index=net.index)
        out["exposure_matched_spy"] = mtm.curve(bench, **KW)
        out["active_return"] = g3.mean_stats(net - bench, KW["periods"])
    if bills is not None:
        try:
            out["cash"] = mtm.cash_views(net, spy, exposure, mtm.tbill_daily(bills, net.index), **KW)
        except ValueError as e:
            out["cash"] = {"error": str(e)}
    return out


def account(row: results.Row, spy: tuple[pd.Series, str] | None, gate: g3.GateV3, bills) -> dict:
    """One account on its own window and on the research window; `why` when its daily file cannot be read."""
    base = {"label": row.label, "role": row.role, "neighbour": row.neighbour, "cost": row.cost,
            "report_summary": row.summary, "own_window": None, "research_window": None, "why": None,
            "spy_kind": spy[1] if spy else None}
    d = results.daily(row)
    if d is None:
        return {**base, "why": "no daily file on this machine"}
    net = mtm.dated(d["ret"])
    series = None
    if spy is not None:
        try:
            series = mtm.align(net, spy[0])[1]
        except ValueError as e:
            base["why"] = f"SPY does not cover its sessions ({e})"
    e = mtm.dated(d["exposure"], "exposure") if "exposure" in d else None
    c = mtm.dated(d["cost"], "cost") if "cost" in d else None
    base["own_window"] = stats(net, series, e, c, bills)
    lo, hi = gate.window
    keep = (net.index >= lo) & (net.index <= hi)
    if keep.sum() >= 30:
        base["research_window"] = stats(net[keep], None if series is None else series[keep],
                                        None if e is None else e[keep], None if c is None else c[keep], bills)
    return base


def export(data: Path, root: Path = ROOT, only: str | None = None, as_of: datetime | None = None) -> dict:
    gate = g3.GateV3.load(root)
    folder = data / "reports"
    found = results.reports(folder)
    if as_of is None and (newest := results.reports_as_of(found)):
        as_of = datetime.fromisoformat(newest)  # #452: the newest report's time, never the clock
    by_file = {f.name: (f, rep) for f, rep in found}
    every = [r for f, rep in found for r in results.rows(f, rep)]
    pool = results.spy_pool(folder)
    bills = tbill.find(data)
    registry = trials.load(None, data / "trials.jsonl")
    by_strategy = registry.by_strategy()
    names = set(prereg_rules(root))
    listed = sorted(names | {owner(gate, r, names) for r in every})
    pages = {}
    for rule in ([only] if only else listed):
        mine = [r for r in every if owner(gate, r, names) == rule]
        base = results.newest_per_rule(mine)
        notes = neighbour_notes(root, rule)
        runs = []
        for (name, kind), row in sorted(base.items(), key=lambda kv: (kv[0][0] != rule,
                                                                         results.evidence_rank(kv[1]), kv[0])):
            f, rep = by_file[row.report]
            spy = results.spy(f, rep, pool)
            acc = account(row, spy, gate, bills)
            nb = [{**account(r, spy, gate, bills), "change": notes.get(r.neighbour or "", "")}
                  for r in mine if r.report == row.report and r.rule == row.rule and r.role == "neighbour"
                  and r.cost in (None, 1.0, row.cost) and r.meta.get("fill") in (None, "base")]
            others = [{"label": r.label, "role": r.role, "cost": r.cost, **r.summary}
                      for r in mine if r.report == row.report and r.rule == row.rule
                      and r.role not in ("base", "neighbour")]
            v2 = rep.get("gate_b")
            if isinstance(rep.get("versions"), dict) and row.meta.get("version") in rep["versions"]:
                v2 = rep["versions"][row.meta["version"]].get("gate_b", v2)
            runs.append({"account_of": name, "run": kind, "report": row.report, "meta": row.meta, "account": acc,
                         "control": g3.control_numbers(row.control),
                         "control_drew_from": (gate.rule(rule) or {}).get("existing_control"),
                         "gate_v2": v2, "verdict_as_reported": rep.get("verdict"), "neighbours": nb,
                         "other_rows": others})
        pages[rule] = {"rule": rule, "preregistered": rule in names, "family": gate.family_of(rule),
                       "generated_at": (as_of or datetime.now(UTC)).isoformat(timespec="seconds"),
                       "trials": {"rule": by_strategy.get(rule, 0), "registry": registry.configurations,
                                  "family": sum(by_strategy.get(r, 0) for r in family_rules(gate, rule))},
                       "tbill": bills is not None, "reports_of_this_rule": sorted({r.report for r in mine}),
                       "runs": runs}
    runs_of = {} if only else run_summaries(found, every, pool, gate, bills, by_strategy, names, as_of)
    written = as_reported(folder)
    for rule, page in pages.items():
        page["as_reported"] = sorted(n for n in written if rule in n or any(k in n for k in SUMMARY_OF.get(rule, ())))
    return {"generated_at": (as_of or datetime.now(UTC)).isoformat(timespec="seconds"), "gate_v3_sha": gate.sha,
            "as_reported": sorted(written),
            "research_window": [str(gate.window[0].date()), str(gate.window[1].date())], "reports_read": len(found),
            "other_files": results.other_files(folder), "registry": registry.summary(), "pages": pages,
            "runs": runs_of}


def _r(x):
    """Floats rounded to six places, recursively: the summaries stay small and stable across platforms."""
    if isinstance(x, float):
        return None if x != x else round(x, 6)
    if isinstance(x, dict):
        return {k: _r(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [_r(v) for v in x]
    return x


def headline(s: dict | None) -> dict | None:
    """The marks of one account on one window (the research window: never a hold-out session)."""
    if s is None:
        return None
    r, ab, em, act, c = s["rule"], s["alpha_beta"], s["exposure_matched_spy"], s["active_return"], s["cash"]
    cash = None
    if c and "error" not in c and c["tbill_cash"]["rule"] is not None:
        b = c["tbill_cash"]
        cash = {"bills_a_year": c["rf"]["mean_ann"], "cagr": b["rule"]["cagr"],
                "sharpe_above_bills": b["rule"]["sharpe"]["sharpe"], "alpha": b["alpha_beta"]["alpha"],
                "alpha_t": b["alpha_beta"]["alpha_t"]}
    return _r({"window": s["window"], "sessions": s["sessions"], "cagr": r["cagr"], "max_drawdown": r["max_drawdown"],
               "sharpe": r["sharpe"]["sharpe"], "mean_exposure": s["mean_exposure"],
               "costs_a_year": s["costs_a_year"], "alpha": ab["alpha"] if ab else None,
               "alpha_t": ab["alpha_t"] if ab else None, "beta": ab["beta"] if ab else None,
               "exposure_matched_spy_cagr": em["cagr"] if em else None,
               "return_above_exposure_matched_spy": act["annualised"] if act else None,
               "return_above_exposure_matched_spy_t": act["t"] if act else None, "idle_cash_in_bills": cash})


CONTROL_KEYS = ("percentile", "runs", "p_value", "matched", "kind")
NEIGHBOUR_KEYS = ("cagr", "max_drawdown", "mean_exposure", "alpha", "alpha_t", "beta")


def _neighbour(n: results.Row, acc: dict) -> dict:
    h = headline(acc["research_window"])
    if h is None:
        return {"neighbour": n.neighbour, "label": n.label, "why": acc["why"] or "not enough sessions"}
    return {"neighbour": n.neighbour, "label": n.label, **{k: h[k] for k in NEIGHBOUR_KEYS}}


def run_summaries(found, every, pool, gate: g3.GateV3, bills, by_strategy: dict, names: set[str],
                  as_of: datetime | None) -> dict[str, dict]:
    """#452: one small summary per report read, keyed by the report's file name without `.json`."""
    out = {}
    for f, rep in found:
        mine = [r for r in every if r.report == f.name]
        bases = [r for r in mine if r.role == "base"]
        if not bases:
            continue
        spy = results.spy(f, rep, pool)
        accounts = []
        for row in sorted(bases, key=lambda r: r.label):
            a = account(row, spy, gate, bills)
            nb = [_neighbour(n, account(n, spy, gate, bills)) for n in sorted(mine, key=lambda r: r.label)
                  if n.rule == row.rule and n.role == "neighbour" and n.cost in (None, 1.0, row.cost)
                  and n.meta.get("fill") in (None, "base")]
            rule = owner(gate, row, names)
            c = g3.control_numbers(row.control) or {}
            accounts.append({"label": row.label, "rule": rule, "run": results.run_kind(row),
                             "research_window": headline(a["research_window"]),
                             "why": a["why"] if a["research_window"] is None else None,
                             "control_as_stored": _r({k: c.get(k) for k in CONTROL_KEYS})
                             if c.get("percentile") is not None else None,
                             "trials_for_rule": by_strategy.get(rule, 0), "neighbours": nb})
        m = bases[0].meta
        v2 = rep.get("gate_b")
        out[f.stem] = {"report": f.name, "set": m.get("set"), "run_at": m.get("run_at"), "code_sha": m.get("code_sha"),
                       "universe_mode": m.get("universe_mode"), "engine": m.get("engine"),
                       "cost_model": m.get("cost_model"), "own_window": [m.get("start"), m.get("end")]
                       if m.get("start") or m.get("end") else m.get("window"),
                       "research_window": [str(gate.window[0].date()), str(gate.window[1].date())],
                       "gate_v2_as_stored": _v2_lines(v2, rep.get("verdict")),
                       "summaries_as_of": (as_of or datetime.now(UTC)).isoformat(timespec="seconds"),
                       "accounts": accounts}
    return out


# ---------------------------------------------------------------- Markdown


def _p(x, digits: int = 2) -> str:
    return "n/a" if x is None else f"{x * 100:.{digits}f}%"


def _f(x, fmt: str = "{:.2f}") -> str:
    return "n/a" if x is None else fmt.format(x)


def _window_rows(name: str, s: dict | None) -> list[str]:
    if s is None:
        return [f"| {name} | not enough sessions | | | | | | | | | |"]
    r, spy, ab, em, act = s["rule"], s["spy"], s["alpha_beta"], s["exposure_matched_spy"], s["active_return"]
    return [f"| {name}: {s['window'][0]} to {s['window'][1]} | {_p(r['cagr'])} | {_p(r['max_drawdown'])} | "
            f"{_f(r['sharpe']['sharpe'])} | {_p(s['mean_exposure'], 0)} | "
            f"{_p(spy['cagr']) if spy else 'n/a'} / {_p(spy['max_drawdown']) if spy else 'n/a'} | "
            f"{_p(em['cagr']) if em else 'n/a'} / {_p(em['max_drawdown']) if em else 'n/a'} | "
            f"{_p(act['annualised']) if act else 'n/a'} ({_f(act['t']) if act else 'n/a'}) | "
            f"{_p(ab['alpha']) if ab else 'n/a'} ({_f(ab['alpha_t']) if ab else 'n/a'}) | "
            f"{_f(ab['beta']) if ab else 'n/a'} | {_p(s['costs_a_year'])} |"]


HEAD = ["| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: "
        "CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |",
        "|---|---|---|---|---|---|---|---|---|---|---|"]


def _cash_lines(s: dict | None) -> list[str]:
    c = (s or {}).get("cash")
    if not c:
        return ["- Idle cash at the Treasury-bill rate: not computed (no bill-rate file on this machine)."]
    if "error" in c:
        return [f"- Idle cash at the Treasury-bill rate: not computed ({c['error']})."]
    z, b = c["zero_cash"], c["tbill_cash"]
    if b["rule"] is None:
        return [f"- Idle cash: bills paid {_p(c['rf']['mean_ann'])} a year. No exposure series, so interest cannot "
                "be added; the simulated curve's Sharpe above bills is "
                f"{_f(b['simulated_excess']['sharpe']['sharpe'])}."]
    return [f"- Idle cash, both versions (research window): bills paid {_p(c['rf']['mean_ann'])} a year and "
            f"{_p(c['mean_idle_share'], 0)} of the account was idle. At 0%: CAGR {_p(z['rule']['cagr'])}, Sharpe "
            f"{_f(z['rule']['sharpe']['sharpe'])}, alpha {_p(z['alpha_beta']['alpha'])} (t "
            f"{_f(z['alpha_beta']['alpha_t'])}). In bills: CAGR {_p(b['rule']['cagr'])}, Sharpe above bills "
            f"{_f(b['rule']['sharpe']['sharpe'])}, alpha {_p(b['alpha_beta']['alpha'])} (t "
            f"{_f(b['alpha_beta']['alpha_t'])}). SPY's Sharpe above bills: {_f(b['benchmark']['sharpe']['sharpe'])}."]


def _v2_lines(v2, verdict) -> list[str]:
    if not v2:
        return ["- Gate v2 marks: none stored in this report."]
    out = []
    if isinstance(v2, list):  # the harness's list: one entry per mark
        cells = [f"{m.get('mark')}: {m.get('verdict')}" for m in v2 if isinstance(m, dict)]
        out.append("- Gate v2 marks as the report stored them: " + "; ".join(cells) + ".")
    elif isinstance(v2, dict) and "marks" in v2:
        cells = [f"{k.replace('mark_', '')}: {_met(m)}" for k, m in v2["marks"].items()]
        out.append(f"- Gate v2 as the report stored it: verdict **{v2.get('verdict')}**; marks " + "; ".join(cells)
                   + ".")
    elif isinstance(v2, dict):  # one evaluation per cost model
        for k, g in v2.items():
            if isinstance(g, dict) and "marks" in g:
                cells = [f"{m.replace('mark_', '')}: {_met(x)}" for m, x in g["marks"].items()]
                out.append(f"- Gate v2 as the report stored it, cost model {k}: verdict **{g.get('verdict')}**; marks "
                           + "; ".join(cells) + ".")
    if verdict:
        out.append(f"- The report's own verdict line: {verdict}.")
    return out or ["- Gate v2 marks: stored in a shape this script does not read; see the report file."]


def render_page(page: dict, research_window: list[str]) -> str:
    rule = page["rule"]
    t = page["trials"]
    out = [f"# {rule}: results of every run on record", "",
           f"Written {page['generated_at']} by `scripts/export_results.py` from the reports on the research Mac. "
           "No rule was re-run. Pre-registration: " + (f"`docs/prereg/{rule}.md`" if page["preregistered"] else
                                                        "none (a version of another rule, or a study)")
           + f". Family in gate v3: {page['family'] or 'none'}.", "",
           f"- Trials logged: {t['rule']:,} configurations for this rule, {t['family']:,} for its family, "
           f"{t['registry']:,} in the whole registry.",
           f"- The research window is {research_window[0]} to {research_window[1]}. A run's own window may go on "
           "into what is now the hold-out; those runs were made before it was frozen.",
           "- Gate v3 marks for these runs: [`gate_v3_rescore.md`](gate_v3_rescore.md)."]
    if page.get("as_reported"):
        out.append("- Pages the run scripts wrote themselves, copied unchanged: "
                   + ", ".join(f"[`{n}`](as_reported/{n})" for n in page["as_reported"]) + ".")
    if not page["runs"]:
        out += ["", "**No account of this rule is in `data/reports` on this machine.** Either it was never run here, "
                    "or it is a study that writes a table and no account (see the copied pages above, if any). "
                    "Nothing is shown rather than a number from memory."]
        return "\n".join(out) + "\n"
    for run in page["runs"]:
        a, m = run["account"], run["meta"]
        title = run["run"] + ("" if run["account_of"] == rule else f" (version {run['account_of']})")
        out += ["", f"## {title}", "",
                f"Report `{run['report']}`, run {m.get('run_at') or 'date not stored'} at code "
                f"`{m.get('code_sha') or '?'}`. Account `{a['label']}`. Engine: {m.get('engine')}. Universe: "
                f"{m.get('universe_mode')}. Cost model: {m.get('cost_model_row') or m.get('cost_model') or a['cost']}. "
                f"SPY series: {a['spy_kind'] or 'none found'}.", ""]
        if m.get("status"):
            out += [f"Status as the report states it: {m['status']}", ""]
        if a["own_window"] is None:
            out += [f"**The account's daily file could not be read: {a['why']}.** The report's own summary numbers: "
                    f"`{json.dumps(a['report_summary'])}`."]
        else:
            if a["why"]:
                out += [f"Note: {a['why']}.", ""]
            out += HEAD + _window_rows("As run", a["own_window"]) + _window_rows("Research window",
                                                                                 a["research_window"])
            out += [""] + _cash_lines(a["research_window"] or a["own_window"])
        c = run["control"]
        if c and c.get("percentile") is not None:
            out.append(f"- Matched random control: percentile {c['percentile']:.1f} of {c['runs']} runs"
                       f"{'' if c['matched'] else ' (NOT matched)'}, p {_f(c['p_value'], '{:.4f}')}; kind "
                       f"{c.get('kind') or 'not stated'}; drew from: {run['control_drew_from'] or 'not stated'}"
                       + (f"; drawdown percentile {c['drawdown_percentile']:.1f}" if c.get("drawdown_percentile")
                          is not None else "; drawdown percentile not in the report") + ".")
        else:
            out.append("- Matched random control: none in this report.")
        out += _v2_lines(run["gate_v2"], run["verdict_as_reported"])
        if run["neighbours"]:
            out += ["", "### Neighbours (the plateau check)", "",
                    "Research window. \"Against the frozen rule\" is the neighbour's CAGR minus the frozen rule's.", "",
                    "| Neighbour | What it changes | CAGR | Against the frozen rule | Max drawdown | Sharpe | Mean "
                    "exposure | Return above SPY at its exposure (t) | Alpha (t) | Beta |",
                    "|---|---|---|---|---|---|---|---|---|---|"]
            mine = a["research_window"] or a["own_window"]
            for n in run["neighbours"]:
                s = n["research_window"] or n["own_window"]
                if s is None:
                    out.append(f"| {n['neighbour']} | {n['change'] or '-'} | not readable: {n['why']} | | | | | | | |")
                    continue
                act, ab = s["active_return"], s["alpha_beta"]
                diff = (s["rule"]["cagr"] - mine["rule"]["cagr"]) * 100 if mine else None
                out.append(f"| {n['neighbour']} | {n['change'] or '-'} | {_p(s['rule']['cagr'])} | "
                           f"{_f(diff, '{:+.2f} points')} | {_p(s['rule']['max_drawdown'])} | "
                           f"{_f(s['rule']['sharpe']['sharpe'])} | {_p(s['mean_exposure'], 0)} | "
                           f"{_p(act['annualised']) if act else 'n/a'} ({_f(act['t']) if act else 'n/a'}) | "
                           f"{_p(ab['alpha']) if ab else 'n/a'} ({_f(ab['alpha_t']) if ab else 'n/a'}) | "
                           f"{_f(ab['beta']) if ab else 'n/a'} |")
        if run["other_rows"]:
            out += ["", "### Other accounts of the same report (the report's own numbers)", "",
                    "| Account | Role | CAGR | Max drawdown | Sharpe | Mean exposure |", "|---|---|---|---|---|---|"]
            for o in run["other_rows"]:
                sharpe = o.get("sharpe", o.get("daily_sharpe_ann"))
                out.append(f"| {o['label']} | {o['role']} | {_f(o.get('cagr_pct'), '{:.2f}%')} | "
                           f"{_f(o.get('max_drawdown_pct'), '{:.2f}%')} | {_f(sharpe)} | "
                           f"{_f(o.get('avg_exposure_pct'), '{:.1f}%')} |")
    return "\n".join(out) + "\n"


def render_index(res: dict) -> str:
    out = ["# Research results on record", "",
           f"Written {res['generated_at']} by `scripts/export_results.py` from {res['reports_read']} reports under "
           "`data/reports` on the research Mac. No rule was re-run. One page per pre-registration; the JSON beside "
           "each page holds every number.", "",
           f"Trial registry: {res['registry']['configurations']:,} configurations in {res['registry']['rows']:,} rows. "
           f"Research window: {res['research_window'][0]} to {res['research_window'][1]}. Gate v3 marks: "
           "[`gate_v3_rescore.md`](gate_v3_rescore.md).", "",
           "| Rule | Pre-registered | Family | Runs on record | Newest report | Headline run: CAGR / max drawdown / "
           "mean exposure / alpha (t), research window |", "|---|---|---|---|---|---|"]
    for rule, page in sorted(res["pages"].items()):
        runs = page["runs"]
        cell = "no report on this machine"
        if runs:
            s = runs[0]["account"]["research_window"] or runs[0]["account"]["own_window"]
            if s:
                ab = s["alpha_beta"]
                cell = (f"{runs[0]['run']}: {_p(s['rule']['cagr'])} / {_p(s['rule']['max_drawdown'])} / "
                        f"{_p(s['mean_exposure'], 0)} / {_p(ab['alpha']) if ab else 'n/a'} "
                        f"({_f(ab['alpha_t']) if ab else 'n/a'})")
            else:
                cell = f"{runs[0]['run']}: daily file not readable"
        newest = max((r["report"] for r in runs), default="-")
        out.append(f"| [{rule}]({rule}.md) | {'yes' if page['preregistered'] else 'no'} | {page['family'] or '-'} | "
                   f"{len(runs)} | {'`' + newest + '`' if runs else '-'} | {cell} |")
    if res.get("runs"):
        out += ["", f"Every run, one small summary per report ({len(res['runs'])} reports): [`runs/`](runs/). Marks "
                "on the research window only; the control and the gate v2 marks as the run stored them."]
    if res.get("as_reported"):
        out += ["", "Pages the run scripts wrote themselves are copied unchanged into [`as_reported/`](as_reported/): "
                + ", ".join(f"`{n}`" for n in res["as_reported"]) + "."]
    if res["other_files"]:
        out += ["", "Files in `data/reports` that hold no account and were not read: "
                + ", ".join(f"`{n}`" for n in res["other_files"][:40])
                + (f" and {len(res['other_files']) - 40} more" if len(res["other_files"]) > 40 else "") + "."]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser(description="Export every research run's summary into docs/research/results")
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--out", default=str(ROOT / OUT))
    ap.add_argument("--rule", default=None, help="one rule's page only (the index is not rewritten)")
    ap.add_argument("--root", default=str(ROOT), help=argparse.SUPPRESS)  # tests: another repository root
    a = ap.parse_args(argv)
    if not (Path(a.data) / "reports").is_dir():
        sys.exit(f"no {Path(a.data) / 'reports'}: this script reads the reports of earlier runs (the research Mac)")
    res = export(Path(a.data), Path(a.root), a.rule)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for rule, page in res["pages"].items():
        (out / f"{rule}.md").write_text(results.portable(render_page(page, res["research_window"]), a.root, a.data))
        (out / f"{rule}.json").write_text(results.portable(json.dumps(page, indent=1, default=str), a.root, a.data))
    if not a.rule:
        (out / "README.md").write_text(results.portable(render_index(res), a.root, a.data))
        (out / "as_reported").mkdir(exist_ok=True)
        for name, src in as_reported(Path(a.data) / "reports").items():
            # what the run printed, unchanged but for this machine's paths (#594)
            (out / "as_reported" / name).write_text(results.portable(src.read_text(), a.root, a.data))
        (out / "runs").mkdir(exist_ok=True)
        for stem, summary in res["runs"].items():
            (out / "runs" / f"{stem}.json").write_text(
                results.portable(json.dumps(summary, indent=1, default=str, sort_keys=True), a.root, a.data) + "\n")
    with_runs = sum(bool(p["runs"]) for p in res["pages"].values())
    print(f"wrote {len(res['pages'])} pages to {out}: {with_runs} with a run on record, "
          f"{len(res['pages']) - with_runs} without; {res['reports_read']} reports read; "
          f"{len(res['runs'])} run summaries in runs/")
    return res


if __name__ == "__main__":
    main()
