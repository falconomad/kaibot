"""Issue #203: the exit-before-earnings variant of `momentum_core.py --universe pit` reads the SEC point-in-time
dates of S85 (#125, data/earnings/announcements.parquet) when the file exists and meets the pre-declared bar of
replay/earnings.py (unchanged), and falls back to the headline path of #113 otherwise. The base version (hold
through earnings) and the Gate B verdict never change. Small seeded synthetic data only, no network."""

import contextlib
import io
import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from kaibot.ingest import sp500_pit
from kaibot.replay import earnings

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "unit"))
sys.path.insert(0, str(ROOT / "scripts"))
import momentum_core as core_script  # noqa: E402
import test_core_pit as tp  # noqa: E402

VERSION = "trend_momentum"


def announcements(dates: np.ndarray, stocks: list[str], every_days: int = 91, first: int = 20) -> pd.DataFrame:
    """One SEC report row per stock every `every_days` calendar days (a quarter), staggered by stock, on the first
    session on or after the target day; a release before the open, so `session` is the release day and
    `last_close_before` the session before it."""
    dates = np.asarray(dates, dtype="datetime64[D]")
    rows = []
    for k, s in enumerate(stocks):
        target = dates[first] + np.timedelta64(k % 7, "D")
        while True:
            i = int(np.searchsorted(dates, target))
            if i >= len(dates):
                break
            d = dates[i].astype(object)
            rows.append({"symbol": s, "company": s, "cik": 1000 + k,
                         "accepted_ts": pd.Timestamp(f"{d} 12:00", tz="UTC"), "session": d,
                         "last_close_before": dates[i - 1].astype(object), "filing_date": d, "form": "8-K",
                         "items": "2.02", "accession": f"{k}-{i}", "report": True, "source": "test"})
            target += np.timedelta64(every_days, "D")
    return pd.DataFrame(rows)


def make_data(root: Path, with_sec: str | None):
    """A point-in-time daily store (tests/unit/test_core_pit.py's synthetic panel, membership and layout) and,
    with `with_sec`, data/earnings/announcements.parquet: "all" stocks report quarterly, "few" only 10 of 30."""
    panel, stocks = tp.raw_market()
    d = panel.dates
    log = [sp500_pit.Change(date(2021, 12, 1), tuple(stocks))]
    ms = sp500_pit.Membership(log, {}, stocks, date(2024, 2, 1))
    tp.write_store(root / "daily", panel, {}, [])
    uni = root / "universe_test.yaml"
    uni.write_text(json.dumps({"sectors": {"XLK": stocks[:14], "XLF": stocks[14:]}, "market": "SPY"}))
    if with_sec:
        (root / "earnings").mkdir(parents=True, exist_ok=True)
        tab = announcements(d, stocks if with_sec == "all" else stocks[:10])
        tab.to_parquet(root / "earnings" / "announcements.parquet")
    return panel, stocks, ms, uni


def run(mp, root: Path, uni: Path, panel, *extra) -> str:
    args = ["--data", str(root), "--universe-file", str(uni), "--start", tp.FIRST, "--end", str(panel.dates[-1]),
            "--control-runs", "0", "--universe", "pit", *extra]
    mp.setattr(sys, "argv", ["momentum_core.py", *args])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            core_script.main()
        except SystemExit as e:
            return f"exit {e.code}"
    return buf.getvalue()


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setattr(core_script, "first_train_month", lambda start, warmup: np.datetime64("2022-03"))
    out = {}
    for case in (None, "all", "few"):
        root = tmp_path_factory.mktemp(f"sec_{case}")
        panel, stocks, ms, uni = make_data(root, case)
        mp.setattr(sp500_pit.Membership, "load", classmethod(lambda cls, *a, ms=ms, **k: ms))
        has_before = run(mp, root, uni, panel, "--has", VERSION)
        text = run(mp, root, uni, panel, "--version", VERSION)
        has_after = run(mp, root, uni, panel, "--has", VERSION)
        f, rep = core_script.find_report(root / "reports", "balanced", VERSION, mode="point_in_time")
        out[case] = {"root": root, "panel": panel, "stocks": stocks, "ms": ms, "uni": uni, "rep": rep,
                     "text": text, "has": (has_before, has_after)}
    yield out, mp
    mp.undo()


def _rows(rep: dict, earn: str) -> dict:
    return {r["label"].rsplit("|", 1)[0]: r for r in rep["rows"] if r["earnings"] == earn}


def test_sec_dates_that_meet_the_bar_drive_the_variant(runs):
    out, _ = runs
    rep = out["all"]["rep"]
    e = rep["earnings"]
    assert rep["earnings_input"] == "sec_pit" and e["source"] == "sec_pit" and e["mode"] == "on"
    assert e["coverage"]["reliable"] and e["coverage"]["bar"] == json.loads(json.dumps(earnings.RELIABLE))
    assert e["coverage"]["flat_before_report_share"] >= 0.85 and e["coverage"]["stocks_plausible_share"] >= 0.8
    assert rep["earnings_variant"] == "run (SEC point-in-time dates)"
    sec = _rows(rep, "exit_sec")
    assert sec and not _rows(rep, "exit_approx") and len(sec) == len(_rows(rep, "hold")) - 5  # no neighbours
    assert "exit before (SEC point-in-time dates)" in out["all"]["text"]
    tr = pd.read_csv(out["all"]["root"] / "reports" / sec[f"{VERSION}|base|1x|cap100"]["files"]["trades"])
    assert (tr["reason"] == "earnings").any()
    base = pd.read_csv(out["all"]["root"] / "reports" / _rows(rep, "hold")[f"{VERSION}|base|1x|cap100"]["files"][
        "trades"])
    assert not (base["reason"] == "earnings").any()
    assert any("SEC 8-K Item 2.02" in c for c in rep["caveats"])


def test_the_base_version_and_the_verdict_do_not_change(runs):
    out, _ = runs
    ref = out[None]["rep"]
    assert ref["earnings_input"] == "headlines" and ref["earnings_variant"] == "skipped"
    for case in ("all", "few"):
        rep = out[case]["rep"]
        assert rep["gate_b"] == ref["gate_b"], case
        assert rep["base_label"] == ref["base_label"]
        a, b = _rows(rep, "hold"), _rows(ref, "hold")
        assert a.keys() == b.keys()
        for k in a:
            for key in ("events_sha256", "trades", "return_pct", "sharpe", "max_drawdown_pct"):
                assert a[k][key] == b[k][key], (case, k, key)


def test_sec_dates_below_the_bar_fall_back_to_the_headline_path(runs):
    out, _ = runs
    rep = out["few"]["rep"]
    e = rep["earnings"]
    assert rep["earnings_input"] == "sec_pit" and e["source"] == "headlines"
    assert e["sec_pit"]["coverage"]["reliable"] is False and e["sec_pit"]["mode"] == "off (not reliable)"
    assert e["sec_pit"]["coverage"]["stocks_with_reports"] == 10
    assert "SEC point-in-time dates BELOW the reliability bar" in e["note"]
    assert "headline estimate is tried instead" in e["note"]
    assert rep["earnings_variant"] == "skipped"  # no news on disk: the headline estimate is unreliable too
    assert {r["earnings"] for r in rep["rows"]} == {"hold"}


def test_has_reruns_a_headline_report_once_the_sec_file_exists(runs, tmp_path):
    out, mp = runs
    for case in (None, "all", "few"):
        assert out[case]["has"] == ("exit 1", "exit 0"), case
    # the report of the no-file run is not "done" any more once the SEC file appears
    c = out[None]
    (c["root"] / "earnings").mkdir(exist_ok=True)
    announcements(c["panel"].dates, c["stocks"]).to_parquet(c["root"] / "earnings" / "announcements.parquet")
    mp.setattr(sp500_pit.Membership, "load", classmethod(lambda cls, *a, ms=c["ms"], **k: ms))
    assert run(mp, c["root"], c["uni"], c["panel"], "--has", VERSION) == "exit 1"
    assert run(mp, c["root"], c["uni"], c["panel"], "--has", VERSION, "--earnings", "off") == "exit 1"
    (c["root"] / "earnings" / "announcements.parquet").unlink()
    assert run(mp, c["root"], c["uni"], c["panel"], "--has", VERSION) == "exit 0"


def test_the_sec_blackout_is_point_in_time_and_flat_before_every_later_report(runs):
    out, _ = runs
    c = out["all"]
    b = SimpleNamespace(dates=c["panel"].dates, symbols=list(c["panel"].symbols), c=c["panel"].c)
    earn, hold, entry = core_script.sec_earnings_filter(c["root"], b, c["stocks"], c["uni"])
    assert earn["mode"] == "on" and hold is not None
    tab = pd.read_parquet(c["root"] / "earnings" / "announcements.parquet")
    dates = np.asarray(b.dates, dtype="datetime64[D]")
    for s, g in tab.groupby("symbol"):
        j = b.symbols.index(s)
        idx = np.searchsorted(dates, np.asarray(list(g["session"]), dtype="datetime64[D]"))
        assert all(hold[e - 1, j] for e in idx[1:]), s  # flat the night before every report after the first
        assert not hold[: idx[0], j].any()  # nothing before the first filing is known
    # deleting every filing after a session leaves that session's rows unchanged (no look-ahead)
    cut = 300
    later = tab[tab["session"] >= dates[cut].astype(object)]
    tab.drop(later.index).to_parquet(c["root"] / "earnings" / "announcements.parquet")
    try:
        _, _, h2, e2 = core_script.sec_blackout(c["root"], b, c["stocks"], c["uni"])
    finally:
        tab.to_parquet(c["root"] / "earnings" / "announcements.parquet")
    assert (h2[:cut] == hold[:cut]).all() and (e2[:cut] == entry[:cut]).all()


def test_today_universe_ignores_the_sec_file(runs, tmp_path):
    out, mp = runs
    c = out["all"]
    a = core_script.argparse.Namespace(earnings="auto")
    assert core_script.earnings_input(a, c["root"], "today") == "headlines"
    assert core_script.earnings_input(a, c["root"], "point_in_time") == "sec_pit"
    assert core_script.earnings_input(core_script.argparse.Namespace(earnings="off"), c["root"],
                                      "point_in_time") == "off"


def test_the_rerun_once_the_sec_file_exists_counts_only_the_new_variant(runs, tmp_path):
    """The #203 rerun repeats every hold-through run exactly (same label, same events): those were counted by the
    earlier report, so only the new SEC variant adds configurations to the trial registry."""
    import shutil

    out, mp = runs
    c = out[None]
    root = tmp_path / "rerun"
    shutil.copytree(c["root"], root)
    before = len((root / "trials.jsonl").read_text().splitlines())
    (root / "earnings").mkdir(exist_ok=True)
    announcements(c["panel"].dates, c["stocks"]).to_parquet(root / "earnings" / "announcements.parquet")
    mp.setattr(sp500_pit.Membership, "load", classmethod(lambda cls, *a, ms=c["ms"], **k: ms))
    run(mp, root, c["uni"], c["panel"], "--version", VERSION)
    new = [json.loads(x) for x in (root / "trials.jsonl").read_text().splitlines()[before:]]
    assert new and {r["params"]["earnings"] for r in new} == {"hold", "exit_sec"}
    assert sum(r["n_trials"] for r in new if r["params"]["earnings"] == "hold") == 0
    sec = [r for r in new if r["params"]["earnings"] == "exit_sec"]
    assert sum(r["n_trials"] for r in sec) == sum(1 for r in sec if r["params"]["fill"] == "base"
                                                  and r["params"]["cost_multiple"] == 1.0) > 0
    first = [json.loads(x) for x in (c["root"] / "trials.jsonl").read_text().splitlines()]
    assert sum(r["n_trials"] for r in first) > 0  # the first report counted its configurations
