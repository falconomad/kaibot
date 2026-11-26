"""The helpers' narrow key path to Supabase (M10 ticket 1.26, issue #451; migration 0038, docs/ops/helper_role.md).

The price relay, the notice publisher and the research scripts are not the engine and must not hold the service
key, which bypasses every policy. A helper signs in as an ordinary Supabase Auth user (email and password) with the
PUBLISHABLE key and gets the `authenticated` role, which has no right on any kaibot table. It can call only the
`kb_helper_*` functions of migration 0038 and send on the private Realtime channel `kb:prices`.

Settings, from the helpers' own env file (`helper_env`; never the engine's .env, which holds the service key):

    SUPABASE_URL                 optional (the project's public URL is the default)
    SUPABASE_PUBLISHABLE_KEY     the project's publishable key (sb_publishable_...; public: the dashboard ships it)
    SUPABASE_HELPER_EMAIL        the helper user's email
    SUPABASE_HELPER_PASSWORD     its password

No Alpaca key (#712): the market-data pair is in data.env (common/secret_files.py), and the trading pair is never
read from this file.

Stdlib only. Nothing here logs or returns a key, a password or a token; errors carry the HTTP status and at most
the first 200 characters of the answer's message.
"""

from __future__ import annotations

import json
import os
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

HELPER_ENV = "KAIBOT_HELPER_ENV_FILE"
DEFAULT_HELPER_ENV = "~/Projects/kaibot-secrets/helper.env"
NAMES = ("SUPABASE_PUBLISHABLE_KEY", "SUPABASE_HELPER_EMAIL", "SUPABASE_HELPER_PASSWORD")
SERVICE_NAMES = ("SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_ROLE_KEY")
# #712: the real paper account's trading pair is not a helper setting. A helpers' file that still holds it (the setup
# before #712) is read without those lines: they never reach the dict. scripts/helper_check.py says to remove them.
TRADING_NAMES = ("ALPACA_API_KEY", "ALPACA_SECRET", "ALPACA_API_SECRET")
REFRESH_BEFORE_S = 120.0  # a new sign-in this long before the token ends
TIMEOUT_S = 10.0


class HelperError(RuntimeError):
    """A helper call failed. `status`: the HTTP status (0: no answer)."""

    def __init__(self, status: int, message: str):
        super().__init__(f"helper {status}: {message[:200]}")
        self.status = status


def helper_env(path: str | None = None, keep_trading: bool = False) -> dict[str, str] | None:
    """The helper settings from the helpers' env file (`path`, else $KAIBOT_HELPER_ENV_FILE, else
    ~/Projects/kaibot-secrets/helper.env), or None when there is no such file (the helper path is not set up yet).
    The file is read into a dict, not into os.environ. A file that holds a service key is refused: the point of the
    file is that the process reading it cannot have that key.
    #712: the trading pair (the setup before data.env) is skipped, unless `keep_trading` (only the price relay asks,
    and only while there is no data.env: Product, 26 Nov 2026, nothing stops before the owner's setup)."""
    p = Path(os.path.expanduser(path or os.environ.get(HELPER_ENV) or DEFAULT_HELPER_ENV))
    if not p.exists():
        return None
    env: dict[str, str] = {}
    for line in p.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            if k.strip() in TRADING_NAMES and not keep_trading:
                continue  # #712: not kept
            env[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    if any(env.get(k) for k in SERVICE_NAMES):
        raise SystemExit(f"{p} holds a Supabase service key: remove it (the helpers' file must not have it)")
    missing = [k for k in NAMES if not env.get(k)]
    if missing:
        raise SystemExit(f"{p}: missing {', '.join(missing)} (docs/ops/helper_role.md)")
    return env


class HelperSession:
    """A signed-in helper. `rpc` calls a `kb_helper_*` function; `broadcast` sends on a private Realtime topic.
    The sign-in is repeated shortly before the access token ends and once after a 401."""

    def __init__(self, url: str, publishable_key: str, email: str, password: str, *, opener=None,
                 clock=time.monotonic, timeout_s: float = TIMEOUT_S) -> None:
        if publishable_key.startswith("sb_secret_"):
            raise ValueError("that is a secret key: the helpers sign in with the publishable key")
        self.url, self._key, self._email, self._password = url.rstrip("/"), publishable_key, email, password
        self._open = opener or (lambda req, timeout: urllib.request.urlopen(  # noqa: S310 - https URL built above
            req, timeout=timeout, context=ssl.create_default_context()))
        self.clock, self.timeout_s = clock, timeout_s
        self._token: str | None = None
        self._until = 0.0

    @classmethod
    def from_env(cls, env: dict[str, str], default_url: str, **kw) -> HelperSession:
        return cls(env.get("SUPABASE_URL") or default_url, env["SUPABASE_PUBLISHABLE_KEY"],
                   env["SUPABASE_HELPER_EMAIL"], env["SUPABASE_HELPER_PASSWORD"], **kw)

    def _post(self, path: str, body: Any, headers: dict[str, str]) -> Any:
        req = urllib.request.Request(f"{self.url}{path}", data=json.dumps(body).encode(), method="POST",
                                     headers={"apikey": self._key, "Content-Type": "application/json", **headers})
        try:
            with self._open(req, timeout=self.timeout_s) as r:
                raw = r.read(8 * 1024 * 1024)
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read(4096) or b"{}")
                msg = str(msg.get("message") or msg.get("msg") or msg.get("error_description") or msg.get("error")
                          or "")
            except (ValueError, AttributeError):
                msg = ""
            raise HelperError(e.code, msg) from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise HelperError(0, type(e).__name__) from None

    def sign_in(self) -> None:
        d = self._post("/auth/v1/token?grant_type=password", {"email": self._email, "password": self._password}, {})
        token = (d or {}).get("access_token")
        if not token:
            raise HelperError(0, "the sign-in answered without an access token")
        self._token = str(token)
        self._until = self.clock() + max(float(d.get("expires_in") or 3600) - REFRESH_BEFORE_S, 30.0)

    def _authed(self, path: str, body: Any, extra: dict[str, str] | None = None) -> Any:
        if self._token is None or self.clock() >= self._until:
            self.sign_in()
        for again in (True, False):
            try:
                return self._post(path, body, {"Authorization": f"Bearer {self._token}", **(extra or {})})
            except HelperError as e:
                if e.status != 401 or not again:
                    raise
                self.sign_in()  # the token ended early (a restart of Auth, a revoked session): once more
        return None

    def rpc(self, fn: str, args: dict) -> Any:
        if not fn.startswith("kb_helper_"):
            raise ValueError(f"not a helper function: {fn}")
        return self._authed(f"/rest/v1/rpc/{fn}", args)

    def broadcast(self, topic: str, event: str, payload: dict) -> None:
        """Realtime's REST broadcast on a PRIVATE topic, as the signed-in helper (0038's INSERT policy)."""
        self._authed("/realtime/v1/api/broadcast",
                     {"messages": [{"topic": topic, "event": event, "payload": payload, "private": True}]})
