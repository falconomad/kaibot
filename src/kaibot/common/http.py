"""Budgeted, bounded HTTPS GETs for ingestion (stdlib urllib only).

Every call: acquires the source's budget (common.ratelimit), refuses non-HTTPS URLs, has a timeout, reads at
most `max_bytes` (streamed in chunks, so a hostile or broken server cannot exhaust memory), and retries 429 /
5xx / network errors with capped exponential backoff. A 429 also holds every other caller of the same source
(`Budget.backoff`), honouring Retry-After up to 60 s. Treat the returned body as untrusted.

Certificates: python.org's macOS Python ships without root certificates, so urllib's default context fails every
request with CERTIFICATE_VERIFY_FAILED. The default opener therefore verifies against certifi's bundle when certifi
is installed and SSL_CERT_FILE / SSL_CERT_DIR are not set (an explicit setting, as scripts/run_engine.sh exports, is
left to the default context exactly as before). Verification is never turned off. A failed verification is
permanent, so it raises `CertificateError` at once instead of being retried.
"""

from __future__ import annotations

import email.utils
import functools
import json
import logging
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime

from kaibot.common.ratelimit import Budget, get_budget

log = logging.getLogger(__name__)
DEFAULT_MAX_BYTES = 16 * 1024 * 1024
_CHUNK = 64 * 1024


CERT_HINT = ("the server's certificate could not be verified (missing root certificates). On macOS with "
             "python.org's Python run \"Install Certificates.command\" (in /Applications/Python 3.x/), or install "
             "certifi (pip install certifi) and run again")


class HttpError(RuntimeError):
    def __init__(self, status: int, url: str, detail: str = ""):
        super().__init__(f"HTTP {status} for {_redact(url)}: {detail[:200]}")
        self.status = status
        self.url = url
        self.detail = detail[:200]


class ResponseTooLarge(HttpError):
    pass


class CertificateError(HttpError):
    """TLS certificate verification failed: permanent for this machine, never retried (status 0)."""

    def __init__(self, url: str, reason: object = ""):
        super().__init__(0, url, f"CERTIFICATE_VERIFY_FAILED: {CERT_HINT}")
        self.reason = str(reason)[:200]


@functools.cache
def ssl_context() -> ssl.SSLContext:
    """The verifying context for the default opener: certifi's bundle when available, else urllib's default."""
    if not (os.environ.get("SSL_CERT_FILE") or os.environ.get("SSL_CERT_DIR")):
        try:
            import certifi
        except ImportError:
            pass
        else:
            return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()


def _is_cert_error(e: BaseException) -> bool:
    return isinstance(e, ssl.SSLCertVerificationError) or isinstance(
        getattr(e, "reason", None), ssl.SSLCertVerificationError)


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str]
    body: bytes

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "")

    def last_modified(self) -> datetime | None:
        v = self.headers.get("last-modified")
        if not v:
            return None
        try:
            t = email.utils.parsedate_to_datetime(v)
        except (TypeError, ValueError):
            return None
        return t.astimezone(UTC) if t.tzinfo else t.replace(tzinfo=UTC)


def _redact(url: str) -> str:
    """Never log query values that may hold keys (FRED api_key, Finnhub token)."""
    parts = urllib.parse.urlsplit(url)
    if not parts.query:
        return url
    q = [(k, "***" if k.lower() in {"api_key", "apikey", "token", "key"} else v)
         for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(q)))


def _read_bounded(r, max_bytes: int, url: str) -> bytes:
    declared = r.headers.get("Content-Length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise ResponseTooLarge(r.status, url, f"Content-Length {declared} > {max_bytes}")
    buf = bytearray()
    while True:
        chunk = r.read(_CHUNK)
        if not chunk:
            return bytes(buf)
        buf += chunk
        if len(buf) > max_bytes:
            raise ResponseTooLarge(r.status, url, f"body > {max_bytes} bytes")


def _retry_after(headers) -> float:
    v = (headers.get("Retry-After") if headers else None) or ""
    return min(float(v), 60.0) if v.strip().isdigit() else 0.0


def get(
    url: str,
    *,
    source: str,
    headers: dict[str, str] | None = None,
    params: dict | None = None,
    timeout: float = 30.0,
    max_bytes: int = DEFAULT_MAX_BYTES,
    attempts: int = 5,
    ok_statuses: tuple[int, ...] = (),
    budget: Budget | None = None,
    opener=None,
) -> Response:
    """GET `url` under `source`'s budget. Raises HttpError for non-retryable statuses unless in `ok_statuses`."""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    if urllib.parse.urlsplit(url).scheme != "https":
        raise ValueError(f"refusing non-HTTPS URL {_redact(url)}")
    b = budget or get_budget(source)
    if opener is None:
        def open_(req, timeout):
            return urllib.request.urlopen(req, timeout=timeout, context=ssl_context())
    else:
        open_ = opener
    last = "no attempt"
    for attempt in range(attempts):
        b.acquire()
        delay = min(2**attempt, 20)
        try:
            req = urllib.request.Request(url, headers=headers or {}, method="GET")
            with open_(req, timeout=timeout) as r:
                body = _read_bounded(r, max_bytes, url)
                return Response(r.status, {k.lower(): v for k, v in r.headers.items()}, body)
        except urllib.error.HTTPError as e:
            if e.code in ok_statuses:
                return Response(e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, b"")
            if e.code == 429:
                wait = max(delay, _retry_after(e.headers))
                log.warning("%s: 429 from %s, backing off %.0fs", source, _redact(url), wait)
                b.backoff(wait)
                last = "429"
                continue
            if e.code >= 500:
                last = f"HTTP {e.code}"
                b.clock.sleep(delay)
                continue
            raise HttpError(e.code, url, e.reason if isinstance(e.reason, str) else "") from None
        except ResponseTooLarge:
            raise
        except (urllib.error.URLError, ssl.SSLCertVerificationError, TimeoutError, ConnectionError) as e:
            if _is_cert_error(e):
                raise CertificateError(url, getattr(e, "reason", e)) from None
            last = type(e).__name__
            b.clock.sleep(delay)
    raise HttpError(0, url, f"giving up after {attempts} attempts ({last})")


def get_json(url: str, *, source: str, **kw) -> object:
    r = get(url, source=source, **kw)
    try:
        return json.loads(r.body, strict=False)
    except ValueError as e:
        raise HttpError(r.status, url, f"invalid JSON: {e}") from None
