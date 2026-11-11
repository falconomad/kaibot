"""A deterministic stand-in for the room's model: no network, no key, the same answer every time.

It implements the gateway's `Client` protocol, so the replay still runs the real Room and the real budget gateway
(prompt building, forced tools, schema validation, evidence checks, admissible()). It reads the DATA block the room
sends and applies the room's written rules mechanically:

1. incomplete news read                                      -> unknown           (veto)
2. a fresh 8-K or material company story about the ticker    -> company_news      (veto)
3. only promotional wire releases about the ticker           -> unknown           (veto: pump risk)
4. fresh ticker news nobody triaged                          -> unknown           (veto)
5. a fresh, material sector/macro story or a macro event     -> sector / macro    (allow; downgrade when there is
                                                                                   one outlet or a minor company story)
6. no news, but the features show the stock moved with its sector -> sector       (downgrade)
7. anything else (no evidence, or only stale evidence)       -> unknown           (veto)

"Fresh" is published in the 24 h before the decision. It is a reference policy for plumbing, regression and cost
checks; it says nothing about how good the real model is. Token usage is estimated from the text length (4
characters per token, no cache discount), so the stub's cost is an estimate of the real prompt size at the prices
in config/llm.yaml, not a bill.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

PROMO_WIRES = frozenset({"prnewswire", "globenewswire", "accesswire", "businesswire", "newsfile"})
FRESH = timedelta(hours=24)
MATERIAL = 0.5
CHARS_PER_TOKEN = 4.0


def _ts(v) -> datetime | None:
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def read_data(user: str) -> dict:
    """The JSON inside the room's <data> block (the last closing tag before the role text wins)."""
    end = user.rindex("\n</data>", 0, user.index("\n\nROLE:"))
    return json.loads(user[len("<data>\n"):end])


def decide(data: dict) -> dict:
    """{"verdict", "cause", "confidence", "support": [ids], "flags": [(id, text)], "why"} from the DATA block."""
    items = [it for it in data.get("evidence") or [] if isinstance(it, dict)]
    ctx = data.get("context") or {}
    as_of = _ts(ctx.get("as_of"))
    fresh, stale = [], []
    for it in items:
        if it.get("kind") not in ("news", "filing"):
            continue
        t = _ts(it.get("published_at"))
        if as_of is None or t is None or t > as_of:  # undated or dated after the decision: not usable
            continue
        (fresh if as_of - t <= FRESH else stale).append(it)

    def tri(it):
        return it.get("triage") if isinstance(it.get("triage"), dict) else {}

    # the room's DATA block does not carry the pack's ticker/sector scope, so "about the ticker" here means
    # "not triaged as a sector or macro story"
    ticker = [it for it in fresh if tri(it).get("cause") not in ("sector", "macro")]
    out = {"support": [], "flags": [], "confidence": "high"}

    def veto(cause, why, flags):
        return {**out, "verdict": "veto", "cause": cause, "why": why, "flags": flags}

    if ctx.get("news_complete") is False:
        return veto("unknown", "the news read was incomplete", [])
    hard = [it for it in ticker if it.get("kind") == "filing"
            or (tri(it).get("cause") == "company_news" and float(tri(it).get("materiality") or 0) >= MATERIAL)]
    if hard:
        return veto("company_news", "fresh company-specific news",
                    [(it["id"], "company news in the last 24 h") for it in hard])
    if ticker and all(str(it.get("source", "")).lower() in PROMO_WIRES for it in ticker):
        return veto("unknown", "only promotional wire releases",
                    [(it["id"], "press-release wire only, no independent story or filing") for it in ticker])
    blind = [it for it in ticker if not tri(it)]
    if blind:
        return veto("unknown", "ticker news that was never triaged", [(it["id"], "untriaged") for it in blind])
    minor = [it for it in ticker if tri(it).get("cause") == "company_news"]
    wide = [it for it in fresh if tri(it).get("cause") in ("sector", "macro")
            and float(tri(it).get("materiality") or 0) >= MATERIAL]
    cal = [it for it in items if it.get("kind") == "calendar"]
    flags = [(it["id"], "minor company story") for it in minor]
    if wide or cal:
        cause = tri(wide[0])["cause"] if wide else "macro"
        strong = bool(cal) or any(int(it.get("n_outlets") or 1) >= 2 for it in wide)
        ok = strong and not minor
        return {"verdict": "allow" if ok else "downgrade", "cause": cause, "confidence": "high" if ok else "medium",
                "support": [it["id"] for it in wide + cal], "flags": flags, "why": f"fresh {cause} evidence"}
    feat = next((it for it in items if it.get("kind") == "features" and isinstance(it.get("value"), dict)), None)
    if feat and not fresh and not stale:
        v = feat["value"]
        rz = v.get("resid_z")
        if rz is not None and abs(float(rz)) < 1.0 and not v.get("news_24h"):
            return {"verdict": "downgrade", "cause": "sector", "confidence": "low", "support": [feat["id"]],
                    "flags": flags, "why": "no news; the drop is explained by the sector move"}
    return veto("unknown", "only stale evidence" if stale else "no evidence of the cause",
                [(it["id"], "older than 24 h") for it in stale])


class StubModel:
    """`Client` for the gateway. `misbehave` makes the Judge break the contract on purpose (tests only):
    "sizing" adds size/stop/price fields, "double" answers a verdict that is not allowed, "invent" cites an
    evidence id that is not in the pack."""

    def __init__(self, misbehave: str | None = None):
        self.misbehave = misbehave
        self.calls = 0

    def create(self, **kw):
        self.calls += 1
        name = kw["tool_choice"]["name"]
        user = kw["messages"][0]["content"]
        data = read_data(user)
        d = decide(data)
        by_id = {it.get("id"): it for it in data.get("evidence") or [] if isinstance(it, dict)}
        cid = (data.get("candidate") or {}).get("id", "")

        def refs(ids):
            return [{"id": i, "source": str(by_id[i].get("source") or by_id[i].get("kind") or "pack"),
                     "published_at": str(by_id[i].get("published_at") or "")} for i in ids[:8]]

        flag_ids = [i for i, _ in d["flags"]]
        if name == "submit_proposer_case":
            ans = {"candidate_id": cid, "thesis": d["why"] if d["support"] else "no supporting evidence in the pack",
                   "cause": d["cause"] if d["support"] else "noise", "evidence": refs(d["support"])}
        elif name == "submit_critic_case":
            ans = {"candidate_id": cid, "strongest_counter": d["why"] if d["verdict"] != "allow" else "none found",
                   "cause": d["cause"], "red_flags": [t for _, t in d["flags"]][:6], "evidence": refs(flag_ids)}
        else:
            ans = {"candidate_id": cid, "verdict": d["verdict"], "cause": d["cause"], "confidence": d["confidence"],
                   "invalidation": "the stock makes a new low of the day", "horizon_minutes": 90,
                   "evidence_ids": (d["support"] + flag_ids)[:8]}
            if self.misbehave == "sizing":
                ans |= {"verdict": "allow", "cause": "sector", "size": 2.0, "stop": 0.01, "price": 1.0, "qty": 999}
            elif self.misbehave == "double":
                ans["verdict"] = "double"
            elif self.misbehave == "invent":
                ans["evidence_ids"] = ["made-up-id"]
        sent = len(kw["system"][0]["text"]) + len(user) + len(json.dumps(kw["tools"]))
        usage = SimpleNamespace(input_tokens=int(sent / CHARS_PER_TOKEN) + 1,
                                output_tokens=int(len(json.dumps(ans)) / CHARS_PER_TOKEN) + 1,
                                cache_read_input_tokens=0, cache_creation_input_tokens=0)
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", name=name, input=ans)], usage=usage)
