"""M12 ticket 3.7 (#469): the drills, played. Not collected by a plain `pytest` (the file name does not start
with test_): each drill plays whole simulated sessions, about 5 to 100 seconds. The weekly `drills` workflow runs
scripts/run_drills.py; locally:

    PYTHONHASHSEED=0 PYTHONPATH=src pytest tests/drills/check_drills.py -q [-k lease]

A drill that names an open engine issue (`known` in drills.DRILLS) is a strict xfail.
"""

from __future__ import annotations

import drills
import pytest


def _params():
    for name, (_fn, _docs, known) in drills.DRILLS.items():
        marks = [pytest.mark.xfail(strict=True, reason=f"#{known}: open engine issue")] if known else []
        yield pytest.param(name, marks=marks, id=name.replace(" ", "-"))


@pytest.mark.parametrize("name", list(_params()))
def test_drill(name, tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONHASHSEED", "0")
    rec = drills.run(name, tmp_path)
    assert rec.ok, "\n".join([rec.error or "", *map(str, rec.checks)])
