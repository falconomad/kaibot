"""#458: scripts/index_core.py, the pre-registered engine + SimBroker run of the index core, on the committed fixture
(the ten years live only on the owner's Mac). It runs every configuration the pre-registration declares, judges the
book with gate v3, counts six configurations, and refuses the hold-out."""

from __future__ import annotations

import json
import sys

from kaibot.engine.config import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
import index_core as script  # noqa: E402


def test_the_run_on_the_fixture_writes_the_book_test_and_counts_six_configurations(tmp_path):
    trials = tmp_path / "trials.jsonl"
    out = script.main(["--fixture", "--data", str(tmp_path), "--out", str(tmp_path / "out"), "--trials",
                       str(trials)])
    labels = {r["label"] for r in out["rows"]}
    assert labels == {"base|1x", "base|2x", "base|3x", "base|0x", "base|stress", "level|balanced",
                      "level|conservative", *{f"neighbour|{n}" for n in ("vol12", "vol18", "lookback63", "daily",
                                                                         "nocap")}}
    rows = [json.loads(x) for x in trials.read_text().splitlines()]
    assert len(rows) == 12 and sum(r["n_trials"] for r in rows) == 6
    assert {r["prereg_sha256"] for r in rows} == {out["prereg_sha256"]}
    assert {r["cost_model"] for r in rows} == {"v2"}  # prereg Amendment 2 (#572)
    rep = out["gate_v3_book"]
    assert rep["gate"] == "B-book" and rep["gate_a"]["listed_as_book"] is True
    assert rep["verdict"] != "pass"  # a fixture of one year from the research harness's data cannot pass
    assert set(rep["marks"]) == {"b1", "b2", "b3", "b4", "b5", "b6", "b7"} and rep["marks"]["b6"]["met"] is True
    for r in out["rows"]:
        assert r["guard_cuts"] == 0 and r["overdraft_days"] == 0
    assert (tmp_path / "out" / (out["file"].rsplit("/", 1)[1][:-5] + ".md")).is_file()


def test_the_run_is_priced_with_cost_model_v2_not_only_labelled_so(tmp_path, monkeypatch):
    """QA on #548: prereg Amendment 2. Every run of the script goes through SimBroker with AuctionCostsV2, the report
    and every trial row say v2, and the fills differ from the same run at v1, so a script that ran v1 under a v2
    label fails here."""
    from kaibot.broker.sim import AuctionCostsV2

    calls = []
    real = script.run_daily

    def spy(*args, **kw):
        r = real(*args, **kw)
        calls.append((args, kw, r))
        return r

    monkeypatch.setattr(script, "run_daily", spy)
    trials = tmp_path / "trials.jsonl"
    out = script.main(["--fixture", "--data", str(tmp_path), "--out", str(tmp_path / "out"), "--trials", str(trials)])
    assert calls and all(kw.get("cost_model") == "v2" for _, kw, _ in calls)
    assert all(isinstance(r.broker.costs, AuctionCostsV2) for _, _, r in calls)
    assert {json.loads(x)["cost_model"] for x in trials.read_text().splitlines()} == {"v2"}
    rep = json.loads((tmp_path / "out" / out["file"].rsplit("/", 1)[1]).read_text())
    assert rep["gate_v3_book"]["source"]["cost_model"] == "v2"
    args, kw, v2 = next(c for c in calls if c[1].get("fill", "base") == "base" and c[1].get("cost", 1.0) == 1.0)
    v1 = real(*args, **{**kw, "cost_model": "v1"})
    assert v2.fills and [f["px"] for f in v2.fills] != [f["px"] for f in v1.fills]
