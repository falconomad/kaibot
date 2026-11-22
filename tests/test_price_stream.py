"""scripts/price_stream.py (the dashboard's streamed prices): no network, no Supabase, no Alpaca.

Throttling and coalescing, market-hours gating, the 30-symbol selection, the reconnect backoff, the 405 and 406
answers, the paper-only rule, the websocket framing, and that the relay can only GET two paper paths."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("price_stream", ROOT / "scripts" / "price_stream.py")
ps = importlib.util.module_from_spec(_spec)
sys.modules["price_stream"] = ps
_spec.loader.exec_module(ps)


# ---------------------------------------------------------------- fakes


class Clock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


class FakeHttp:
    def __init__(self, is_open=True, positions=None):
        self.is_open, self.positions = is_open, positions if positions is not None else [
            {"symbol": "XOM", "market_value": "3173.6", "asset_class": "us_equity"}]
        self.gets: list[str] = []
        self.broadcasts: list[dict] = []
        self.beats: list[dict] = []
        self.fail_broadcast = False

    def alpaca_get(self, path):
        self.gets.append(path)
        if path == "/v2/clock":
            return {"is_open": self.is_open, "next_close": "2099-01-01T16:00:00-05:00"}
        return self.positions

    def broadcast(self, payload):
        if self.fail_broadcast:
            raise OSError("down")
        self.broadcasts.append(payload)
        return 202

    def heartbeat(self, detail):
        self.beats.append(detail)
        return 201


class FakeWs:
    """Answers the handshake, then plays `script` (lists of messages, one list per recv)."""

    def __init__(self, clock: Clock, script=None, auth=None):
        self.clock = clock
        self.queue = [[{"T": "success", "msg": "connected"}],
                      auth or [{"T": "success", "msg": "authenticated"}], *(script or [])]
        self.sent: list[dict] = []
        self.closed = False
        self.pings = 0

    def recv(self, timeout):
        if self.queue:
            return [json.dumps(self.queue.pop(0))]
        self.clock.t += timeout
        return []

    def send_text(self, text):
        self.sent.append(json.loads(text))

    def ping(self):
        self.pings += 1

    def close(self):
        self.closed = True


def relay(http=None, clock=None, sockets=None):
    clock = clock or Clock()
    made: list = []

    def connect():
        if sockets is None:
            ws = FakeWs(clock)
        else:
            nxt = sockets.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            ws = nxt
        made.append(ws)
        return ws

    r = ps.Relay(http or FakeHttp(), "KEY", "SECRET", connect=connect, mono=clock, sleep=clock.sleep, host="test")
    return r, clock, made


# ---------------------------------------------------------------- throttling and coalescing


def test_at_most_one_snapshot_a_second_and_only_after_a_change():
    c = ps.Coalescer(gap=1.0)
    assert c.take(0.0) is None                     # nothing yet
    c.trade("XOM", 158.6)
    c.trade("XOM", 158.7)
    c.trade("TMO", 686.0)
    assert c.take(0.0) == {"TMO": 686.0, "XOM": 158.7}  # many trades, one message with the last prices
    c.trade("XOM", 158.8)
    assert c.take(0.5) is None                     # inside the second
    assert c.take(1.0) == {"TMO": 686.0, "XOM": 158.8}
    c.trade("XOM", 158.8)                          # the same price again is no change
    assert c.take(5.0) is None


def test_bad_prices_are_ignored_and_a_sold_stock_is_forgotten():
    c = ps.Coalescer()
    for p in (0, -1, float("nan"), "158", None, True):
        c.trade("XOM", p)
    assert c.take(0.0) is None
    c.trade("XOM", 1.0)
    c.trade("TMO", 2.0)
    c.keep(["TMO"])
    assert c.take(0.0) == {"TMO": 2.0}


def test_the_relay_publishes_once_per_second_with_the_payload_the_page_reads():
    http = FakeHttp()
    trades = [[{"T": "t", "S": "XOM", "p": 158.6 + i / 100}] for i in range(10)]
    r, clock, _ = relay(http)
    r.tick()  # clock, positions, connect + auth + subscribe
    for msg in trades:
        r.ws.queue.append(msg)
    for _ in range(10):
        r.tick()
        clock.t += 0.25
    assert 2 <= len(http.broadcasts) <= 4         # 10 trades over ~2.5 s -> one message a second at most
    p = http.broadcasts[-1]
    assert set(p) == {"as_of", "feed", "prices"} and p["feed"] == "iex"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", p["as_of"])
    assert list(p["prices"]) == ["XOM"]


def test_no_price_from_before_a_gap_or_an_earlier_session_is_broadcast_again():
    held = [{"symbol": s, "market_value": "1000", "asset_class": "us_equity"} for s in ("AAA", "BBB")]
    http = FakeHttp(positions=held)
    r, clock, _ = relay(http)
    r.tick()
    r.ws.queue.append([{"T": "t", "S": "AAA", "p": 10.0}, {"T": "t", "S": "BBB", "p": 20.0}])
    r.tick()
    assert http.broadcasts[-1]["prices"] == {"AAA": 10.0, "BBB": 20.0}
    http.is_open = False  # the close: the stream ends
    clock.t += 61
    r.tick()
    http.is_open = True  # the next session: only AAA trades
    clock.t += 61
    r.tick()
    r.ws.queue.append([{"T": "t", "S": "AAA", "p": 11.0}])
    clock.t += 2
    r.tick()
    assert http.broadcasts[-1]["prices"] == {"AAA": 11.0}   # BBB's price from the day before is not sent as new
    r.fail(clock.t, ps.ConnectionClosed("lost"))           # a dropped connection forgets the prices too
    assert r.coalescer.prices == {} and r.coalescer.take(clock.t + 5) is None


def test_a_failed_broadcast_is_dropped_and_never_stops_the_relay():
    http = FakeHttp()
    http.fail_broadcast = True
    r, clock, _ = relay(http)
    r.tick()
    r.ws.queue.append([{"T": "t", "S": "XOM", "p": 158.6}])
    r.tick()
    assert r.ws is not None and r.last_error == "broadcast OSError"


def test_trades_of_a_stock_not_subscribed_are_ignored():
    http = FakeHttp()
    r, clock, _ = relay(http)
    r.tick()
    r.ws.queue.append([{"T": "t", "S": "NVDA", "p": 140.0}])
    r.tick()
    assert http.broadcasts == []


# ---------------------------------------------------------------- market hours


def test_closed_market_no_connection_and_an_idle_heartbeat():
    http = FakeHttp(is_open=False)
    r, clock, made = relay(http)
    for _ in range(50):
        r.tick()
    assert made == [] and "/v2/positions" not in http.gets
    assert http.beats and http.beats[-1]["state"] == "idle"
    assert http.gets.count("/v2/clock") <= 50 * 5 / 60 + 2   # the clock once a minute, not every turn


def test_the_close_ends_the_stream_and_an_unreadable_clock_counts_as_closed():
    http = FakeHttp()
    r, clock, made = relay(http)
    r.tick()
    assert r.ws is not None
    http.is_open = False
    clock.t += 61
    r.tick()
    assert r.ws is None and made[0].closed and r.state == "idle"

    class Broken(FakeHttp):
        def alpaca_get(self, path):
            raise OSError("no network")

    r2, _, made2 = relay(Broken())
    r2.tick()
    assert made2 == [] and r2.state == "idle"


def test_an_early_close_between_two_clock_reads_stops_the_stream():
    http = FakeHttp()
    r, clock, _ = relay(http)
    r.tick()
    r.next_close = ps.datetime(2000, 1, 1, tzinfo=ps.UTC)
    r.tick()
    assert r.ws is None and r.state == "idle"


def test_nothing_held_no_connection():
    r, clock, made = relay(FakeHttp(positions=[]))
    r.tick()
    assert made == [] and r.state == "no_positions"


# ---------------------------------------------------------------- the 30 symbols


def _rows(n):
    return [{"symbol": f"S{i:02d}", "market_value": str(1000 + i), "asset_class": "us_equity"} for i in range(n)]


def test_more_than_30_held_streams_the_30_largest():
    rows = _rows(35) + [{"symbol": "SHORT", "market_value": "-99999", "asset_class": "us_equity"},
                        {"symbol": "BTCUSD", "market_value": "1e9", "asset_class": "crypto"},
                        {"symbol": "bad sym", "market_value": "1e9"}]
    syms, left = ps.pick_symbols(rows)
    assert len(syms) == 30 and left == 6
    assert "SHORT" in syms and "BTCUSD" not in syms   # by size; equities only
    assert {"S00", "S01", "S02", "S03", "S04", "S05"}.isdisjoint(syms)  # the six smallest stay on the poll


def test_ties_are_by_symbol_and_a_few_held_are_all_streamed():
    tie = [{"symbol": "B", "market_value": "1"}, {"symbol": "A", "market_value": "1"}]
    assert ps.pick_symbols(tie, 1) == (["A"], 1)
    assert ps.pick_symbols(_rows(3)) == (["S00", "S01", "S02"], 0)


def test_a_change_in_holdings_resubscribes_only_the_difference():
    http = FakeHttp(positions=[{"symbol": "XOM", "market_value": "1"}, {"symbol": "TMO", "market_value": "2"}])
    r, clock, made = relay(http)
    r.tick()
    assert made[0].sent[1] == {"action": "subscribe", "trades": ["TMO", "XOM"]}
    http.positions = [{"symbol": "XOM", "market_value": "1"}, {"symbol": "BIIB", "market_value": "2"}]
    clock.t += 61
    r.tick()
    assert made[0].sent[2:] == [{"action": "unsubscribe", "trades": ["TMO"]},
                                {"action": "subscribe", "trades": ["BIIB"]}]
    assert len(made) == 1                          # the same connection


def test_auth_is_sent_with_the_keys_and_nothing_else():
    r, clock, made = relay()
    r.tick()
    assert made[0].sent[0] == {"action": "auth", "key": "KEY", "secret": "SECRET"}
    assert all(m["action"] in ("auth", "subscribe", "unsubscribe") for m in made[0].sent)


# ---------------------------------------------------------------- backoff, 405, 406


def test_backoff_doubles_to_60_and_resets():
    b = ps.Backoff()
    assert [b.next() for _ in range(9)] == [1, 2, 4, 8, 16, 32, 60, 60, 60]
    b.reset()
    assert b.next() == 1
    assert ps.Backoff().at_least(60) == 60


def test_a_lost_connection_comes_back_with_growing_waits_never_a_spin():
    clock = Clock()
    socks = [OSError("refused")] * 8 + [FakeWs(clock)]
    r, _, made = relay(clock=clock, sockets=socks)
    attempts = []
    start = clock.t
    while r.ws is None and clock.t - start < 600:
        before = len(socks)
        r.tick()
        if len(socks) != before:
            attempts.append(clock.t - start)
    gaps = [b - a for a, b in zip(attempts, attempts[1:], strict=False)]
    assert len(attempts) == 9 and r.ws is not None
    assert all(g >= 0.9 for g in gaps) and max(gaps) <= 61
    assert gaps[-1] >= 59                           # capped at 60 s


def test_406_connection_limit_waits_a_minute_and_logs(caplog):
    clock = Clock()
    refused = FakeWs(clock, auth=[{"T": "error", "code": 406, "msg": "connection limit exceeded"}])
    r, _, made = relay(clock=clock, sockets=[refused, FakeWs(clock)])
    with caplog.at_level("WARNING"):
        r.tick()
    assert r.ws is None and refused.closed
    assert r.retry_at - clock.t >= 59
    assert "406 connection limit" in caplog.text
    turns = 0
    while r.ws is None:
        r.tick()
        turns += 1
    assert len(made) == 2 and turns <= 70          # one attempt after the wait, about one turn a second meanwhile


def test_405_symbol_limit_halves_the_set_on_the_same_connection_until_the_next_session():
    http = FakeHttp(positions=_rows(30))
    clock = Clock()
    ws = FakeWs(clock, script=[[{"T": "error", "code": 405, "msg": "symbol limit exceeded"}]])
    r, _, made = relay(http, clock, sockets=[ws])
    r.tick()
    r.tick()
    assert r.cap == 15 and len(r.sent) == 15 and r.ws is ws
    assert ws.sent[-2]["action"] == "unsubscribe" and len(ws.sent[-2]["trades"]) == 30
    assert ws.sent[-1] == {"action": "subscribe", "trades": [f"S{i:02d}" for i in range(15, 30)]}
    http.is_open = False
    clock.t += 61
    r.tick()
    assert r.cap == ps.MAX_SYMBOLS


def test_auth_failure_reconnects_with_backoff():
    clock = Clock()
    bad = FakeWs(clock, auth=[{"T": "error", "code": 402, "msg": "auth failed"}])
    r, _, _ = relay(clock=clock, sockets=[bad])
    r.tick()
    assert r.ws is None and r.retry_at > clock.t and r.last_error == "error 402 auth failed"


# ---------------------------------------------------------------- paper only, what it may call


def test_a_live_base_url_is_refused():
    assert ps.paper_only(None) == ps.PAPER_TRADING_URL
    assert ps.paper_only("https://paper-api.alpaca.markets/") == ps.PAPER_TRADING_URL
    with pytest.raises(SystemExit):
        ps.paper_only("https://api.alpaca.markets")
    with pytest.raises(SystemExit):
        ps.Http("k", "s", "https://x.supabase.co", "sk", base="https://api.alpaca.markets")


def test_only_two_paper_paths_can_be_read():
    h = ps.Http("k", "s", "https://x.supabase.co", "sb_secret_x")
    with pytest.raises(ValueError):
        h.alpaca_get("/v2/orders")
    assert "Authorization" not in h.sb_h           # an sb_secret_ key is not a JWT
    assert ps.Http("k", "s", "https://x.supabase.co", "eyJabc").sb_h["Authorization"] == "Bearer eyJabc"


def test_the_script_cannot_trade():
    src = (ROOT / "scripts" / "price_stream.py").read_text()
    imports = set(re.findall(r"^\s*(?:from|import) (kaibot[\w.]*)", src, re.M))
    # M10 1.26 (#451): plus the helpers' Supabase client, which itself imports nothing of kaibot
    assert imports == {"kaibot.engine.config", "kaibot.common.helper_session"}
    client = (ROOT / "src" / "kaibot" / "common" / "helper_session.py").read_text()
    assert not re.findall(r"^\s*(?:from|import) (kaibot[\w.]*)", client, re.M)
    for word in ("/v2/orders", "AlpacaPaper", "BrokerAdapter", "ledger_", "method=\"DELETE\"", "PATCH"):
        assert word not in src
    assert src.count('method="POST"') == 1 and "realtime/v1/api/broadcast" in src and "rest/v1/heartbeat" in src


def test_topic_matches_the_page_and_the_migration():
    web = ROOT / "apps" / "web" / "src" / "lib" / "stream.ts"
    if web.exists():  # the dashboard's half is its own PR and may merge after this one
        assert f'STREAM_TOPIC = "{ps.TOPIC}"' in web.read_text() and f'STREAM_EVENT = "{ps.EVENT}"' in web.read_text()
    mig = next((ROOT / "migrations").glob("0032_*.sql")).read_text()
    assert f"'{ps.TOPIC}'" in mig


# ---------------------------------------------------------------- websocket framing


def test_frames_round_trip_masked_and_in_pieces():
    for n in (0, 5, 125, 126, 70_000):
        payload = bytes(range(256)) * (n // 256) + bytes(range(n % 256))
        frame = ps.encode_frame(0x1, payload, mask=b"\x01\x02\x03\x04")
        buf = bytearray()
        for i in range(0, len(frame), 7):        # arrives in small pieces
            assert ps.decode_frame(buf) is None
            buf += frame[i:i + 7]
        assert ps.decode_frame(buf) == (True, 0x1, payload) and buf == bytearray()


class FakeSock:
    def __init__(self, data: bytes):
        self.data, self.out = bytearray(data), bytearray()

    def settimeout(self, t):
        pass

    def recv(self, n):
        if not self.data:
            raise TimeoutError
        chunk = bytes(self.data[:3])
        del self.data[:3]
        return chunk

    def sendall(self, b):
        self.out += b


def _server(op: int, payload: bytes, fin: bool = True) -> bytes:
    return bytes([(0x80 if fin else 0) | op, len(payload)]) + payload  # unmasked, as a server sends


def test_recv_answers_pings_joins_fragments_and_reports_a_close():
    data = _server(0x9, b"hi") + _server(0x1, b'[{"T":', fin=False) + _server(0x0, b'"t"}]')
    ws = ps.WebSocket(FakeSock(data))
    assert ws.recv(0.1) == ['[{"T":"t"}]']
    assert ps.decode_frame(bytearray(ws.sock.out)) == (True, 0xA, b"hi")  # the pong
    assert ws.recv(0.01) == []                   # nothing more: no block, no error
    ws2 = ps.WebSocket(FakeSock(_server(0x8, b"\x03\xe8")))
    with pytest.raises(ps.ConnectionClosed):
        ws2.recv(0.1)


def test_only_wss_is_accepted():
    with pytest.raises(ValueError):
        ps.WebSocket.connect("ws://stream.data.alpaca.markets/v2/iex")
