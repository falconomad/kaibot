"""The only door to Claude (story S32). Every call:

1. estimates its worst case (input tokens over-estimated from the text, plus max_tokens of output),
2. reserves that amount in Postgres (kaibot.llm_reserve, under a row lock: no process can overspend),
3. calls the API with a hard timeout and a forced tool (structured output only),
4. settles the actual cost from the response's usage (kaibot.llm_settle), even when the call fails.

If the reservation is refused, the key is missing, or anything goes wrong, the caller gets None and must fall back
to the rules-only path (fail closed). This module is the only place in Kaibot that imports `anthropic`.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml

log = logging.getLogger("kaibot.llm")
ROOT = Path(__file__).resolve().parents[3]
CHARS_PER_TOKEN = 3.0  # deliberately low (real English is ~4): over-estimating input keeps reservations safe


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float
    cache_write: float
    max_prompt_tokens: int | None = None


@dataclass(frozen=True)
class LLMConfig:
    daily_soft_usd: float
    monthly_alert_usd: float
    monthly_hard_usd: float
    reservation_ttl_s: int
    models: dict[str, str]
    prices: dict[str, Price]
    limits: dict[str, dict]

    @classmethod
    def load(cls, path: Path = ROOT / "config" / "llm.yaml") -> LLMConfig:
        raw = yaml.safe_load(Path(path).read_text())
        b = raw["budget"]
        return cls(float(b["daily_soft_usd"]), float(b["monthly_alert_usd"]), float(b["monthly_hard_usd"]),
                   int(b["reservation_ttl_s"]), dict(raw["models"]),
                   {m: Price(**p) for m, p in raw["prices"].items()}, dict(raw["limits"]))


@dataclass
class LLMResult:
    data: dict  # the forced tool's input (validated by the caller against its pydantic contract)
    cost_usd: float
    latency_ms: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    model: str


class Client(Protocol):  # the subset of anthropic.Anthropic used here (a fake implements it in tests)
    def create(self, **kwargs) -> Any: ...


def estimate_tokens(*texts: str) -> int:
    return int(sum(len(t) for t in texts) / CHARS_PER_TOKEN) + 50


def cost_of(price: Price, inp: int, out: int, cache_read: int = 0, cache_write: int = 0) -> float:
    """Anthropic bills uncached input, cache reads and cache writes separately."""
    return (inp * price.input + out * price.output + cache_read * price.cache_read
            + cache_write * price.cache_write) / 1e6


class AnthropicClient:
    """Thin adapter so tests never need the SDK or a key."""

    def __init__(self, api_key: str):
        import anthropic  # the only anthropic import in the codebase

        self._c = anthropic.Anthropic(api_key=api_key, max_retries=0)

    def create(self, **kwargs):
        return self._c.messages.create(**kwargs)


class Gateway:
    def __init__(self, store, client: Client | None, cfg: LLMConfig | None = None, clock=time.monotonic):
        self.store, self.client, self.cfg, self.clock = store, client, cfg or LLMConfig.load(), clock

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def state(self) -> dict:
        """{'day', 'month', 'state': ok|rules_only|no_entries, 'alert'} from Postgres. Fails closed."""
        try:
            return self.store.rpc("llm_budget_state", {"p_daily_soft": self.cfg.daily_soft_usd,
                                                       "p_monthly_hard": self.cfg.monthly_hard_usd,
                                                       "p_monthly_alert": self.cfg.monthly_alert_usd})
        except Exception:
            log.exception("budget state unreadable")
            return {"state": "rules_only", "day": None, "month": None, "alert": False}

    def call(self, purpose: str, model: str, system: str, user: str, tool: dict, max_tokens: int,
             timeout_s: float, cache_system: bool = True, daily_soft_usd: float | None = None,
             report: dict | None = None) -> LLMResult | None:
        """`daily_soft_usd` is an optional tighter daily cap for this purpose (e.g. limits.preopen.daily_usd). It is
        checked against the whole day's spend, so it can only lower the configured cap, never raise it.
        `report`, if given, gets "reason" set when the answer is None: no_client | no_price | over_tier |
        reserve_failed | refused | error (the caller's own dict, so parallel calls never share it)."""
        why = report if report is not None else {}
        if self.client is None:
            why["reason"] = "no_client"
            return None
        price = self.cfg.prices.get(model)
        if price is None:
            log.error("no price for %s: refusing the call", model)
            why["reason"] = "no_price"
            return None
        est_in = estimate_tokens(system, user, json.dumps(tool))
        if price.max_prompt_tokens and est_in > price.max_prompt_tokens:
            log.warning("%s prompt ~%d tokens is over the cheap tier: refused", purpose, est_in)
            why["reason"] = "over_tier"
            return None
        # worst case: every input token billed as a cache write (the dearest input rate), all output used
        worst = max(cost_of(price, 0, max_tokens, cache_write=est_in), cost_of(price, est_in, max_tokens))
        worst = round(worst * 1.1 + 1e-5, 5)
        cap = self.cfg.daily_soft_usd if daily_soft_usd is None else min(self.cfg.daily_soft_usd, float(daily_soft_usd))
        try:
            call_id = self.store.rpc("llm_reserve", {"p_model": model, "p_purpose": purpose, "p_reserved": worst,
                                                     "p_daily_soft": cap,
                                                     "p_monthly_hard": self.cfg.monthly_hard_usd,
                                                     "p_ttl_s": self.cfg.reservation_ttl_s})
        except Exception:
            log.exception("reservation failed: no call")
            why["reason"] = "reserve_failed"
            return None
        if not call_id:
            log.info("budget refused %s ($%.4f): rules-only", purpose, worst)
            why["reason"] = "refused"
            return None
        t0 = self.clock()
        cache = {"cache_control": {"type": "ephemeral"}} if cache_system else {}
        sys_block: Any = [{"type": "text", "text": system, **cache}]
        usage = (0, 0, 0, 0)
        try:
            resp = self.client.create(model=model, max_tokens=max_tokens, system=sys_block,
                                      messages=[{"role": "user", "content": user}], tools=[tool],
                                      tool_choice={"type": "tool", "name": tool["name"]}, timeout=timeout_s)
            u = resp.usage
            usage = (int(u.input_tokens or 0), int(u.output_tokens or 0),
                     int(getattr(u, "cache_read_input_tokens", 0) or 0),
                     int(getattr(u, "cache_creation_input_tokens", 0) or 0))
            blocks = [b for b in resp.content if getattr(b, "type", None) == "tool_use" and b.name == tool["name"]]
            if not blocks:
                raise ValueError("no tool_use block in the response")
            data = blocks[0].input if isinstance(blocks[0].input, dict) else json.loads(blocks[0].input)
            cost = cost_of(price, usage[0], usage[1], usage[2], usage[3])
            self._settle(call_id, cost, usage, None)
            return LLMResult(data, cost, int((self.clock() - t0) * 1000), usage[0], usage[1], usage[2], model)
        except Exception as e:
            cost = cost_of(price, usage[0], usage[1], usage[2], usage[3])
            self._settle(call_id, cost, usage, f"{type(e).__name__}: {str(e)[:200]}")
            log.warning("%s call failed: %s", purpose, e)
            why["reason"] = "error"
            return None

    def _settle(self, call_id, cost, usage, error) -> None:
        try:
            self.store.rpc("llm_settle", {"p_id": call_id, "p_actual": round(cost, 6), "p_in": usage[0],
                                          "p_out": usage[1], "p_cache_read": usage[2], "p_cache_write": usage[3],
                                          "p_error": error})
        except Exception:
            log.exception("settle failed for call %s (the reservation expires after its TTL)", call_id)


def from_env(store, env: dict) -> Gateway:
    """Gateway with a real client only if ANTHROPIC_API_KEY is set and KAIBOT_ROOM=on; otherwise disabled."""
    key = env.get("ANTHROPIC_API_KEY") or ""
    on = (env.get("KAIBOT_ROOM") or "").lower() in ("1", "on", "true", "yes")
    client = AnthropicClient(key) if key and on else None
    return Gateway(store, client)
