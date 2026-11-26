"""Read the research reports under `data/reports` (M11 tickets 2.1, 2.2 and 2.5).

Every research script writes one JSON report per run plus one daily CSV per account. The layouts differ by
script; this module reads them all into one shape so that a result page or a re-score never re-runs a rule:

    reports(dir)           every report file that holds accounts, newest last
    rows(report)           one `Row` per account in it: the rule, the role, the daily file, the control
    daily(row)             the account's sessions: return, exposure at the close, cost as a share of equity
    spy(report, dir)       SPY's return on the report's sessions (total return when the report has it)

Layouts read (each from the script that writes it):

- core, core_pit: `core_<level>_<version>[_pit|_store][_costsv2]_<ts>.json` (momentum_core.py); `rows[]`, the
  files named in each row;
- engine_core: `engine_<level>_<version>_pit_<ts>.json` (the engine with SimBroker); `rows[]`, daily files in
  the folder of the same name;
- oneshots: `oneshots_<rule>_<ts>.json` (oneshots.py); `versions.<name>.rows[]`;
- spy: `spy_<level>_<version>_<ts>.json` (the SPY noise-band script); `rows[]`, `timing_control`;
- spy_engine: `spy_engine_<level>_<ts>.json` (spy_engine.py); `rows[]`, the files named in each row;
- spy_trend: `spy_trend_<ts>.json` (spy_trend.py); `rows[]`, daily files in the folder, `control.<model>`;
- the walk-forwards (intraday and multi-day first look): `walkforward_<level>_<tag>.json`; `files.tuned`
  and `files.default`;
- intraday one-shots: `oneshots_<rule>_<ts>.json` with `variants.<name>.account`; daily files hold `ret`
  (and `exposure` since #677).

Since #677 the walk-forward, one-shot, spy_engine and spy_cost_rerun daily files carry `exposure` too; the
runs written before it have none and stay unscored (nothing is re-run without a pre-registration).

A row whose daily file is missing is still listed, with `daily_file` None and the report's own summary numbers:
nothing is dropped silently. A file that is not a report (a gate report, a study, a skipped marker) is ignored
here and counted by `other_files`.

pandas and the standard library only. Nothing here is imported by the engine.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

STAT_KEYS = ("cagr_pct", "return_pct", "sharpe", "max_drawdown_pct", "avg_exposure_pct", "trades",
             "turnover_pct_per_year", "cost_bp_per_year", "positive_years", "n_years", "net_return_pct",
             "daily_sharpe_ann")
NOT_ACCOUNTS = ("break_even_input", "gross")


@dataclass(frozen=True)
class Row:
    """One simulated account of one report."""

    report: str  # the report's file name
    set: str  # the report's `set`: core, engine_core, oneshots, spy, spy_engine, spy_trend, walkforward, ...
    rule: str  # the pre-registration name (the spec before "~")
    label: str
    role: str  # base | neighbour | cost | fill | exposure | control | variant | other
    neighbour: str | None
    cost: Any  # the cost multiple (1.0, 2.0, ...) or the report's own cost label ("0.5 bp", "model v1")
    daily_file: Path | None
    control: dict | None
    summary: dict = field(default_factory=dict)  # the report's own numbers for the row
    meta: dict = field(default_factory=dict)  # universe_mode, cost_model, engine, run_at, code_sha, prereg


def portable(text: str, root: Path | str | None = None, data: Path | str | None = None) -> str:
    """`text` with no absolute path of this machine in it (issue #594): a file is written into the repository.

    A path inside the data folder the script read becomes `data/...` (#452: the research Mac's results carried
    `~/Projects/kaibot/data/...` when the script ran from another checkout than the one holding the data); a path
    inside the repository root becomes repo-relative (the root itself `.`); any other path under the home folder
    starts with `~`. Each folder is matched as given, resolved (macOS `/private/...`, symlinked clones) and spelled
    with `~` (a path an earlier run already made portable). A path matches only whole (`/Users/ab` never matches
    inside `/Users/abc`)."""
    home = Path.home()

    def spellings(p: Path) -> list[str]:
        out = {str(p), str(p.resolve())}
        for s in list(out):
            if Path(s).is_relative_to(home) and Path(s) != home:
                out.add("~/" + str(Path(s).relative_to(home)))
        return sorted(out - {"", "/", ".", "~"}, key=len, reverse=True)

    end = r"(?![\w.-])"
    for base, short in ([(data, "data")] if data is not None else []) + ([(root, ".")] if root is not None else []):
        for s in spellings(Path(base)):
            text = re.sub(re.escape(s) + "/", "" if short == "." else f"{short}/", text)
            text = re.sub(re.escape(s) + end, short, text)
    for base in sorted({str(home), str(home.resolve())} - {"", "/"}, key=len, reverse=True):
        text = re.sub(re.escape(base) + end, "~", text)
    return text


def reports_as_of(found: list[tuple[Path, dict]]) -> str | None:
    """#452: the time of the newest report read (its `run_at`, else the stamp in its name), so a page written twice
    from the same reports is the same file byte for byte. None when no report has a time."""
    times = []
    for f, rep in found:
        for t in (rep.get("run_at"), _stamp(f.name)):
            try:
                x = pd.Timestamp(t)
            except (TypeError, ValueError):  # not a time: try the name's stamp, else skip the report
                continue
            if not pd.isna(x):  # a missing time or an empty stamp is NaT
                times.append(x)
                break
    if not times:
        return None
    t = max(x.tz_localize("UTC") if x.tzinfo is None else x.tz_convert("UTC") for x in times)
    return t.isoformat(timespec="seconds")


def reports(folder: Path | str) -> list[tuple[Path, dict]]:
    """Every JSON file in `folder` that holds accounts, oldest first (by the time in its name, then by name)."""
    out = []
    for f in sorted(Path(folder).glob("*.json"), key=lambda p: (_stamp(p.name), p.name)):
        try:
            rep = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(rep, dict) and (_raw_rows(rep) or _walkforward_files(rep)):
            out.append((f, rep))
    return out


def other_files(folder: Path | str) -> list[str]:
    have = {f.name for f, _ in reports(folder)}
    return sorted(f.name for f in Path(folder).glob("*.json") if f.name not in have)


def _stamp(name: str) -> str:
    m = re.search(r"(\d{8}T\d{6})", name)
    return m.group(1) if m else ""


def _raw_rows(rep: dict) -> list[tuple[str | None, dict, dict]]:
    """(version name, row, the version's block) for every account row of a report."""
    out = []
    if isinstance(rep.get("rows"), list):
        out += [(None, r, rep) for r in rep["rows"] if isinstance(r, dict) and "label" in r]
    for name, block in (rep.get("versions") or {}).items() if isinstance(rep.get("versions"), dict) else ():
        out += [(name, r, block) for r in (block.get("rows") or []) if isinstance(r, dict) and "label" in r]
    if isinstance(rep.get("variants"), dict) and rep.get("rule"):
        # the intraday one-shots (oneshots.py): variants.<name>.account, daily files named after the account
        for name, v in rep["variants"].items():
            if isinstance(v, dict) and isinstance(v.get("account"), dict):
                label = rep["rule"] if name == "base" else f"{rep['rule']}~{name}"
                out.append((None, {"label": label, "spec": label, "kind": "base" if name == "base" else "neighbour",
                                   **v["account"]}, rep))
    return out


def _walkforward_files(rep: dict) -> dict:
    files = rep.get("files")
    return {k: v for k, v in files.items() if isinstance(v, dict) and "daily" in v} if isinstance(files, dict) \
        else {}


def _safe(label: str) -> str:
    return label.replace("|", "_").replace("~", "-").replace(" ", "")


def _set_of(name: str, rep: dict) -> str:
    if rep.get("set"):
        return str(rep["set"])
    return "walkforward" if name.startswith("walkforward_") else "?"


def _meta(f: Path, rep: dict) -> dict:
    s = _set_of(f.name, rep)
    mode = rep.get("universe_mode") or ("point_in_time" if "_pit_" in f.name else None)
    if s in ("spy", "spy_engine", "spy_trend"):
        mode = "point_in_time"  # one instrument: there is no stock list to be wrong about
    pre = rep.get("prereg")
    return {"set": s, "universe_mode": mode or "today",
            "engine": "simbroker" if s in ("engine_core", "spy_engine") else "research_harness",
            "cost_model": rep.get("costs") if isinstance(rep.get("costs"), str) else None,
            "run_at": rep.get("run_at"), "code_sha": rep.get("code_sha"), "status": rep.get("status"),
            "prereg_sha256": (pre or {}).get("sha256") if isinstance(pre, dict) else rep.get("prereg_sha256"),
            "window": rep.get("oos_window") or rep.get("window") or (rep.get("summary") or {}).get("oos_window"),
            "start": rep.get("start"), "end": rep.get("end"), "bars": rep.get("bars"),
            "report_rule": rep.get("rule") or rep.get("version") or rep.get("experiment")}


def _control(rep: dict, block: dict, row: dict) -> dict | None:
    """The matched control that belongs to a row, wherever the script stored it."""
    if isinstance(row.get("control"), dict) and row["control"]:
        full = (rep.get("control") or {}).get(row["label"]) if isinstance(rep.get("control"), dict) else None
        return {**row["control"], **({"control_result": full["control_result"]} if isinstance(full, dict)
                                     and isinstance(full.get("control_result"), dict) else {})}
    for holder in (block, rep):
        c = holder.get("control")
        if isinstance(c, dict):
            if isinstance(c.get(row["label"]), dict):
                return c[row["label"]]
            model = row.get("costs")
            if isinstance(model, str) and isinstance(c.get(model), dict):  # spy_trend: one control per cost model
                return {k: v for k, v in c[model].items() if k != "per_run"}
            if "runs" in c and holder is block:
                return c
        t = holder.get("timing_control")
        if isinstance(t, dict) and isinstance(t.get("control"), dict):
            at = t["control"].get("cost")  # the timing control was run at ONE cost: it belongs to that scenario
            if isinstance(at, int | float) and isinstance(row.get("cost"), str) \
                    and not row["cost"].startswith(f"{at:g} "):
                return None
            return {k: v for k, v in t["control"].items() if k != "per_run"}
    return None


def _role(rep: dict, row: dict, version: str | None, first: bool) -> str:
    spec, kind = str(row.get("spec", "")), row.get("kind")
    if kind in NOT_ACCOUNTS:
        return "cost"
    if kind == "control" or "control_of" in rep.get("versions", {}).get(version or "", {}):
        return "control"
    if "~" in spec or kind == "neighbour":
        return "neighbour"
    if rep.get("base_label"):
        if row["label"] == rep["base_label"]:
            return "base"
    elif kind == "base" or (first and kind in (None, "grid")):
        return "base"
    cost, mult = row.get("cost"), row.get("mult")
    if isinstance(cost, int | float) and cost != 1.0 or isinstance(mult, int | float) and mult != 1.0:
        return "cost"
    if row.get("fill") not in (None, "base"):
        return "fill"
    if isinstance(row.get("exposure_pct"), int | float) and row["exposure_pct"] != 100.0:
        return "exposure"
    return "variant"


def rows(f: Path, rep: dict) -> list[Row]:
    """Every account of one report."""
    meta, folder, out = _meta(f, rep), f.parent, []
    seen_base: set[str] = set()
    for version, r, block in _raw_rows(rep):
        spec = str(r.get("spec") or version or rep.get("rule") or rep.get("version") or "?")
        rule = spec.split("~")[0]
        first = rule not in seen_base and not rep.get("base_label") and "~" not in spec \
            and r.get("kind") not in (*NOT_ACCOUNTS, "control", "neighbour") \
            and r.get("cost") in (None, 1.0) and r.get("mult") in (None, 1.0) and r.get("fill") in (None, "base") \
            and r.get("exposure_pct") in (None, 100.0)
        role = _role(rep, r, version, first)
        if role == "base":
            seen_base.add(rule)
        rel = (r.get("files") or {}).get("daily") if isinstance(r.get("files"), dict) else None
        cands = [folder / rel] if rel else []
        cands.append(folder / f.stem / f"daily_{_safe(r['label'])}.csv")
        cands.append(folder / f.stem / f"daily_{r['label']}.csv")  # the intraday one-shots keep the "~"
        daily_file = next((c for c in cands if c.is_file()), None)
        trel = (r.get("files") or {}).get("trades") if isinstance(r.get("files"), dict) else None
        trades_file = folder / trel if trel and (folder / trel).is_file() else None
        out.append(Row(f.name, meta["set"], rule, r["label"], role, spec.split("~")[1] if "~" in spec else None,
                       r.get("cost", r.get("mult")), daily_file, _control(rep, block, r),
                       {k: r[k] for k in STAT_KEYS if k in r}, {**meta, "version": version, "fill": r.get("fill"),
                                                                "exposure_cap_pct": r.get("exposure_pct"),
                                                                "cost_model_row": r.get("costs"),
                                                                "trades_file": trades_file}))
    for name in {r.rule for r in out} - seen_base:
        # a report without a base row (the SPY sets: one account per cost scenario): each scenario at the rule's
        # own settings is a base row of its own kind of run
        out = [Row(r.report, r.set, r.rule, r.label, "base", r.neighbour, r.cost, r.daily_file, r.control,
                   r.summary, {**r.meta, "scenario": r.label.split("|", 1)[-1]})
               if r.rule == name and r.role == "variant" else r for r in out]
    rule = str(rep.get("experiment") or rep.get("rule") or "?")
    for which, files in _walkforward_files(rep).items():
        p = folder / files["daily"]
        out.append(Row(f.name, meta["set"], rule, f"{rule}|{which}", "base" if which == "default" else "variant",
                       None, 1.0, p if p.is_file() else None, None,
                       {k: v for k, v in ((rep.get("summary") or {}).get(which) or {}).items() if k in STAT_KEYS},
                       {**meta, "version": which}))
    return out


def daily(row: Row) -> pd.DataFrame | None:
    """The account's sessions: `ret`, and when the file has them `exposure` (share of equity invested at the
    close) and `cost` (that session's costs as a share of its starting equity). None when the file is missing.

    An intraday SPY rule writes no exposure; its `carry_out` flag (still long at the close) is used as 0 or 1,
    so a rule that is flat at every close has exposure 0."""
    if row.daily_file is None:
        return None
    d = pd.read_csv(row.daily_file, parse_dates=["date"]).set_index("date").sort_index()
    out = pd.DataFrame(index=d.index)
    if "ret" in d.columns:
        out["ret"] = d["ret"].astype(float)
    elif {"pnl", "start"} <= set(d.columns):
        out["ret"] = (d["pnl"] / d["start"]).astype(float)
    else:
        return None
    if "exposure" in d.columns:
        out["exposure"] = d["exposure"].astype(float)
    elif "carry_out" in d.columns:
        out["exposure"] = d["carry_out"].astype(str).str.lower().isin(("true", "1", "1.0")).astype(float)
    if {"cost", "start"} <= set(d.columns):
        out["cost"] = (d["cost"] / d["start"]).astype(float)
    if "exposure_intraday" in d.columns:
        out["exposure_intraday"] = d["exposure_intraday"].astype(float)
    elif row.meta.get("trades_file") is not None:
        tw = intraday_exposure(Path(row.meta["trades_file"]), out.index)
        if tw is not None:
            out["exposure_intraday"] = tw
    return out[np.isfinite(out["ret"])]


SESSION_MINUTES = 390  # 09:30-16:00 ET; an early close is counted as a full session (a slight understatement)


def intraday_exposure(trades_file: Path, sessions: pd.Index) -> pd.Series | None:
    """#703: each session's time-weighted share of its minutes invested, from an intraday SPY account's trades file
    (`date`, `entry_m`, `exit_date`, `exit_m`, `reason`; the SPY noise-band script, all in when long). A daily file
    written before #703 has no `exposure_intraday` column; the trades file it sits beside has what is needed, so
    nothing is re-run. A trade that exits at the close holds the last minute too; a carry counts to the close (its
    exit is the next open). None when the file lacks the columns."""
    t = pd.read_csv(trades_file)
    if t.empty:
        return pd.Series(0.0, index=sessions)
    if not {"date", "entry_m", "exit_date", "exit_m"} <= set(t.columns):
        return None
    same = t["exit_date"].astype(str) == t["date"].astype(str)
    close = t["reason"].astype(str).eq("close") if "reason" in t.columns else pd.Series(False, index=t.index)
    end = np.where(same, t["exit_m"].astype(float) + close.astype(float), SESSION_MINUTES)
    held = np.clip(np.minimum(end, SESSION_MINUTES) - t["entry_m"].to_numpy(dtype=float), 0.0, None)
    per = pd.Series(held, index=pd.to_datetime(t["date"])).groupby(level=0).sum() / SESSION_MINUTES
    return per.reindex(sessions).fillna(0.0).clip(0.0, 1.0).astype(float)


def _spy_file(path: Path) -> tuple[pd.Series, str] | None:
    d = pd.read_csv(path, parse_dates=["date"]).set_index("date").sort_index()
    kind = "spy_total_return"
    if "ret_total" in d.columns and d["ret_total"].notna().all():
        s = d["ret_total"]
    elif "ret" in d.columns:
        s = d["ret"]
        if "ret_kind" in d.columns:
            kind = str(d["ret_kind"].iloc[0])
        elif "ret_total" in d.columns:
            kind = "spy_price_only"
        else:
            kind = "spy_not_stated"
    else:
        return None
    return s.astype(float), kind


def spy(f: Path, rep: dict, pool: dict[str, tuple[pd.Series, str]] | None = None) -> tuple[pd.Series, str] | None:
    """SPY's return per session for a report: its own `spy_daily` file, else (an SPY-only report writes none)
    the longest total-return series any other report in the folder wrote (`spy_pool`). Returns the series and
    its kind (`spy_total_return`, `spy_price_only`, `spy_not_stated`), or None."""
    files = rep.get("files") if isinstance(rep.get("files"), dict) else {}
    for c in ([f.parent / files["spy_daily"]] if isinstance(files.get("spy_daily"), str) else []) + \
            [f.parent / f.stem / "spy_daily.csv"]:
        if c.is_file():
            got = _spy_file(c)
            if got is not None:
                if isinstance(rep.get("spy_daily"), dict) and rep["spy_daily"].get("ret"):
                    got = (got[0], str(rep["spy_daily"]["ret"]))
                return got
    best = None
    for s, kind in (pool or {}).values():  # total return before anything else, then the longest
        rank = (kind == "spy_total_return", s.size)
        if best is None or rank > best[0]:
            best = (rank, (s, kind))
    return best[1] if best else None


def spy_pool(folder: Path | str) -> dict[str, tuple[pd.Series, str]]:
    """Every SPY series the reports of a folder wrote, by report file name."""
    out = {}
    for f, rep in reports(folder):
        got = spy(f, rep)
        if got is not None:
            out[f.name] = got
    return out


def newest_per_rule(all_rows: list[Row], *, role: str = "base") -> dict[tuple[str, str], Row]:
    """The newest row of a role per (rule, kind of run), where the kind of run is the set plus the universe and
    the cost model: a point-in-time run and a today's-list run of the same rule are both kept."""
    out: dict[tuple[str, str], Row] = {}
    for r in all_rows:  # oldest first, so a later report replaces an earlier one
        if r.role == role:
            out[(r.rule, run_kind(r))] = r
    return out


EVIDENCE = {"engine_core": 0, "spy_engine": 0, "spy_cost_rerun": 0, "core": 1, "oneshots": 1, "spy_trend": 1,
            "spy": 2, "walkforward": 4}  # any other set (the multi-day first look): 5


def evidence_rank(r: Row) -> tuple:
    """Lower is better evidence: the engine with SimBroker first, then the point-in-time universe, then cost
    model v2. Used to pick the run a page or a table leads with; it judges nothing."""
    cm = r.meta.get("cost_model_row") or r.meta.get("cost_model")
    return (EVIDENCE.get(r.set, 5), 0 if r.meta.get("universe_mode") == "point_in_time" else 1,
            0 if cm == "v2" else 1, r.label)


def run_kind(r: Row) -> str:
    cm = r.meta.get("cost_model_row") or r.meta.get("cost_model") or ""
    scenario = f", {r.meta['scenario']}" if r.meta.get("scenario") else ""
    return f"{r.set}, {r.meta.get('universe_mode')}" + (f", costs {cm}" if cm else "") + scenario
