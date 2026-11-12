import io
import random
import threading
import urllib.error
from bisect import bisect_right

import pytest

from kaibot.common import http
from kaibot.common.ratelimit import Budget, BudgetSpec, Registry, TokenBucket, load_specs


class FakeClock:
    """Monotonic fake: sleep advances time instantly (thread-safe), so no test ever really sleeps."""

    def __init__(self, t: float = 0.0):
        self.t = t
        self.sleeps: list[float] = []
        self._lock = threading.Lock()

    def monotonic(self):
        with self._lock:
            return self.t

    def sleep(self, s):
        with self._lock:
            self.sleeps.append(s)
            self.t += s


def _max_in_window(times: list[float], per: float) -> int:
    ts = sorted(times)
    # count grants in the half-open window [t, t+per) starting at each grant
    return max(bisect_right(ts, t + per - 1e-9) - i for i, t in enumerate(ts))


def test_bucket_allows_capacity_then_blocks():
    b = TokenBucket(capacity=3, per_seconds=60, clock=FakeClock())
    assert all(b.try_acquire() for _ in range(3))
    assert not b.try_acquire()


def test_bucket_rejects_bad_config():
    with pytest.raises(ValueError):
        TokenBucket(capacity=0, per_seconds=1)


def test_sliding_window_never_exceeds_capacity_single_thread():
    clock = FakeClock()
    b = Budget("x", BudgetSpec(((5, 1.0), (150, 60.0))), clock)
    grants = [b.acquire() for _ in range(400)]
    assert _max_in_window(grants, 1.0) <= 5
    assert _max_in_window(grants, 60.0) <= 150
    assert grants == sorted(grants)


def test_min_interval_spacing():
    clock = FakeClock(100.0)
    b = Budget("gdelt", BudgetSpec(min_interval=6.0), clock)
    grants = [b.acquire() for _ in range(4)]
    assert grants == [100.0, 106.0, 112.0, 118.0]
    assert clock.sleeps == [6.0, 6.0, 6.0]


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_budget_never_exceeded_under_concurrent_threads(seed):
    rnd = random.Random(seed)
    clock = FakeClock()
    spec = BudgetSpec(((5, 1.0), (40, 60.0)), min_interval=0.05)
    b = Budget("sec", spec, clock)
    grants: list[float] = []
    lock = threading.Lock()
    start = threading.Barrier(16)

    def worker():
        start.wait()
        for _ in range(25):
            t = b.acquire()
            with lock:
                grants.append(t)
            if rnd.random() < 0.2:
                clock.sleep(rnd.random())  # jitter, like real work between calls

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=20)
    assert len(grants) == 400
    assert _max_in_window(grants, 1.0) <= 5
    assert _max_in_window(grants, 60.0) <= 40
    s = sorted(grants)
    assert min(b - a for a, b in zip(s, s[1:], strict=False)) >= 0.05 - 1e-9


def test_backoff_holds_all_callers():
    clock = FakeClock()
    b = Budget("a", BudgetSpec(((100, 60.0),)), clock)
    b.acquire()
    b.backoff(8)
    assert b.acquire() == 8.0


def test_load_specs_parses_all_windows(tmp_path):
    f = tmp_path / "s.yaml"
    f.write_text(
        "market_data:\n"
        "  alpaca: {use: primary, budget_per_min: 150}\n"
        "  tiingo: {budget_per_hour: 40}\n"
        "filings:\n"
        "  sec_edgar: {budget_per_s: 5}\n"
        "  gdelt_doc: {min_seconds_between_requests: 6, poll_seconds: 900}\n"
        "  yfinance: {use: offline}\n"
    )
    s = load_specs(f)
    assert s["alpaca"].windows == ((150, 60.0),)
    assert s["tiingo"].windows == ((40, 3600.0),)
    assert s["sec_edgar"].windows == ((5, 1.0),)
    assert s["gdelt_doc"].min_interval == 6.0 and s["gdelt_doc"].windows == ()
    assert s["yfinance"].unlimited


@pytest.mark.parametrize("bad", ["budget_per_min: 0", "budget_per_min: -3", "budget_per_s: 2.5",
                                 "budget_per_min: lots", "min_seconds_between_requests: -1", "budget_per_s: true"])
def test_load_specs_rejects_bad_budgets(tmp_path, bad):
    f = tmp_path / "s.yaml"
    f.write_text(f"sec:\n  x: {{{bad}}}\n")
    with pytest.raises(ValueError, match="sec.x"):
        load_specs(f)


def test_repo_sources_yaml_has_budgets_for_every_source_we_call():
    s = load_specs()
    for name in ("alpaca", "alpaca_trading", "sec_edgar", "fred", "finnhub", "finra_regsho", "gdelt_doc"):
        assert not s[name].unlimited, name
    assert s["sec_edgar"].windows[0][0] <= 10  # SEC fair access: max 10 req/s
    assert s["gdelt_doc"].min_interval >= 5  # GDELT: one request every 5 s


def test_registry_shares_and_tightens():
    reg = Registry(loader=lambda: {"alpaca": BudgetSpec(((150, 60.0),))}, clock=FakeClock())
    assert reg.get("alpaca") is reg.get("alpaca")
    with pytest.raises(KeyError):
        reg.get("nope")
    t = reg.tighten("alpaca", BudgetSpec(((60, 60.0),)))
    assert t is reg.get("alpaca") and set(t.spec.windows) == {(150, 60.0), (60, 60.0)}


# --- common.http ------------------------------------------------------------------------------------------


class _Resp(io.BytesIO):
    def __init__(self, body, status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener(script, seen):
    def op(req, timeout):
        seen.append((req.full_url, timeout))
        step = script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    return op


def _e(code, headers=None):
    return urllib.error.HTTPError("https://x", code, "nope", headers or {}, io.BytesIO(b""))


def test_http_429_backs_off_with_retry_after_and_retries():
    clock = FakeClock()
    b = Budget("gdelt_doc", BudgetSpec(min_interval=6.0), clock)
    seen = []
    op = _opener([_e(429, {"Retry-After": "30"}), _e(429), _Resp(b'{"a": 1}')], seen)
    assert http.get_json("https://api.example/x", source="gdelt_doc", budget=b, opener=op) == {"a": 1}
    assert len(seen) == 3
    assert clock.t == 30 + 6  # Retry-After 30 s (> 1 s backoff), then 2 s backoff < 6 s gap


def test_http_refuses_plain_http_and_redacts_keys():
    with pytest.raises(ValueError):
        http.get("http://example.com/x", source="x", budget=Budget("x", BudgetSpec(), FakeClock()))
    err = http.HttpError(403, "https://api.stlouisfed.org/fred?series_id=V&api_key=SECRET123")
    assert "SECRET123" not in str(err) and "series_id=V" in str(err)


def test_http_size_limit_and_4xx():
    b = Budget("x", BudgetSpec(), FakeClock())
    with pytest.raises(http.ResponseTooLarge):
        http.get("https://e/x", source="x", budget=b, max_bytes=10, opener=_opener([_Resp(b"y" * 11)], []))
    with pytest.raises(http.ResponseTooLarge):
        big = _Resp(b"", headers={"Content-Length": "999999"})
        http.get("https://e/x", source="x", budget=b, max_bytes=10, opener=_opener([big], []))
    with pytest.raises(http.HttpError) as e:
        http.get("https://e/x", source="x", budget=b, opener=_opener([_e(403)], []))
    assert e.value.status == 403
    r = http.get("https://e/x", source="x", budget=b, ok_statuses=(404,), opener=_opener([_e(404)], []))
    assert r.status == 404 and r.body == b""


def test_http_gives_up_after_attempts():
    clock = FakeClock()
    b = Budget("x", BudgetSpec(), clock)
    with pytest.raises(http.HttpError) as e:
        http.get("https://e/x", source="x", budget=b, opener=_opener([_e(502)] * 5, []))
    assert e.value.status == 0 and clock.sleeps == [1, 2, 4, 8, 16]


# --- S74d (#154): a slot that comes too late is not booked ---

def test_reserve_with_latest_books_nothing_when_the_slot_is_too_late():
    clock = FakeClock(0.0)
    b = Budget("t", BudgetSpec(((2, 10.0),)), clock)
    assert b.reserve() == 0.0 and b.reserve() == 0.0
    assert b.reserve(latest=5.0) is None  # the next slot is at 10.0
    assert b.reserve(latest=5.0) is None
    assert b.reserve() == 10.0  # not 20.0: the refused reservations left no grant behind
    assert b.reserve(latest=10.0) == 10.0  # a slot at exactly `latest` is booked
