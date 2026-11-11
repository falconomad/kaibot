"""Story S40: outage drill. Each scenario kills or starves the engine mid-session while it holds a bracketed position
and checks that the next start (a NEW Engine on the same broker and store state, as after a real crash) recovers
in under 5 simulated minutes without adding risk. Fakes only, no network.

`Sim` mirrors the real loops: the engine loop of `kaibot.engine.__main__` (every 10 s: commands, a tick once per
completed minute at second >= 7, then a heartbeat upserted with the store's clock) and the watchdog's 15 s poll.
The fake venue keeps processing bars while the engine is dead, so broker-held legs can fill during an outage."""

from __future__ import annotations

import contextlib
import os
import stat
import subprocess
import textwrap
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from test_engine import fake_candidate, make, synth_frames

from kaibot.engine.core import Engine
from kaibot.engine.market import LiveMarket
from kaibot.engine.watchdog import Watchdog
from kaibot.risk.profiles import load_profiles

RECOVERY_S = 300  # acceptance: recovery < 5 min, measured from the restart
ROOT = Path(__file__).resolve().parents[1]


def drift_up(c):
    """A smooth, slow climb from 10:25: the dip entry at 10:25 fills at 10:26 and neither leg triggers all day."""
    c = c.copy()
    c[55:] = c[55] * np.exp(np.linspace(0, 0.002, 335))
    return c


class Sim:
    """Simulated wall clock driving the fake venue, the engine loop and the watchdog loop (5 s resolution)."""

    def __init__(self, eng, broker, clock, store, open_utc):
        self.eng, self.broker, self.clock, self.store, self.open_utc = eng, broker, clock, store, open_utc
        self.wd = Watchdog(broker, store, now=clock)
        self.alive = True
        self.last_minute = None
        self.status: dict = {"state": "starting"}
        self.beats: list[tuple[datetime, dict]] = []

    def at(self, k: int, sec: int = 0) -> datetime:
        return self.open_utc + timedelta(minutes=k, seconds=sec)

    def run_until(self, t: datetime) -> None:
        while self.clock.t < t:
            nxt = self.clock.t + timedelta(seconds=5)
            if nxt.replace(second=0) > self.clock.t:  # a minute boundary was crossed: that bar is complete
                self.broker.advance(pd.Timestamp(nxt.replace(second=0) - timedelta(minutes=1)))
            self.clock.t = nxt
            if self.alive and self.clock.t.second % 10 == 0:
                self._engine_iteration()
            if self.clock.t.second % 15 == 0:
                with contextlib.suppress(Exception):  # the real watchdog loop logs and retries
                    self.wd.step()

    def _engine_iteration(self) -> None:
        """One pass of kaibot.engine.__main__.main's loop."""
        eng, now = self.eng, self.clock()
        try:
            eng.apply_commands()
            minute = now.replace(second=0, microsecond=0)
            if minute != self.last_minute and now.second >= 7:
                self.status = eng.tick()
                eng.on_tick_ok()
                self.last_minute = minute
        except Exception as e:
            eng.on_tick_error(e)
            self.status = {"state": "error", "error": f"{type(e).__name__}: {e}"[:300]}
        detail = {**self.status, "host": "sim"}
        self.store.upsert("heartbeat", {"process": eng.process, "ts": now.isoformat(), "detail": detail}, "process")
        self.beats.append((now, detail))

    def kill(self) -> None:
        """The process dies (SIGKILL, laptop sleeps): no clean shutdown, no last heartbeat."""
        self.alive = False

    def restart(self, tmp_path, offer: bool = True) -> Engine:
        """A new process: new Engine and LiveMarket objects; the broker, the store and the history cache survive."""
        old = self.eng.market
        market = LiveMarket(old.data, old.symbols, old.etf_of, old.spy, tmp_path / "hist", old.vix_loader)
        eng = Engine(self.broker, market, self.store, load_profiles(), set(), now=self.clock)
        fake_candidate(eng, at=tuple(range(4, 380, 5)) if offer else ())  # A on every 5-minute bar: never double
        self.eng, self.alive, self.last_minute, self.status = eng, True, None, {"state": "starting"}
        self.restarted_at = self.clock()
        eng.startup()
        return eng

    def recovered_after(self, healthy) -> float | None:
        """Seconds from the restart to the first heartbeat that satisfies `healthy(detail)`."""
        for ts, d in self.beats:
            if ts >= self.restarted_at and healthy(d):
                return (ts - self.restarted_at).total_seconds()
        return None


def healthy_with(*symbols):
    def ok(d):
        return (d.get("state") in ("trading", "managing") and d.get("account_clean") is True
                and all(d.get("positions", {}).get(s) == "open" for s in symbols))
    return ok


def start(tmp_path, shock=drift_up, level="balanced"):
    eng, broker, clock, store, open_utc = make(tmp_path, synth_frames(shock=shock), start_level=level)
    fake_candidate(eng, at=(54,))
    sim = Sim(eng, broker, clock, store, open_utc)
    return sim, eng, broker, store


def buys(broker):
    return [o for o in broker.orders.values() if o["side"] == "buy"]


def stop_leg(broker, symbol="A"):
    o = next(o for o in buys(broker) if o["symbol"] == symbol)
    return next(leg for leg in o["legs"] if leg["type"] == "stop")


def heartbeat(store):
    return store.select("heartbeat", {"process": "eq.engine"})[0]


# ---------- a. the engine dies and a new instance starts on the same state ----------
def test_a_crash_restart_adopts_the_position_and_recovers_fast(tmp_path):
    sim, eng, broker, store = start(tmp_path)
    sim.run_until(sim.at(60))
    assert eng.pos["A"].status == "open"
    leg = dict(stop_leg(broker))
    n_orders = len(broker.orders)
    sim.kill()
    sim.run_until(sim.at(61, 3))
    eng2 = sim.restart(tmp_path)
    # adopted with its stop intact, account clean before the first tick
    p = eng2.pos["A"]
    assert p.status == "open" and p.stop_order_id == leg["id"]
    assert abs(p.stop - float(leg["stop_price"])) < 0.01 and stop_leg(broker)["status"] == "new"
    assert eng2.account_clean
    sim.run_until(sim.at(61, 30))  # first tick at 10:31:07
    assert sim.status["positions"] == {"A": "open"}  # managed on the first tick
    assert store.tables["ledger"][-1]["positions"]["A"]["stop"] == round(p.stop, 2)
    sim.run_until(sim.at(70))
    assert len(broker.orders) == n_orders and len(buys(broker)) == 1  # no duplicate entry, no stray order
    assert len(store.tables["trade"]) == 1 and len(store.tables["order"]) == 1
    took = sim.recovered_after(healthy_with("A"))
    assert took is not None and took < RECOVERY_S, took
    hb = heartbeat(store)
    assert hb["detail"]["account_clean"] is True and hb["ts"] == sim.clock().isoformat()


def test_a_position_is_still_managed_to_the_1555_flatten_after_restart(tmp_path):
    sim, eng, broker, store = start(tmp_path)
    sim.run_until(sim.at(60))
    sim.kill()
    sim.run_until(sim.at(62))
    sim.restart(tmp_path)
    sim.run_until(sim.at(388))
    t = store.tables["trade"][0]
    assert t["exit_reason"] == "time" and broker.pos == {}
    assert len(buys(broker)) == 1


def test_a_restart_keeps_todays_daily_stop_and_entry_count(tmp_path):
    """Found by this drill: a restart rebuilt the session from zero, so a daily stop already hit today was
    forgotten (entries resumed) and today's entries and realized P&L were not counted."""
    def crash(c):
        c = c.copy()
        c[56:] = c[56] * np.exp(np.linspace(0, -0.05, 334))
        return c

    sim, eng, broker, store = start(tmp_path, shock=crash, level="aggressive")
    tiny = type(eng.policies["aggressive"])(**{**eng.policies["aggressive"].__dict__, "max_daily_loss_pct": 0.05})
    eng.policies = {**eng.policies, "aggressive": tiny}  # tiny daily stop, as in test_engine
    sim.run_until(sim.at(80))
    assert eng.day.halted and broker.pos == {}
    pnl = store.tables["trade"][0]["pnl_usd"]
    assert pnl < 0
    sim.kill()
    sim.run_until(sim.at(81))
    eng2 = sim.restart(tmp_path)
    eng2.policies = eng.policies
    sim.run_until(sim.at(120))  # A is offered at every 5-minute bar after the restart
    assert eng2.day.halted, "the daily stop must survive a restart"
    assert eng2.day.entries == 1
    assert abs(eng2.day.realized_reported - pnl) < 0.01
    assert len(store.tables["trade"]) == 1 and len(buys(broker)) == 1


# ---------- b. dead for 10 minutes: the watchdog acts, the restart recovers ----------
def test_b_dead_10_min_watchdog_cancels_unfilled_entry_and_restart_closes_it(tmp_path):
    sim, eng, broker, store = start(tmp_path)
    sim.run_until(sim.at(55, 9))  # 10:25:07 tick sent the bracket; it fills on the 10:26 bar
    assert eng.pos["A"].status == "pending"
    sim.kill()
    sim.broker.advance = lambda t: None  # the venue is slow: the entry stays unfilled while the engine is dead
    sim.run_until(sim.at(65, 10))
    o = buys(broker)[0]
    assert o["status"] == "canceled"  # the watchdog cancelled the unfilled entry
    assert any(h["kind"] == "watchdog" for h in store.tables["halt"])
    del sim.broker.advance
    eng2 = sim.restart(tmp_path, offer=False)
    sim.run_until(sim.at(70))
    assert "A" not in eng2.pos and store.tables["trade"][0]["exit_reason"] == "not_filled"
    took = sim.recovered_after(lambda d: d.get("state") in ("trading", "managing") and d.get("account_clean"))
    assert took is not None and took < RECOVERY_S
    assert any(e["type"] == "watchdog_recovered" for e in store.tables["event"])


def test_b_dead_10_min_keeps_the_broker_held_stop_and_restart_recovers(tmp_path):
    sim, eng, broker, store = start(tmp_path)
    sim.run_until(sim.at(60))
    leg = dict(stop_leg(broker))
    sim.kill()
    sim.run_until(sim.at(70, 5))  # 10 minutes dead
    assert any(h["kind"] == "watchdog" for h in store.tables["halt"])  # it noticed (heartbeat > 90 s)
    assert stop_leg(broker) == leg and "A" in broker.pos  # stop untouched, position still held
    sim.restart(tmp_path)
    sim.run_until(sim.at(76))
    took = sim.recovered_after(healthy_with("A"))
    assert took is not None and took < RECOVERY_S, took
    assert len(buys(broker)) == 1
    assert any(e["type"] == "watchdog_recovered" for e in store.tables["event"])


def test_b_stop_that_fills_while_dead_is_booked_on_restart(tmp_path):
    def dip_later(c):
        c = drift_up(c)
        c[63:] = c[62] * 0.99  # through the stop at 10:33 while the engine is down
        return c

    sim, eng, broker, store = start(tmp_path, shock=dip_later)
    sim.run_until(sim.at(60))
    sim.kill()
    sim.run_until(sim.at(70, 5))
    assert stop_leg(broker)["status"] == "filled" and broker.pos == {}
    eng2 = sim.restart(tmp_path, offer=False)
    sim.run_until(sim.at(72))
    t = store.tables["trade"][0]
    assert t["exit_reason"] == "stop" and t["pnl_usd"] < 0 and eng2.pos == {}
    # booked once: by the first sync after the restart, not also by the restored session state
    assert eng2.day.entries == 1 and abs(eng2.day.realized_reported - t["pnl_usd"]) < 0.01
    assert len([f for f in store.tables["fill"] if f["side"] == "sell"]) == 1


# ---------- c. data / broker API outages ----------
def _raise(*a, **k):
    raise ConnectionError("simulated outage")


@pytest.mark.parametrize("what", ["data", "broker"])
@pytest.mark.parametrize("seconds", [20, 120, 600])
def test_c_api_outage_fails_closed_and_recovers(tmp_path, what, seconds):
    """The loop retries every 10 s, so an outage of 30 s or more is 3 failed iterations in a row: the kill switch
    trips and entries stay off for the rest of the session (fail closed). Exits keep being managed either way."""
    sim, eng, broker, store = start(tmp_path)
    sim.run_until(sim.at(60, 2))
    leg = dict(stop_leg(broker))
    n_orders = len(broker.orders)
    fake_candidate(eng, at=tuple(range(59, 380, 5)))  # a setup on every bar, during the outage too
    if what == "data":
        targets = [(eng.market.data, n) for n in ("bars", "latest_quotes")]
    else:
        targets = [(broker, n) for n in ("clock", "positions", "open_orders", "order", "account",
                                          "submit_bracket", "close_position", "replace_stop")]
    saved = [(o, n, getattr(o, n)) for o, n in targets]
    for o, n in targets:
        setattr(o, n, _raise)
    end = sim.clock() + timedelta(seconds=seconds)
    sim.run_until(end)
    assert sim.status["state"] == "error"
    assert len(broker.orders) == n_orders and stop_leg(broker) == leg  # nothing new, protection untouched
    for o, n, f in saved:
        setattr(o, n, f)
    sim.run_until(end + timedelta(minutes=3))
    ticks = [d for ts, d in sim.beats if ts > end and d.get("minute") is not None]
    assert ticks, "no tick after the outage"
    first_ok = next(i for i, d in enumerate(ticks) if healthy_with("A")(d))
    assert first_ok == 0  # bounded: healthy on the first tick after the outage
    assert eng.error_streak == 0 and len(buys(broker)) == 1
    if seconds >= 30:
        assert eng.kill == "engine_errors" and not eng._may_enter(eng.day.last_m)
    else:
        assert eng.kill is None


def test_c_long_outage_still_flattens_at_1555(tmp_path):
    sim, eng, broker, store = start(tmp_path)
    sim.run_until(sim.at(60))
    real = eng.market.data.bars
    eng.market.data.bars = _raise
    sim.run_until(sim.at(75))
    eng.market.data.bars = real
    sim.run_until(sim.at(388))
    assert store.tables["trade"][0]["exit_reason"] == "time" and broker.pos == {}


# ---------- d. crash between submitting an order and writing its row ----------
@pytest.mark.parametrize("dies_writing", ["order", "trade"])
def test_d_crash_after_submit_restart_adopts_without_duplicating(tmp_path, dies_writing):
    sim, eng, broker, store = start(tmp_path)
    real_insert = store.insert

    def insert(table, row, returning=True):
        if table == dies_writing:
            sim.kill()
            raise SystemExit("process killed after the broker accepted the order")
        return real_insert(table, row, returning)

    store.insert = insert
    with pytest.raises(SystemExit):
        sim.run_until(sim.at(56))
    store.insert = real_insert
    assert len(buys(broker)) == 1 and not any(t for t in store.tables.get("trade", []))
    coid = buys(broker)[0]["client_order_id"]
    sim.run_until(sim.at(57, 3))  # the entry fills at the broker while we are down
    assert "A" in broker.pos
    eng2 = sim.restart(tmp_path)
    assert eng2.account_clean, "an order with our own client id for an approved signal must be adopted"
    assert eng2.pos["A"].coid == coid
    sim.run_until(sim.at(66))
    p = eng2.pos["A"]
    assert p.status == "open" and p.stop_order_id == stop_leg(broker)["id"]
    assert abs(p.stop - float(stop_leg(broker)["stop_price"])) < 0.01
    assert len(buys(broker)) == 1
    assert len(store.tables["order"]) == 1 and len(store.tables["trade"]) == 1
    assert store.tables["order"][0]["client_order_id"] == coid
    took = sim.recovered_after(healthy_with("A"))
    assert took is not None and took < RECOVERY_S
    assert any(e["type"] == "order_adopted" for e in store.tables["event"])


def test_d_unknown_kb_order_is_still_not_adopted(tmp_path):
    """Only an order whose client id matches an approved signal of this run is adopted; anything else still
    blocks entries (adopting blindly could double a position)."""
    sim, eng, broker, store = start(tmp_path)
    sim.run_until(sim.at(30))
    broker.orders["x1"] = {"id": "x1", "client_order_id": f"kb-{eng._rid()}-99999-1", "symbol": "A", "qty": 5,
                           "side": "buy", "type": "market", "status": "new", "legs": []}
    sim.kill()
    eng2 = sim.restart(tmp_path)
    assert not eng2.account_clean and "A" not in eng2.pos


def test_fake_broker_refuses_a_duplicate_client_order_id(tmp_path):
    """Alpaca rejects a reused client_order_id (422) and the adapter returns the existing order; the fake
    raises so that any duplicate submission in these drills fails loudly."""
    sim, eng, broker, store = start(tmp_path)
    broker.submit_bracket("kb-1-1-1", "A", 1, 90.0, 110.0)
    with pytest.raises(ValueError, match="client_order_id"):
        broker.submit_bracket("kb-1-1-1", "A", 1, 90.0, 110.0)


def test_status_reports_account_clean_when_the_market_is_closed(tmp_path):
    """Found by this drill: outside the session the heartbeat had no account_clean, so the off-hours drill (and
    the Health page) could not see that the restarted engine reconciled."""
    sim, eng, broker, store = start(tmp_path)
    st = eng.tick()  # 09:00 ET, market closed
    assert st["state"] == "market_closed" and st["account_clean"] is True
    broker.foreign = {"ZZZ": {"qty": "1"}}
    eng.check_account()
    assert eng.tick()["account_clean"] is False


# ---------- e. two engines at once ----------
def _stub(dirp: Path, name: str, body: str) -> None:
    p = dirp / name
    p.write_text("#!/usr/bin/env bash\n" + textwrap.dedent(body))
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


def _guard(tmp_path, docker_ps: str | None, ps_out: str, *args):
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    for f in bin_.iterdir():
        f.unlink()
    if docker_ps is not None:
        (tmp_path / "ps.txt").write_text(docker_ps)
        _stub(bin_, "docker", f"""\
            [ "$1" = info ] && exit 0
            [ "$1" = ps ] && cat {tmp_path / 'ps.txt'}
            exit 0
            """)
    (tmp_path / "ps_ax.txt").write_text(ps_out)
    _stub(bin_, "ps", f"cat {tmp_path / 'ps_ax.txt'}\n")
    env = {**os.environ, "PATH": f"{bin_}:/usr/bin:/bin"}
    return subprocess.run(["bash", str(ROOT / "scripts" / "engine_guard.sh"), *args], env=env,
                          capture_output=True, text=True, timeout=30)


def test_e_guard_passes_when_no_engine_runs(tmp_path):
    r = _guard(tmp_path, "", "")
    assert r.returncode == 0 and r.stdout == ""
    r = _guard(tmp_path, None, "")  # no docker installed at all
    assert r.returncode == 0


def test_e_guard_sees_docker_compose_and_python_engines(tmp_path):
    ps = "abc123\tkaibot-engine\t\ndef456\tkaibot-watchdog\t\nfff999\tkaibot-engine-1\tengine\n" \
         "aaa111\tkaibot-watchdog-1\twatchdog\n"
    procs = ("  4242 /x/.venv/bin/python -m kaibot.engine\n"
             "  4241 caffeinate -is .venv/bin/python -m kaibot.engine\n"
             "  4200 /x/.venv/bin/python -m kaibot.engine.watchdog\n")
    r = _guard(tmp_path, ps, procs, "--list")
    assert r.returncode == 1
    assert r.stdout.splitlines() == ["docker abc123 kaibot-engine", "compose fff999 kaibot-engine-1",
                                     "python 4242"]


def test_e_guard_ignores_the_watchdog_and_check_runs(tmp_path):
    procs = ("  10 /x/.venv/bin/python -m kaibot.engine.watchdog\n  11 /x/.venv/bin/python -m kaibot.engine --check\n"
             "  12 docker run --rm --name kaibot-engine kaibot-engine python -m kaibot.engine --check\n")
    r = _guard(tmp_path, "def456\tkaibot-watchdog\t\n", procs)
    assert r.returncode == 0 and r.stdout == ""


def test_e_run_engine_refuses_a_second_engine():
    """run_engine.sh calls the guard before starting anything (but not for --check, which never trades)."""
    sh = (ROOT / "scripts" / "run_engine.sh").read_text()
    guard_at = sh.index("engine_guard.sh")
    assert guard_at < sh.index("docker run -d") and guard_at < sh.index("nohup")
    assert 'CHECK" = 0' in sh[sh.rindex("\n", 0, guard_at) - 200: guard_at + 40]


def test_drill_script_is_valid_bash_and_paper_only():
    sh = ROOT / "scripts" / "outage_drill.sh"
    assert subprocess.run(["bash", "-n", str(sh)], capture_output=True).returncode == 0
    text = sh.read_text()
    assert "https://paper-api.alpaca.markets" in text and "https://api.alpaca.markets" not in text



# ---------- the real drill script (dry runs only, stubbed date/ps/curl: no network, nothing is stopped) ----------
SECRET = "sb_secret_DO_NOT_PRINT_123"


def _drill(tmp_path, *args, hm="1000", dow="3", base="https://paper-api.alpaca.markets", engine=True):
    bin_ = tmp_path / "dbin"
    bin_.mkdir(exist_ok=True)
    env_file = tmp_path / "drill.env"
    env_file.write_text(f"ALPACA_BASE_URL={base}\nSUPABASE_URL=https://example.invalid\n"
                        f"SUPABASE_SERVICE_KEY={SECRET}\n")
    _stub(bin_, "date", f"""\
        case "$*" in
          +%u) echo {dow};; +%H%M) echo {hm};; +%H:%M) echo {hm[:2]}:{hm[2:]};; *) exec /bin/date "$@";;
        esac
        """)
    _stub(bin_, "ps", "echo '  4242 /x/.venv/bin/python -m kaibot.engine'\n" if engine else "true\n")
    _stub(bin_, "curl", """\
        cat >/dev/null  # the -K - config with the key headers
        python3 -c 'from datetime import UTC, datetime; import json
        print(json.dumps([{"ts": datetime.now(UTC).isoformat(), "detail": {"state": "market_closed",
              "account_clean": True}}]))'
        """)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ALPACA_", "SUPABASE_", "KAIBOT_"))}
    env.update(PATH=f"{bin_}:/usr/bin:/bin", KAIBOT_ENV_FILE=str(env_file))
    r = subprocess.run(["bash", str(ROOT / "scripts" / "outage_drill.sh"), *args], env=env, capture_output=True,
                       text=True, timeout=60)
    assert SECRET not in r.stdout + r.stderr
    return r


def test_drill_refuses_a_live_broker_url(tmp_path):
    r = _drill(tmp_path, "--dry-run", hm="1800", base="https://example.com/live")
    assert r.returncode == 2 and "paper" in r.stderr.lower()


def test_drill_refuses_during_the_session_without_the_flag(tmp_path):
    r = _drill(tmp_path, "--dry-run", hm="1000")
    assert r.returncode == 2 and "--during-session" in r.stderr
    r = _drill(tmp_path, "--dry-run", hm="1000", dow="6")  # Saturday: not a session
    assert r.returncode == 0


def test_drill_during_session_warns_loudly(tmp_path):
    r = _drill(tmp_path, "--dry-run", "--during-session", hm="1000")
    assert r.returncode == 0 and "DRILL DURING THE SESSION" in r.stdout and "watchdog" in r.stdout
    assert _drill(tmp_path, "--dry-run", "--during-session", hm="1552").returncode == 2  # too close to 15:55


def test_drill_dry_run_prints_the_plan_and_stops_nothing(tmp_path):
    r = _drill(tmp_path, "--dry-run", hm="1800")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "engine: python 4242" in out and "account_clean=true" in out
    assert "kill -KILL 4242" in out and "run_engine.sh --python" in out and "Plan (dry run" in out
    assert "stale beat" in out  # outside the session the watchdog idles: wait for a stale heartbeat instead


def test_drill_refuses_without_a_running_engine(tmp_path):
    assert _drill(tmp_path, hm="1800", engine=False).returncode == 2
