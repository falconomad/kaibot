"""M12 3.8 (#470): the test-writing agent proposes tests, a human accepts or rejects them; it never patches code.

No network, no key, no spend: the gateway is a fake. The sandbox tests run the real sandbox (unshare on Linux,
sandbox-exec on macOS) and are skipped where neither exists.
"""

from __future__ import annotations

import json
import os
import textwrap
from datetime import date
from pathlib import Path

import pytest

from kaibot.agents.gateway import LLMConfig, LLMResult
from kaibot.agents.room import FORBIDDEN_FIELDS, _field_names
from kaibot.agents.testwriter import core, guard, sandbox

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 11, 22)

GOOD = textwrap.dedent("""\
    from hypothesis import given
    from hypothesis import strategies as st

    from kaibot.engine.toy import add


    @given(a=st.integers(), b=st.integers())
    def test_add_commutes(a, b):
        assert add(a, b) == add(b, a)
    """)
FAILING = textwrap.dedent("""\
    from hypothesis import given
    from hypothesis import strategies as st

    from kaibot.engine.toy import add


    @given(a=st.integers(min_value=1, max_value=5))
    def test_add_zero_is_identity(a):
        assert add(a, 0) == a + 1
    """)
MUTANT_KILLER = textwrap.dedent("""\
    from kaibot.engine.toy import add


    def test_add_two_and_three():
        assert add(2, 3) == 5
    """)
TOY = "def add(a, b):\n    return a + b\n"
TOY_MUTANT = textwrap.dedent("""\
    --- src/kaibot/engine/toy.py
    +++ src/kaibot/engine/toy.py
    @@ -1,2 +1,2 @@
     def add(a, b):
    -    return a + b
    +    return a - b
    """)


def _root(tmp_path: Path) -> Path:
    """A tiny checkout: one money-critical module and its targets.toml."""
    r = tmp_path / "repo"
    (r / "src" / "kaibot" / "engine").mkdir(parents=True)
    (r / "src" / "kaibot" / "__init__.py").write_text("")
    (r / "src" / "kaibot" / "engine" / "__init__.py").write_text("")
    (r / "src" / "kaibot" / "engine" / "toy.py").write_text(TOY)
    (r / "config").mkdir()
    (r / "config" / "llm.yaml").write_text((ROOT / "config" / "llm.yaml").read_text())
    (r / "tests" / "mutation").mkdir(parents=True)
    (r / "tests" / "mutation" / "targets.toml").write_text(
        '[toy]\nsource = "src/kaibot/engine/toy.py"\ntests = ["tests/test_toy.py"]\n')
    return r


# -------------------- the static guard


def test_guard_accepts_a_plain_property_test():
    assert guard.check_code(GOOD, "test_add_commutes", "kaibot.engine.toy") == []
    assert guard.check_code(MUTANT_KILLER, "test_add_two_and_three", "kaibot.engine.toy") == []


@pytest.mark.parametrize("snippet", [
    "import os\n",
    "import subprocess\n",
    "import socket\n",
    "from pathlib import Path\n",
    "import urllib.request\n",
    "from kaibot.engine.store import Store\n",
    "from kaibot.agents.gateway import Gateway\n",
    "from kaibot.broker.alpaca import AlpacaBroker\n",
    "from kaibot.engine import config\n",
    "from . import x\n",
])
def test_guard_refuses_imports_that_reach_outside(snippet):
    code = snippet + "\n\ndef test_x_ok():\n    assert True\n"
    assert guard.check_code(code, "test_x_ok"), snippet


@pytest.mark.parametrize("body", [
    "open('/etc/passwd')",
    "eval('1')",
    "exec('x = 1')",
    "__import__('os')",
    "getattr(int, 'real')",
    "(1).__class__.__mro__",
    "globals()",
])
def test_guard_refuses_dangerous_names(body):
    code = f"def test_x_ok():\n    {body}\n"
    assert guard.check_code(code, "test_x_ok"), body


def test_guard_refuses_patching_the_code_under_test():
    code = "from kaibot.engine import toy\n\n\ndef test_x_ok():\n    toy.add = lambda a, b: 0\n    assert True\n"
    assert any("no patches" in e for e in guard.check_code(code, "test_x_ok", "kaibot.engine.toy"))
    code2 = "import kaibot.engine.toy as t\n\n\ndef test_x_ok():\n    t.CONST += 1\n"
    assert any("no patches" in e for e in guard.check_code(code2, "test_x_ok", "kaibot.engine.toy"))


def test_guard_refuses_fixtures_and_shape_errors():
    assert guard.check_code("def test_x_ok(monkeypatch):\n    pass\n", "test_x_ok")
    assert guard.check_code("def test_x_ok(tmp_path):\n    pass\n", "test_x_ok")
    two = "def test_x_ok():\n    pass\n\n\ndef test_y_ok():\n    pass\n"
    assert guard.check_code(two, "test_x_ok")
    assert guard.check_code("def test_other():\n    pass\n", "test_x_ok")
    assert guard.check_code("class TestX:\n    pass\n", "test_x_ok")
    assert guard.check_code("def test_x_ok(:\n", "test_x_ok")
    assert guard.check_code("def test_x_ok():\n    pass\n" + "#\n" * 200, "test_x_ok")
    assert guard.check_code("def Bad():\n    pass\n", "Bad")


def test_secret_shapes_are_found_without_echoing_them():
    fake = "sk-" + "ant-" + "api03-" + "x" * 30
    found = guard.secret_findings(f"key = {fake}")
    assert "anthropic_key" in found and fake not in " ".join(found)
    assert guard.secret_findings("A" + "KIA" + "ABCDEFGHIJKLMNOP")
    assert guard.secret_findings("-----BEGIN RSA " + "PRIVATE KEY-----")
    assert guard.secret_findings("plain source code with no key") == []
    assert guard.secret_findings("value abcdefghijklmnop here", ("abcdefghijklmnop",)) == ["environment_value"]
    assert guard.secret_env_values({"ANTHROPIC_API_KEY": "v1", "HOME": "/x", "DB_PASSWORD": "v2"}) == ("v1", "v2")


# -------------------- the prompt and contract


def test_contract_has_no_patch_and_no_trade_fields():
    names = _field_names(core.TOOL["input_schema"])
    assert not names & FORBIDDEN_FIELDS
    assert not names & {"patch", "diff", "fix", "path", "file"}
    assert core.TOOL["name"] == "submit_test_proposals"


def test_prompt_is_one_money_critical_module_and_no_environment(tmp_path):
    r = _root(tmp_path)
    module, user = core.build_prompt("toy", r)
    assert module == "kaibot.engine.toy" and "return a + b" in user
    assert user.startswith("<data>") and "<" not in user[len("<data>"):user.index("</data>")]
    with pytest.raises(ValueError, match="unknown target"):
        core.build_prompt("nope", r)
    with pytest.raises(ValueError, match="credential"):  # a secret value that happens to be in the text: not sent
        core.build_prompt("toy", r, env_values=("return a + b",))
    (r / "tests" / "mutation" / "targets.toml").write_text('[x]\nsource = "config/llm.yaml"\n')
    with pytest.raises(ValueError, match="not a module"):
        core.build_prompt("x", r)


def test_every_real_target_builds_a_clean_prompt():
    for name in core.targets():
        module, user = core.build_prompt(name)
        assert module.startswith("kaibot.") and guard.secret_findings(core.SYSTEM + user) == []


def test_limits_come_from_config_and_only_tighten():
    lim = core.limits(LLMConfig.load())
    assert lim.model in LLMConfig.load().prices
    assert 0 < lim.daily_usd <= core.CEILINGS["daily_usd"] and 0 < lim.monthly_usd <= core.CEILINGS["monthly_usd"]
    cfg = LLMConfig.load()
    loose = LLMConfig(cfg.daily_soft_usd, cfg.monthly_alert_usd, cfg.monthly_hard_usd, cfg.reservation_ttl_s,
                      cfg.models, cfg.prices, {**cfg.limits, "testwriter": {"max_tokens": 10**6, "timeout_s": 999,
                                                                             "daily_usd": 50, "monthly_usd": 500}})
    big = core.limits(loose)
    assert (big.daily_usd, big.monthly_usd, big.max_tokens) == (0.25, 2.0, 6000)
    with pytest.raises(ValueError):
        core.limits(LLMConfig(cfg.daily_soft_usd, cfg.monthly_alert_usd, cfg.monthly_hard_usd, cfg.reservation_ttl_s,
                              cfg.models, cfg.prices, {}))


# -------------------- propose (fake gateway)


class FakeStore:
    def __init__(self, spent=0.0):
        self.spent, self.selects = spent, []

    def select(self, table, params):
        self.selects.append((table, params))
        return [{"actual_usd": self.spent, "reserved_usd": 0}] if self.spent else []


class FakeGateway:
    def __init__(self, data, spent=0.0, answer=True):
        self.cfg, self.store, self.data, self.answer, self.calls = LLMConfig.load(), FakeStore(spent), data, answer, []

    def call(self, purpose, model, system, user, tool, max_tokens, timeout_s, daily_soft_usd=None, report=None):
        self.calls.append({"purpose": purpose, "model": model, "system": system, "user": user,
                           "daily": daily_soft_usd, "tool": tool["name"]})
        if not self.answer:
            report["reason"] = "refused"
            return None
        return LLMResult(self.data, 0.012, 900, 4000, 800, 0, model)


def _answer(*proposals):
    return {"module": "kaibot.engine.toy", "proposals": [
        {"test_name": n, "kind": k, "mutant": m, "claim": f"claim {n}", "code": c} for n, k, m, c in proposals]}


def test_propose_writes_valid_proposals_and_refuses_bad_ones(tmp_path):
    r = _root(tmp_path)
    bad = "import os\n\n\ndef test_reads_env():\n    assert os.environ\n"
    gw = FakeGateway(_answer(("test_add_commutes", "property", None, GOOD), ("test_reads_env", "property", None, bad)))
    rows = core.propose(gw, "toy", today=TODAY, root=r)
    assert [x["event"] for x in rows] == ["proposed", "refused"]
    assert gw.calls[0]["purpose"] == "testwriter" and gw.calls[0]["daily"] == core.limits(gw.cfg).daily_usd
    assert gw.store.selects[0][1]["purpose"] == "eq.testwriter"
    f = r / rows[0]["file"]
    assert f.parent == r / "tests" / "candidates" / "proposed" and f.read_text().endswith(GOOD)
    assert rows[0]["prompt_sha256"] == core.sha256(core.SYSTEM + gw.calls[0]["user"])
    assert any("import of os" in e for e in rows[1]["reasons"])
    assert not (r / "tests" / "candidates" / "proposed" / "test_2026_11_22_toy_test_reads_env.py").exists()
    ledger = core.read_ledger(r / "tests" / "candidates" / "ledger.jsonl")
    assert [x["event"] for x in ledger] == ["proposed", "refused"]
    assert (r / "src" / "kaibot" / "engine" / "toy.py").read_text() == TOY  # nothing but the candidates written


def test_propose_stops_at_its_monthly_cap_and_without_an_answer(tmp_path):
    r = _root(tmp_path)
    gw = FakeGateway(_answer(), spent=1.999)
    with pytest.raises(RuntimeError, match="monthly cap"):
        core.propose(gw, "toy", today=TODAY, root=r)
    assert gw.calls == []  # no call at all
    gw2 = FakeGateway(_answer(), answer=False)
    with pytest.raises(RuntimeError, match="refused"):
        core.propose(gw2, "toy", today=TODAY, root=r)


def test_a_broken_answer_is_logged_as_refused(tmp_path):
    r = _root(tmp_path)
    rows = core.propose(FakeGateway({"module": "kaibot.engine.toy", "proposals": [{"patch": "x"}]}), "toy",
                        today=TODAY, root=r)
    assert rows[0]["event"] == "refused" and rows[0]["reasons"][0].startswith("schema")


# -------------------- triage and the sandbox


def _sandbox():
    try:
        return sandbox.isolation()
    except sandbox.SandboxUnavailable as e:
        if os.environ.get("KAIBOT_REQUIRE_SANDBOX") == "1":  # CI: the sandbox must exist, these tests must run
            pytest.fail(f"KAIBOT_REQUIRE_SANDBOX=1 but {e}")
        pytest.skip("no network and filesystem isolation on this machine")


def test_sandbox_has_no_network_and_no_credentials(tmp_path, monkeypatch):
    mode = _sandbox()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key-for-the-test")
    code = textwrap.dedent("""\
        import os
        import _socket


        def test_isolated():
            assert "ANTHROPIC_API_KEY" not in os.environ
            assert set(os.environ) <= {"PATH", "HOME", "TMPDIR", "PYTHONHASHSEED", "PYTHONDONTWRITEBYTECODE",
                                       "LANG", "LC_CTYPE", "PWD", "SHLVL", "_", "PYTEST_CURRENT_TEST",
                                       "PYTEST_VERSION"}
            assert not os.path.exists(os.path.join(os.getcwd(), ".env"))
            assert os.environ["HOME"] == "/tmp"
            s = _socket.socket()
            s.settimeout(2)
            assert s.connect_ex(("1.1.1.1", 443)) != 0
        """)
    res = sandbox.run_test(code, module="kaibot.engine.toy", root=_root(tmp_path), mode=mode, resolve_imports=False)
    assert res.outcome == "passed", res.output


def test_triage_classifies_and_decide_records_the_human(tmp_path):
    mode = _sandbox()
    r = _root(tmp_path)
    gw = FakeGateway(_answer(("test_add_commutes", "property", None, GOOD),
                             ("test_add_zero_is_identity", "property", None, FAILING),
                             ("test_add_two_and_three", "mutant", "kaibot.engine.toy.x_add__mutmut_1", MUTANT_KILLER)))
    core.propose(gw, "toy", today=TODAY, root=r)
    surv = [{"mutant": "kaibot.engine.toy.x_add__mutmut_1", "diff": TOY_MUTANT}]
    res = {x["id"].rsplit("-", 1)[-1]: x["result"] for x in core.triage(root=r, survivors=surv, mode=mode)}
    assert res == {"test_add_commutes": "holds", "test_add_zero_is_identity": "fails_on_main",
                   "test_add_two_and_three": "kills_mutant"}
    assert core.triage(root=r, survivors=surv, mode=mode) == []  # triaged once
    assert (r / "src" / "kaibot" / "engine" / "toy.py").read_text() == TOY  # the mutant touched only the copy

    ids = {cid.rsplit("-", 1)[-1]: cid for cid in core.state(core.read_ledger(r / "tests/candidates/ledger.jsonl"))}
    with pytest.raises(ValueError, match="name of the human"):
        core.decide(ids["test_add_commutes"], "accept", " ", root=r)
    row = core.decide(ids["test_add_commutes"], "accept", "reviewer", "good invariant", root=r)
    assert (r / row["file"]).exists() and row["file"].startswith("tests/candidates/accepted/")
    with pytest.raises(ValueError, match="already accepted"):
        core.decide(ids["test_add_commutes"], "reject", "reviewer", root=r)
    with pytest.raises(ValueError, match="--bug"):
        core.decide(ids["test_add_zero_is_identity"], "accept", "reviewer", root=r)
    row = core.decide(ids["test_add_zero_is_identity"], "accept", "reviewer", bug="#999", root=r)
    text = (r / row["file"]).read_text()
    assert "@pytest.mark.xfail(strict=True" in text and "import pytest" in text
    assert guard.check_code(text, "test_add_zero_is_identity", "kaibot.engine.toy") == []
    # xfail strict: green until the bug is fixed
    assert sandbox.run_test(text, module="kaibot.engine.toy", root=r, mode=mode).outcome == "passed"
    row = core.decide(ids["test_add_two_and_three"], "reject", "reviewer", "duplicate", root=r)
    assert not list((r / "tests" / "candidates" / "proposed").glob("*.py"))

    rep = core.monthly(core.read_ledger(r / "tests/candidates/ledger.jsonl"), "2026-11", 2.0)
    assert (rep["calls"], rep["proposed"], rep["accepted"], rep["rejected"]) == (1, 3, 2, 1)
    assert rep["spend_usd"] == pytest.approx(0.012) and rep["under_cap"]
    assert not core.monthly([], "2026-11", 0.0)["calls"]


def test_triage_refuses_a_proposal_edited_after_the_guard(tmp_path):
    r = _root(tmp_path)
    rows = core.propose(FakeGateway(_answer(("test_add_commutes", "property", None, GOOD))), "toy", today=TODAY,
                        root=r)
    f = r / rows[0]["file"]
    f.write_text(f.read_text() + "\nimport os\n")
    ran = []
    out = core.triage(root=r, runner=lambda *a, **k: ran.append(1))
    assert out[0]["result"] == "refused" and ran == []


def test_monthly_report_flags_spend_over_the_cap():
    rows = [{"event": "proposed", "ts": "2026-11-02T03:00:00+00:00", "day": "2026-11-02", "target": "ledger",
             "prompt_sha256": "a", "call_cost_usd": 1.5},
            {"event": "proposed", "ts": "2026-11-09T03:00:00+00:00", "day": "2026-11-09", "target": "ledger",
             "prompt_sha256": "b", "call_cost_usd": 0.8}]
    rep = core.monthly(rows, "2026-11", 2.0)
    assert rep["spend_usd"] == pytest.approx(2.3) and not rep["under_cap"]


# -------------------- QA's escapes on #470 (each failed before the fix)

TOY_REEXPORT = "import os\nfrom pathlib import Path\n\n\ndef add(a, b):\n    return a + b\n"


def test_qa_a_reexported_path_is_refused_by_the_static_check(tmp_path):
    """QA: `from kaibot.evaluation.trials import Path` passed the guard: Kaibot imports were checked by name only.
    Now a proposal may import from the module under test and the Kaibot modules it imports, nothing else."""
    r = _root(tmp_path)
    code = "from kaibot.evaluation.trials import Path\n\n\ndef test_x_ok():\n    assert Path\n"
    errs = guard.check_code(code, "test_x_ok", "kaibot.engine.toy", r)
    assert any("kaibot.evaluation.trials" in e for e in errs)
    assert guard.check_code(code, "test_x_ok", "kaibot.engine.ledger")  # the real money modules do not import it
    assert guard.allowed_kaibot("kaibot.engine.toy", r) == ["kaibot.engine.toy"]
    assert "kaibot.engine.ledger" in guard.allowed_kaibot("kaibot.engine.ledger")
    assert guard.check_code("from kaibot.engine.toy import *\n\n\ndef test_x_ok():\n    pass\n", "test_x_ok",
                            "kaibot.engine.toy", r)


@pytest.mark.parametrize("code", [
    "from kaibot.engine.toy import Path\n\n\ndef test_x_ok():\n    Path('/tmp/escape').write_text('x')\n",
    "from kaibot.engine import toy\n\n\ndef test_x_ok():\n    toy.Path('/tmp/escape').write_text('x')\n",
    "import kaibot.engine.toy as t\n\n\ndef test_x_ok():\n    t.os.system('true')\n",
    "import kaibot.engine.toy\n\n\ndef test_x_ok():\n    kaibot.engine.toy.os.getcwd()\n",
])
def test_qa_a_reexported_name_is_resolved_and_refused_before_the_test_runs(tmp_path, code):
    """The name-level check cannot see that the module under test re-exports pathlib's Path or os: the sandbox
    resolves every imported name and attribute chain to the module that defines it, before any test code runs."""
    mode = _sandbox()
    r = _root(tmp_path)
    (r / "src" / "kaibot" / "engine" / "toy.py").write_text(TOY_REEXPORT)
    assert guard.check_code(code, "test_x_ok", "kaibot.engine.toy", r) == []  # the names alone look fine
    res = sandbox.run_test(code, module="kaibot.engine.toy", root=r, mode=mode)
    assert res.outcome == "refused", res.output
    assert "pathlib" in res.output or "'os'" in res.output


def test_qa_the_sandbox_cannot_touch_the_checkout_home_or_any_env(tmp_path):
    """QA: through sandbox.run_test a test wrote into the real checkout and read a planted .env. With the import
    check switched off (the boundary must hold on its own) nothing outside the copy exists for the test."""
    mode = _sandbox()
    r = _root(tmp_path)
    (r / ".env").write_text("SECRET=planted-by-qa\n")
    (r / "config" / ".env").write_text("SECRET=planted-by-qa\n")
    real = ROOT / "tests" / "candidates" / "escape-qa.txt"
    home = Path.home()
    code = textwrap.dedent(f"""\
        import os


        def _raises(fn):
            try:
                fn()
            except OSError:
                return True
            return False


        def _only_python(top, bound):
            # the home folder may exist only as the path to a bound Python installation (a venv under it)
            for d, dirs, files in os.walk(top):
                inside = [b for b in bound if d == b or d.startswith(b + "/")]
                if inside:
                    dirs[:] = []
                    continue
                if files or not any(b.startswith(d + "/") for b in bound):
                    return False
            return True


        def test_isolated():
            assert not os.path.exists({str(r / ".env")!r})
            assert _raises(lambda: open({str(r / ".env")!r}).read())
            assert not os.path.exists("/work/config/.env") and not os.path.exists("/work/.env")
            assert _raises(lambda: open({str(r / "escaped.txt")!r}, "w").write("x"))
            assert _raises(lambda: open({str(real)!r}, "w").write("x"))
            assert not os.path.exists({str(ROOT / "CLAUDE.md")!r})
            assert _only_python({str(home)!r}, {sandbox.python_dirs()!r})
            assert not os.path.exists("/etc/passwd")
            assert _raises(lambda: open("/work/written.txt", "w").write("x"))
            open("/tmp/scratch.txt", "w").write("ok")
        """)
    res = sandbox.run_test(code, module="kaibot.engine.toy", root=r, mode=mode, resolve_imports=False)
    assert res.outcome == "passed", res.output
    assert not (r / "escaped.txt").exists() and not real.exists()
    assert (r / ".env").read_text() == "SECRET=planted-by-qa\n"


def test_qa_a_chroot_climb_or_a_remount_does_not_leave_the_sandbox(tmp_path):
    """QA (second round on #470): with a plain chroot the test was root of its user namespace with every capability:
    os.chroot into a subfolder, fchdir back and chdir("..") reached the host, read a planted .env and wrote the
    checkout; `mount -o remount,rw` could undo the read-only binds. The guard can be walked round (operator.attrgetter,
    typing.get_type_hints), so the boundary itself must hold with the import check off."""
    mode = _sandbox()
    r = _root(tmp_path)
    (r / ".env").write_text("SECRET=planted-by-qa\n")
    code = textwrap.dedent(f"""\
        import ctypes
        import os


        def _climb():
            os.makedirs("/tmp/jail", exist_ok=True)
            fd = os.open("/", os.O_RDONLY)
            os.chroot("/tmp/jail")
            os.fchdir(fd)
            for _ in range(64):
                os.chdir("..")
            os.chroot(".")


        def _remounts():  # mount(8) needs /proc: the syscall, MS_REMOUNT | MS_BIND without MS_RDONLY
            libc = ctypes.CDLL(None, use_errno=True)
            return [t for t in (b"/usr", b"/work") if libc.mount(None, t, None, 32 | 4096, None) == 0]


        def test_isolated():
            assert ctypes.CDLL(None, use_errno=True).chroot(b"/tmp") != 0  # no capability left
            assert _remounts() == []
            try:
                _climb()
            except OSError:
                pass
            assert not os.path.exists({str(r / ".env")!r})
            assert not os.path.exists({str(ROOT / "CLAUDE.md")!r})
            assert not os.path.exists("/.old")
            # a nested user namespace gets capabilities back, but only over this tree, whose binds are locked
            os.unshare(os.CLONE_NEWUSER | os.CLONE_NEWNS)
            assert _remounts() == []
            _climb()
            assert not os.path.exists({str(r / ".env")!r})
            assert not os.path.exists({str(ROOT / "CLAUDE.md")!r})
        """)
    res = sandbox.run_test(code, module="kaibot.engine.toy", root=r, mode=mode, resolve_imports=False)
    assert res.outcome == "passed", res.output
    assert (r / ".env").read_text() == "SECRET=planted-by-qa\n" and not (r / "jail").exists()


def test_qa_the_ledger_keeps_only_the_verdict_and_a_hash(tmp_path):
    """QA: raw sandbox output went into the committed ledger. Now the ledger keeps the result and the output's
    sha256; the output, scrubbed, goes to tests/candidates/runs/ (ignored by git)."""
    r = _root(tmp_path)
    rows = core.propose(FakeGateway(_answer(("test_add_commutes", "property", None, GOOD))), "toy", today=TODAY,
                        root=r)
    fake_key = "sk-" + "ant-" + "api03-" + "y" * 30
    raw = f"FAILED {r}/src/kaibot/engine/toy.py: {fake_key} secretvalue123 at {Path.home()}/x"

    def runner(code, **k):
        return sandbox.RunResult("failed", 1, raw)

    out = core.triage(root=r, runner=runner, env_values=("secretvalue123",))
    ledger = core.read_ledger(r / "tests" / "candidates" / "ledger.jsonl")
    tri = [x for x in ledger if x["event"] == "triaged"]
    assert out and tri and "output" not in tri[0] and tri[0]["output_sha256"] == core.sha256(raw)
    text = (r / "tests" / "candidates" / "ledger.jsonl").read_text()
    assert fake_key not in text and "secretvalue123" not in text and str(r) not in text
    log = (r / "tests" / "candidates" / "runs" / f"{rows[0]['id']}.log").read_text()
    assert fake_key not in log and "secretvalue123" not in log and str(r) not in log
    assert "[redacted]" in log and "<checkout>" in log
    assert "tests/candidates/runs/" in (ROOT / ".gitignore").read_text()


def test_qa_the_mac_profile_denies_writes_and_reads_outside_the_copy():
    prof = sandbox.mac_profile("/private/tmp/w", "/private/tmp/s", "/Users/me", "/Users/me/kaibot", ["/opt/py"])
    lines = prof.splitlines()
    assert "(deny network*)" in lines and "(deny file-write*)" in lines
    first_allow = next(i for i, x in enumerate(lines) if x.startswith("(allow file-write*"))
    assert lines.index("(deny file-write*)") < first_allow
    deny_read = next(x for x in lines if x.startswith("(deny file-read*"))
    assert '"/Users/me"' in deny_read and '"/Users/me/kaibot"' in deny_read and ".env" in deny_read
    allow_read = lines[-1]
    assert allow_read.startswith("(allow file-read*") and '"/Users/me"' not in allow_read
    assert '"/private/tmp/w"' in allow_read and '"/opt/py"' in allow_read


# -------------------- the repository


def test_accepted_candidates_still_pass_the_guard():
    acc = ROOT / "tests" / "candidates" / "accepted"
    rows = core.state(core.read_ledger())
    for f in acc.glob("test_*.py"):
        c = next((c for c in rows.values() if c.get("file", "").endswith(f.name)), None)
        assert c is not None, f"{f.name} is not in tests/candidates/ledger.jsonl"
        assert guard.check_code(f.read_text(), c["test_name"], c.get("module")) == [], f.name


def test_ledger_rows_are_known_events():
    for row in core.read_ledger():
        assert row["event"] in core.EVENTS and row.get("id")


def test_the_engine_never_imports_the_test_writer():
    for p in (ROOT / "src" / "kaibot").rglob("*.py"):
        if "testwriter" in p.parts:
            continue
        assert "testwriter" not in p.read_text(), p
    assert json.loads(json.dumps(core.CEILINGS)) == core.CEILINGS
