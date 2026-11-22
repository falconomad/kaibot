"""scripts/preview_next_send.py (QA-Ops, follow-up of M9 1.3 / 1.4): a read-only preview of the next send window.

The preview is checked against what the live daily path then really does on `SimBroker` (the harness of
test_m9_late_decision.py): the same kind of send and, per stock, the same shares. And it must be harmless: the
journal file is not changed by a byte, no entry is added, and the broker is never used."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from datetime import date, datetime, time
from pathlib import Path

import pytest
from test_live_daily import POL, RID, _at
from test_m9_late_decision import Days, S, first_buy_day
from test_m9_late_send import QuotedDays, close_day, open_day, orders_on, window

from kaibot.engine import daily_report as dr
from kaibot.engine import live_daily as ld
from kaibot.engine.daily import DailyData, momentum_limits

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("preview_next_send", ROOT / "scripts" / "preview_next_send.py")
pv = importlib.util.module_from_spec(spec)
sys.modules["preview_next_send"] = pv
spec.loader.exec_module(pv)


@pytest.fixture(scope="module")
def data() -> DailyData:
    m, _ = dr.load_fixture(dr.FIXTURE)
    return DailyData.from_market(m)


@pytest.fixture(scope="module")
def fill(data, tmp_path_factory) -> int:
    return first_buy_day(data, tmp_path_factory.mktemp("probe"))


def digest(h) -> tuple:
    """The journal as it is on disk and in the engine: the file, its write-ahead log, and its last entry. SQLite's
    lock table (`-shm`) is left out: every reader of a running engine's journal marks its place there; it holds no
    entry."""
    files = sorted(p for p in h.path.parent.iterdir()
                   if p.name.startswith(h.path.name) and not p.name.endswith("-shm"))
    return [(p.name, hashlib.sha256(p.read_bytes()).hexdigest()) for p in files], h.journal.last_seq()


def look(h, data, day: date, at: time = time(12, 0), **kw) -> dict:
    """The preview at `at` ET of `day`, on the harness's journal file while its engine has it open. Asserts that
    nothing changed."""
    before = digest(h)
    rule = ld.Rule(S, ld.live_builder(S, momentum_limits(S, POL, 100.0)))
    kw.setdefault("load", lambda k, now: ld.as_of(data, data.index_of(k)))
    p = pv.preview(h.path, h.path.parent, now=_at(day, at), rules=[rule], **kw)
    assert digest(h) == before
    assert p["run_id"] == RID and p["session"] == str(day)
    return p


def planned_orders(p: dict) -> list[tuple]:
    (r,) = p["rules"]
    return sorted((x["symbol"], "buy" if x["buy"] else "sell", x["buy"] or x["sell"])
                  for x in r["lines"] if x["buy"] or x["sell"])


def real_orders(h, day) -> list[tuple]:
    return sorted((o["symbol"], o["side"], float(o["qty"])) for o in orders_on(h, day))


def test_a_fill_session_is_previewed_as_the_decisions_own_send_with_the_orders_that_then_go_out(data, fill, tmp_path):
    h = Days(data, tmp_path)
    h.run(fill - 1)
    day = data.day(fill)
    p = look(h, data, day)
    (r,) = p["rules"]
    assert r["kind"] == "decision's own send" and p["not_written"] == [] and r["lines"]
    assert all(x["held"] == 0 and x["buy"] == x["target"] for x in r["lines"])
    open_day(h, fill)
    window(h, fill)
    assert planned_orders(p) == real_orders(h, day) and real_orders(h, day)
    text = pv.render(p)
    assert "THE DECISION'S OWN SEND" in text and "Nothing is written and nothing is sent" in text
    assert all(x["symbol"] in text for x in r["lines"])


def test_a_missed_window_is_previewed_as_a_late_send_with_the_orders_that_then_go_out(data, fill, tmp_path):
    h = Days(data, tmp_path, no_window={fill})
    h.run(fill)
    day, nxt = data.day(fill), data.day(fill + 1)
    p = look(h, data, nxt)
    (r,) = p["rules"]
    assert (r["kind"], r["late_of"]) == ("late send", str(day))
    assert r["latest_decision"]["fill_session"] == str(day) and r["latest_decision"]["sent_on"] is None
    open_day(h, fill + 1)
    window(h, fill + 1)
    assert planned_orders(p) == real_orders(h, nxt) and real_orders(h, nxt)
    assert "LATE SEND" in pv.render(p) and "NOT sent" in pv.render(p)
    close_day(h, fill + 1)
    after = look(h, data, data.day(fill + 2))  # sent once: the session after has nothing left to do
    assert after["rules"][0]["kind"] == "nothing" and after["rules"][0]["lines"] == []
    assert after["rules"][0]["latest_decision"] == {"session": str(data.day(fill - 1)), "fill_session": str(day),
                                                    "sent_on": str(nxt), "late_of": str(day)}


def test_a_buy_still_short_is_previewed_as_a_carry_over_at_the_plans_own_shares(data, fill, tmp_path):
    """A buy the pre-send check withheld on the fill session is short of its target: the next window carries it."""
    probe = Days(data, tmp_path / "a")
    probe.run(fill)
    day, nxt = data.day(fill), data.day(fill + 1)
    short = sorted(o["symbol"] for o in orders_on(probe, day))[0]
    h = QuotedDays(data, tmp_path / "b")
    h.stale = {(str(day), short)}
    h.run(fill)
    p = look(h, data, nxt)
    (r,) = p["rules"]
    assert (r["kind"], r["carry_of"]) == ("carry-over", str(day))
    (line,) = r["lines"]
    assert (line["symbol"], line["purpose"], line["held"], line["buy"]) == (short, "carry", 0.0, line["target"])
    open_day(h, fill + 1)
    window(h, fill + 1)
    assert planned_orders(p) == real_orders(h, nxt) == [(short, "buy", float(line["target"]))]
    assert "CARRY-OVER" in pv.render(p) and "never above the target" in pv.render(p)


def test_a_decision_the_engine_has_not_taken_yet_is_previewed_and_not_written(data, fill, tmp_path):
    """The engine was off after the last close (M9 1.3): the preview takes the decision in memory, says so, and
    the journal still has no record of it."""
    h = Days(data, tmp_path, late={fill})
    h.run(fill - 1)
    day = data.day(fill)
    assert not [b for b in h.decisions() if b["fill_session"] == str(day)]
    p = look(h, data, day, time(10, 0))
    (r,) = p["rules"]
    assert r["kind"] == "decision's own send" and [k for k, _ in p["not_written"]] == ["md_decision"]
    assert any("previewed here, not written" in n for n in r["notes"])
    assert not [b for b in h.decisions() if b["fill_session"] == str(day)]
    on_time = Days(data, tmp_path / "b")
    on_time.run(fill)
    assert planned_orders(p) == real_orders(on_time, day)


def test_without_the_previous_close_in_the_daily_store_the_preview_says_so_and_plans_nothing(data, fill, tmp_path):
    h = Days(data, tmp_path, late={fill})
    h.run(fill - 1)

    def load(k, now):
        raise ld.DataNotReady(f"the daily store has no SPY bar for {k}")

    p = look(h, data, data.day(fill), time(10, 0), load=load)
    (r,) = p["rules"]
    assert r["kind"] == "no decision" and r["lines"] == []
    assert "no SPY bar" in " ".join(r["notes"]) and "NO DECISION" in pv.render(p)


def test_the_default_session_is_today_before_the_window_ends_and_the_next_session_after(data, fill, tmp_path):
    h = Days(data, tmp_path)
    h.run(fill - 1)
    day = data.day(fill)
    rule = ld.Rule(S, ld.live_builder(S, momentum_limits(S, POL, 100.0)))
    kw = {"rules": [rule], "load": lambda k, now: ld.as_of(data, data.index_of(k))}
    assert pv.preview(h.path, tmp_path, now=_at(day, time(15, 56)), **kw)["session"] == str(day)
    assert pv.preview(h.path, tmp_path, now=_at(day, time(15, 57)), **kw)["session"] == str(data.day(fill + 1))
    sat = date(2023, 2, 4)
    assert pv.preview(h.path, tmp_path, now=_at(sat, time(11, 0)), **kw)["session"] == "2023-02-06"
    assert pv.preview(h.path, tmp_path, now=_at(sat, time(11, 0)), session=day, **kw)["session"] == str(day)


def test_the_journal_is_opened_read_only_and_the_broker_cannot_be_used(data, fill, tmp_path):
    h = Days(data, tmp_path)
    h.run(fill - 1)
    before = digest(h)
    j = pv.ReadOnlyJournal(h.path)
    with pytest.raises(Exception, match="readonly|read-only"):
        j.db.execute("insert into entry (ts, kind, body) values ('x', 'x', '{}')")
    n = len(j.entries())
    assert j.append("md_send", {"at": datetime(2023, 2, 1)}) == j.last + 1  # kept in memory, read back, not on disk
    assert len(j.entries()) == n + 1 and j.entries(kinds=("md_send",))[-1][3] == {"at": "2023-02-01 00:00:00"}
    j.close()
    assert digest(h) == before
    with pytest.raises(RuntimeError, match="never talks to the broker"):
        getattr(pv.NoBroker(), "submit_order")  # noqa: B009
    with pytest.raises(FileNotFoundError):
        pv.ReadOnlyJournal(tmp_path / "nowhere.sqlite")
    assert not (tmp_path / "nowhere.sqlite").exists()  # a wrong path creates no file


def test_the_command_reads_a_stopped_engines_journal_and_prints_plain_words(data, fill, tmp_path, capsys, monkeypatch):
    """The command line on a journal whose engine has stopped (the file closed), with the repository's own rules and
    an empty daily store: it says what it can and what it cannot know, writes nothing and exits 0."""
    h = Days(data, tmp_path / "live", no_window={fill})
    h.run(fill)
    h.journal.close()
    files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (tmp_path / "live").iterdir()}
    env = tmp_path / ".env"
    env.write_text(f"KAIBOT_DATA_DIR={tmp_path / 'live'}\nKAIBOT_DAILY_DATA={tmp_path / 'store'}\n"
                   "ALPACA_API_KEY=never-used\n")
    for k in ("KAIBOT_DATA_DIR", "KAIBOT_DAILY_DATA", "KAIBOT_ENV_FILE", "KAIBOT_MULTIDAY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ALPACA_API_KEY", "")  # the env file's keys are not needed and stay unset by the run
    now = _at(data.day(fill + 1), time(12, 0)).isoformat()
    assert pv.main(["--env", str(env), "--now", now]) == 0
    out = capsys.readouterr().out
    assert f"session {data.day(fill + 1)}" in out and "Nothing is written and nothing is sent" in out
    assert "trend_momentum:" in out and "Not known offline" in out and "KAIBOT_MULTIDAY is not on" in out
    assert "never-used" not in out
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (tmp_path / "live").iterdir()} == files
    assert not (tmp_path / "store").exists()
    assert pv.main(["--journal", str(tmp_path / "none.sqlite")]) == 2
    assert "no journal at" in capsys.readouterr().err
