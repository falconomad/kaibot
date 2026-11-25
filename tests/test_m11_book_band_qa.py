"""QA-Ops second key on #652 (M11 ticket 2.10, #461): extra checks of the book stop's switch.

- Fail closed: a failure anywhere in the new code (the switch's loader, the band's table lookup, the band's verdict)
  gives today's fixed line, and the book still ends at it: never "no stop".
- The ceiling: at every level's fixed line (13 / 21 / 27%) a band table at 100% gives a 27% line, a fall short of
  27% never ends the book and a fall past it does, at 27%; in mode fixed no level's line is above 27%.
- Restarts: in mode band a breach journalled before a restart is confirmed after it, at the band's line.

The harness is tests/test_m9_book_end.py's (a $10,000 pot, 40 B held, the run at the balanced level).
"""

from __future__ import annotations

import pytest
from test_failsafe_drills import MD
from test_m9_book_end import Feed, _held, _kinds
from test_m11_book_band import _config, _end, _fall, _table

from kaibot.risk import book_stop as bs
from kaibot.risk.gate import BOOK_HARD_CEILINGS


def _boom(*a, **k):
    raise RuntimeError("planted")


# ---------------------------------------------------------------- fail closed: never "no stop"


@pytest.mark.xfail(strict=True, raises=RuntimeError, reason=(
    "QA-Ops on #652: core._book_end_check calls self._book_stop() again outside its try (the heartbeat's `stop` "
    "block), so a loader that raises escapes the book check at every tick: no stop. Remove this mark with the fix."))
def test_a_switch_loader_that_raises_is_the_fixed_line_and_the_book_still_ends(tmp_path, monkeypatch):
    """load_or_fixed never raises today; if it ever did (a regression), the engine must still end the book at the
    fixed line, at every later check too, not raise out of the book check."""
    _config(tmp_path, monkeypatch, table=_table(tmp_path, 0.9))
    monkeypatch.setattr(bs, "load_or_fixed", _boom)
    a, f = _held(tmp_path, monkeypatch)
    _fall(a, f, 0.20)
    e = _end(a)
    assert e["end_pct"] == 5.0 and "why" not in e
    assert a.eng.daily.account.book_ended() and a.eng.daily.retirement(MD) is not None


def test_a_table_lookup_that_raises_is_the_fixed_line(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, table=_table(tmp_path, 0.9))
    monkeypatch.setattr(bs.BandTable, "at", _boom)
    a, f = _held(tmp_path, monkeypatch)
    _fall(a, f, 0.20)
    assert _end(a)["end_pct"] == 5.0 and "the band failed" in a.eng.book_line["stop"]["why"]


def test_a_band_verdict_that_raises_is_the_fixed_line(tmp_path, monkeypatch):
    """The band's own verdict raises (any cfg in mode band); the fallback is the fixed verdict, which ends the book."""
    _config(tmp_path, monkeypatch, table=_table(tmp_path, 0.9))
    real = bs.verdict

    def band_boom(*a, cfg, **k):
        if cfg.mode == "band":
            raise RuntimeError("planted")
        return real(*a, cfg=cfg, **k)

    monkeypatch.setattr(bs, "verdict", band_boom)
    a, f = _held(tmp_path, monkeypatch)
    _fall(a, f, 0.20)
    assert _end(a)["end_pct"] == 5.0 and "the band failed" in a.eng.book_line["stop"]["why"]


def test_a_table_file_that_is_not_json_is_the_fixed_line(tmp_path, monkeypatch):
    bad = tmp_path / "band.json"
    bad.write_text("{not json")
    _config(tmp_path, monkeypatch, table=bad)
    a, f = _held(tmp_path, monkeypatch)
    _fall(a, f, 0.20)
    assert _end(a)["end_pct"] == 5.0 and "unusable" in a.eng.book_line["stop"]["why"]


@pytest.mark.parametrize("mode", ["Band", "off", "none", ""])
def test_an_unknown_mode_is_the_fixed_line(tmp_path, monkeypatch, mode):
    _config(tmp_path, monkeypatch, mode=mode, table=_table(tmp_path, 0.9))
    a, f = _held(tmp_path, monkeypatch)
    _fall(a, f, 0.20)
    assert _end(a)["end_pct"] == 5.0 and "stop" not in a.eng.book_line


# ---------------------------------------------------------------- the ceiling, in the engine, at every level's line


@pytest.mark.parametrize("book_end", [13.0, 21.0, 27.0])
def test_the_band_line_is_27_at_every_levels_line_and_a_fall_past_it_ends_the_book(tmp_path, monkeypatch, book_end):
    _config(tmp_path, monkeypatch, table=_table(tmp_path, 1.0, q95=[0.99] * 3))
    a, f = _held(tmp_path, monkeypatch, book_end=book_end)
    assert a.eng.book_line["end_pct"] == 27.0
    _fall(a, f, 0.26)
    assert not _kinds(a.eng, "md_book_end") and a.eng.book_line["end_pct"] == 27.0
    _fall(a, f, 0.28, start=43)
    e = _end(a)
    assert (e["why"], e["line_pct"], e["end_pct"]) == ("band_99", 27.0, 27.0) and e["drawdown_pct"] >= 27.0


@pytest.mark.parametrize("book_end", [13.0, 21.0, 27.0])
def test_in_mode_fixed_each_levels_line_ends_the_book_and_is_never_past_27(tmp_path, monkeypatch, book_end):
    assert book_end <= BOOK_HARD_CEILINGS.run_drawdown_end_pct
    a, f = _held(tmp_path, monkeypatch, book_end=book_end)  # the committed config: mode fixed
    _fall(a, f, book_end / 100 + 0.01)
    e = _end(a)
    assert e["end_pct"] == book_end and "why" not in e and "stop" not in a.eng.book_line


def test_no_levels_book_line_is_past_the_hard_ceiling():
    from kaibot.risk.profiles import rule_book_limits

    for level in ("conservative", "balanced", "aggressive"):
        assert rule_book_limits(MD, level, research=True).run_drawdown_end_pct <= 27.0


# ---------------------------------------------------------------- restarts in mode band


@pytest.mark.parametrize("mode, fall, line", [("fixed", 0.07, 5.0), ("band", 0.03, 2.0)])
def test_a_breach_journalled_before_a_restart_is_confirmed_after_it(tmp_path, monkeypatch, mode, fall, line):
    """Two checks a minute apart, with a restart between them: the breach is read back from the journal and the
    second check after the restart confirms it, at the fixed line (mode fixed) or the band's (mode band)."""
    _config(tmp_path, monkeypatch, mode=mode, table=_table(tmp_path, 0.02))
    a, f = _held(tmp_path, monkeypatch)
    held = 40 * float(f._close("B"))
    f.bar = f.broker = 1 - fall * a.eng.book_line["peak"] / held
    a.ticks(a.open1, 40, 41)  # one check: the breach is journalled, not confirmed
    assert _kinds(a.eng, "md_book_breach") and not _kinds(a.eng, "md_book_end")
    factor = f.bar
    f.undo()
    eng = a.restart()
    f2 = Feed(a, eng)
    f2.bar = f2.broker = factor
    a.ticks(a.open1, 42, 44)
    (e,) = _kinds(eng, "md_book_end")
    assert e["end_pct"] == line
    if mode == "band":
        assert (e["why"], e["line_pct"]) == ("band_99", 2.0)
    else:
        assert "why" not in e and "stop" not in eng.book_line
    f2.undo()
