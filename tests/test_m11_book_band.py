"""M11 ticket 2.10 (#461, decision 17 section 7 step 3): the engine's book stop asks `risk/book_stop.verdict`.

RISK RULE, tests first. The switch is `mode` in config/book_stop.yaml and the repository keeps `fixed`:
- mode fixed (the default, and whatever goes wrong with the file or the table) is today's stop, bit for bit;
- mode band ends the book at its band table's 99% line, never past the 27% hard ceiling at any level (#600);
- the band is read point in time: a table whose research window has not ended before the decision's day is not
  used, and the horizon counts only sessions closed before today since the book's start;
- the file is read once per engine: an edit takes effect at the next restart (#600).

The harness (tests/test_m9_book_end.py): a $10,000 pot, 40 B held at about $91.68 (about $3,667) at the balanced
level, the level's fixed line set to 5%. `_fall(f, x)` moves B so the book is about x below its peak.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from test_failsafe_drills import MD
from test_m9_book_end import _held, _kinds

from kaibot.engine import core
from kaibot.engine.config import ROOT
from kaibot.risk import book_stop as bs
from kaibot.risk.gate import BOOK_HARD_CEILINGS


def _table(tmp_path: Path, q99, q95=None, horizons=(5, 20, 252), window=("2017-05-01", "2024-12-31"),
           level="balanced", name="band.json") -> Path:
    q99 = list(q99) if isinstance(q99, list | tuple) else [q99] * len(horizons)
    q95 = list(q95) if q95 is not None else [x * 0.8 for x in q99]
    src = {"window": list(window)} if window is not None else {}
    p = tmp_path / name
    p.write_text(json.dumps({"rule": MD, "level": level, "horizons": list(horizons), "q95": q95, "q99": q99,
                             "source": src}))
    return p


def _config(tmp_path: Path, monkeypatch, mode="band", table: Path | str | None = None, level="balanced",
            **band) -> Path:
    cfg = yaml.safe_load((ROOT / "config" / "book_stop.yaml").read_text())
    cfg["mode"] = mode
    cfg["band"].update(band)
    cfg["tables"] = {MD: {level: str(table)}} if table is not None else {}
    p = tmp_path / "book_stop.yaml"
    p.write_text(yaml.safe_dump(cfg))
    monkeypatch.setattr(core, "BOOK_STOP_PATH", p)
    return p


def _fall(a, f, x: float, start=40, n=3):
    """B's bars and the broker agree that B fell so the book is about x (a fraction) below its peak."""
    line = a.eng.book_line
    held = 40 * float(f._close("B"))
    f.bar = f.broker = max(1 - x * line["peak"] / held, 0.01)
    a.ticks(a.open1, start, start + n)


def _end(a):
    (e,) = _kinds(a.eng, "md_book_end")
    return e


# ---------------------------------------------------------------- the switch: fixed is today's stop


def test_the_repository_switch_is_fixed_and_the_engine_keeps_todays_stop(tmp_path, monkeypatch):
    """The committed config (mode fixed, the #602 table registered) and no config at all give the same stop, the
    same journal and the same heartbeat: the level's 5% line ends the book at a 20% fall."""
    assert bs.load(core.BOOK_STOP_PATH).mode == "fixed"
    runs = []
    for path in (core.BOOK_STOP_PATH, tmp_path / "missing" / "book_stop.yaml"):
        monkeypatch.setattr(core, "BOOK_STOP_PATH", path)
        d = tmp_path / path.parent.name
        d.mkdir(exist_ok=True)
        a, f = _held(d, monkeypatch)
        _fall(a, f, 0.20)
        e = _end(a)
        assert e["end_pct"] == 5.0 and "why" not in e and "stop" not in a.eng.book_line
        book = [(k, b) for *_, k, b in a.eng.journal.entries(
            kinds=("md_book_peak", "md_book_breach", "md_book_breach_clear", "md_book_end", "md_book_band_alert"))]
        runs.append((book, {k: v for k, v in a.eng.book_line.items()}))
        f.undo()
    assert runs[0] == runs[1]
    assert not [k for k, _ in runs[0][0] if k == "md_book_band_alert"]


def test_the_file_is_read_once_and_an_edit_takes_effect_at_the_next_restart(tmp_path, monkeypatch):
    p = _config(tmp_path, monkeypatch, mode="fixed", table=_table(tmp_path, 0.9))
    a, f = _held(tmp_path, monkeypatch)
    cfg = yaml.safe_load(p.read_text())
    cfg["mode"] = "band"
    p.write_text(yaml.safe_dump(cfg))  # edited while the engine runs
    a.ticks(a.open1, 40, 41)
    assert "stop" not in a.eng.book_line and a.eng.book_line["end_pct"] == 5.0  # still the fixed line
    eng = a.restart()
    a.ticks(a.open1, 41, 42)
    assert eng.book_line["stop"]["mode"] == "band" and eng.book_line["end_pct"] == 27.0


# ---------------------------------------------------------------- the band replaces the line


def test_a_band_tighter_than_the_line_ends_the_book_at_its_99_line(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, table=_table(tmp_path, 0.02))
    a, f = _held(tmp_path, monkeypatch)
    _fall(a, f, 0.03)  # inside the fixed 5% line, past the band's 2%
    e = _end(a)
    assert (e["why"], e["horizon"], e["line_pct"], e["end_pct"]) == ("band_99", 20, 2.0, 2.0)
    assert a.eng.daily.retirement(MD) is not None and a.eng.daily.account.book_ended()
    assert _kinds(a.eng, "md_book_breach")[0]["end_pct"] == 2.0  # two checks, as today


def test_the_band_alert_is_a_record_once_a_day_and_sells_nothing(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, table=_table(tmp_path, 0.10, q95=[0.02] * 3))
    a, f = _held(tmp_path, monkeypatch)
    _fall(a, f, 0.04, n=5)
    (al,) = _kinds(a.eng, "md_book_band_alert")
    assert al["alert_pct"] == 2.0 and al["end_pct"] == 10.0 and al["horizon"] == 20
    assert not _kinds(a.eng, "md_book_end") and "B" in a.broker.pos


# ---------------------------------------------------------------- the ceiling: never past 27%, at any level


def test_a_band_above_the_ceiling_is_capped_at_27_percent(tmp_path, monkeypatch):
    """The table's 99% line is 90%: the line is 27%. A 20% fall (past the level's fixed 5%) does not end the book;
    a 29% fall does, at 27%."""
    _config(tmp_path, monkeypatch, table=_table(tmp_path, 0.9))
    a, f = _held(tmp_path, monkeypatch)
    _fall(a, f, 0.20)
    assert not _kinds(a.eng, "md_book_end") and a.eng.book_line["end_pct"] == 27.0
    assert "(ceiling)" in a.eng.book_line["stop"]["why"]
    _fall(a, f, 0.29, start=43)
    e = _end(a)
    assert e["why"] == "band_99" and e["line_pct"] == 27.0 and e["drawdown_pct"] >= 27.0


def test_a_config_ceiling_above_27_is_refused_and_the_engine_keeps_the_fixed_line(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, table=_table(tmp_path, 0.9), ceiling_pct=40)
    a, f = _held(tmp_path, monkeypatch)
    _fall(a, f, 0.20)
    assert _end(a)["end_pct"] == 5.0 and "stop" not in a.eng.book_line  # the file is unusable: mode fixed


hyp = pytest.importorskip("hypothesis")
st = hyp.strategies


@hyp.given(level=st.sampled_from(["conservative", "balanced", "aggressive"]),
           q99=st.floats(0.001, 1.0), sessions=st.integers(0, 5000), fall=st.floats(0.0, 0.99),
           ceil=st.floats(0.5, 27.0))
@hyp.settings(max_examples=300, deadline=None)
def test_property_the_band_never_passes_27_percent_at_any_level(level, q99, sessions, fall, ceil):
    """Each level's book limits (config/profiles.yaml) with any table and any allowed ceiling: the end line is
    at most the hard ceiling, and a fall to the hard ceiling always ends the book."""
    from kaibot.risk.profiles import rule_book_limits

    lim = rule_book_limits(MD, level, research=True)
    cfg = bs.BookStopConfig("band", 0.99, 0.95, 20, ceil, False, 5, {}, {})
    t = bs.BandTable(MD, level, (5, 20, 252), (q99 * 0.5,) * 3, (q99,) * 3)
    v = bs.verdict(100.0, 100.0 * (1 - fall), lim, cfg=cfg, table=t, sessions=sessions)
    assert v.mode == "band" and v.line <= BOOK_HARD_CEILINGS.run_drawdown_end_pct / 100 + 1e-12
    if fall >= BOOK_HARD_CEILINGS.run_drawdown_end_pct / 100:
        assert v.end


def test_the_committed_table_is_capped_at_27_at_every_horizon():
    """#602's table (trend_momentum at aggressive): its raw 99% line goes past 27%, the line used never does."""
    from dataclasses import replace

    from kaibot.risk.profiles import rule_book_limits

    cfg = replace(bs.load(core.BOOK_STOP_PATH), mode="band")
    t = bs.table_for(cfg, MD, "aggressive")
    assert t is not None and max(t.q99) > 0.27
    lim = rule_book_limits(MD, "aggressive", research=True)
    for h in (0, *t.horizons, 5000):
        v = bs.verdict(100.0, 73.0, lim, cfg=cfg, table=t, sessions=h)
        assert v.mode == "band" and v.line <= 0.27 + 1e-12 and v.end  # a 27% fall ends it at every horizon


# ---------------------------------------------------------------- point in time


def test_pure_a_table_is_used_only_after_its_window_ended():
    from datetime import date

    t = bs.BandTable(MD, "balanced", (5,), (0.1,), (0.2,), {"window": ["2017-05-01", "2024-12-31"]})
    assert bs.table_unusable(t, date(2025, 1, 1)) is None
    assert "ends on 2024-12-31" in bs.table_unusable(t, date(2024, 12, 31))
    assert "ends on" in bs.table_unusable(t, date(2020, 6, 1))
    for src in ({}, {"window": None}, {"window": ["2017-05-01"]}, {"window": ["a", "b"]}):
        assert "no research window" in bs.table_unusable(bs.BandTable(MD, "balanced", (5,), (0.1,), (0.2,), src),
                                                         date(2026, 1, 1))


def test_pure_the_horizon_counts_only_sessions_closed_before_the_decision_day():
    from datetime import date

    s = ["2026-08-03", "2026-08-04", "2026-08-04", "2026-08-05", "not a date", "2026-07-31"]
    assert bs.sessions_traded(s, date(2026, 8, 3), date(2026, 8, 5)) == 2  # today (8-05) and before the start: out
    assert bs.sessions_traded(s, date(2026, 8, 3), date(2026, 8, 3)) == 0
    assert bs.sessions_traded([], date(2026, 8, 3), date(2026, 9, 1)) == 0


def test_a_table_whose_window_has_not_ended_keeps_the_fixed_line(tmp_path, monkeypatch):
    """The harness trades on 3 Aug 2026; a table drawn on a window to that day is not known yet."""
    _config(tmp_path, monkeypatch, table=_table(tmp_path, 0.9, window=("2017-05-01", "2026-08-03")))
    a, f = _held(tmp_path, monkeypatch)
    _fall(a, f, 0.20)
    assert _end(a)["end_pct"] == 5.0 and "why" not in _end(a)
    assert "ends on 2026-08-03" in a.eng.book_line["stop"]["why"] and a.eng.book_line["stop"]["mode"] == "fixed"


def test_the_engine_horizon_does_not_count_the_session_under_way(tmp_path, monkeypatch):
    """Horizons 1, 2, 3 at 2%, 50%, 60% with min_sessions 1. On DAY1 nothing has closed (h = 1); on DAY2 one
    session has closed, so h = 1 still: counting the session under way would give the 50% line."""
    _config(tmp_path, monkeypatch, table=_table(tmp_path, (0.02, 0.5, 0.6), horizons=(1, 2, 3)), min_sessions=1)
    a, f = _held(tmp_path, monkeypatch)
    assert (a.eng.book_line["stop"]["horizon"], a.eng.book_line["end_pct"]) == (1, 2.0)
    a.ticks(a.open1, 40, 390)
    a.after_close(a.open1)
    a.ticks(a.open2, 30, 32)
    assert [c.session for c in a.eng.ledger.book(a.rid).closes] == ["2026-08-03"]
    assert (a.eng.book_line["stop"]["horizon"], a.eng.book_line["end_pct"]) == (1, 2.0)


# ---------------------------------------------------------------- fall back to today's stop


def test_a_missing_band_file_falls_back_to_the_fixed_line(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, table=tmp_path / "nowhere" / "band.json")
    a, f = _held(tmp_path, monkeypatch)
    _fall(a, f, 0.20)
    e = _end(a)
    assert e["end_pct"] == 5.0 and "why" not in e
    assert a.eng.book_line["stop"]["mode"] == "fixed" and "unusable" in a.eng.book_line["stop"]["why"]


def test_a_band_mode_without_a_table_for_the_level_falls_back_to_the_fixed_line(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, table=_table(tmp_path, 0.02, level="aggressive"), level="aggressive")
    a, f = _held(tmp_path, monkeypatch)  # the run is at balanced
    _fall(a, f, 0.03)
    assert not _kinds(a.eng, "md_book_end")  # the band's 2% would have ended it; the fixed 5% does not
    assert "no band table is committed for trend_momentum at balanced" in a.eng.book_line["stop"]["why"]


def test_a_missing_config_file_is_the_fixed_line(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "BOOK_STOP_PATH", tmp_path / "nope.yaml")
    a, f = _held(tmp_path, monkeypatch)
    _fall(a, f, 0.20)
    assert _end(a)["end_pct"] == 5.0 and "stop" not in a.eng.book_line


def test_a_failure_inside_the_band_is_the_fixed_line_never_no_stop(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, table=_table(tmp_path, 0.9))
    a, f = _held(tmp_path, monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("planted")

    monkeypatch.setattr(bs, "sessions_traded", boom)
    _fall(a, f, 0.20)
    assert _end(a)["end_pct"] == 5.0 and "the band failed" in a.eng.book_line["stop"]["why"]
