"""Every KAIBOT_* environment switch, declared once (M10 ticket 1.23, issue #448).

Settings stay env-only (decision 11). What changes is that each variable is declared here with its type, its default
(the same as before this ticket), the team that owns it and a review date, and that the engine's switches are parsed
and validated in ONE place at start (`load`, called by engine/config.Settings.from_env): a value that is not one of
the switch's values stops the start with every bad variable named. Before, an unknown value silently meant the
default (`KAIBOT_ROOM=maybe` was off, `KAIBOT_NEWS_POLL=maybe` was on); now it is refused (fail closed). Valid values
mean exactly what they meant.

- `scope="engine"`: read by the Python package (the engine, the watchdog, the agents, the research helpers).
  Parsed by `value(name, env)` (strict) and validated together by `load` at start. Readers call `get` (the same
  parser; a value the switch does not have means its default there, as before, since the start has refused it
  already). A few readers in files the M10 book-loop rewrite (#538) is moving, and the helpers' path (which
  imports nothing of the package), still read the variable themselves; tests/test_switches.py checks that they
  agree with `get`.
- `scope="script"`: read only by shell or research scripts under scripts/. Declared (name, owner, what) so that no
  variable exists without an owner; not parsed here.

tests/test_switches.py fails on a KAIBOT_* name in src/ or scripts/ that is not declared here, on a Python read of
an engine switch outside this module that is not on its known list, and on a switch past its review date.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ON = ("1", "on", "true", "yes")
OFF = ("0", "off", "false", "no")


class SwitchError(ValueError):
    """One or more KAIBOT_* variables hold a value their switch does not have."""


@dataclass(frozen=True)
class Switch:
    name: str
    kind: str  # flag | on_off | choice | path | text
    owner: str  # the team (docs/teams.md)
    review: date  # declared again or removed by then (tests/test_switches.py)
    what: str
    default: object = None
    choices: tuple[str, ...] = ()
    empty_ok: bool = True  # "" means unset (the default); False: "" is refused
    scope: str = "engine"
    readers: tuple[str, ...] = field(default=())  # files outside this module that read it themselves (#538's)
    padded_ok: bool = True  # False: a value with spaces around it is refused (it was never on where it mattered)

    field_name: str = ""  # the field of `Switches` when it is not the name in lower case without KAIBOT_

    @property
    def attr(self) -> str:
        return self.field_name or self.name.removeprefix("KAIBOT_").lower()


REVIEW = date(2027, 3, 31)  # every switch is looked at again at the end of Q1 2027 (M10 1.23)


def _s(name, kind, owner, what, **kw) -> Switch:
    return Switch(name, kind, owner, kw.pop("review", REVIEW), what, **kw)


SWITCHES: dict[str, Switch] = {s.name: s for s in (
    # ---- the engine's behaviour switches
    _s("KAIBOT_MULTIDAY", "on_off", "backend-quant", "the multi-day daily path (#206); only 'on' turns it on",
       default=False, readers=("src/kaibot/strategies/registry.py",), field_name="multi_day"),
    # QA-Ops on #588: the room gateway never stripped, so " on" was OFF there (agents/preopen.py stripped). The room
    # is a stakeholder-only switch (CLAUDE.md): a padded value must not turn it on, so it is refused (fail closed).
    _s("KAIBOT_ROOM", "flag", "agents-ai", "the agent room (M3); also needed by the pre-open review", default=False,
       padded_ok=False),
    _s("KAIBOT_PREOPEN", "choice", "agents-ai", "#310: 'shadow' = the observer pre-open review",
       default="off", choices=("off", "shadow")),
    _s("KAIBOT_PREOPEN_REVIEW", "flag", "agents-ai", "#131: the pre-open review that can veto or halve buys "
       "(with KAIBOT_ROOM)", default=False),
    _s("KAIBOT_PREOPEN_MASKED", "flag", "agents-ai", "M12 3.9 (#471): the shadow review's masked arm (tickers and "
       "names hidden; a second call a session); on unless switched off", default=True),
    _s("KAIBOT_DAILY_NOTE", "flag", "agents-ai", "#311: the daily report in plain words", default=False),
    _s("KAIBOT_FILING_FLAG", "choice", "data", "#375: 'shadow' = the 8-K filing-flag observer",
       default="off", choices=("off", "shadow")),
    _s("KAIBOT_MARKET_ALARM", "on_off", "data", "S103 (#377): the market-event alarm as a logged notice",
       default=False),
    _s("KAIBOT_NEWS_POLL", "flag", "data", "#354: the engine's news poller (on unless switched off)", default=True),
    _s("KAIBOT_DATA_CHECKS", "flag", "data", "M12 3.11 (#473): the minute-bar data checks, report only",
       default=False, readers=("src/kaibot/engine/market.py",)),
    _s("KAIBOT_REENABLE", "text", "backend-quant", "the owner's word to switch a retired multi-day rule back on "
       "(read once at start)", readers=("src/kaibot/engine/live_daily.py",)),
    _s("KAIBOT_TESTWRITER", "flag", "qa-ops", "M12 3.8 (#470): the test-writing agent (spends API credit)",
       default=False, field_name="test_writer"),
    # ---- where things are, and names
    _s("KAIBOT_ENV_FILE", "path", "qa-ops", "the .env file to read the keys from"),
    _s("KAIBOT_DATA_DIR", "path", "qa-ops", "the engine's data folder (default data/live)", empty_ok=False),
    _s("KAIBOT_DAILY_DATA", "path", "data", "the daily store's root (default data)"),
    _s("KAIBOT_TRIALS_FILE", "path", "backend-quant", "the trial registry file"),
    _s("KAIBOT_SOURCES_FILE", "path", "data", "config/sources.yaml elsewhere (tests)"),
    _s("KAIBOT_HELPER_ENV_FILE", "path", "qa-ops", "#451: the helpers' env file (read by common/helper_session.py "
       "itself: the helpers' path imports nothing of the package)", readers=("src/kaibot/common/helper_session.py",)),
    _s("KAIBOT_HOLDOUT_OPENING", "text", "backend-quant", "M11 2.6: the id of a committed hold-out opening"),
    _s("KAIBOT_ENGINE_NAME", "text", "backend-quant", "the heartbeat's process name (default 'engine')",
       default="engine", empty_ok=False),
    _s("KAIBOT_CODE_SHA", "text", "qa-ops", "the code version the heartbeat reports (Docker builds set it)",
       readers=("src/kaibot/engine/core.py",)),
    # ---- read only by scripts under scripts/ (declared with their owner; not parsed here)
    *(_s(n, "text", owner, what, scope="script") for n, owner, what in (
        ("KAIBOT_RELEASE", "qa-ops", "the pinned release (scripts/release.sh)"),
        ("KAIBOT_REEXEC", "qa-ops", "run_engine.sh's re-exec guard after a release switch"),
        ("KAIBOT_FAKE_ET", "qa-ops", "release.sh's test clock"),
        ("KAIBOT_SERVICE_MODE", "qa-ops", "the always-on service's mode (install_service.sh)"),
        ("KAIBOT_START_HOUR", "qa-ops", "the service's daily start hour"),
        ("KAIBOT_LOG_DIR", "qa-ops", "the service's log folder"),
        ("KAIBOT_LOG_KEEP", "qa-ops", "how many logs the service keeps"),
        ("KAIBOT_LAUNCH_AGENTS_DIR", "qa-ops", "where uninstall_service.sh looks for the launch agents"),
        ("KAIBOT_BACKUP_DIR", "qa-ops", "backup_local.sh's target"),
        ("KAIBOT_BACKUP_KEEP_DAYS", "qa-ops", "backup_local.sh's retention"),
        ("KAIBOT_PRICE_STREAM", "frontend", "the price relay (scripts/price_stream.py) in the service"),
        ("KAIBOT_STREAM_START_DELAY", "frontend", "the price relay's start delay in the service"),
        ("KAIBOT_NOTICES", "data", "the market-notice job (scripts/publish_notices.py) in the service"),
        ("KAIBOT_TICKER_CHECKS", "data", "the custom-ticker checks job in the service"),
        ("KAIBOT_CHECKS_START_DELAY", "data", "that job's start delay"),
        ("KAIBOT_DAILY_FETCH", "data", "the evening daily-bars fetch in the service"),
        ("KAIBOT_HISTORY_START", "data", "fetch_history.sh's first day"),
        ("KAIBOT_FORMER_MEMBERS", "data", "fetch_daily.sh: also fetch former index members"),
        ("KAIBOT_EXP_START", "quant", "run_experiments.sh's first day"),
        ("KAIBOT_EXP_END", "quant", "run_experiments.sh's last day"),
        ("KAIBOT_EXP_UNIVERSE", "quant", "run_experiments.sh's universe"),
        ("KAIBOT_SPY_CONTROL_RUNS", "quant", "run_experiments.sh: control runs of the SPY rule"),
        ("KAIBOT_SPY_TREND_CONTROL_RUNS", "quant", "run_experiments.sh: control runs of the SPY trend rule"),
        ("KAIBOT_SPY_ENGINE_JOBS", "quant", "run_experiments.sh: parallel engine jobs"),
        ("KAIBOT_CORE_CONTROL_RUNS", "quant", "run_experiments.sh: control runs of the index core"),
        ("KAIBOT_CORE_COSTS", "quant", "run_experiments.sh: the index core's cost model"),
        ("KAIBOT_ONESHOT_SEEDS", "quant", "run_experiments.sh: seeds of the one-shot runs"),
        ("KAIBOT_ONESHOT_CONTROL_RUNS", "quant", "run_experiments.sh: control runs of the one-shot runs"),
    )),
)}


def _raw(name: str, env: Mapping[str, str] | None) -> str | None:
    env = os.environ if env is None else env
    v = env.get(name)
    return None if v is None else str(v)


def get(name: str, env: Mapping[str, str] | None = None):
    """What the readers call: `value`, but a value the switch does not have means the default, as it always did.
    The start refuses such a value (`load`, from Settings.from_env), so a running engine never meets one; a test,
    a script or a caller with its own mapping keeps the old lenient meaning (zero behaviour change)."""
    try:
        return value(name, env)
    except SwitchError:
        return SWITCHES[name].default


def value(name: str, env: Mapping[str, str] | None = None):
    """The typed value of one engine switch (`env`: os.environ when None). Raises SwitchError on a value the switch
    does not have. flag / on_off -> bool, choice -> str, path -> Path or None, text -> str or None."""
    s = SWITCHES[name]
    if s.scope != "engine":
        raise KeyError(f"{name} is a script switch: it is not parsed by the package")
    raw = _raw(name, env)
    if raw is None or (raw.strip() == "" and s.empty_ok):
        return s.default
    v = raw.strip()
    if not s.padded_ok and v != raw:
        raise SwitchError(f"{name}={raw!r}: spaces around the value are refused; write it without them ({s.what})")
    if s.kind in ("path", "text"):
        if not v:
            raise SwitchError(f"{name} is set but empty ({s.what})")
        return Path(raw) if s.kind == "path" else raw
    low = v.lower()
    if s.kind == "flag":
        if low in ON:
            return True
        if low in OFF:
            return False
        raise SwitchError(f"{name}={raw!r}: use one of {', '.join(ON + OFF)} ({s.what})")
    if s.kind == "on_off":
        if low in ("on", "off"):
            return low == "on"
        raise SwitchError(f"{name}={raw!r}: use on or off ({s.what})")
    if s.kind == "choice":
        if low in s.choices:
            return low
        raise SwitchError(f"{name}={raw!r}: use one of {', '.join(s.choices)} ({s.what})")
    raise AssertionError(s.kind)


@dataclass(frozen=True)
class Switches:
    """The engine's switches as parsed at start (`load`): one typed object."""

    multi_day: bool
    room: bool
    preopen: str
    preopen_review: bool
    preopen_masked: bool
    daily_note: bool
    filing_flag: str
    market_alarm: bool
    news_poll: bool
    data_checks: bool
    reenable: str | None
    test_writer: bool
    env_file: Path | None
    data_dir: Path | None
    daily_data: Path | None
    trials_file: Path | None
    sources_file: Path | None
    helper_env_file: Path | None
    holdout_opening: str | None
    engine_name: str
    code_sha: str | None


def load(env: Mapping[str, str] | None = None) -> Switches:
    """Every engine switch, parsed and validated together. Raises SwitchError naming every bad variable."""
    vals, bad = {}, []
    for s in SWITCHES.values():
        if s.scope != "engine":
            continue
        try:
            vals[s.attr] = value(s.name, env)
        except SwitchError as e:
            bad.append(str(e))
    if bad:
        raise SwitchError("invalid settings (nothing was started): " + "; ".join(bad))
    return Switches(**vals)
