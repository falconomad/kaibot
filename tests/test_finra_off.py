"""#623: the FINRA Reg SHO collector is switched off (stakeholder decision, 24 Nov 2026; finra.org terms forbid a
database of FINRA website data and its use with predictive analytics). It refuses to run, the store refuses the
source, and no live or research feature list references it. The two pre-registered ablation rules that still name
FINRA features wait for Product's amendment; until then run_ablation refuses the repo config."""

from __future__ import annotations

import importlib.util
import re
import urllib.request
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import yaml

from kaibot.evaluation.ablation import parse_config
from kaibot.features import alt as alt_features_mod
from kaibot.ingest.alt import finra, store

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 24, 23, 45, tzinfo=UTC)
FINRA_REFS = re.compile(r"finra_regsho|ingest\.alt\.finra|ingest\.alt import[^\n]*\bfinra\b|short_volume|svr_rel|"
                        r"cdn\.finra\.org|CNMSshvol")
# The only places that may still name the source: the refusing stub, the store's refusal list, and the CLI and
# ablation runner that turn a request for it into a refusal.
ALLOWED = {"src/kaibot/ingest/alt/finra.py", "src/kaibot/ingest/alt/store.py", "scripts/collect_alt.py",
           "scripts/run_ablation.py", "src/kaibot/features/alt.py"}  # features/alt.py: a "(removed)" docstring line
# Frozen pre-registration (config/ablation.yaml, sha256 in every trial): not edited by the data team. Product
# decides and amends it (#623); this list must only shrink.
PENDING_AMENDMENT = {"sv_rel_no_spike", "sv_level_not_extreme"}


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
        if rel in ALLOWED or rel == "config/ablation.yaml":  # ablation.yaml: checked below, frozen
            continue
        if FINRA_REFS.search(f.read_text(errors="replace")):
            offenders.append(rel)
    assert offenders == []


def test_frozen_ablation_rules_on_finra_are_only_the_known_pending_ones():
    cfg = parse_config((ROOT / "config" / "ablation.yaml").read_bytes())
    finra_rules = {r.name for r in cfg.rules if r.feature.split(".")[0] == "short_volume"}
    assert finra_rules <= PENDING_AMENDMENT  # no new rule may use FINRA; this shrinks to empty after the amendment


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
