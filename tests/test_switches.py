"""M10 ticket 1.23 (#448): every KAIBOT_* switch declared once (kaibot.common.switches) and the engine's switches
parsed, validated and loaded once, at start, into one typed object that every reader goes through.

- A KAIBOT_* name in src/ or scripts/ that is not declared fails here, and so does a Python read of a KAIBOT_*
  variable from the environment outside switches.py (an AST scan and a plain grep of src/ and scripts/) that is
  not on that switch's `readers` list (helper_session.py and healthcheck.py, which import nothing of the package).
- After a start, every reader answers from the object loaded then, not from a later os.environ.
- Every valid value means what it meant before: each reader is compared, value by value, with the code it replaced
  (copied below as OLD), and the readers that still read the variable themselves agree with `switches.value`.
- An invalid value stops the start (Settings.from_env), naming every bad variable; the defaults are today's.
- No switch is past its review date.
"""

from __future__ import annotations

import ast
import os
import re
from datetime import date
from pathlib import Path

import pytest

from kaibot.common import switches as sw
from kaibot.engine.config import ROOT

ENGINE = {n: s for n, s in sw.SWITCHES.items() if s.scope == "engine"}
NAME_RE = re.compile(r"\bKAIBOT_[A-Z0-9_]*[A-Z0-9]\b")


def _files():
    yield from (ROOT / "src").rglob("*.py")
    yield from (ROOT / "scripts").rglob("*.py")
    yield from (ROOT / "scripts").rglob("*.sh")


# ---------------------------------------------------------------- declared once
def test_every_kaibot_name_in_the_code_is_declared():
    seen = {}
    for p in _files():
        for n in NAME_RE.findall(p.read_text()):
            seen.setdefault(n, str(p.relative_to(ROOT)))
    missing = {n: f for n, f in seen.items() if n not in sw.SWITCHES}
    assert not missing, f"declare in src/kaibot/common/switches.py: {missing}"


def test_every_declared_switch_is_still_used():
    text = "".join(p.read_text() for p in _files() if p.name != "switches.py")
    assert [n for n in sw.SWITCHES if n not in text] == []


def test_every_switch_has_an_owner_a_reason_and_a_review_date_not_past():
    teams = {"backend-quant", "data", "agents-ai", "qa-ops", "frontend", "quant", "design", "product"}
    for s in sw.SWITCHES.values():
        assert s.owner in teams and s.what and s.scope in ("engine", "script"), s.name
        assert s.review >= date.today(), f"{s.name}: past its review date {s.review}: declare it again or remove it"


def _reads(path: Path) -> list[tuple[str, int]]:
    """(switch, line) of every environment read of a KAIBOT_* variable in a Python file: any `.get(<name>)` or
    `os.getenv(<name>)`, `os.environ[...]` / `env[...]`, and `<name> in os.environ`, with the name as a literal or
    a module constant. `switches.get/value/text` are the registry's own."""
    tree = ast.parse(path.read_text())
    consts = {t.id: n.value.value for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
              and isinstance(n.value.value, str) for t in n.targets if isinstance(t, ast.Name)}

    def name_of(a):
        v = a.value if isinstance(a, ast.Constant) else consts.get(a.id) if isinstance(a, ast.Name) else None
        return v if isinstance(v, str) and v.startswith("KAIBOT_") else None

    def is_env(x):
        return (isinstance(x, ast.Attribute) and x.attr == "environ") or (isinstance(x, ast.Name)
                                                                         and x.id in ("env", "environ"))
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and n.args:
            f = n.func
            hit = (isinstance(f, ast.Attribute) and f.attr in ("get", "getenv", "pop")  # any mapping's .get
                   and not (isinstance(f.value, ast.Name) and f.value.id == "switches"))  # but the registry's own
            if hit and name_of(n.args[0]):
                out.append((name_of(n.args[0]), n.lineno))
        if isinstance(n, ast.Subscript) and is_env(n.value) and name_of(n.slice) and isinstance(n.ctx, ast.Load):
            out.append((name_of(n.slice), n.lineno))
        if (isinstance(n, ast.Compare) and len(n.ops) == 1 and isinstance(n.ops[0], (ast.In, ast.NotIn))
                and is_env(n.comparators[0]) and name_of(n.left)):
            out.append((name_of(n.left), n.lineno))
    return out


# Files whose `.get("KAIBOT_...")` is not an environment read (the scan cannot tell a dict from os.environ).
NOT_ENV = {"scripts/go_live_paper.py": "reads the --check report's JSON (its multi_day.KAIBOT_MULTIDAY field)"}
SELF = "src/kaibot/common/switches.py"


def _py_files():
    yield from (ROOT / "src").rglob("*.py")
    yield from (ROOT / "scripts").rglob("*.py")


def _allowed(rel: str, name: str) -> bool:
    return rel == SELF or rel in NOT_ENV or (name in sw.SWITCHES and rel in sw.SWITCHES[name].readers)


def test_no_kaibot_variable_is_read_from_the_environment_outside_the_registry():
    """The AST scan: `os.environ.get(X)`, `os.getenv(X)`, `env[X]`, `X in os.environ`, X a literal or a constant."""
    bad = []
    for p in _py_files():
        rel = str(p.relative_to(ROOT))
        for name, line in _reads(p):
            if not _allowed(rel, name):
                bad.append(f"{rel}:{line}: {name}")
    assert bad == [], "read it with kaibot.common.switches.get/text(...): " + "; ".join(bad)


ENV_READ = re.compile(r"os\.environ\.get\(|os\.getenv\(|os\.environ\[|\bin os\.environ\b|environ\.get\(")
WRITE = re.compile(r"os\.environ\.setdefault\(|os\.environ\[[^]]+\]\s*=[^=]")


def test_grep_no_stray_os_environ_read_of_a_kaibot_variable():
    """The plain grep: a line that reads os.environ and names a KAIBOT_* variable. Writes (`setdefault`, an
    assignment) are not reads: scripts/run_ticker_checks.py sets KAIBOT_ENV_FILE before Settings.from_env."""
    bad = []
    for p in _py_files():
        rel = str(p.relative_to(ROOT))
        for i, line in enumerate(p.read_text().splitlines(), 1):
            line = line.split("  # ")[0]  # not a trailing comment
            if "KAIBOT_" not in line or not ENV_READ.search(line) or WRITE.search(line):
                continue
            if not all(_allowed(rel, n) for n in NAME_RE.findall(line)):
                bad.append(f"{rel}:{i}: {line.strip()}")
    assert bad == [], "read it with kaibot.common.switches.get/text(...): " + "; ".join(bad)


def test_the_scans_catch_a_planted_stray_read(tmp_path):
    f = tmp_path / "planted.py"
    f.write_text('import os\nFLAG = "KAIBOT_MULTIDAY"\n\n\ndef on():\n    return os.environ.get(FLAG) == "on" '
                 'or os.getenv("KAIBOT_ROOM") or "KAIBOT_NEWS_POLL" in os.environ or os.environ["KAIBOT_DATA_DIR"]\n')
    assert sorted(n for n, _ in _reads(f)) == ["KAIBOT_DATA_DIR", "KAIBOT_MULTIDAY", "KAIBOT_NEWS_POLL",
                                                "KAIBOT_ROOM"]
    read = 'x = os.environ.get("KAIBOT_ROOM")'
    assert ENV_READ.search(read) and not WRITE.search(read)
    assert WRITE.search('os.environ.setdefault("KAIBOT_ENV_FILE", a.env)')


def test_the_known_readers_still_read_it_themselves():
    """The files on a `readers` list (they import nothing of the package) still read the variable themselves."""
    for name, s in sw.SWITCHES.items():
        for f in s.readers:
            assert name in {n for n, _ in _reads(ROOT / f)}, (name, f)
    assert {f for s in sw.SWITCHES.values() for f in s.readers} == {"src/kaibot/common/helper_session.py",
                                                                      "src/kaibot/common/secret_files.py",
                                                                      "scripts/healthcheck.py",
                                                                      "scripts/research_runner.py"}


# ---------------------------------------------------------------- the same meaning as before (OLD = the code replaced)
def _vals(*extra):
    return [None, "", " ", *extra]


FLAGS = _vals(*sw.ON, *sw.OFF, "ON", " Yes ", "Off")
OLD = {
    # agents/gateway.py, the room itself: no strip, so " on" was OFF (agents/preopen.py stripped; see PREOPEN_ROOM)
    "KAIBOT_ROOM": lambda v: (v or "").lower() in sw.ON,
    "KAIBOT_PREOPEN_REVIEW": lambda v: (v or "").strip().lower() in sw.ON,
    "KAIBOT_DAILY_NOTE": lambda v: (v or "").strip().lower() in sw.ON,
    "KAIBOT_TESTWRITER": lambda v: (v or "").strip().lower() in sw.ON,
    "KAIBOT_DATA_CHECKS": lambda v: (v or "").strip().lower() in sw.ON,
    "KAIBOT_NEWS_POLL": lambda v: (v or "on").strip().lower() not in sw.OFF,
    "KAIBOT_MULTIDAY": lambda v: (v if v is not None else "off").strip().lower() == "on",
    "KAIBOT_MARKET_ALARM": lambda v: str(v if v is not None else "off").strip().lower() == "on",
    "KAIBOT_PREOPEN": lambda v: "shadow" if (v or "").strip().lower() == "shadow" else "off",
    "KAIBOT_FILING_FLAG": lambda v: "shadow" if str(v if v is not None else "off").strip().lower() == "shadow"
    else "off",
}
# QA-Ops on #588: agents/preopen.py's old reading of KAIBOT_ROOM (stripped). The new one is never looser than it.
PREOPEN_ROOM = lambda v: (v or "").strip().lower() in sw.ON  # noqa: E731
PADDED = (" on", "on ", " 1", "\ton", " Yes ", "on\n")
VALUES = {n: FLAGS for n in OLD} | {"KAIBOT_ROOM": _vals(*sw.ON, *sw.OFF, "ON", "Yes", "Off"),
                                    "KAIBOT_MULTIDAY": _vals("on", "off", "ON", " on "),
                                    "KAIBOT_MARKET_ALARM": _vals("on", "off", "On"),
                                    "KAIBOT_PREOPEN": _vals("off", "shadow", "Shadow"),
                                    "KAIBOT_FILING_FLAG": _vals("off", "shadow", " SHADOW")}


INVALID = ("maybe", "2", "shadoww", "on ", " 1", "of")


@pytest.mark.parametrize("name", sorted(OLD))
def test_every_valid_value_means_what_it_meant(name):
    for v in VALUES[name]:
        env = {} if v is None else {name: v}
        assert sw.value(name, env) == OLD[name](v), (name, v)


@pytest.mark.parametrize("name", sorted(OLD))
def test_the_readers_lenient_get_means_what_the_old_code_meant_for_any_value(name):
    """Only the start refuses a bad value; a reader called with its own mapping answers as before."""
    for v in (*VALUES[name], *INVALID):
        env = {} if v is None else {name: v}
        if name == "KAIBOT_NEWS_POLL" and v in ("of",):
            assert sw.get(name, env) is True and OLD[name](v) is True
        assert sw.get(name, env) == OLD[name](v), (name, v)


@pytest.mark.parametrize(("name", "bad"), [("KAIBOT_ROOM", "maybe"), ("KAIBOT_NEWS_POLL", "of"),
                                           ("KAIBOT_MULTIDAY", "1"), ("KAIBOT_MULTIDAY", "true"),
                                           ("KAIBOT_MARKET_ALARM", "yes"), ("KAIBOT_PREOPEN", "on"),
                                           ("KAIBOT_FILING_FLAG", "shadoww"), ("KAIBOT_DATA_DIR", ""),
                                           ("KAIBOT_ENGINE_NAME", " ")])
def test_a_value_the_switch_does_not_have_is_refused(name, bad):
    with pytest.raises(sw.SwitchError, match=name):
        sw.value(name, {name: bad})


@pytest.mark.parametrize("bad", PADDED)
def test_a_padded_room_value_is_refused_and_never_turns_the_room_on(bad):
    """KAIBOT_ROOM is the stakeholder's switch (CLAUDE.md). The gateway read `" on"` as off; it must not become on."""
    from kaibot.agents import gateway, preopen

    with pytest.raises(sw.SwitchError, match="KAIBOT_ROOM"):
        sw.value("KAIBOT_ROOM", {"KAIBOT_ROOM": bad})
    with pytest.raises(sw.SwitchError, match="KAIBOT_ROOM"):
        sw.load({"KAIBOT_ROOM": bad})
    env = {"KAIBOT_ROOM": bad, "KAIBOT_PREOPEN_REVIEW": "on", "ANTHROPIC_API_KEY": "k"}
    assert sw.get("KAIBOT_ROOM", env) is False
    assert gateway.from_env(None, env).client is None
    assert preopen.enabled(env) is False


def test_the_defaults_are_todays():
    got = sw.load({})
    assert (got.multi_day, got.room, got.preopen, got.preopen_review, got.daily_note, got.filing_flag,
            got.market_alarm, got.news_poll, got.data_checks, got.reenable, got.test_writer) == (
        False, False, "off", False, False, "off", False, True, False, None, False)
    assert got.engine_name == "engine" and got.data_dir is None and got.daily_data is None and got.code_sha is None
    assert got.preopen_masked is True and got.headline_feed is True and got.headline_edgar is False
    assert got.data_checks_block == () and got.sec_user_agent is None and got.env_file is None


def test_paths_and_texts_keep_the_value_as_written():
    got = sw.load({"KAIBOT_DATA_DIR": "/x/live", "KAIBOT_TRIALS_FILE": "/t.jsonl", "KAIBOT_REENABLE": "trend_momentum",
                   "KAIBOT_ENGINE_NAME": "engine-vm", "KAIBOT_HOLDOUT_OPENING": "open-1"})
    assert got.data_dir == Path("/x/live") and got.trials_file == Path("/t.jsonl")
    assert (got.reenable, got.engine_name, got.holdout_opening) == ("trend_momentum", "engine-vm", "open-1")


def test_load_names_every_bad_variable_at_once():
    with pytest.raises(sw.SwitchError) as e:
        sw.load({"KAIBOT_ROOM": "maybe", "KAIBOT_MULTIDAY": "1", "KAIBOT_PREOPEN": "x"})
    for n in ("KAIBOT_ROOM", "KAIBOT_MULTIDAY", "KAIBOT_PREOPEN"):
        assert n in str(e.value)


def test_the_engine_start_refuses_an_invalid_switch(monkeypatch):
    from kaibot.engine.config import Settings

    for k, v in (("ALPACA_API_KEY", "k"), ("ALPACA_SECRET", "s"), ("SUPABASE_SERVICE_KEY", "x"),
                 ("FRED_API_KEY", "f"), ("KAIBOT_ENV_FILE", "/nonexistent/.env")):
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("KAIBOT_DATA_DIR", raising=False)
    monkeypatch.delenv("KAIBOT_ENGINE_NAME", raising=False)
    assert Settings.from_env().engine_name == "engine"
    monkeypatch.setenv("KAIBOT_ROOM", "maybe")
    with pytest.raises(SystemExit, match="KAIBOT_ROOM"):
        Settings.from_env()
    monkeypatch.setenv("KAIBOT_ROOM", " on")
    with pytest.raises(SystemExit, match="KAIBOT_ROOM"):
        Settings.from_env()
    monkeypatch.setenv("KAIBOT_ROOM", "on")
    monkeypatch.setenv("KAIBOT_DATA_DIR", "/tmp/kb-live")
    s = Settings.from_env()
    assert s.data_dir == Path("/tmp/kb-live")


# ---------------------------------------------------------------- the readers
def test_the_readers_moved_from_538s_files_answer_as_before(monkeypatch):
    """registry (KAIBOT_MULTIDAY), market (KAIBOT_DATA_CHECKS), core (KAIBOT_CODE_SHA) and live_daily
    (KAIBOT_REENABLE) read the variable themselves before this change; their old code is OLD, or inline here."""
    from kaibot.engine import core, market
    from kaibot.strategies import registry

    for v in [*VALUES["KAIBOT_MULTIDAY"], *INVALID, "1", "true"]:
        if v is None:
            monkeypatch.delenv("KAIBOT_MULTIDAY", raising=False)
        else:
            monkeypatch.setenv("KAIBOT_MULTIDAY", v)
        assert registry.multi_day_enabled() == OLD["KAIBOT_MULTIDAY"](v), v
    for v in [*FLAGS, *INVALID]:
        env = {} if v is None else {"KAIBOT_DATA_CHECKS": v}
        assert market.data_checks_on(env) == OLD["KAIBOT_DATA_CHECKS"](v), v
    monkeypatch.setenv("KAIBOT_CODE_SHA", "abc123")
    assert core.code_sha() == sw.value("KAIBOT_CODE_SHA") == "abc123"
    for v in (None, "", "~/x/helper.env"):  # common/helper_session.py: `os.environ.get(...) or DEFAULT`
        env = {} if v is None else {"KAIBOT_HELPER_ENV_FILE": v}
        assert (env.get("KAIBOT_HELPER_ENV_FILE") or None) == (str(sw.get("KAIBOT_HELPER_ENV_FILE", env) or "")
                                                               or None)
    for v in (None, "", " ", "trend_momentum", " trend_momentum "):  # live_daily: `(reenable or "").strip()`
        env = {} if v is None else {"KAIBOT_REENABLE": v}
        assert (env.get("KAIBOT_REENABLE") or "").strip() == (sw.get("KAIBOT_REENABLE", env) or "").strip()


def test_text_is_the_scripts_or_default_idiom():
    for v, want in ((None, None), ("", None), (" ", None), ("/x/.env", "/x/.env"), ("~/k/.env", "~/k/.env")):
        env = {} if v is None else {"KAIBOT_ENV_FILE": v}
        assert sw.text("KAIBOT_ENV_FILE", env) == want, v
        assert (sw.text("KAIBOT_ENV_FILE", env) or "D") == (env.get("KAIBOT_ENV_FILE", "").strip() and v or "D")
    assert sw.text("KAIBOT_CODE_SHA", {"KAIBOT_CODE_SHA": "abc"}) == "abc"
    assert sw.text("KAIBOT_DATA_DIR", {"KAIBOT_DATA_DIR": ""}) is None  # refused at a start; a script's default here


# ---------------------------------------------------------------- loaded once, at start
def _engine_env(monkeypatch, **extra):
    for k, v in (("ALPACA_API_KEY", "k"), ("ALPACA_SECRET", "s"), ("SUPABASE_SERVICE_KEY", "x"),
                 ("FRED_API_KEY", "f"), ("KAIBOT_ENV_FILE", "/nonexistent/.env"), *extra.items()):
        monkeypatch.setenv(k, v)


def test_after_the_start_every_reader_answers_from_the_object_loaded_then(monkeypatch):
    from kaibot.agents import daily_note, gateway, preopen
    from kaibot.engine import core, market
    from kaibot.engine.config import Settings
    from kaibot.strategies import registry

    _engine_env(monkeypatch, KAIBOT_MULTIDAY="on", KAIBOT_DATA_CHECKS="1", KAIBOT_CODE_SHA="sha-at-start",
                KAIBOT_ROOM="on", KAIBOT_PREOPEN_REVIEW="on", KAIBOT_DAILY_NOTE="on")
    assert sw.current() == sw.load()  # before a start: the environment as it is now
    Settings.from_env()
    loaded = sw.current()
    assert loaded.multi_day and loaded.data_checks and loaded.code_sha == "sha-at-start"
    for k in ("KAIBOT_MULTIDAY", "KAIBOT_DATA_CHECKS", "KAIBOT_ROOM", "KAIBOT_PREOPEN_REVIEW", "KAIBOT_DAILY_NOTE"):
        monkeypatch.setenv(k, "off")  # a later change of the environment is not seen: loaded once
    monkeypatch.setenv("KAIBOT_CODE_SHA", "changed")
    assert sw.current() is loaded
    assert registry.multi_day_enabled() and market.data_checks_on() and core.code_sha() == "sha-at-start"
    assert preopen.enabled() and daily_note.enabled() and preopen.enabled(os.environ)
    assert gateway.from_env(None, {**os.environ, "ANTHROPIC_API_KEY": "k"}).client is None  # its own mapping
    assert preopen.enabled({"KAIBOT_ROOM": "off"}) is False  # a mapping of its own is parsed
    sw.reset()
    assert not registry.multi_day_enabled()  # nothing loaded: the environment again


def test_a_refused_start_keeps_nothing(monkeypatch):
    from kaibot.engine.config import Settings

    _engine_env(monkeypatch, KAIBOT_MULTIDAY="1")
    with pytest.raises(SystemExit, match="KAIBOT_MULTIDAY"):
        Settings.from_env()
    assert sw._LOADED is None


def test_the_watchdogs_lenient_start_loads_defaults_for_the_refused_switches(monkeypatch):
    from kaibot.engine.config import Settings

    _engine_env(monkeypatch, KAIBOT_ROOM="maybe", KAIBOT_NEWS_POLL="off")
    Settings.from_env(strict_switches=False)
    assert sw.get("KAIBOT_ROOM") is False and sw.get("KAIBOT_NEWS_POLL") is False
    assert set(sw.refused()) == {"KAIBOT_ROOM"}
    with pytest.raises(sw.SwitchError, match="KAIBOT_ROOM"):
        sw.value("KAIBOT_ROOM")


def test_an_undeclared_kaibot_variable_is_named_in_a_warning_at_start(monkeypatch, caplog):
    import logging

    from kaibot.engine.config import Settings

    _engine_env(monkeypatch, KAIBOT_MULTI_DAY="on")  # a misspelt KAIBOT_MULTIDAY
    with caplog.at_level(logging.WARNING, logger="kaibot.engine"):
        Settings.from_env()
    assert "KAIBOT_MULTI_DAY" in caplog.text and "on" not in caplog.text.split("KAIBOT_MULTI_DAY")[1][:3]
    assert sw.undeclared({"KAIBOT_MULTIDAY": "on", "KAIBOT_TEST_PG": "x", "KAIBOT_NOPE": "1"}) == ["KAIBOT_NOPE"]


def test_the_readers_moved_to_the_registry_answer_as_before():
    from kaibot.agents import daily_note, gateway, preopen, preopen_shadow
    from kaibot.engine import __main__ as engine_main
    from kaibot.features import filing_flag
    from kaibot.ingest import news_poll

    for v in (*FLAGS, *INVALID):
        e = {} if v is None else {"KAIBOT_DAILY_NOTE": v, "KAIBOT_ROOM": v, "KAIBOT_PREOPEN_REVIEW": v,
                                  "KAIBOT_NEWS_POLL": v}
        assert daily_note.enabled(e) == OLD["KAIBOT_DAILY_NOTE"](v)
        assert preopen.enabled(e) == (OLD["KAIBOT_ROOM"](v) and OLD["KAIBOT_PREOPEN_REVIEW"](v))
        assert preopen.enabled(e) <= (PREOPEN_ROOM(v) and OLD["KAIBOT_PREOPEN_REVIEW"](v))  # never looser
        assert news_poll.enabled(e) == OLD["KAIBOT_NEWS_POLL"](v)
        g = gateway.from_env(None, {**e, "ANTHROPIC_API_KEY": "k"})
        assert (g.client is not None) == OLD["KAIBOT_ROOM"](v)
    for v in (*VALUES["KAIBOT_PREOPEN"], *INVALID):
        e = {} if v is None else {"KAIBOT_PREOPEN": v}
        assert preopen_shadow.enabled(e) == (OLD["KAIBOT_PREOPEN"](v) == "shadow")
    for v in (*VALUES["KAIBOT_FILING_FLAG"], *INVALID):
        e = {} if v is None else {"KAIBOT_FILING_FLAG": v}
        on = OLD["KAIBOT_FILING_FLAG"](v) == "shadow"
        assert (filing_flag.from_env(e) is not None) == on
        assert (engine_main.filing_flags_from_env(e) is not None) == on
    for v in (*VALUES["KAIBOT_MARKET_ALARM"], *INVALID):
        e = {} if v is None else {"KAIBOT_MARKET_ALARM": v}
        if not OLD["KAIBOT_MARKET_ALARM"](v):
            assert engine_main.market_alarm_from_env(e) is None


def test_the_watchdog_starts_on_a_bad_behaviour_switch_but_not_on_a_bad_data_dir(monkeypatch, caplog):
    """QA-Ops on #588: a typo in a switch the watchdog never reads stops the engine, not the watchdog that protects
    the open positions meanwhile. Its own two (KAIBOT_DATA_DIR, KAIBOT_ENGINE_NAME) stay strict."""
    import logging

    from kaibot.engine.config import Settings

    for k, v in (("ALPACA_API_KEY", "qa-key-1"), ("ALPACA_SECRET", "qa-key-2"), ("SUPABASE_SERVICE_KEY", "qa-key-3"),
                 ("FRED_API_KEY", "qa-key-4"), ("KAIBOT_ENV_FILE", "/nonexistent/.env"), ("KAIBOT_ROOM", " on"),
                 ("KAIBOT_DATA_DIR", "/tmp/kb-live"), ("KAIBOT_ENGINE_NAME", "engine-vm")):
        monkeypatch.setenv(k, v)
    with pytest.raises(SystemExit, match="KAIBOT_ROOM") as e:
        Settings.from_env()
    assert "qa-key" not in str(e.value)  # no secret in the refusal
    with caplog.at_level(logging.ERROR, logger="kaibot.engine"):
        s = Settings.from_env(strict_switches=False)
    assert (s.data_dir, s.engine_name) == (Path("/tmp/kb-live"), "engine-vm") and "KAIBOT_ROOM" in caplog.text
    assert "qa-key" not in caplog.text
    monkeypatch.setenv("KAIBOT_ENGINE_NAME", "")
    with pytest.raises(SystemExit, match="KAIBOT_ENGINE_NAME"):
        Settings.from_env(strict_switches=False)
    src = (ROOT / "src" / "kaibot" / "engine" / "watchdog.py").read_text()
    assert "Settings.from_env(strict_switches=False)" in src
