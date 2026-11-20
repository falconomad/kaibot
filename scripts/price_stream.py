#!/usr/bin/env python3
"""Price relay (stakeholder, 20 Nov 2026): the dashboard's prices about once a second instead of every 10 s.

A small process beside the engine, never inside it. It
- reads the paper account's open positions (GET paper-api /v2/positions) every 60 s and the market clock
  (GET /v2/clock), with the paper keys; a base URL that is not the paper endpoint is refused at start;
- holds ONE Alpaca market-data websocket on the free IEX feed (wss://stream.data.alpaca.markets/v2/iex) and
  subscribes to trades ONLY for the stocks held: the 30 largest by market value when more are held (the free plan's
  cap; the rest stay on the dashboard's 10 s poll). Error 405 (symbol limit) halves the set until the next session;
  406 (connection limit: something else holds the keys' one connection) waits 60 s and says so, never spins. A lost
  connection comes back after 1, 2, 4 ... seconds, at most 60;
- coalesces the trades and broadcasts at most one message a second, and only when a price changed, on the private
  Supabase Realtime channel `kb:prices` (event `prices`, payload {as_of, feed: "iex", prices: {SYM: price}}) with the
  service key. Only the owner may read the channel (migration 0032). No table row is written for prices;
- streams only while the market is open by Alpaca's clock (09:30-16:00 ET, holidays and early closes included) and
  holds no connection otherwise;
- writes its heartbeat to kaibot.heartbeat with process 'price_stream' once a minute.

What it cannot do: it sends no order, cancels nothing, writes no ledger or trading table, and imports none of the
engine's trading code (only kaibot.engine.config for the .env parsing and the paper URL). Its only HTTP calls are the
two GETs above, the broadcast POST and the heartbeat upsert. Keys are read from the env file and never logged.

    python scripts/price_stream.py --env ~/Projects/kaibot-secrets/.env

scripts/service_run.sh runs it beside the engine when KAIBOT_PRICE_STREAM=on (off by default); its failure never
touches the engine. IEX is one exchange: its last trade can differ from the consolidated price by a few cents."""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import json
import logging
import os
import re
import signal
import socket
import ssl
import struct
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kaibot.engine.config import DEFAULT_SUPABASE_URL, PAPER_TRADING_URL, load_env_file  # noqa: E402

log = logging.getLogger("price_stream")

FEED = "iex"
FEED_URL = "wss://stream.data.alpaca.markets/v2/iex"
TOPIC = "kb:prices"  # apps/web/src/lib/stream.ts STREAM_TOPIC; migration 0032's policy
EVENT = "prices"
PROCESS = "price_stream"
MAX_SYMBOLS = 30  # Alpaca's free plan: 30 symbols on the one IEX connection
PUBLISH_GAP_S = 1.0
POSITIONS_EVERY_S = 60.0
CLOCK_EVERY_S = 60.0
HEARTBEAT_EVERY_S = 60.0
BACKOFF_CAP_S = 60.0
PING_EVERY_S = 30.0
SILENT_S = 75.0  # no frame at all (not even our ping's pong) for this long: the connection is dead
HTTP_TIMEOUT_S = 5.0
SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9./]{0,11}$")

# Alpaca stream error codes (docs: market data, real-time stream)
SYMBOL_LIMIT, CONNECTION_LIMIT = 405, 406


class StreamError(RuntimeError):
    def __init__(self, code: int, msg: str):
        super().__init__(f"{code} {msg}")
        self.code, self.msg = code, msg


class ConnectionClosed(RuntimeError):
    pass


# ---------------------------------------------------------------- pure parts (unit-tested)


def pick_symbols(rows: Iterable[dict], cap: int = MAX_SYMBOLS) -> tuple[list[str], int]:
    """The paper account's positions -> (the stocks to stream, how many held stocks are left out). The `cap` largest
    by market value (absolute, so a short would count by its size), ties by symbol; non-equities and odd symbols out."""
    held: dict[str, float] = {}
    for r in rows:
        sym = r.get("symbol") if isinstance(r, dict) else None
        if not isinstance(sym, str) or not SYMBOL.match(sym) or r.get("asset_class", "us_equity") != "us_equity":
            continue
        try:
            mv = abs(float(r.get("market_value") or 0.0))
        except (TypeError, ValueError):
            mv = 0.0
        held[sym] = held.get(sym, 0.0) + mv
    ranked = sorted(held, key=lambda s: (-held[s], s))
    cap = max(0, cap)
    return sorted(ranked[:cap]), max(0, len(ranked) - cap)


class Coalescer:
    """Trades in, at most one snapshot per `gap` seconds out, and only when a price changed since the last one."""

    def __init__(self, gap: float = PUBLISH_GAP_S):
        self.gap = gap
        self.prices: dict[str, float] = {}
        self.dirty = False
        self.sent_at = float("-inf")

    def trade(self, symbol: str, price: Any) -> None:
        if not isinstance(price, int | float) or isinstance(price, bool) or not price > 0 or price != price:
            return
        p = float(price)
        if self.prices.get(symbol) != p:
            self.prices[symbol] = p
            self.dirty = True

    def keep(self, symbols: Iterable[str]) -> None:
        """Forget stocks no longer streamed, so a sold stock's last price is never broadcast again."""
        keep = set(symbols)
        for s in [s for s in self.prices if s not in keep]:
            del self.prices[s]

    def take(self, now: float) -> dict[str, float] | None:
        if not self.dirty or not self.prices or now - self.sent_at < self.gap:
            return None
        self.dirty, self.sent_at = False, now
        return dict(sorted(self.prices.items()))


class Backoff:
    """1, 2, 4 ... seconds, never more than `cap`; `at_least` for an answer that says wait (406)."""

    def __init__(self, base: float = 1.0, cap: float = BACKOFF_CAP_S):
        self.base, self.cap, self.n = base, cap, 0

    def next(self) -> float:
        d = min(self.cap, self.base * 2**self.n)
        self.n = min(self.n + 1, 30)
        return d

    def at_least(self, s: float) -> float:
        return max(min(s, self.cap), self.next())

    def reset(self) -> None:
        self.n = 0


def paper_only(base: str | None) -> str:
    """The paper endpoint or nothing: a live URL is refused (same rule as engine/config.py and api/marks.ts)."""
    b = (base or PAPER_TRADING_URL).rstrip("/")
    if b != PAPER_TRADING_URL:
        raise SystemExit("price_stream is paper-only: ALPACA_BASE_URL must be the paper endpoint")
    return b


# ---------------------------------------------------------------- the websocket (stdlib only: no new dependency)

_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_FRAME = 4 * 1024 * 1024


def encode_frame(opcode: int, payload: bytes, mask: bytes | None = None) -> bytes:
    """One final client frame (always masked, RFC 6455 5.3)."""
    mask = mask if mask is not None else os.urandom(4)
    n = len(payload)
    head = bytes([0x80 | opcode])
    if n < 126:
        head += bytes([0x80 | n])
    elif n < 1 << 16:
        head += bytes([0x80 | 126]) + struct.pack(">H", n)
    else:
        head += bytes([0x80 | 127]) + struct.pack(">Q", n)
    return head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


def decode_frame(buf: bytearray) -> tuple[bool, int, bytes] | None:
    """(fin, opcode, payload) of the first complete frame in `buf` (removed from it), or None while incomplete."""
    if len(buf) < 2:
        return None
    fin, op, masked, n, i = bool(buf[0] & 0x80), buf[0] & 0x0F, buf[1] & 0x80, buf[1] & 0x7F, 2
    if n == 126:
        if len(buf) < 4:
            return None
        n, i = struct.unpack(">H", buf[2:4])[0], 4
    elif n == 127:
        if len(buf) < 10:
            return None
        n, i = struct.unpack(">Q", buf[2:10])[0], 10
    if n > MAX_FRAME:
        raise ConnectionClosed(f"frame of {n} bytes")
    key = b""
    if masked:
        if len(buf) < i + 4:
            return None
        key, i = bytes(buf[i:i + 4]), i + 4
    if len(buf) < i + n:
        return None
    payload = bytes(buf[i:i + n])
    del buf[:i + n]
    if key:
        payload = bytes(b ^ key[j % 4] for j, b in enumerate(payload))
    return fin, op, payload


class WebSocket:
    """A minimal RFC 6455 client: text messages, ping/pong, close. `recv` never blocks longer than its timeout."""

    def __init__(self, sock):
        self.sock, self.buf, self.parts, self.last_frame = sock, bytearray(), b"", time.monotonic()

    @classmethod
    def connect(cls, url: str, timeout: float = 10.0) -> WebSocket:
        u = urlsplit(url)
        if u.scheme != "wss" or not u.hostname:
            raise ValueError("refusing a websocket that is not wss://")
        raw = socket.create_connection((u.hostname, u.port or 443), timeout=timeout)
        try:
            sock = _ssl_context().wrap_socket(raw, server_hostname=u.hostname)
            key = base64.b64encode(os.urandom(16)).decode()
            sock.sendall((f"GET {u.path or '/'} HTTP/1.1\r\nHost: {u.hostname}\r\nUpgrade: websocket\r\n"
                          f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
                          "User-Agent: kaibot-price-stream\r\n\r\n").encode())
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = sock.recv(4096)
                if not chunk or len(data) > 65536:
                    raise ConnectionClosed("no handshake answer")
                data += chunk
            head, rest = data.split(b"\r\n\r\n", 1)
            lines = head.decode("latin-1").split("\r\n")
            if " 101 " not in f"{lines[0]} ":
                raise StreamError(_http_status(lines[0]), "websocket upgrade refused")
            headers = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:])}
            want = base64.b64encode(hashlib.sha1(key.encode() + _GUID).digest()).decode()
            if headers.get("sec-websocket-accept") != want:
                raise ConnectionClosed("bad handshake")
        except BaseException:
            raw.close()
            raise
        ws = cls(sock)
        ws.buf += rest
        return ws

    def send_text(self, text: str) -> None:
        self.sock.sendall(encode_frame(0x1, text.encode()))

    def ping(self) -> None:
        self.sock.sendall(encode_frame(0x9, b"kb"))

    def recv(self, timeout: float) -> list[str]:
        """The text messages that arrive within `timeout` seconds (often none). Raises ConnectionClosed."""
        out: list[str] = []
        deadline = time.monotonic() + timeout
        while True:
            while (f := decode_frame(self.buf)) is not None:
                self.last_frame = time.monotonic()
                fin, op, payload = f
                if op == 0x9:
                    self.sock.sendall(encode_frame(0xA, payload))
                elif op == 0x8:
                    code = struct.unpack(">H", payload[:2])[0] if len(payload) >= 2 else 1005
                    with contextlib.suppress(OSError):
                        self.sock.sendall(encode_frame(0x8, payload[:2]))
                    raise ConnectionClosed(f"closed by the server ({code})")
                elif op in (0x1, 0x0):
                    self.parts += payload
                    if fin:
                        out.append(self.parts.decode("utf-8", "replace"))
                        self.parts = b""
            if out:
                return out
            left = deadline - time.monotonic()
            if left <= 0:
                return out
            self.sock.settimeout(left)
            try:
                chunk = self.sock.recv(65536)
            except TimeoutError:
                return out
            except (ssl.SSLError, OSError) as e:
                raise ConnectionClosed(type(e).__name__) from None
            if not chunk:
                raise ConnectionClosed("connection lost")
            self.buf += chunk

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self.sock.sendall(encode_frame(0x8, struct.pack(">H", 1000)))
        with contextlib.suppress(OSError):
            self.sock.close()


def _http_status(line: str) -> int:
    m = re.match(r"HTTP/\S+ (\d{3})", line)
    return int(m[1]) if m else 0


def _ssl_context() -> ssl.SSLContext:
    """certifi's roots when present (python.org's macOS Python ships none), else the system's."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


# ---------------------------------------------------------------- the HTTP calls (GET Alpaca, POST Supabase)


class Http:
    """The relay's only HTTP calls. Alpaca: GET on two paper-api paths. Supabase: the broadcast and the heartbeat."""

    ALPACA_PATHS = ("/v2/positions", "/v2/clock")

    def __init__(self, alpaca_key: str, alpaca_secret: str, supabase_url: str, service_key: str,
                 base: str = PAPER_TRADING_URL):
        self.base = paper_only(base)
        self.alpaca_h = {"APCA-API-KEY-ID": alpaca_key, "APCA-API-SECRET-KEY": alpaca_secret,
                         "Accept": "application/json"}
        self.sb = supabase_url.rstrip("/")
        self.sb_h = {"apikey": service_key, "Content-Type": "application/json"}
        if service_key.startswith("eyJ"):  # a legacy JWT key goes in Authorization too; an sb_secret_ key does not
            self.sb_h["Authorization"] = f"Bearer {service_key}"

    def alpaca_get(self, path: str) -> Any:
        if path not in self.ALPACA_PATHS:
            raise ValueError(f"not a path this relay may read: {path}")
        req = urllib.request.Request(self.base + path, headers=self.alpaca_h, method="GET")
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S, context=_ssl_context()) as r:
            return json.loads(r.read(4 * 1024 * 1024))

    def _post(self, url: str, body: Any, extra: dict[str, str] | None = None) -> int:
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={**self.sb_h, **(extra or {})})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S, context=_ssl_context()) as r:
            r.read(65536)
            return r.status

    def broadcast(self, payload: dict) -> int:
        """Realtime's REST broadcast, private channel: only clients the 0032 policy lets in receive it."""
        return self._post(f"{self.sb}/realtime/v1/api/broadcast",
                          {"messages": [{"topic": TOPIC, "event": EVENT, "payload": payload, "private": True}]})

    def heartbeat(self, detail: dict) -> int:
        row = {"process": PROCESS, "ts": datetime.now(UTC).isoformat(), "detail": detail}
        return self._post(f"{self.sb}/rest/v1/heartbeat?on_conflict=process", row,
                          {"Content-Profile": "kaibot", "Prefer": "resolution=merge-duplicates,return=minimal"})


def _why(e: BaseException) -> str:
    """An error in a few words, never a URL's query or a header (no key can reach the log)."""
    if isinstance(e, urllib.error.HTTPError):
        return f"HTTP {e.code}"
    if isinstance(e, StreamError):
        return f"error {e.code} {e.msg}"[:120]
    return type(e).__name__


# ---------------------------------------------------------------- the relay


class Relay:
    def __init__(self, http, key: str, secret: str, *, connect: Callable[[], Any] | None = None,
                 mono: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 host: str = ""):
        self.http, self.key, self.secret = http, key, secret
        self.connect_ws = connect or (lambda: WebSocket.connect(FEED_URL))
        self.mono, self.sleep, self.host = mono, sleep, host or socket.gethostname()
        self.ws = None
        self.backoff = Backoff()
        self.coalescer = Coalescer()
        self.open = False
        self.next_close: datetime | None = None
        self.held: list[dict] = []
        self.wanted: list[str] = []
        self.left_out = 0
        self.sent: set[str] = set()  # what we asked the stream for
        self.cap = MAX_SYMBOLS
        self.retry_at = float("-inf")
        self.next_clock = self.next_positions = self.next_beat = float("-inf")
        self.healthy_since: float | None = None
        self.last_ping = float("-inf")
        self.state = "starting"
        self.last_error: str | None = None
        self.published = 0
        self.last_publish: str | None = None
        self.broadcast_fail_logged = 0.0

    # -- market hours and holdings
    def refresh_clock(self, t: float) -> None:
        self.next_clock = t + CLOCK_EVERY_S
        try:
            c = self.http.alpaca_get("/v2/clock")
            self.open = c.get("is_open") is True
            nc = c.get("next_close")
            self.next_close = datetime.fromisoformat(nc) if self.open and isinstance(nc, str) else None
        except Exception as e:  # cannot tell: closed (no stream is the safe side; the poll still runs)
            log.warning("clock read failed (%s): idle until the next check", _why(e))
            self.open, self.next_close = False, None

    def market_open(self) -> bool:
        if self.next_close is not None and datetime.now(UTC) >= self.next_close:
            self.open = False  # an early close passes between two clock reads
        return self.open

    def refresh_positions(self, t: float) -> None:
        self.next_positions = t + POSITIONS_EVERY_S
        try:
            rows = self.http.alpaca_get("/v2/positions")
            if not isinstance(rows, list):
                raise ValueError("positions: not a list")
            self.held = rows
        except Exception as e:  # keep the last list
            log.warning("positions read failed (%s): keeping the last %d", _why(e), len(self.wanted))
            return
        self.pick()

    def pick(self) -> None:
        wanted, self.left_out = pick_symbols(self.held, self.cap)
        if wanted != self.wanted:
            log.info("holdings to stream: %d (%s)%s", len(wanted), " ".join(wanted) or "none",
                     f"; {self.left_out} more stay on the 10 s poll" if self.left_out else "")
        self.wanted = wanted
        self.coalescer.keep(wanted)

    # -- the connection
    def disconnect(self, why: str) -> None:
        if self.ws is not None:
            log.info("closing the stream: %s", why)
            with contextlib.suppress(Exception):
                self.ws.close()
        self.ws, self.sent, self.healthy_since = None, set(), None

    def fail(self, t: float, e: BaseException) -> None:
        self.disconnect(_why(e))
        self.last_error = _why(e)
        if isinstance(e, StreamError) and e.code == CONNECTION_LIMIT:
            wait = self.backoff.at_least(BACKOFF_CAP_S)
            log.warning("406 connection limit: another client holds these keys' one IEX connection; trying again in "
                        "%.0f s (the dashboard stays on the 10 s poll)", wait)
        else:
            wait = self.backoff.next()
            log.warning("stream down (%s): reconnecting in %.0f s", _why(e), wait)
        self.retry_at = t + wait

    def _expect(self, ws, want: str, timeout: float = 10.0) -> None:
        end = self.mono() + timeout
        while self.mono() < end:
            for text in ws.recv(min(1.0, max(0.0, end - self.mono()))):
                for m in _messages(text):
                    if m.get("T") == "error":
                        raise StreamError(int(m.get("code") or 0), str(m.get("msg") or ""))
                    if m.get("T") == "success" and m.get("msg") == want:
                        return
        raise StreamError(0, f"no '{want}' within {timeout:.0f} s")

    def open_stream(self, t: float) -> None:
        try:
            ws = self.connect_ws()
        except Exception as e:
            self.fail(t, e)
            return
        self.ws = ws
        try:
            self._expect(ws, "connected")
            ws.send_text(json.dumps({"action": "auth", "key": self.key, "secret": self.secret}))
            self._expect(ws, "authenticated")
        except Exception as e:
            self.fail(t, e)
            return
        log.info("stream connected (IEX)")
        self.healthy_since, self.last_ping, self.sent = t, t, set()
        self.resubscribe()

    def resubscribe(self) -> None:
        want = set(self.wanted)
        gone, new = sorted(self.sent - want), sorted(want - self.sent)
        if gone:
            self.ws.send_text(json.dumps({"action": "unsubscribe", "trades": gone}))
        if new:
            self.ws.send_text(json.dumps({"action": "subscribe", "trades": new}))
        self.sent = want

    def handle(self, t: float, m: dict) -> None:
        kind = m.get("T")
        if kind == "t":
            sym = m.get("S")
            if isinstance(sym, str) and sym in self.sent:
                self.coalescer.trade(sym, m.get("p"))
        elif kind == "subscription":
            trades = m.get("trades") or []
            log.info("subscribed to trades of %d stocks", len(trades))
        elif kind == "error":
            code = int(m.get("code") or 0)
            if code == SYMBOL_LIMIT:
                self.cap = max(1, len(self.sent) // 2)
                log.warning("405 symbol limit at %d stocks: streaming the %d largest until the next session",
                            len(self.sent), self.cap)
                self.last_error = "405 symbol limit"
                self.ws.send_text(json.dumps({"action": "unsubscribe", "trades": sorted(self.sent)}))
                self.sent = set()
                self.pick()
                self.resubscribe()
            else:
                raise StreamError(code, str(m.get("msg") or ""))

    def pump(self, t: float, timeout: float = 0.2) -> None:
        try:
            for text in self.ws.recv(timeout):
                for m in _messages(text):
                    self.handle(t, m)
            if t - self.last_ping >= PING_EVERY_S:
                self.ws.ping()
                self.last_ping = t
            last = getattr(self.ws, "last_frame", None)
            if last is not None and time.monotonic() - last > SILENT_S:
                raise ConnectionClosed(f"silent for {SILENT_S:.0f} s")
        except (StreamError, ConnectionClosed, OSError) as e:
            self.fail(t, e)

    # -- out
    def publish(self, t: float) -> None:
        prices = self.coalescer.take(t)
        if prices is None:
            return
        as_of = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        try:
            self.http.broadcast({"as_of": as_of, "feed": FEED, "prices": prices})
            self.published += 1
            self.last_publish = as_of
        except Exception as e:  # dropped: the next second sends the prices again
            self.last_error = f"broadcast {_why(e)}"
            if t - self.broadcast_fail_logged >= 60:
                log.warning("broadcast failed (%s): the dashboard stays on the 10 s poll", _why(e))
                self.broadcast_fail_logged = t

    def beat(self, t: float, force: bool = False) -> None:
        if not force and t < self.next_beat:
            return
        self.next_beat = t + HEARTBEAT_EVERY_S
        detail = {"state": self.state, "feed": FEED, "host": self.host, "pid": os.getpid(),
                  "streamed": len(self.sent), "held": len(self.wanted) + self.left_out, "left_out": self.left_out,
                  "published": self.published, "last_publish": self.last_publish, "error": self.last_error}
        try:
            self.http.heartbeat(detail)
        except Exception as e:
            log.warning("heartbeat failed (%s)", _why(e))

    # -- one turn of the loop
    def tick(self) -> None:
        t = self.mono()
        if t >= self.next_clock:
            self.refresh_clock(t)
        if not self.market_open():
            if self.ws is not None:
                self.disconnect("market closed")
            if self.state != "idle":
                log.info("market closed: idle")
            self.state, self.cap = "idle", MAX_SYMBOLS  # a 405 cut lasts one session
            self.backoff.reset()
            self.beat(t)
            self.sleep(min(5.0, max(0.5, self.next_clock - t)))
            return
        if t >= self.next_positions:
            self.refresh_positions(t)
        if not self.wanted:
            if self.ws is not None:
                self.disconnect("nothing held")
            self.state = "no_positions"
            self.beat(t)
            self.sleep(1.0)
            return
        if self.ws is None:
            if t < self.retry_at:
                self.state = "waiting"
                self.beat(t)
                self.sleep(min(1.0, self.retry_at - t))
                return
            self.open_stream(t)
            return
        if set(self.wanted) != self.sent:
            try:
                self.resubscribe()
            except OSError as e:
                self.fail(t, e)
                return
        self.state = "streaming"
        if self.healthy_since is not None and t - self.healthy_since >= BACKOFF_CAP_S:
            self.backoff.reset()
        self.pump(t)
        self.publish(self.mono())
        self.beat(t)

    def run(self, stopping: Callable[[], bool]) -> None:
        while not stopping():
            try:
                self.tick()
            except Exception as e:  # a bug must not end the relay in a tight loop
                log.exception("relay turn failed (%s)", _why(e))
                self.disconnect("error")
                self.sleep(5.0)
        self.disconnect("stopping")
        self.state = "stopped"
        self.beat(self.mono(), force=True)


def _messages(text: str) -> list[dict]:
    try:
        v = json.loads(text)
    except ValueError:
        return []
    v = v if isinstance(v, list) else [v]
    return [m for m in v if isinstance(m, dict)]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env", default=os.path.expanduser("~/Projects/kaibot-secrets/.env"))
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_env_file(a.env)
    base = paper_only(os.environ.get("ALPACA_BASE_URL"))
    key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_SECRET") or os.environ.get("ALPACA_API_SECRET")
    service = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    missing = [n for n, v in (("ALPACA_API_KEY", key), ("ALPACA_SECRET", secret), ("SUPABASE_SERVICE_KEY", service))
               if not v]
    if missing:
        raise SystemExit(f"missing settings: {', '.join(missing)} (put them in the .env file)")
    http = Http(key, secret, os.environ.get("SUPABASE_URL") or DEFAULT_SUPABASE_URL, service, base)
    stop = {"now": False}

    def on_signal(*_):
        stop["now"] = True

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    log.info("price relay starting (paper positions, IEX trades, at most one broadcast a second on %s)", TOPIC)
    Relay(http, key, secret).run(lambda: stop["now"])
    log.info("price relay stopped")


if __name__ == "__main__":
    main()
