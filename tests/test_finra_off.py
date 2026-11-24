"""#623: the FINRA Reg SHO collector is switched off (stakeholder decision, 24 Nov 2026; finra.org terms forbid a
database of FINRA website data and its use with predictive analytics). It refuses to run, the store refuses the
source, and no live or research feature list references it. Product removed the two pre-registered ablation rules on
FINRA features by Amendment 1 of config/ablation.yaml (24 Nov 2026); run_ablation still refuses any config with a
FINRA rule and runs the five remaining rules."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import urllib.request
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest
import yaml

from kaibot.evaluation.ablation import parse_config
from kaibot.features import alt as alt_features_mod
from kaibot.ingest.alt import finra, form4, store

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 24, 23, 45, tzinfo=UTC)
FINRA_REFS = re.compile(r"finra_regsho|ingest\.alt\.finra|ingest\.alt import[^\n]*\bfinra\b|short_volume|svr_rel|"
                        r"cdn\.finra\.org|CNMSshvol")
# The only places that may still name the source: the refusing stub, the store's refusal list, and the CLI and
# ablation runner that turn a request for it into a refusal.
ALLOWED = {"src/kaibot/ingest/alt/finra.py", "src/kaibot/ingest/alt/store.py", "scripts/collect_alt.py",
           "scripts/run_ablation.py", "src/kaibot/features/alt.py"}  # features/alt.py: a "(removed)" docstring line
# Frozen pre-registration (config/ablation.yaml, sha256 in every trial). Product removed its two FINRA rules by
# Amendment 1 (#623): no rule name may use FINRA any more.
PENDING_AMENDMENT: set[str] = set()
REMOVED_BY_AMENDMENT_1 = {"sv_rel_no_spike", "sv_level_not_extreme"}
VERSION_1_SHA256 = "5d5e49aa502016dbdd414c72e52d22428b619f2c5088b44522653cd2bdb59537"  # the file as of PR #630
# The five rules that remain, exactly as pre-registered in version 1 (name, feature, op, value).
KEPT_RULES = {
    "insider_no_net_selling": ("insider_30d.net_usd", ">=", 0.0),
    "insider_any_buying": ("insider_30d.n_buys", ">=", 1.0),
    "tariffs_tone_not_shock": ("gdelt_tone_z.tariffs", ">=", -2.0),
    "conflict_tone_not_shock": ("gdelt_tone_z.conflict", ">=", -2.0),
    "recession_tone_not_shock": ("gdelt_tone_z.recession", ">=", -2.0),
}


def _no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network call")

    monkeypatch.setattr(urllib.request, "urlopen", boom)


def test_collector_refuses_to_run_and_cites_the_decision(tmp_path, monkeypatch):
    _no_network(monkeypatch)
    with pytest.raises(store.SourceDisabled, match="#623"):
        finra.collect(tmp_path, date(2026, 11, 9), now=NOW)
    assert not (tmp_path / "alt").exists()


def test_store_refuses_the_source_even_with_old_files_on_disk(tmp_path):
    old = tmp_path / "alt" / "finra_regsho" / "date=2026-11-09"
    old.mkdir(parents=True)
    (old / "part-x.parquet").write_bytes(b"old data")
    for call in (lambda: store.read_pit(tmp_path, "finra_regsho", NOW, ["trade_date", "symbol"]),
                 lambda: store.read_source(tmp_path, "finra_regsho"),
                 lambda: store.read_partition(tmp_path, "finra_regsho", date(2026, 11, 9)),
                 lambda: store.source_lock(tmp_path, "finra_regsho").__enter__()):
        with pytest.raises(store.SourceDisabled, match="#623"):
            call()
    assert (old / "part-x.parquet").read_bytes() == b"old data"  # refused, never deleted by code


def test_sources_yaml_and_nightly_runners_drop_finra():
    doc = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text())
    assert all("finra_regsho" not in (sec or {}) for sec in doc.values())
    spec = importlib.util.spec_from_file_location("collect_alt_623", ROOT / "scripts" / "collect_alt.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert "finra" not in mod.RUNNERS


def test_alt_features_has_no_finra_feature(tmp_path):
    out = alt_features_mod.alt_features(tmp_path, "ACME", NOW)
    assert "short_volume" not in out
    assert not hasattr(alt_features_mod, "short_volume")
    assert "finra" not in alt_features_mod.__dict__


def test_no_live_or_research_code_or_config_references_finra():
    files = [*(ROOT / "src").rglob("*.py"), *(ROOT / "scripts").rglob("*.py"), *(ROOT / "scripts").rglob("*.sh"),
             *(ROOT / "config").rglob("*.yaml"), *(ROOT / "apps" / "web" / "src").rglob("*.ts*")]
    offenders = []
    for f in files:
        rel = f.relative_to(ROOT).as_posix()
        if rel in ALLOWED or rel == "config/ablation.yaml":  # ablation.yaml: its amendment names the removed rules
            continue
        if FINRA_REFS.search(f.read_text(errors="replace")):
            offenders.append(rel)
    assert offenders == []


def test_frozen_ablation_rules_on_finra_are_only_the_known_pending_ones():
    cfg = parse_config((ROOT / "config" / "ablation.yaml").read_bytes())
    finra_rules = {r.name for r in cfg.rules if r.feature.split(".")[0] == "short_volume"}
    assert finra_rules <= PENDING_AMENDMENT == set()  # after Amendment 1: zero FINRA rules


def test_amendment_1_removes_only_the_two_finra_rules_and_changes_nothing_else():
    raw = (ROOT / "config" / "ablation.yaml").read_bytes()
    doc, cfg = yaml.safe_load(raw), parse_config(raw)
    assert hashlib.sha256(raw).hexdigest() != VERSION_1_SHA256  # a new version, so its runs are new trials
    assert doc["version"] == 2 and [a["n"] for a in doc["amendments"]] == [1]
    am = doc["amendments"][0]
    assert str(am["date"]) == "2026-11-24" and "#623" in am["heading"] and "#623" in am["issue"]
    assert "predictive" in am["reason"] and am["supersedes_sha256"] == VERSION_1_SHA256
    assert {r["name"] for r in am["removed"]} == REMOVED_BY_AMENDMENT_1
    assert all(r["feature"].startswith("short_volume.") for r in am["removed"])
    assert "No other rule, threshold or setting changes" in am["unchanged"]
    assert "keep their recorded config_sha256" in am["unchanged"]
    assert {r.name: (r.feature, r.op, r.value) for r in cfg.rules} == KEPT_RULES
    assert all(r.hypothesis for r in cfg.rules)
    assert (cfg.seed, cfg.n_random) == (39, 1000)
    assert raw.decode().rstrip().endswith(am["unchanged"].split()[-1])  # the amendment is the file's last entry


def _script():
    spec = importlib.util.spec_from_file_location("run_ablation_623b", ROOT / "scripts" / "run_ablation.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _form4_buy(root, symbol, avail):
    acc, tdate = "0000000000-26-000623", avail.date()
    led = [{"accession": acc, "symbol": symbol, "issuer_cik": "0000000001", "form": "4", "filing_date": tdate,
            "status": "ok", "n_rows": 1, "accepted_ts": avail, "available_ts": avail, "ingested_ts": avail}]
    tx = [{"accession": acc, "row": 0, "symbol": symbol, "issuer_cik": "0000000001", "form": "4",
           "derivative": False, "transaction_date": tdate, "code": "P", "shares": 100, "price": 10.0,
           "filing_date": tdate, "accepted_ts": avail, "available_ts": avail, "ingested_ts": avail}]
    store.append_versions(root, form4.LEDGER, tdate, pa.Table.from_pylist(led, schema=form4.LEDGER_SCHEMA),
                          ["accession"], ["status"])
    store.append_versions(root, form4.SOURCE, tdate, pa.Table.from_pylist(tx, schema=form4.TX_SCHEMA),
                          form4.TX_KEY, form4.TX_VALUES)


def test_the_five_remaining_rules_run_on_the_fixture_as_version_2(tmp_path, monkeypatch):
    """The repo config as amended runs end to end (no refusal), tests exactly the five remaining rules, and logs a
    version-2 trial after the fixture's past rows, which stay byte for byte as they were."""
    _no_network(monkeypatch)
    mod = _script()
    monkeypatch.setattr(mod.trials_mod, "guard", lambda strategy: "prereg-sha")
    days = pd.bdate_range("2026-06-01", periods=70)
    rows = [{"symbol": f"S{i % 7}", "date": str(days[i % 70].date()), "entry_m": 30 + i % 100, "pnl": float(np.sin(i)),
             "risk": 10.0} for i in range(150)]
    pd.DataFrame(rows).to_csv(tmp_path / "trades.csv", index=False)
    data = tmp_path / "out"
    _form4_buy(data, "S0", datetime(2026, 5, 20, 21, 0, tzinfo=UTC))  # an insider buy known before every decision
    past = (ROOT / "tests" / "fixtures" / "trials_sample.jsonl").read_bytes()
    (data / "trials.jsonl").write_bytes(past)
    res = mod.main(["--trades", str(tmp_path / "trades.csv"), "--data", str(data)])
    assert [p["name"] for p in res["rules"]] == list(KEPT_RULES)
    assert not {p["name"] for p in res["rules"]} & REMOVED_BY_AMENDMENT_1
    assert res["config_version"] == 2 and res["config_amendments"][0].startswith("Amendment 1 (24 Nov 2026")
    cov = {p["name"]: p["coverage"] for p in res["rules"]}
    assert cov["insider_any_buying"] > 0 and cov["insider_no_net_selling"] > 0  # the rules read the alt store
    after = (data / "trials.jsonl").read_bytes()
    assert after.startswith(past)  # past trials: not erased, not renumbered, not rewritten
    new = [json.loads(x) for x in after[len(past):].decode().splitlines() if x.strip()]
    assert len(new) == 1 and new[0]["kind"] == "ablation" and new[0]["config_version"] == 2
    assert new[0]["config_sha256"] == hashlib.sha256((ROOT / "config" / "ablation.yaml").read_bytes()).hexdigest()
    assert new[0]["config_sha256"] != VERSION_1_SHA256 and " v2 " in new[0]["params"]
    old_rows, _ = mod.trials_mod.read_jsonl(ROOT / "tests" / "fixtures" / "trials_sample.jsonl")
    old_ids = [r.get("trial_id") for r in old_rows]
    assert new[0]["trial_id"] not in old_ids
    assert mod.trials_mod.count_file(data / "trials.jsonl") == mod.trials_mod.count_file(
        ROOT / "tests" / "fixtures" / "trials_sample.jsonl") + new[0]["n_trials"]


@pytest.mark.parametrize("tail, why", [
    ("version: 2\n", "needs 1 appended amendment"),
    ("version: 1\namendments:\n  - {n: 1, heading: h, date: 2026-11-24, reason: r}\n", "needs 0 appended"),
    ("version: 2\namendments:\n  - {n: 2, heading: h, date: 2026-11-24, reason: r}\n", "never renumbered"),
    ("version: 2\namendments:\n  - {n: 1, heading: h, date: 2026-11-24, reason: r, removed: [{name: ins}]}\n",
     "does not come back"),
])
def test_run_ablation_refuses_a_version_that_does_not_match_its_amendments(tmp_path, monkeypatch, tail, why):
    mod = _script()
    monkeypatch.setattr(mod.trials_mod, "guard", lambda strategy: "sha")
    (tmp_path / "t.csv").write_text("symbol,entry_ts,pnl_usd,risk_usd\nACME,2026-11-02T15:00:00Z,1,1\n")
    cfg = tmp_path / "ablation.yaml"
    cfg.write_text("seed: 1\nn_random: 1000\nrules:\n  - {name: ins, feature: insider_30d.n_buys, op: '>=', "
                   "value: 1}\n" + tail)
    with pytest.raises(SystemExit, match=why):
        mod.main(["--trades", str(tmp_path / "t.csv"), "--data", str(tmp_path), "--config", str(cfg)])
    assert not (tmp_path / "trials.jsonl").exists() and not (tmp_path / "ablation").exists()


def test_run_ablation_refuses_a_config_with_finra_rules(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("run_ablation_623", ROOT / "scripts" / "run_ablation.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod.trials_mod, "guard", lambda strategy: "sha")
    (tmp_path / "t.csv").write_text("symbol,entry_ts,pnl_usd,risk_usd\nACME,2026-11-02T15:00:00Z,1,1\n")
    cfg = tmp_path / "ablation.yaml"
    cfg.write_text("seed: 1\nn_random: 1000\nrules:\n  - {name: sv, feature: short_volume.svr, op: '<=', value: 1}\n")
    with pytest.raises(SystemExit, match="#623"):
        mod.main(["--trades", str(tmp_path / "t.csv"), "--data", str(tmp_path), "--config", str(cfg)])
    assert not (tmp_path / "trials.jsonl").exists() and not (tmp_path / "ablation").exists()
