"""#546 (weekly mutation testing): tests for surviving mutants of `engine/journal.py`'s pure helpers.

- `select_rows` / `_cmp`: the journal answers the engine's PostgREST-style reads from memory, so each comparison
  must keep its boundary (lt is strict, lte is not), compare numbers as numbers (9 < 10) and text as text (ISO
  dates), and treat a bool as text, not as 0 or 1;
- `_json`: datetimes leave as UTC ISO text, dates as ISO dates, numpy scalars as plain numbers, anything else is
  refused;
- `_schema_missing`: every PostgREST / Postgres answer for a missing table or column is recognised;
- `_refusal`: only a 4xx that carries a row's own database code is a refusal of that row.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import numpy as np
import pytest

from kaibot.engine import journal as J

ROWS = [{"id": i, "d": f"2026-11-0{i}", "f": i / 2} for i in range(1, 6)]


def ids(params, rows=ROWS):
    return [r["id"] for r in J.select_rows(rows, params)]


@pytest.mark.parametrize("op, want", [("lt", [1, 2]), ("lte", [1, 2, 3]), ("gt", [4, 5]), ("gte", [3, 4, 5])])
def test_each_comparison_keeps_its_boundary_on_numbers(op, want):
    assert ids({"id": f"{op}.3"}) == want


@pytest.mark.parametrize("op, want", [("lt", [1, 2]), ("lte", [1, 2, 3]), ("gt", [4, 5]), ("gte", [3, 4, 5])])
def test_each_comparison_keeps_its_boundary_on_iso_dates(op, want):
    assert ids({"d": f"{op}.2026-11-03"}) == want


def test_numbers_compare_as_numbers_not_as_text():
    rows = [{"id": 9}, {"id": 10}, {"id": 100}]
    assert ids({"id": "lt.10"}, rows) == [9]          # as text "100" < "9" and "10" < "9"
    assert ids({"id": "gte.10"}, rows) == [10, 100]
    assert ids({"id": "gt.2.5"}, [{"id": 2}, {"id": 3}]) == [3]


def test_a_number_against_a_non_number_falls_back_to_text():
    assert J._cmp("lt", 5, "abc") is True             # "5" < "abc"
    assert J._cmp("gt", 5, "abc") is False


def test_a_bool_is_compared_as_text():
    assert J._cmp("gt", True, "0") is True            # "true" > "0"; as a number True would be 1 > 0 too
    assert J._cmp("lt", True, "2") is False           # as a number 1 < 2 would be True
    assert J._cmp("lte", False, "false") is True


def test_bools_are_filtered_as_postgrest_spells_them():
    rows = [{"id": 1, "b": True}, {"id": 2, "b": False}, {"id": 3, "b": None}, {"id": 4, "b": 1}]
    assert ids({"b": "eq.true"}, rows) == [1]
    assert ids({"b": "eq.false"}, rows) == [2]
    assert ids({"b": "neq.true"}, rows) == [2, 3, 4]
    assert ids({"b": "in.(false,1)"}, rows) == [2, 4]
    assert J._s(True) == "true" and J._s(False) == "false" and J._s(1) == "1" and J._s(0) == "0"


def test_a_row_without_the_column_never_matches_a_comparison():
    rows = [{"id": 1, "x": None}, {"id": 2}]
    for op in ("lt", "lte", "gt", "gte"):
        assert ids({"x": f"{op}.0"}, rows) == []


def test_json_turns_datetimes_into_utc_iso_text():
    t = datetime(2026, 11, 24, 15, 30, tzinfo=timezone(timedelta(hours=1)))
    assert J._json(t) == "2026-11-24T14:30:00+00:00"
    assert J.canon({"t": t, "d": date(2026, 11, 24)}) == {"t": "2026-11-24T14:30:00+00:00", "d": "2026-11-24"}


def test_json_turns_numpy_scalars_into_plain_numbers_and_refuses_the_rest():
    assert J._json(np.int64(7)) == 7 and type(J._json(np.int64(7))) is int
    assert J._json(np.float64(0.5)) == 0.5
    with pytest.raises(TypeError):
        J._json(object())
    with pytest.raises(TypeError):
        J.canon({"x": {1, 2}})


def test_json_keeps_a_utc_datetime_as_it_is():
    assert J._json(datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)) == "2026-01-02T03:04:05+00:00"


@pytest.mark.parametrize("text", [
    "GET /rest/v1/x -> 404: {\"code\":\"PGRST205\",\"message\":\"m\"}",
    "POST /rest/v1/x -> 400: {\"code\":\"PGRST204\"}",
    "POST /rest/v1/x -> 404: {\"code\":\"42P01\"}",
    "POST /rest/v1/x -> 400: {\"code\":\"42703\"}",
    "relation kaibot.x does not exist",
    "Could not find the 'multi_day' column",
])
def test_every_missing_table_or_column_answer_is_recognised(text):
    assert J._schema_missing(RuntimeError(text)) is True


@pytest.mark.parametrize("text", ["POST /rest/v1/x -> 503: unavailable", "timed out", "23505 duplicate key"])
def test_other_failures_are_not_a_missing_schema(text):
    assert J._schema_missing(RuntimeError(text)) is False


def _err(status, code=None):
    body = f'{{"code":"{code}","message":"m"}}' if code else "no body"
    return RuntimeError(f"POST /rest/v1/x -> {status}: {body}")


@pytest.mark.parametrize("status, code", [
    (400, "22P02"), (409, "23505"), (400, "23514"), (409, "23503"), (400, "42703"), (404, "42P01"),
    (400, "42804"), (404, "42883"), (400, "PGRST102"), (400, "PGRST204"), (404, "PGRST205"), (499, "22001"),
])
def test_a_rows_own_fault_is_a_refusal_with_its_code(status, code):
    assert J._refusal(_err(status, code)) == code


@pytest.mark.parametrize("status, code", [
    (401, "23505"), (403, "23505"), (408, "23505"), (429, "23505"),   # the key, a timeout, a quota: not the row
    (500, "23505"), (503, "22P02"), (399, "23505"),                    # not a 4xx
    (405, "25006"), (400, "40001"), (400, "55P03"), (406, "PGRST106"),  # the database or the gateway, every row
    (400, None), (404, None),                                          # a proxy's answer, no database code
    (400, "42P0X"), (400, "PGRST2050"),                               # near misses of a row code
])
def test_anything_else_is_not_a_refusal_of_the_row(status, code):
    assert J._refusal(_err(status, code)) is None


def test_an_error_without_a_status_is_not_a_refusal():
    assert J._refusal(RuntimeError('connection reset {"code":"23505"}')) is None


def test_an_and_group_applies_every_condition_in_it():
    assert ids({"and": "(id.gt.1,id.lt.4)"}) == [2, 3]
    assert ids({"and": "(id.gte.2,d.lte.2026-11-02)"}) == [2]


def test_not_is_null_keeps_the_rows_that_have_the_column():
    rows = [{"id": 1, "x": 0}, {"id": 2, "x": None}, {"id": 3}, {"id": 4, "x": "a"}]
    assert ids({"x": "not.is.null"}, rows) == [1, 4]
    assert ids({"x": "is.null"}, rows) == [2, 3]


def test_order_by_several_columns_each_with_its_own_direction():
    rows = [{"id": 5, "k": "a"}, {"id": 4, "k": "b"}, {"id": 3, "k": "a"}, {"id": 2, "k": "b"}, {"id": 1, "k": "a"}]
    assert ids({"order": "k.desc,id.asc"}, rows) == [2, 4, 1, 3, 5]
    assert ids({"order": "k.asc,id.desc"}, rows) == [5, 3, 1, 4, 2]


def test_order_without_a_direction_is_ascending():
    rows = [{"id": 3}, {"id": 1}, {"id": 2}]
    assert ids({"order": "id"}, rows) == [1, 2, 3]


def test_rows_without_the_order_column_come_last_ascending_and_first_descending():
    # as PostgREST does by default (nulls last for asc, nulls first for desc)
    rows = [{"id": 1, "v": 2}, {"id": 2, "v": None}, {"id": 3, "v": 1}, {"id": 4}]
    assert ids({"order": "v.asc"}, rows) == [3, 1, 2, 4]
    assert ids({"order": "v.desc"}, rows) == [2, 4, 1, 3]
    assert ids({"order": "v.asc", "limit": "2"}, rows) == [3, 1]


def test_not_is_null_is_combined_with_the_other_conditions():
    rows = [{"id": 1, "x": 0}, {"id": 2, "x": None}, {"id": 3, "x": 5}, {"id": 4, "x": "a"}]
    assert ids({"x": "not.is.null", "id": "gte.3"}, rows) == [3, 4]
