"""Every KAIBOT_* environment switch, declared once (M10 ticket 1.23, issue #448).

Settings stay env-only (decision 11). What changes is that each variable is declared here with its type, its default
(the same as before this ticket), the team that owns it and a review date, and that the switches are parsed,
validated and loaded ONCE, at start, into one typed object (`Switches`):

- `start` (called by engine/config.Settings.from_env, after the .env file is read) parses every switch, refuses a
  value that is not one of the switch's values (every bad variable named, nothing started) and keeps the result.
  Before #588 an unknown value silently meant the default (`KAIBOT_ROOM=maybe` was off, `KAIBOT_NEWS_POLL=maybe`
  was on); now it is refused (fail closed). Valid values mean exactly what they meant. A KAIBOT_* variable that is
  not declared here is named in a warning at start (a misspelt switch is otherwise silently ignored).
- `current()` is that object. Every reader goes through it: `get(name)` / `value(name)` without a mapping (or
  with `os.environ` itself) answer from the object loaded at start, so the running engine reads each variable
  exactly once. With a mapping of their own (tests, the evaluation set's fixed ON_ENV) they parse that mapping.
  Before a start (a test, a one-off research script that never calls Settings.from_env) `current()` parses the
  environment as it is at that moment, leniently: a value the switch does not have means its default there, as it
  always did.
- `scope="engine"`: read by Python (the engine, the watchdog, the agents, the research scripts). Typed fields of
  `Switches`. Two files read their variable themselves and are listed in `readers`: common/helper_session.py
  (the helpers' path imports nothing of the package) and scripts/healthcheck.py (stdlib only, the Mac's system
  Python). tests/test_switches.py checks that they agree with `get`.
- `scope="script"`: read only by shell scripts under scripts/. Declared (name, owner, what) so that no variable
  exists without an owner; not parsed here.

tests/test_switches.py fails on a KAIBOT_* name in src/ or scripts/ that is not declared here, on a Python read of
a KAIBOT_* variable from the environment outside this module (a grep and an AST scan of src/ and scripts/), and on
a switch past its review date.
"""

from __future__ import annotations

import logging
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
    kind: str  # flag | on_off | choice | checks | path | text
    owner: str  # the team (docs/teams.md)
    review: date  # declared again or removed by then (tests/test_switches.py)
    what: str
    default: object = None
    choices: tuple[str, ...] = ()
    empty_ok: bool = True  # "" means unset (the default); False: "" is refused
    scope: str = "engine"
    readers: tuple[str, ...] = field(default=())  # files that read it themselves, with the reason (docstring)
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
       default=False, field_name="multi_day"),
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
    _s("KAIBOT_HEADLINE_FEED", "flag", "backend-quant", "the dashboard's headline line: the headlines the engine "
       "fetches anyway, copied to kaibot.headline (0043; on unless switched off)", default=True),
    _s("KAIBOT_HEADLINE_EDGAR", "flag", "backend-quant", "the headline line's SEC 8-K source (licence public, for "
       "multi-user); needs SEC_USER_AGENT", default=False),
    _s("KAIBOT_DATA_CHECKS", "flag", "data", "M12 3.11 (#473): the minute-bar data checks, report only",
       default=False),
    _s("KAIBOT_DATA_CHECKS_BLOCK", "checks", "backend-quant", "M12 3.11 (#473): which minute-bar data checks block "
       "entries: off, on (all but bar_gap) or a comma list", default=(),
       choices=("stale", "no_bars", "bad_price", "unmatched_move", "bar_gap")),
    # audit 2 5.15 (#695): on by default (Product under #600; docs/ops/switches.md). Buys only, never a sale
    _s("KAIBOT_DAILY_CHECKS_BLOCK", "checks", "data", "M12 3.11 (#473): which daily-store data checks block buys "
       "of the multi-day path: off, on (all but price_jump; the default since #695) or a comma list "
       "(kaibot.ingest.entry_checks)",
       default=("benchmark_bar_missing", "daily_bar_stale", "price_not_positive", "split_not_applied",
                "universe_stale"),
       choices=("benchmark_bar_missing", "daily_bar_stale", "price_not_positive", "split_not_applied",
                "universe_stale", "price_jump")),
    _s("KAIBOT_ENGINE_FETCH", "flag", "backend-quant", "audit 2 5.12 (#692): the engine starts "
       "scripts/fetch_daily.sh itself, at start-up or on waking, when the daily store lacks the bars its next "
       "multi-day send needs (on unless switched off)", default=True),
    _s("KAIBOT_REENABLE", "text", "backend-quant", "the owner's word to switch a retired multi-day rule back on "
       "(read once at start)"),
    _s("KAIBOT_TESTWRITER", "flag", "qa-ops", "M12 3.8 (#470): the test-writing agent (spends API credit)",
       default=False, field_name="test_writer"),
    # ---- where things are, and names
    _s("KAIBOT_ENV_FILE", "path", "qa-ops", "the .env file to read the keys from (each script keeps its own "
       "fallback when unset)", readers=("scripts/healthcheck.py",)),
    _s("KAIBOT_DATA_DIR", "path", "qa-ops", "the engine's data folder (default data/live)", empty_ok=False),
    _s("KAIBOT_DAILY_DATA", "path", "data", "the daily store's root (default data)"),
    _s("KAIBOT_TRIALS_FILE", "path", "backend-quant", "the trial registry file"),
    _s("KAIBOT_SOURCES_FILE", "path", "data", "config/sources.yaml elsewhere (tests)"),
    _s("KAIBOT_RATELIMIT_DIR", "path", "qa-ops", "audit 2 7.6 (#716): the folder of the machine-wide rate-limit "
       "windows (default data/ratelimit; the research runner sets it for its jobs to the engine checkout's)",
       readers=("scripts/research_runner.py",)),
    _s("KAIBOT_HELPER_ENV_FILE", "path", "qa-ops", "#451: the helpers' env file (read by common/helper_session.py "
       "itself: the helpers' path imports nothing of the package)", readers=("src/kaibot/common/helper_session.py",)),
    _s("KAIBOT_ENGINE_CHECKOUT", "path", "qa-ops", "QA-Ops audit (25 Nov 2026): the engine's checkout, which the "
       "research runner refuses to run in (install_runner.sh writes it into the runner's plist)", scope="script",
       readers=("scripts/research_runner.py",)),
    _s("KAIBOT_RUNNER_GH_TOKEN_FILE", "path", "qa-ops", "audit 2 7.1 (#711): the research runner's fine-grained GitHub "
       "token file (default ~/Projects/kaibot-secrets/runner_gh_token, mode 600)", scope="script",
       readers=("scripts/research_runner.py",)),
    _s("KAIBOT_DATA_ENV_FILE", "path", "qa-ops", "audit 2 7.2 (#712): the market-data key file of the price relay and "
       "the ticker-check worker (default ~/Projects/kaibot-secrets/data.env: the data pair of a second, empty Alpaca "
       "paper account; common/secret_files.py reads it itself)", scope="script",
       readers=("src/kaibot/common/secret_files.py",)),
    _s("KAIBOT_HOLDOUT_OPENING", "text", "backend-quant", "M11 2.6: the id of a committed hold-out opening"),
    _s("KAIBOT_ENGINE_NAME", "text", "backend-quant", "the heartbeat's process name (default 'engine')",
       default="engine", empty_ok=False),
    _s("KAIBOT_CODE_SHA", "text", "qa-ops", "the code version the heartbeat reports (Docker builds set it)"),
    _s("KAIBOT_SEC_USER_AGENT", "text", "data", "fetch_sec_fundamentals.py: the declared SEC User-Agent "
       "(\"Name email\"); the fetch refuses to run without it"),
    # ---- read only by scripts under scripts/ (declared with their owner; not parsed here)
    *(_s(n, "text", owner, what, scope="script") for n, owner, what in (
        ("KAIBOT_RELEASE", "qa-ops", "the pinned release (scripts/release.sh)"),
        ("KAIBOT_FOLLOW_MAIN", "qa-ops", "audit 2 5.2 (#682): may an unpinned start follow main (1, the default for "
         "now, with WARNING lines) or is it refused (0)? release.sh, from the environment or the .env"),
        ("KAIBOT_RUNNER_CHECKOUT", "qa-ops", "the research runner's own checkout (install_runner.sh; default "
         "~/Projects/kaibot-runner)"),
        ("KAIBOT_REEXEC", "qa-ops", "run_engine.sh's re-exec guard after a release switch"),
        ("KAIBOT_SERVICE_RUN", "qa-ops", "#613: the service run's pid, so run_engine.sh lets the service start it"),
        ("KAIBOT_FAKE_ET", "qa-ops", "release.sh's test clock"),
        ("KAIBOT_SERVICE_MODE", "qa-ops", "the always-on service's mode (install_service.sh)"),
        ("KAIBOT_START_HOUR", "qa-ops", "the service's daily start hour"),
        ("KAIBOT_LOG_DIR", "qa-ops", "the service's log folder"),
        ("KAIBOT_LOG_KEEP", "qa-ops", "how many logs the service keeps"),
        ("KAIBOT_REFUSED_BACKOFF_S", "qa-ops", "the service's wait after a start refused with exit code 5 (300 s)"),
        ("KAIBOT_LAUNCH_AGENTS_DIR", "qa-ops", "where uninstall_service.sh looks for the launch agents"),
        ("KAIBOT_BACKUP_DIR", "qa-ops", "backup_local.sh's target"),
        ("KAIBOT_BACKUP_KEEP_DAYS", "qa-ops", "backup_local.sh's retention"),
        ("KAIBOT_BACKUP_OFFBOX_DIR", "qa-ops", "audit 2 7.5 (#715): backup_local.sh's encrypted off-box folder "
         "(iCloud Drive, an external disk; unset: no off-box copy)"),
        ("KAIBOT_BACKUP_OFFBOX_KEEP_DAYS", "qa-ops", "#715: the off-box copies' retention (default 14 days)"),
        ("KAIBOT_BACKUP_AGE_RECIPIENTS", "qa-ops", "#715: the age public key file the off-box copy is encrypted to"),
        ("KAIBOT_BACKUP_PASSFILE", "qa-ops", "#715: the openssl passphrase file, when there is no age key"),
        ("KAIBOT_PRICE_STREAM", "frontend", "the price relay (scripts/price_stream.py) in the service"),
        ("KAIBOT_STREAM_START_DELAY", "frontend", "the price relay's start delay in the service"),
        ("KAIBOT_NOTICES", "data", "the market-notice job (scripts/publish_notices.py) in the service"),
        ("KAIBOT_TICKER_CHECKS", "data", "the custom-ticker checks job in the service"),
        ("KAIBOT_CHECKS_START_DELAY", "data", "that job's start delay"),
        ("KAIBOT_DAILY_FETCH", "data", "the evening daily-bars fetch in the service"),
        ("KAIBOT_HISTORY_START", "data", "fetch_history.sh's first day"),
        ("KAIBOT_FORMER_MEMBERS", "data", "fetch_daily.sh: also fetch former index members"),
        ("KAIBOT_FETCH_LOCKED", "backend-quant", "fetch_daily.sh: set while it runs under its lock (audit 2 5.12)"),
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


def _from_start(env: Mapping[str, str] | None) -> bool:
    """The question is about this process's environment and the switches were loaded at start: answer from them."""
    return _LOADED is not None and (env is None or env is os.environ)


def get(name: str, env: Mapping[str, str] | None = None):
    """What the readers call: `value`, but a value the switch does not have means the default, as it always did.
    The start refuses such a value (`start`, from Settings.from_env), so a running engine never meets one; a test,
    a script or a caller with its own mapping keeps the old lenient meaning (zero behaviour change)."""
    if _from_start(env):
        return getattr(_LOADED, SWITCHES[name].attr)
    try:
        return _parse(name, env)
    except SwitchError:
        return SWITCHES[name].default


def text(name: str, env: Mapping[str, str] | None = None) -> str | None:
    """`get` as a string for the scripts' `... or DEFAULT` idiom: None when unset or blank, else the value (a path as
    `str(Path(value))`)."""
    v = get(name, env)
    return None if v is None or v == "" else str(v)


def value(name: str, env: Mapping[str, str] | None = None):
    """The typed value of one engine switch (`env`: os.environ when None). Raises SwitchError on a value the switch
    does not have. flag / on_off -> bool, choice -> str, path -> Path or None, text -> str or None. After a start,
    os.environ's answer is the loaded one (a value the lenient start refused still raises here)."""
    if _from_start(env):
        if name in _REFUSED:
            raise SwitchError(_REFUSED[name])
        return getattr(_LOADED, SWITCHES[name].attr)
    return _parse(name, env)


def _parse(name: str, env: Mapping[str, str] | None):
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
    if s.kind == "checks":  # off -> (), on -> every choice but the last (bar_gap), or a comma list of choices
        if low in OFF:
            return ()
        if low in ON:
            return s.choices[:-1]
        names = tuple(sorted({x.strip() for x in low.split(",") if x.strip()}))
        unknown = [x for x in names if x not in s.choices]
        if unknown or not names:
            raise SwitchError(f"{name}={raw!r}: unknown check(s) {unknown}; use on, off or a comma list of "
                              f"{', '.join(s.choices)} ({s.what})")
        return names
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
    headline_feed: bool
    headline_edgar: bool
    data_checks: bool
    data_checks_block: tuple[str, ...]
    daily_checks_block: tuple[str, ...]
    engine_fetch: bool
    reenable: str | None
    test_writer: bool
    env_file: Path | None
    data_dir: Path | None
    daily_data: Path | None
    trials_file: Path | None
    sources_file: Path | None
    ratelimit_dir: Path | None
    helper_env_file: Path | None
    holdout_opening: str | None
    engine_name: str
    code_sha: str | None
    sec_user_agent: str | None


_LOADED: Switches | None = None  # the switches as loaded at start (`start`); None before (and in each new test)
_REFUSED: dict[str, str] = {}  # lenient start only (the watchdog): the switches whose value was refused, and why


def _parse_all(env: Mapping[str, str] | None) -> tuple[dict[str, object], dict[str, str]]:
    vals: dict[str, object] = {}
    bad: dict[str, str] = {}
    for s in SWITCHES.values():
        if s.scope != "engine":
            continue
        try:
            vals[s.attr] = _parse(s.name, env)
        except SwitchError as e:
            bad[s.name] = str(e)
            vals[s.attr] = s.default
    return vals, bad


def load(env: Mapping[str, str] | None = None) -> Switches:
    """Every engine switch, parsed and validated together (`env`: os.environ when None). Raises SwitchError naming
    every bad variable. Parses only: `start` is what keeps the result for the process."""
    vals, bad = _parse_all(env)
    if bad:
        raise SwitchError("invalid settings (nothing was started): " + "; ".join(bad.values()))
    return Switches(**vals)  # type: ignore[arg-type]


def start(*, strict: bool = True) -> Switches:
    """Load every switch from os.environ once, for the rest of the process (Settings.from_env calls it after the
    .env file is read). strict: a bad value raises SwitchError and nothing is kept. Not strict (the watchdog): a
    bad value means its default for `get`, `value` raises on it, and `refused()` returns the errors."""
    global _LOADED, _REFUSED
    vals, bad = _parse_all(None)
    if bad and strict:
        raise SwitchError("invalid settings (nothing was started): " + "; ".join(bad.values()))
    _LOADED, _REFUSED = Switches(**vals), dict(bad)  # type: ignore[arg-type]
    extra = undeclared()
    if extra:
        logging.getLogger("kaibot.engine").warning(
            "KAIBOT_* variables that are not declared in common/switches.py are ignored: %s (a misspelt switch?)",
            ", ".join(extra))
    return _LOADED


def refused() -> dict[str, str]:
    """After a lenient start: the switches whose value was refused (name -> message)."""
    return dict(_REFUSED)


def current() -> Switches:
    """The switches loaded at start; before a start, the environment as it is now (lenient, not kept)."""
    if _LOADED is not None:
        return _LOADED
    return Switches(**_parse_all(None)[0])  # type: ignore[arg-type]


def reset() -> None:
    """Forget the loaded switches (tests: each test starts with none loaded)."""
    global _LOADED, _REFUSED
    _LOADED, _REFUSED = None, {}


def undeclared(env: Mapping[str, str] | None = None) -> list[str]:
    """KAIBOT_* variables in the environment that no switch declares (names only, never values). KAIBOT_TEST_*
    belong to the test suite (tests/conftest.py, CI)."""
    env = os.environ if env is None else env
    return sorted(k for k in env if k.startswith("KAIBOT_") and k not in SWITCHES and not k.startswith("KAIBOT_TEST_"))
