"""Sample v2 of the headline tags (#379): the store draw (draw_store.py) and its measurement (measure.py --v2).

No network and no real store: a small parquet store in tmp_path, shaped like ingest/alpaca_history.fetch_news.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from kaibot.news import tags as T
from kaibot.news.tagset import draw_store as D
from kaibot.news.tagset import measure as M

HEADS = [
    "Acme Reports Q3 Earnings, Revenue Up 10%",
    "Acme Raises FY Guidance After Strong Quarter",
    "Acme Cuts Full-Year Outlook on Weak Demand",
    "Citigroup Adjusts Price Target on Acme to $90 From $95",
    "Acme to Acquire Widget Corp for $2 Billion",
    "Acme Names New Chief Financial Officer",
    "Acme Opens New Store in Ohio",
    "Acme Announces $1 Billion Share Repurchase Program",
]


def _store(tmp_path, years=(2016, 2019, 2024)):
    news = tmp_path / "news"
    for sym in ("ACME", "BETA"):
        for y in years:
            rows = [(datetime(y, m, 10, 14, 0, tzinfo=UTC), f"{h} ({y}-{m})", "benzinga", f"{sym}")
                    for m in range(1, 13) for h in HEADS]
            if sym == "BETA":  # the same story fetched for a second symbol is one headline with both symbols
                rows = [(t, h, s, "ACME,BETA") for t, h, s, _ in rows[:5]]
            out = news / f"symbol={sym}" / f"{y}.parquet"
            out.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(pa.table({
                "created_at": pa.array([r[0] for r in rows], pa.timestamp("s", tz="UTC")),
                "headline": [r[1] for r in rows], "source": [r[2] for r in rows],
                "symbols": [r[3] for r in rows]}), out)
    return news


def test_the_store_is_read_once_per_story_with_all_its_symbols(tmp_path):
    rows = D.read_store(_store(tmp_path))
    assert len(rows) == 3 * 12 * len(HEADS)
    first = [r for r in rows if r["headline"] == f"{HEADS[0]} (2016-1)"]
    assert len(first) == 1 and first[0]["symbols"] == ["ACME", "BETA"]
    assert all(r["created_at"].tzinfo is not None for r in rows)
    assert rows == sorted(rows, key=lambda r: (r["created_at"], r["headline"]))


def test_history_lists_each_year_and_source(tmp_path):
    md = D.history(D.read_store(_store(tmp_path)))
    assert "| 2016 | 96 | benzinga 96 |" in md and "| 2024 | 96 | benzinga 96 |" in md
    assert "Oldest: 2016-01-10T14:00:00+00:00" in md
    assert "no headlines" in D.history([])


def test_the_draw_is_deterministic_and_stratified(tmp_path):
    rows = D.read_store(_store(tmp_path))
    days = {("ACME", "2016-03-10")}
    a = D.draw(rows, days, per_year=10, per_tag=3)
    b = D.draw(rows, days, per_year=10, per_tag=3)
    assert a == b
    heads, strata = a
    assert [h["n"] for h in heads] == [s["n"] for s in strata] == list(range(1, len(heads) + 1))
    c = {}
    for s in strata:
        c[s["stratum"]] = c.get(s["stratum"], 0) + 1
    assert c["random"] == 30  # 10 per year, three years
    assert all(k == "random" or k.startswith("fired:") for k in c)
    assert len({h["headline"] for h in heads}) == len(heads)  # nothing drawn twice
    assert c["fired:guidance_up:other"] <= 3 and "fired:analyst_up" not in c  # analyst_up shipped in v1


def test_the_labeller_file_does_not_say_which_rule_picked_a_headline(tmp_path):
    heads, _ = D.draw(D.read_store(_store(tmp_path)), None, per_year=5, per_tag=2)
    assert set(heads[0]) == {"n", "created_at", "symbols", "source", "headline", "earnings_day"}
    # shuffled: the random stratum is not simply the first block
    assert all(h["earnings_day"] is None for h in heads)


def test_fired_strata_hold_only_headlines_the_rule_tags(tmp_path):
    heads, strata = D.draw(D.read_store(_store(tmp_path)), None, per_year=5, per_tag=4)
    by_n = {h["n"]: h for h in heads}
    for s in strata:
        if s["stratum"].startswith("fired:"):
            tag = s["stratum"].split(":")[1]
            assert tag in T.all_tags(by_n[s["n"]]["headline"])


def test_earnings_day_uses_the_new_york_date_of_any_symbol():
    row = {"created_at": datetime(2024, 1, 31, 2, 0, tzinfo=UTC), "symbols": ["BETA", "ACME"]}  # 30 Jan in NY
    assert D.on_earnings_day(row, {("ACME", "2024-01-30")}) is True
    assert D.on_earnings_day(row, {("ACME", "2024-01-31")}) is False
    assert D.on_earnings_day(row, None) is None


def test_earnings_days_reads_session_and_acceptance_date(tmp_path):
    import pandas as pd

    p = tmp_path / "announcements.parquet"
    pd.DataFrame({"symbol": ["ACME"], "accepted_ts": [pd.Timestamp("2024-01-30T21:05:00Z")],
                  "session": [pd.Timestamp("2024-01-31")]}).to_parquet(p)
    assert D.earnings_days(p) == {("ACME", "2024-01-30"), ("ACME", "2024-01-31")}
    assert D.earnings_days(tmp_path / "missing.parquet") is None


def test_a_new_rule_version_refuses_to_draw(monkeypatch):
    monkeypatch.setattr(D, "RULES_VERSION", 2)
    with pytest.raises(SystemExit):
        D.draw([], None)


def test_draw_writes_both_files(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(D, "NEWS", _store(tmp_path))
    monkeypatch.setattr(D, "ANNOUNCEMENTS", tmp_path / "none.parquet")
    out = tmp_path / "out"
    out.mkdir()
    assert D.main(["draw", "--out", str(out)]) == 0
    heads = (out / "headlines_v2.jsonl").read_text().splitlines()
    assert len(heads) == len((out / "draw_v2.jsonl").read_text().splitlines()) > 0
    assert "not available" in capsys.readouterr().out


def _labelled(tmp_path):
    heads, strata = D.draw(D.read_store(_store(tmp_path)), {("ACME", "2016-02-10")}, per_year=20, per_tag=25)
    labels = [{"n": h["n"], "tags": sorted(T.all_tags(h["headline"]))} for h in heads]  # a perfect labeller
    for name, rows in (("headlines_v2.jsonl", heads), ("labels_v2.jsonl", labels), ("draw_v2.jsonl", strata)):
        (tmp_path / name).write_text("".join(json.dumps(r) + "\n" for r in rows))
    return heads


def test_measure_v2_needs_all_three_files(tmp_path):
    with pytest.raises(SystemExit):
        M.load_v2(tmp_path)


def test_measure_v2_reports_precision_on_the_fired_stratum_and_recall_on_random(tmp_path):
    _labelled(tmp_path)
    res = M.measure_v2(M.load_v2(tmp_path))
    assert res["years"] == ["2016", "2019", "2024"]
    gu = res["tags"]["guidance_up"]
    assert gu["fired"]["tagged"] >= 20 and gu["precision"] == 1.0 and gu["recall"] == 1.0
    assert set(gu["by_earnings_day"]) == {"earnings_day", "other_day"}
    assert gu["by_earnings_day"]["earnings_day"]["fired"]["tagged"] >= 1
    assert res["tags"]["analyst_up"]["fired"] is None  # no fired stratum: judged on the random one
    md = M.markdown_v2(res)
    assert "| `guidance_up` | earnings_day |" in md


def test_measure_v2_does_not_ship_a_tag_with_a_false_positive_rate_above_5_percent(tmp_path):
    _labelled(tmp_path)
    lab = [json.loads(x) for x in (tmp_path / "labels_v2.jsonl").read_text().splitlines()]
    strata = {json.loads(x)["n"]: json.loads(x)["stratum"] for x in (tmp_path / "draw_v2.jsonl").read_text()
              .splitlines()}
    k = 0
    for r in lab:  # the labeller disagrees with the rule on 3 of the 25 fired `deal` headlines
        if strata[r["n"]] == "fired:deal" and k < 3:
            r["tags"] = [t for t in r["tags"] if t != "deal"]
            k += 1
    (tmp_path / "labels_v2.jsonl").write_text("".join(json.dumps(r) + "\n" for r in lab))
    res = M.measure_v2(M.load_v2(tmp_path))
    assert res["tags"]["deal"]["fired"]["fp"] == 3 and not res["tags"]["deal"]["ships"]


def test_the_declared_draw_does_not_follow_the_newest_measurement():
    """v2's fired strata are frozen with the draw declared in #399 (the ten tags v1 left out, in this order), so a
    later change of tags.SHIPPED cannot change what `draw` picks from the same store and seed."""
    assert D.DRAWN_FOR == ("results", "guidance_up", "guidance_down", "analyst_down", "deal", "offering", "buyback",
                           "management_change", "legal_regulatory", "listing")
    src = Path(D.__file__).read_text()
    assert "import NOT_SHIPPED" not in src and ", NOT_SHIPPED" not in src and "in NOT_SHIPPED" not in src
