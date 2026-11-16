"""Issue #250: `engine.shadow._utc_ns` without the deprecated `Series.values` on a tz-aware column (pandas 3.1; in
pandas 4 it returns a DatetimeArray and the shadow desk failed on every minute). Same int64 nanoseconds as the old
expression for every input the shadow desk sees, and the engine on fakes runs with the deprecation as an error with
an unchanged golden."""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
import pytest

from kaibot.engine.shadow import _utc_ns


def old(ts: pd.Series) -> np.ndarray:
    """The expression before #250 (warnings silenced: it is the reference)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        v = ts.values
        if v.dtype.kind != "M":
            v = pd.to_datetime(ts, utc=True).values
        return v.astype("datetime64[ns]").astype(np.int64)


T = ["2026-09-15 13:30:00", "2026-09-15 13:31:00", "2026-09-15 19:59:00"]
CASES = {
    "utc_ns": pd.Series(pd.to_datetime(T, utc=True)),
    "utc_us": pd.Series(pd.to_datetime(T, utc=True).as_unit("us")),
    "new_york": pd.Series(pd.to_datetime(T, utc=True).tz_convert("America/New_York")),
    "naive": pd.Series(pd.to_datetime(T)),
    "strings": pd.Series([f"{t}+00:00" for t in T], dtype=object),
    "with_nat": pd.Series(pd.to_datetime([T[0], None, T[2]], utc=True)),
    "empty": pd.Series(pd.to_datetime([], utc=True)),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_same_nanoseconds_as_before_without_the_deprecated_call(name):
    ts = CASES[name]
    want = old(ts)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        got = _utc_ns(ts)
    assert got.dtype == np.int64 and np.array_equal(got, want)


def test_the_engine_on_fakes_runs_with_the_pandas_4_deprecation_as_an_error(tmp_path):
    import spine_fixture as sf
    from test_spine_golden import check, engine_on_fakes, engine_summary

    with warnings.catch_warnings():
        warnings.simplefilter("error", pd.errors.Pandas4Warning)
        eng, broker, store = engine_on_fakes(tmp_path, sf.load())
    check("engine_on_fakes.json", engine_summary(broker, store))
