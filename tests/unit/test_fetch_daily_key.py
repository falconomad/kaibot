"""#812 (the PSKY gap): `scripts/fetch_daily.py --key SYMBOL@ASOF` fetches exactly one point-in-time store key, as
of its date, under the daily fetch lock, and only a key `sp500_pit.Membership` names. Offline (`FakeAlpaca`)."""

from __future__ import annotations

import fcntl
import os
import sys

import pytest
from daily_helpers import ROOT, START, FakeAlpaca, api, bars, load_script, run, sessions

from kaibot.ingest import daily_bars as db

sys.path.insert(0, str(ROOT / "scripts"))
import research_runner as rr  # noqa: E402

DAYS = sessions(START)
KEY = "PSKY@2026-11-05"


@pytest.fixture
def alpaca(monkeypatch):
    fake = FakeAlpaca()
    monkeypatch.setattr("urllib.request.urlopen", fake)
    fake.add("SPY", bars(DAYS, [600.0] * len(DAYS)))
    fake.add("PSKY", bars(DAYS, [10.0 + i * 0.01 for i in range(len(DAYS))]), asof="2026-11-05")
    return fake


@pytest.fixture(autouse=True)
def pit(monkeypatch):
    """`run` loads the script afresh; its pit_keys reads the committed membership. Pinned here for the fake keys."""
    import daily_helpers

    real = daily_helpers.load_script

    def load(name="fetch_daily"):
        mod = real(name)
        mod.pit_keys = lambda: {KEY, "PARA@2025-07-31"}
        return mod

    monkeypatch.setattr(daily_helpers, "load_script", load)  # what `run` calls


def store(tmp_path):
    return tmp_path / "data" / "daily"


def test_the_key_is_fetched_as_of_its_date_and_nothing_else(tmp_path, alpaca):
    assert run(tmp_path, "--key", KEY) == 0
    asked = [(s, q.get("asof"), q["adjustment"]) for s, q in alpaca.bar_requests()]
    assert asked == [("PSKY", "2026-11-05", "raw"), ("PSKY", "2026-11-05", "all")], "no SPY, no universe"
    assert db.store_keys(store(tmp_path)) == [KEY]
    meta = db.read_meta(db.bars_path(store(tmp_path), KEY))
    assert meta["asof"] == "2026-11-05" and meta["symbol"] == "PSKY" and meta["rows"] == len(DAYS)


def test_a_second_run_asks_alpaca_for_no_bars(tmp_path, alpaca):
    assert run(tmp_path, "--key", KEY) == 0
    first = db.bars_path(store(tmp_path), KEY).stat().st_mtime_ns
    n = len(alpaca.bar_requests())
    assert run(tmp_path, "--key", KEY, "--retry-holes") == 0
    assert len(alpaca.bar_requests()) == n and db.bars_path(store(tmp_path), KEY).stat().st_mtime_ns == first


def test_a_hole_marker_is_kept_unless_retry_holes(tmp_path, alpaca, capsys):
    alpaca.status["PSKY"] = 422
    assert run(tmp_path, "--key", KEY, "--no-actions") == 0  # (with actions: 1, no key has bars)
    marker = db.bars_path(store(tmp_path), KEY).with_suffix(".empty")
    assert marker.exists()
    del alpaca.status["PSKY"]
    assert run(tmp_path, "--key", KEY, "--no-actions") == 0
    assert "hole marker (http_422); kept" in capsys.readouterr().out and not db.store_keys(store(tmp_path))
    assert run(tmp_path, "--key", KEY, "--retry-holes") == 0
    assert "asked again" in capsys.readouterr().out
    assert db.store_keys(store(tmp_path)) == [KEY] and not marker.exists()
    assert "PSKY" not in (store(tmp_path) / "holes.csv").read_text()


@pytest.mark.parametrize("args, why", [
    (["--key", "PSKY@2026-11-04"], "not a key of the point-in-time universe"),
    (["--key", "PSKY@2026-11-11"], "not a key of the point-in-time universe"),
    (["--key", "PSKY"], "SYMBOL@YYYY-MM-DD"),
    (["--key", "PSKY@soon"], "SYMBOL@YYYY-MM-DD"),
    (["--key", "../x@2026-11-05"], "SYMBOL@YYYY-MM-DD"),
    (["--key", "PSKY@2015-06-01"], "between"),
    (["--key", KEY, "--symbols", "AAA"], "exactly those keys"),
    (["--key", KEY, "--no-universe"], "exactly those keys"),
])
def test_anything_else_is_refused_before_any_request(tmp_path, alpaca, capsys, args, why):
    with pytest.raises(SystemExit) as e:
        run(tmp_path, *args)
    assert e.value.code not in (0, None) and why in str(e.value.code)
    assert alpaca.requests == [] and not (tmp_path / "data").exists()


def test_a_fetch_holding_the_lock_means_nothing_is_asked(tmp_path, alpaca, capsys):
    (tmp_path / "data").mkdir()
    fd = os.open(tmp_path / "data" / "fetch_daily.lock", os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert run(tmp_path, "--key", KEY) == 1
    finally:
        os.close(fd)
    assert "Another daily fetch is running" in capsys.readouterr().out and alpaca.requests == []


def test_the_lock_is_the_one_next_to_the_linked_store(tmp_path, alpaca):
    """The research runner's data/daily is a link into the engine's data/: the lock is the engine's file, the one
    scripts/fetch_daily.sh takes."""
    engine = tmp_path / "engine" / "data"
    (engine / "daily").mkdir(parents=True)
    runner = tmp_path / "runner" / "data"
    runner.mkdir(parents=True)
    (runner / "daily").symlink_to(engine / "daily")
    fd = os.open(engine / "fetch_daily.lock", os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert load_script().main(["--data", str(runner), "--start", START, "--key", KEY], api=api()) == 1
    finally:
        os.close(fd)
    assert alpaca.requests == []
    assert load_script().main(["--data", str(runner), "--start", START, "--key", KEY, "--no-actions"],
                              api=api()) == 0
    assert db.store_keys(engine / "daily") == [KEY]


def test_the_committed_membership_names_psky_from_its_own_listing_only():
    """PARA -> PSKY was a merger into a new company (Paramount Skydance, new CUSIP, 7 Aug 2025), not a rename: the
    committed data keeps Paramount Global (CBS -> VIAC -> PARA, ticker_map renames) and PSKY apart, so PSKY's key is
    asked as of a date inside its own life and its bars begin with its own listing."""
    from kaibot.ingest import sp500_pit as pit

    m = pit.Membership.load()
    assert m.keys("PSKY") == [KEY] and m.keys("PARA") == ["PARA@2025-07-31"]
    assert [(s.start.isoformat(), s.tickers) for s in m.spells["PSKY"]] == [("2025-08-08", ("PSKY",))]
    assert m.spells["PARA"][-1].tickers == ("CBS", "VIAC", "PARA") and m.spells["PARA"][-1].end.isoformat() == \
        "2025-08-08"
    keys = load_script().pit_keys()  # the real one (this module's load_script is not the pinned one)
    assert KEY in keys and "PARA@2025-07-31" in keys and "PSKY" not in keys


# ---------------------------------------------------------------- the research runner's allow-list and queue


def test_fetch_daily_is_allowed_one_pit_key_at_a_time_only():
    """#812: the runner may fetch named SYMBOL@ASOF keys (the script also refuses a key the pit universe does not
    name), never the whole universe, a symbols file, another store or a refresh, and not faster than 40 a minute."""
    spec = rr.load_allowed().specs["scripts/fetch_daily.py"]
    assert set(spec.flags) == {"--key", "--retry-holes", "--per-min", "--status"} and spec.heavy and not spec.writes
    assert rr.check_args(spec, ["--key", "PSKY@2026-11-05", "--retry-holes", "--per-min", "40"])
    for bad in (["--key", "PSKY"], ["--key", "psky@2026-11-05"], ["--key", "../x@2026-11-05"],
                ["--key", "PSKY@2026-13-05"], ["--per-min", "41"], ["--per-min", "120"], ["--symbols", "AAPL"],
                ["--symbols-file", "x.csv"], ["--refresh"], ["--no-universe"], ["--start", "2016-01-01"]):
        with pytest.raises(rr.JobError):
            rr.check_args(spec, bad)


def test_the_psky_job_is_queued_with_the_key_the_membership_names():
    cfg = rr.load_allowed()
    jobs = [rr.check_job(j, cfg) for j in rr.load_queue() if j["id"] == "daily-key-psky-2026-11-27"]
    assert len(jobs) == 1 and jobs[0].args[:2] == ("--key", KEY) and "--retry-holes" in jobs[0].args
    assert KEY in load_script().pit_keys()
