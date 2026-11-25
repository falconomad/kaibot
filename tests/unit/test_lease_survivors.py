"""#546 (weekly mutation testing): tests for surviving mutants of `engine/lease.py` that change behaviour.

- `holder_id`: a data folder whose parents do not exist yet still keeps the holder id (a restart gets its own lease
  back at once instead of waiting out the TTL);
- a lease that was never taken is not held, not lost, has no age, and neither renews nor releases (a release of a
  lease this engine never held must not reach the database);
- the "another engine holds the lease" message names the other engine's host, start and time left, and says "?"
  when the database does not give the time left.
"""

from __future__ import annotations

import pytest

from kaibot.engine import lease as L


class Store:
    def __init__(self, acquire=None) -> None:
        self.calls: list[str] = []
        self._acquire = acquire or {"acquired": True, "host": "mac"}

    def lease_acquire(self, holder, host, ttl, machine):
        self.calls.append("acquire")
        return self._acquire

    def lease_renew(self, holder, ttl, machine, epoch=None):
        self.calls.append("renew")
        return True

    def lease_release(self, holder, machine, epoch=None):
        self.calls.append("release")
        return True


def _lease(store):
    t = [1000.0]
    return L.EngineLease(store, "h", "mac", mono=lambda: t[0], wall=lambda: t[0], machine="m1"), t


def test_holder_id_is_kept_in_a_data_folder_whose_parents_do_not_exist_yet(tmp_path):
    data = tmp_path / "not" / "there" / "yet"
    first = L.holder_id(data, "mac")
    assert (data / L.HOLDER_FILE).read_text().strip() == first
    assert L.holder_id(data, "mac") == first  # the restart finds it


def test_a_lease_never_taken_is_neither_held_nor_lost_and_has_no_age():
    lease, _ = _lease(Store())
    assert lease.status() == "none"
    assert lease.ok is False and lease.reducing_only is False
    assert lease.age_s() == float("inf")
    assert lease.epoch is None and lease.last_error is None


def test_a_lease_never_taken_does_not_renew_or_release_at_the_database():
    store = Store()
    lease, _ = _lease(store)
    assert lease.renew() is False
    assert lease.renew(force=True) is False
    assert lease.release() is False
    assert store.calls == []


def test_a_released_lease_is_not_released_twice():
    store = Store()
    lease, _ = _lease(store)
    lease.acquire()
    assert lease.release() is True
    assert lease.release() is False
    assert store.calls == ["acquire", "release"]
    assert lease.status() == "none"


def test_the_held_message_names_the_other_engines_host_start_and_time_left():
    store = Store({"acquired": False, "host": "other-mac", "holder": "someone-else",
                   "started_at": "2026-11-24T14:30:00Z", "expires_in_s": 42})
    lease, _ = _lease(store)
    with pytest.raises(L.LeaseHeld) as e:
        lease.acquire()
    msg = str(e.value)
    assert "Another Kaibot engine holds the lease" in msg
    assert "'other-mac'" in msg and "started 2026-11-24T14:30:00Z," in msg and "about 42 s more" in msg
    assert lease.held is False and lease.status() == "none"


def test_the_held_message_says_question_mark_when_the_time_left_is_unknown():
    store = Store({"acquired": False, "host": "other-mac", "holder": "someone-else", "started_at": "t0"})
    lease, _ = _lease(store)
    with pytest.raises(L.LeaseHeld) as e:
        lease.acquire()
    assert "about ? s more" in str(e.value)


class Journal:
    """The two calls the lease makes on the engine's journal."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, dict]] = []

    def append(self, kind, payload):
        self.rows.append((kind, payload))

    def entries(self, kinds=()):
        return [(i, 0, k, p) for i, (k, p) in enumerate(self.rows) if not kinds or k in kinds]


def _journalled(acquire):
    j = Journal()
    t = [1000.0]
    lease = L.EngineLease(Store(acquire), "h", "mac", mono=lambda: t[0], wall=lambda: t[0], journal=j, machine="m1")
    return lease, j


def test_a_takeover_is_journalled_as_acquired_with_the_previous_host():
    lease, j = _journalled({"acquired": True, "host": "mac", "taken_over": True, "previous_host": "old-mac"})
    lease.acquire()
    assert j.rows == [(L.LEASE_KIND, {"state": "acquired", "holder": "h", "host": "mac", "machine": "m1",
                                      "taken_over": True, "previous_host": "old-mac"})]
    assert lease.last_record()["state"] == "acquired"
    assert lease.degraded_allowed()[0] is True


def test_a_plain_acquire_is_journalled_without_a_previous_host():
    lease, j = _journalled({"acquired": True, "host": "mac"})
    lease.acquire()
    assert j.rows[-1][1] == {"state": "acquired", "holder": "h", "host": "mac", "machine": "m1", "taken_over": False}


def test_a_lease_held_by_another_engine_is_journalled_and_blocks_a_degraded_start():
    lease, j = _journalled({"acquired": False, "host": "other-mac", "holder": "x"})
    with pytest.raises(L.LeaseHeld):
        lease.acquire()
    assert j.rows == [(L.LEASE_KIND, {"state": "held_by_other", "holder": "h", "host": "mac", "machine": "m1",
                                      "other_host": "other-mac"})]
    assert lease.degraded_allowed()[0] is False


def test_a_clean_release_is_journalled():
    lease, j = _journalled({"acquired": True, "host": "mac"})
    lease.acquire()
    lease.release()
    assert [p["state"] for _, p in j.rows] == ["acquired", "released"]


@pytest.mark.parametrize("answer, own_holder", [
    ({"acquired": False, "host": "b", "holder": "someone-else", "reason": "holder_on_other_machine"}, True),
    ({"acquired": False, "host": "b", "holder": "h"}, True),  # this install's holder id, no reason (older SQL)
    ({"acquired": False, "host": "b", "holder": "someone-else", "reason": "held"}, False),
    ({"acquired": False, "host": "b", "holder": "someone-else"}, False),
])
def test_the_held_message_says_whether_the_holder_id_is_this_installs(answer, own_holder):
    lease, _ = _lease(Store({**answer, "started_at": "t0", "expires_in_s": 7}))
    with pytest.raises(L.LeaseHeld) as e:
        lease.acquire()
    msg = str(e.value)
    assert ("held by THIS install's holder id" in msg) is own_holder
    assert ("Another Kaibot engine holds the lease" in msg) is not own_holder
    assert "'b'" in msg and "started t0," in msg and "about 7 s more" in msg


def test_the_own_holder_message_says_question_mark_when_the_time_left_is_unknown():
    lease, _ = _lease(Store({"acquired": False, "host": "b", "holder": "h", "started_at": "t0"}))
    with pytest.raises(L.LeaseHeld) as e:
        lease.acquire()
    assert "about ? s more" in str(e.value)


def test_a_lost_lease_is_no_longer_held():
    class Gone(Store):
        def lease_renew(self, holder, ttl, machine, epoch=None):
            return False

    lease, _ = _lease(Gone())
    lease.acquire()
    with pytest.raises(L.LeaseLost):
        lease.renew(force=True)
    assert lease.held is False and lease.lost is True and lease.status() == "lost"


def test_the_linux_machine_id_is_read_from_the_first_file_that_has_one(tmp_path, monkeypatch):
    empty, real = tmp_path / "empty", tmp_path / "machine-id"
    empty.write_text("\n")
    real.write_text("abc123\n")
    monkeypatch.setattr(L.platform, "system", lambda: "Linux")
    monkeypatch.setattr(L, "MACHINE_ID_FILES", (str(tmp_path / "missing"), str(empty), str(real)))
    assert L.os_machine_id() == "abc123"
    monkeypatch.setattr(L, "MACHINE_ID_FILES", (str(tmp_path / "missing"), str(empty)))
    assert L.os_machine_id() == ""


def test_the_mac_machine_id_is_the_platform_uuid(monkeypatch):
    seen = []

    def run(cmd, **kw):
        seen.append(cmd)
        return type("R", (), {"stdout": '  "IOPlatformUUID" = "1234-ABCD"\n'})()

    monkeypatch.setattr(L.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(L.subprocess, "run", run)
    monkeypatch.setattr(L, "MACHINE_ID_FILES", ())
    assert L.os_machine_id() == "1234-ABCD"
    assert seen and seen[0][0] == "ioreg"


def test_a_lost_lease_is_journalled_as_lost_and_blocks_a_degraded_start():
    class Gone(Store):
        def lease_renew(self, holder, ttl, machine, epoch=None):
            return None

    j = Journal()
    t = [1000.0]
    lease = L.EngineLease(Gone({"acquired": True, "host": "mac"}), "h", "mac", mono=lambda: t[0], wall=lambda: t[0],
                          journal=j, machine="m1")
    lease.acquire()
    with pytest.raises(L.LeaseLost):
        lease.renew(force=True)
    assert [p["state"] for _, p in j.rows] == ["acquired", "lost"]
    assert j.rows[-1][1] == {"state": "lost", "holder": "h", "host": "mac", "machine": "m1"}
    ok, why = lease.degraded_allowed()
    assert ok is False and "'lost'" in why
