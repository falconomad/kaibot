"""#472 (Product, 22 Nov 2026): every writer of data/trials.jsonl goes through `trials.append`; every research run is
registered, and an unregistered run is refused BEFORE it reads any data.

1. A static scan: no file in scripts/ or src/ opens a trial registry file in append or write mode, or writes it with
   `write_text`, except `evaluation/trials.py` itself. A new script that writes `{"strategy": "my_idea"}` straight to
   trials.jsonl fails here (QA's planted case on #575).
2. Every file that appends trial rows also calls an entry-point guard (`trials.guard`, `require_registered`,
   `prereg.require`) or writes only rows of a registered kind of its own (config replay, screen, proposal).
3. Behaviour: with the pre-registration refused, every research entry point exits with "no run" on an empty data
   directory, so the refusal comes before any read (a script that read first would fail on the missing data
   instead)."""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

from kaibot.engine.config import ROOT
from kaibot.evaluation import prereg

SCRIPTS = ROOT / "scripts"
SOURCES = sorted([*SCRIPTS.glob("*.py"), *(ROOT / "src" / "kaibot").rglob("*.py")])
OWNER = ROOT / "src" / "kaibot" / "evaluation" / "trials.py"
OWN_KINDS = {"run_config_replays.py": "CONFIG_REPLAY", "propose.py": "proposal", "screen_batch.py": "screen"}
GUARDS = ("guard(", "require_registered(", "prereg.require(", "prereg_mod.require(")


def _append_mode(call: ast.Call) -> bool:
    modes = [a.value for a in call.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
    modes += [k.value.value for k in call.keywords if k.arg == "mode" and isinstance(k.value, ast.Constant)]
    return any(isinstance(m, str) and ("a" in m or "w" in m) for m in modes)


REGISTRY_MARKS = ("trials.jsonl", "registry_file(", "TRIALS_ENV", "KAIBOT_TRIALS_FILE")


def _registry_names(tree: ast.AST) -> set[str]:
    """Names bound to the registry file's path (`REG = data / "trials.jsonl"`), so a writer cannot hide behind a
    variable whose name says nothing about trials (QA on #575)."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None \
                and any(m in ast.unparse(node.value) for m in REGISTRY_MARKS):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names |= {n.id for tg in targets for n in ast.walk(tg) if isinstance(n, ast.Name)}
    return names


def direct_writes(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    where = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
    reg = _registry_names(tree)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
        text = ast.unparse(node)
        used = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} & reg
        if name == "open" and _append_mode(node) and (
                "trial" in text.lower() or used or any(m in text for m in REGISTRY_MARKS)):
            out.append(f"{where}:{node.lineno}: {text[:80]}")
        if name in ("write_text", "write_bytes") and "trials.jsonl" in text:
            out.append(f"{where}:{node.lineno}: {text[:80]}")
    return out


def test_no_file_writes_the_trial_registry_except_through_trials_append():
    bad = [w for p in SOURCES if p != OWNER for w in direct_writes(p)]
    assert bad == [], "write trial rows with kaibot.evaluation.trials.append:\n" + "\n".join(bad)


def test_the_scan_catches_a_planted_direct_writer(tmp_path):
    rogue = tmp_path / "rogue.py"
    rogue.write_text('import json\nwith open("data/trials.jsonl", "a") as fh:\n'
                     '    fh.write(json.dumps({"strategy": "my_idea"}) + "\\n")\n')
    sneaky = tmp_path / "sneaky.py"
    sneaky.write_text('from pathlib import Path\ntrials_f = Path("data") / "trials.jsonl"\n'
                      'with trials_f.open("a") as fh:\n    fh.write("{}")\n')
    assert direct_writes(rogue) and direct_writes(sneaky)


def test_the_scan_catches_a_writer_behind_an_unrelated_variable_name(tmp_path):
    # QA on #575: the first scan only looked for "trial" in the open() call itself
    hidden = tmp_path / "hidden.py"
    hidden.write_text('import json\nfrom pathlib import Path\nREG = Path("data") / "trials.jsonl"\n'
                      'with open(REG, "a") as fh:\n    fh.write(json.dumps({"strategy": "my_idea"}))\n'
                      'with REG.open(mode="a") as fh:\n    fh.write("{}")\n')
    assert len(direct_writes(hidden)) == 2


def test_every_trial_writer_registers_its_run_first():
    missing = []
    for p in SOURCES:
        if p == OWNER:
            continue
        text = p.read_text()
        if not any(x in text for x in ("trials_mod.append", "trials.append(", "trials_registry.append",
                                       "append_many(")):
            continue
        if p.name in OWN_KINDS and OWN_KINDS[p.name] in text:
            continue
        if not any(g in text for g in GUARDS):
            missing.append(str(p.relative_to(ROOT)))
    # engine/daily_report.py is called by engine/research.py's multi_day path, which guards with prereg.require
    missing = [m for m in missing if m != "src/kaibot/engine/daily_report.py"]
    assert missing == [], f"trial writers without an entry-point guard: {missing}"


def _module(name: str):
    spec = importlib.util.spec_from_file_location(f"_hyg_{name}", SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPTS))
    spec.loader.exec_module(mod)
    return mod


ENTRY_POINTS = [  # script, argv (the minimum to reach the guard), whether main takes argv
    ("run_replay", ["--env", "x.env", "--data", "{d}", "--end", "2024-12-31"], False),
    ("run_spy_momentum_replay", ["--start", "2020-01-02", "--end", "2024-12-31", "--data", "{d}"], False),
    ("run_shadow_replay", ["--strategy", "orb", "--data", "{d}", "--end", "2024-12-31"], True),
    ("run_ablation", ["--trades", "{d}/none.csv", "--data", "{d}"], True),
    ("walk_forward", ["--env", "x.env", "--data", "{d}", "--end", "2024-12-31"], False),
    ("walk_forward_multiday", ["--rule", "trend_highs", "--data", "{d}", "--end", "2024-12-31"], False),
    ("spy_trend", ["--data", "{d}", "--end", "2024-12-31"], True),
    ("spy_noise_band", ["--version", "spy_noise_band", "--data", "{d}", "--end", "2024-12-31"], False),
    ("momentum_core", ["--version", "trend_momentum", "--data", "{d}", "--end", "2024-12-31", "--universe",
                       "pit"], False),
    ("oneshots", ["--rule", "pullback_uptrend", "--data", "{d}", "--end", "2024-12-31"], False),
]


@pytest.mark.parametrize("name, argv, takes_argv", ENTRY_POINTS, ids=[e[0] for e in ENTRY_POINTS])
def test_an_unregistered_run_is_refused_before_it_reads_anything(name, argv, takes_argv, tmp_path, monkeypatch):
    def refuse(rule, root=None, **kw):
        raise prereg.PreregError(f"no pre-registration for rule '{rule}' (test)")

    monkeypatch.setattr(prereg, "require", refuse)
    mod = _module(name)
    if hasattr(mod, "prereg_mod"):
        monkeypatch.setattr(mod.prereg_mod, "require", refuse)
    args = [a.replace("{d}", str(tmp_path)) for a in argv]
    monkeypatch.setattr(sys, "argv", [name, *args])
    with pytest.raises(SystemExit) as e:
        mod.main(args) if takes_argv else mod.main()
    assert "no run" in str(e.value.code) or "no pre-registration" in str(e.value.code), e.value.code
    assert not (tmp_path / "trials.jsonl").exists() and not (tmp_path / "reports").exists()


def test_the_intraday_research_path_is_refused_before_it_reads_bars(tmp_path, monkeypatch):
    from kaibot.engine import research

    def refuse(rule, root=None, **kw):
        raise prereg.PreregError(f"no pre-registration for rule '{rule}' (test)")

    monkeypatch.setattr(prereg, "require", refuse)
    monkeypatch.setattr(research, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    with pytest.raises(SystemExit, match="no run"):
        research.main(["--strategy", "orb_stocks_in_play", "--bars", str(tmp_path)])


HOLDOUT_ENTRY_POINTS = [e for e in ENTRY_POINTS if e[0] != "run_ablation"]  # run_ablation reads no bars


@pytest.mark.parametrize("name, argv, takes_argv", HOLDOUT_ENTRY_POINTS, ids=[e[0] for e in HOLDOUT_ENTRY_POINTS])
def test_an_unregistered_run_is_refused_before_the_holdout_check(name, argv, takes_argv, tmp_path, monkeypatch):
    """QA on #575: the hold-out check logs (and uses up) one read of an opening, so it must not run for a run that
    is refused. The conftest makes `holdout.check` a no-op; here it fails the test if it is reached first."""
    from kaibot.evaluation import holdout

    def refuse(rule, root=None, **kw):
        raise prereg.PreregError(f"no pre-registration for rule '{rule}' (test)")

    def checked(*a, **k):
        raise AssertionError("the hold-out was checked (and an opening's read logged) before the registration")

    monkeypatch.setattr(prereg, "require", refuse)
    monkeypatch.setattr(holdout, "check", checked)
    mod = _module(name)
    if hasattr(mod, "prereg_mod"):
        monkeypatch.setattr(mod.prereg_mod, "require", refuse)
    args = [a.replace("{d}", str(tmp_path)) for a in argv]
    monkeypatch.setattr(sys, "argv", [name, *args])
    with pytest.raises(SystemExit) as e:
        mod.main(args) if takes_argv else mod.main()
    assert "no run" in str(e.value.code) or "no pre-registration" in str(e.value.code), e.value.code
    assert not any(tmp_path.rglob("*")), sorted(tmp_path.rglob("*"))
