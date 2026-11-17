"""Offline cost of the pre-open review, and cheaper variants beside it (issue #299). No network, no key, no spend.

Nothing here is imported by the engine and nothing here changes the review: it stays off by default, and the prompt
the live path would send (`agents/preopen.GatewayReviewer`) is untouched. Run it with

    python -m kaibot.agents.evalset.preopen_cost

What the numbers are, exactly:
- **Input tokens are ESTIMATES** from `gateway.estimate_tokens`, the same over-estimator the gateway reserves with
  (characters / 3 + 50; real English is nearer 4 characters a token, so real input is likely about a quarter lower).
  The repository holds no recorded model answers and no recorded usage for the pre-open review.
- **Output tokens are ESTIMATES** from the JSON of the answer `evalset.preopen.StubReviewer` gives (short reasons),
  and from the same answer with every reason at the contract's 200 characters ("long"). The cap is
  `limits.preopen.max_tokens`.
- **Prices** come only from `config/llm.yaml`. **Budget caps** come from `agents/budget.BudgetGateway` (the same
  numbers as `config/llm.yaml`, which a test checks).
- **Cache**: `Gateway.call` marks the system prompt as cacheable. Whether the API then bills the fixed part (tool
  schema + system prompt) as a cache write, a cache read or plain input cannot be seen offline, so every cost is
  given for all three. With one call per rule per session nothing is read back from the cache.
- **Quality** is the eval set's `expected_match` with a reviewer that reads only what the variant's prompt shows
  (`PackReader`: the pack is rendered, parsed back and given to the stub's written policy). It proves that a pack
  still carries what the written policy needs; it says nothing about how a real model answers. A variant that
  changes the model or the number of calls has no offline quality number at all.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date

from kaibot.agents.budget import BudgetGateway, ModelPrice
from kaibot.agents.contracts import PreopenVerdicts
from kaibot.agents.evalset.preopen import ON_ENV, StubReviewer, evaluate, load_lists
from kaibot.agents.gateway import LLMConfig, Price, cost_of, estimate_tokens
from kaibot.agents.preopen import PREOPEN_TOOL, SYSTEM, ReviewCandidate, candidates, preopen_limits, render
from kaibot.agents.room import _clean_deep, _escape
from kaibot.strategies.base import TargetList

SESSIONS = 21  # one month of sessions, as the story asks
ASK = "Review every candidate above."
PACKS = ("today", "compact", "hoisted")
CACHE_MODES = ("plain", "cache_write", "cache_read")


# ---------------------------------------------------------------- packs: the same data, laid out more cheaply

def _wrap(data: dict, indent: int | None) -> str:
    seps = None if indent else (",", ":")
    return "<data>\n" + _escape(json.dumps(data, default=str, indent=indent, separators=seps))[:60_000] + "\n</data>"


def render_pack(pack: str, session: date, cands: list[ReviewCandidate]) -> str:
    """The DATA block of a variant. "today" is `preopen.render` itself. "compact" is the same JSON without
    indentation. "hoisted" is compact, names the session once, groups the candidates by rule so a rule's reasons
    are written once, and leaves out `strategy` and `symbol` (both are in the candidate id)."""
    if pack == "today":
        return render(session, cands)
    shown = [_clean_deep(c.shown(), 300) for c in cands]
    if pack == "compact":
        return _wrap({"session": session.isoformat(), "candidates": shown}, None)
    if pack != "hoisted":
        raise ValueError(f"unknown pack {pack!r}")
    rules: list[dict] = []
    for s in shown:
        if not rules or rules[-1]["strategy"] != s["strategy"] or rules[-1]["reasons"] != s["reasons"]:
            rules.append({"strategy": s["strategy"], "reasons": s["reasons"], "candidates": []})
        rules[-1]["candidates"].append({k: v for k, v in s.items()
                                        if k not in ("strategy", "symbol", "session", "reasons")})
    return _wrap({"session": session.isoformat(), "rules": rules}, None)


def decode_pack(pack: str, text: str) -> dict:
    """A variant's DATA block back in today's shape ({"session", "candidates": [shown, ...]}), so two packs can be
    compared field for field. Raises when the block was cut off (over 60 000 characters) or is not the pack."""
    data = json.loads(text.split("<data>\n", 1)[1].rsplit("\n</data>", 1)[0])
    if pack in ("today", "compact"):
        return data
    out = []
    for r in data["rules"]:
        for c in r["candidates"]:
            symbol = c["id"][len(r["strategy"]) + 1:]
            out.append({"id": c["id"], "strategy": r["strategy"], "symbol": symbol, "session": data["session"],
                        "kind": c["kind"], "rank": c["rank"], "reasons": r["reasons"], "features": c["features"],
                        **({"evidence": c["evidence"]} if "evidence" in c else {})})
    return {"session": data["session"], "candidates": out}


def lossless(pack: str, session: date, cands: list[ReviewCandidate]) -> bool:
    """True when the pack, parsed back, is field for field what today's pack shows."""
    try:
        return decode_pack(pack, render_pack(pack, session, cands)) == decode_pack("today", render(session, cands))
    except (ValueError, KeyError, IndexError):
        return False


def cut_off(pack: str, session: date, cands: list[ReviewCandidate]) -> bool:
    """True when the pack is longer than the 60 000 characters `preopen.render` keeps: the JSON is cut mid-list,
    the model cannot answer for the candidates it never saw, and the live review would fail closed (no buys)."""
    try:
        return len(decode_pack(pack, render_pack(pack, session, cands))["candidates"]) != len(cands)
    except (ValueError, KeyError, IndexError):
        return True


def largest_list_that_fits(pack: str, news: int, upto: int = 50) -> int:
    """The largest synthetic list (`scenario`, `news` stories per buy) whose pack is not cut off."""
    fits = 0
    for n in range(1, upto + 1):
        if cut_off(pack, *scenario(n, news)):
            break
        fits = n
    return fits


class PackReader:
    """A reviewer that sees only the rendered prompt of a pack: it parses the DATA block back and gives the
    candidates found there to the stub's written policy. If a pack drops something the policy needs, the eval
    set's `expected_match` falls; if it drops or renames a candidate, the review fails closed."""

    def __init__(self, pack: str):
        self.pack, self.stub = pack, StubReviewer()

    def __call__(self, session: date, cands: list[ReviewCandidate]) -> PreopenVerdicts:
        data = decode_pack(self.pack, render_pack(self.pack, session, cands))
        seen = [ReviewCandidate(c["id"], c["strategy"], c["symbol"], date.fromisoformat(c["session"]), c["kind"],
                                c["rank"], tuple(c["reasons"]), dict(c["features"]), tuple(c.get("evidence") or ()))
                for c in data["candidates"]]
        return self.stub(date.fromisoformat(data["session"]), seen)


# ---------------------------------------------------------------- one call: tokens and cost

@dataclass(frozen=True)
class Call:
    """One model call, sized offline. All token counts are estimates (see the module text)."""

    model: str
    candidates: int
    fixed_in: int  # tool schema + system prompt (+ the estimator's flat 50): the part a cache could hold
    pack_in: int  # the DATA block and the closing ask
    out: int  # the stub's answer
    out_long: int  # the same answer with every reason at the contract's 200 characters
    max_tokens: int
    cut_off: bool = False  # the DATA block is over preopen.render's 60 000 characters: the model sees a cut list

    @property
    def tokens_in(self) -> int:
        return self.fixed_in + self.pack_in

    def cost(self, price: Price, cache: str = "plain", long: bool = False) -> float:
        out = self.out_long if long else self.out
        if cache == "plain":
            return cost_of(price, self.tokens_in, out)
        if cache == "cache_write":
            return cost_of(price, self.pack_in, out, cache_write=self.fixed_in)
        if cache == "cache_read":
            return cost_of(price, self.pack_in, out, cache_read=self.fixed_in)
        raise ValueError(f"unknown cache mode {cache!r}")

    def reserved(self, price: Price) -> float:
        """What `Gateway.call` reserves before the call (its own formula; a test holds the two together)."""
        worst = max(cost_of(price, 0, self.max_tokens, cache_write=self.tokens_in),
                    cost_of(price, self.tokens_in, self.max_tokens))
        return round(worst * 1.1 + 1e-5, 5)


def _answer_tokens(session: date, cands: list[ReviewCandidate]) -> tuple[int, int]:
    ans = StubReviewer()(session, cands).model_dump(mode="json")
    long = {**ans, "verdicts": [{**v, "reason": "x" * 200} for v in ans["verdicts"]]}
    return estimate_tokens(json.dumps(ans)), estimate_tokens(json.dumps(long))


def size_call(model: str, pack: str, session: date, cands: list[ReviewCandidate], max_tokens: int) -> Call:
    """The gateway's own input estimate for this prompt, split into the fixed part and the pack."""
    tool = json.dumps(PREOPEN_TOOL)
    user = f"{render_pack(pack, session, cands)}\n\n{ASK}"
    total, fixed = estimate_tokens(SYSTEM, user, tool), estimate_tokens(SYSTEM, tool)
    out, out_long = _answer_tokens(session, cands)
    return Call(model, len(cands), fixed, total - fixed, out, out_long, max_tokens, cut_off(pack, session, cands))


# ---------------------------------------------------------------- variants

@dataclass(frozen=True)
class Variant:
    name: str
    what: str
    pack: str = "today"
    first_pass: bool = False  # the triage model reads every candidate; the room model only those it did not allow


VARIANTS = (
    Variant("today", "one call to the room model, indented JSON pack (what GatewayReviewer sends)"),
    Variant("compact", "same call, the same JSON without indentation", pack="compact"),
    Variant("hoisted", "same call, compact, session and a rule's reasons written once, no repeated id parts",
            pack="hoisted"),
    Variant("triage_first", "the triage model reads every candidate; the room model reviews only those it did not "
            "allow", first_pass=True),
)


def variant(name: str) -> Variant:
    return next(v for v in VARIANTS if v.name == name)


def flagged_by_stub(session: date, cands: list[ReviewCandidate]) -> list[ReviewCandidate]:
    """The candidates the stub's written policy does not allow: the stand-in for "the first pass flagged it"."""
    ans = StubReviewer()(session, cands)
    hit = {v.candidate_id for v in ans.verdicts if v.verdict != "allow"}
    return [c for c in cands if c.candidate_id in hit]


def calls_for(v: Variant, session: date, cands: list[ReviewCandidate], cfg: LLMConfig,
              flagged: list[ReviewCandidate] | None = None) -> list[Call]:
    """The model calls one review makes under a variant (none when no buy grows). `flagged`: the candidates a
    first pass sends on to the room model (default: those the stub does not allow)."""
    if not cands:
        return []
    lim, err = preopen_limits(type("_Cfg", (), {"cfg": cfg})())
    if lim is None:
        raise ValueError(err)
    room, triage = cfg.models["room"], cfg.models["triage"]
    if not v.first_pass:
        return [size_call(room, v.pack, session, cands, lim["max_tokens"])]
    first = size_call(triage, v.pack, session, cands, lim["max_tokens"])
    cap = cfg.prices[triage].max_prompt_tokens
    if cap and first.tokens_in > cap:
        raise ValueError(f"first pass of {first.tokens_in} tokens is over the triage model's {cap}-token tier")
    flagged = flagged_by_stub(session, cands) if flagged is None else flagged
    return [first] + ([size_call(room, v.pack, session, flagged, lim["max_tokens"])] if flagged else [])


def review_cost(calls: list[Call], cfg: LLMConfig, cache: str = "plain", long: bool = False) -> float:
    return sum(c.cost(cfg.prices[c.model], cache, long) for c in calls)


# ---------------------------------------------------------------- the eval set and sizing scenarios

def eval_reviews(lists: list[dict] | None = None) -> list[tuple[str, date, list[ReviewCandidate]]]:
    """(case id, session, candidates) for every stored list, built as `preopen.review` builds them."""
    lists = load_lists() if lists is None else lists
    return [(c["case_id"], c["session"], candidates(c["session"], c["targets"], c["plan"], c["current"]))
            for c in lists]


def scenario(n: int, news: int, session: date = date(2026, 11, 18), strategy: str = "trend_momentum",
             sells: int = 3) -> tuple[date, list[ReviewCandidate]]:
    """A SYNTHETIC sizing scenario, not part of the eval set: `n` new buys of one rule, each with `news` ticker
    stories, laid out like a live day (the rule's reasons are its sells, repeated for every candidate; every 5th
    candidate reports earnings inside two sessions, so a first pass has something to flag)."""
    syms = [f"SYN{i:02d}" for i in range(n)]
    feats = {s: {"score": round(2.0 - i * 0.03, 2), "ret_12_1": round(0.6 - i * 0.01, 3),
                 "earnings_in_days": 1 if i % 5 == 0 else 20 + i, "sector_etf": "XLK"} for i, s in enumerate(syms)}
    t = TargetList(strategy, session, {s: 1.0 - i * 0.001 for i, s in enumerate(syms)},
                   reasons=tuple(f"sell OLD{i:02d}: rank" for i in range(sells)), features=feats)
    ev = {s: [{"id": f"news:{1000 + i * 10 + k}", "scope": "ticker", "source": "synthetic-wire",
               "published_at": f"2026-11-17T{12 + k:02d}:05:00+00:00", "n_outlets": 1 + k % 3,
               "headline": f"{s} synthetic headline {k}: company update of a typical wire-story length for sizing"}
              for k in range(news)] for i, s in enumerate(syms)}
    return session, candidates(session, [t], {(strategy, s): 10 for s in syms}, {}, ev)


def quality(v: Variant, lists: list[dict] | None = None) -> dict:
    """Eval-set quality of a variant, or why there is none. See the module text for what it does and does not show."""
    if v.first_pass:
        return {"measurable": False, "expected_match": None, "contract_violations": None, "lossless": None,
                "why": "needs answers from the triage model; the repository has none and the stub is not a model"}
    lists = load_lists() if lists is None else lists
    r = evaluate(lists, reviewer=PackReader(v.pack))
    same = all(lossless(v.pack, s, c) for _, s, c in eval_reviews(lists))
    return {"measurable": True, "expected_match": r["expected_match"], "statuses": r["statuses"],
            "contract_violations": len(r["contract_violations"]), "lossless": same, "why": None}


def measure(v: Variant, reviews: list[tuple[date, list[ReviewCandidate]]], cfg: LLMConfig | None = None,
            flagged_fn=None) -> dict:
    """Token and cost totals of a variant over reviews. Reviews with no candidate make no call and cost nothing;
    the per-review means are over the reviews that make a call. `flagged_fn(session, candidates)`: what a first
    pass sends on (default: what the stub does not allow)."""
    cfg = cfg or LLMConfig.load()
    per = [calls_for(v, s, c, cfg, flagged_fn(s, c) if flagged_fn else None) for s, c in reviews]
    called = [calls for calls in per if calls]
    n = max(1, len(called))
    flat = [c for calls in called for c in calls]
    cands = sum(calls[0].candidates for calls in called)
    out = {"variant": v.name, "reviews": len(reviews), "reviews_with_a_call": len(called), "candidates": cands,
           "calls": len(flat), "cut_off": sum(c.cut_off for c in flat),
           "tokens_in": sum(c.tokens_in for c in flat), "fixed_in": sum(c.fixed_in for c in flat),
           "pack_in": sum(c.pack_in for c in flat), "tokens_out": sum(c.out for c in flat),
           "tokens_out_long": sum(c.out_long for c in flat),
           "reserved_usd": sum(c.reserved(cfg.prices[c.model]) for c in flat) / n}
    for k in ("tokens_in", "fixed_in", "pack_in", "tokens_out", "tokens_out_long"):
        out[f"{k}_per_review"] = out[k] / n
    out["pack_in_per_candidate"] = out["pack_in"] / max(1, cands)
    out["tokens_out_per_candidate"] = out["tokens_out"] / max(1, cands)
    out["tokens_out_long_per_candidate"] = out["tokens_out_long"] / max(1, cands)
    for mode in CACHE_MODES:
        out[f"usd_{mode}"] = sum(review_cost(calls, cfg, mode) for calls in called) / n
        out[f"usd_{mode}_long"] = sum(review_cost(calls, cfg, mode, long=True) for calls in called) / n
    return out


def budget(cfg: LLMConfig | None = None) -> BudgetGateway:
    """`agents/budget.BudgetGateway` with its own caps and the configured prices."""
    cfg = cfg or LLMConfig.load()
    return BudgetGateway({m: ModelPrice(p.input, p.output) for m, p in cfg.prices.items()})


def against_budget(usd_per_review: float, reserved_usd: float, b: BudgetGateway | None = None) -> dict:
    """One review and SESSIONS reviews (one rule) against the caps in `agents/budget.py`."""
    b = b or budget()
    month = usd_per_review * SESSIONS
    return {"usd_review": usd_per_review, "usd_month": month, "share_of_daily_cap": usd_per_review / b.daily_soft_usd,
            "share_of_monthly_hard_cap": month / b.monthly_hard_usd,
            "share_of_monthly_alert": month / b.monthly_alert_usd,
            "reservations_inside_daily_cap": math.floor(b.daily_soft_usd / reserved_usd) if reserved_usd else None}


# ---------------------------------------------------------------- the report

def _usd(x: float) -> str:
    return f"${x:.5f}"


def report(cfg: LLMConfig | None = None) -> str:
    """The measurement and the comparison as Markdown tables (what the PR of #299 shows)."""
    cfg = cfg or LLMConfig.load()
    lists = load_lists()
    ev = eval_reviews(lists)
    b = budget(cfg)
    room, triage = cfg.models["room"], cfg.models["triage"]
    today = variant("today")
    lines = [f"Models: room `{room}`, triage `{triage}`; prices and limits from config/llm.yaml. All tokens are "
             "estimates (characters / 3 + 50, the gateway's reservation estimator); no model was called.", "",
             "### Today, per stored list of the eval set (preopen_v1)", "",
             "| list | candidates | input | fixed part | pack | pack / candidate | output | output / candidate "
             "| cost plain | cost cache write | reserved |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for cid, s, c in ev:
        calls = calls_for(today, s, c, cfg)
        if not calls:
            lines.append(f"| {cid} | 0 | no call | | | | | | $0 | $0 | $0 |")
            continue
        k, p = calls[0], cfg.prices[room]
        lines.append(f"| {cid} | {k.candidates} | {k.tokens_in} | {k.fixed_in} | {k.pack_in} | "
                     f"{k.pack_in / k.candidates:.0f} | {k.out} | {k.out / k.candidates:.0f} | "
                     f"{_usd(k.cost(p))} | {_usd(k.cost(p, 'cache_write'))} | {_usd(k.reserved(p))} |")
    def earnings(_s, cands):  # the scenario's every-5th candidate: an ASSUMED 20 % sent on by a first pass
        return [c for c in cands if (c.features.get("earnings_in_days") or 99) <= 2]

    def everything(_s, cands):
        return list(cands)

    sets = [("eval set (mean of the lists with a call)", [(s, c) for _, s, c in ev], None)]
    sets += [(f"synthetic sizing, {n} buys x {m} news each (not the eval set)", [scenario(n, m)], earnings)
             for n, m in ((10, 0), (10, 8), (20, 8), (30, 8))]
    for title, reviews, flagged in sets:
        lines += ["", f"### Variants on: {title}", "",
                  "| variant | calls | input / review | pack / candidate | output / review (stub; long) | "
                  "cost / review plain | cache write | cache read | long answer, plain | "
                  f"{SESSIONS} sessions plain | of monthly hard cap | reserved / review | vs today | pack cut off |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        base = measure(today, reviews, cfg)["usd_plain"]
        rows = [(v.name, v, flagged) for v in VARIANTS]
        if flagged is not None:
            rows[-1] = ("triage_first, 20 % sent on (assumed)", VARIANTS[-1], flagged)
            rows.append(("triage_first, all sent on (worst)", VARIANTS[-1], everything))
        for name, v, fn in rows:
            m = measure(v, reviews, cfg, fn)
            ab = against_budget(m["usd_plain"], m["reserved_usd"], b)
            lines.append(
                f"| {name} | {m['calls'] / max(1, m['reviews_with_a_call']):.1f} | {m['tokens_in_per_review']:.0f} "
                f"| {m['pack_in_per_candidate']:.0f} | {m['tokens_out_per_review']:.0f}; "
                f"{m['tokens_out_long_per_review']:.0f} | {_usd(m['usd_plain'])} | {_usd(m['usd_cache_write'])} | "
                f"{_usd(m['usd_cache_read'])} | {_usd(m['usd_plain_long'])} | {_usd(ab['usd_month'])} | "
                f"{ab['share_of_monthly_hard_cap']:.3%} | {_usd(m['reserved_usd'])} | "
                f"{(m['usd_plain'] / base - 1) if base else 0:+.0%} | {'YES' if m['cut_off'] else 'no'} |")
    lines += ["", "### Largest synthetic list whose pack is not cut off at 60 000 characters", "",
              "| pack | 4 news each | 8 news each |", "|---|---|---|"]
    lines += [f"| {p} | {largest_list_that_fits(p, 4)} | {largest_list_that_fits(p, 8)} |" for p in PACKS]
    lines += ["", "### Eval-set quality (preopen_v1, stub policy on what each prompt shows)", "",
              "| variant | expected_match | statuses | contract violations | same data as today | note |",
              "|---|---|---|---|---|---|"]
    for v in VARIANTS:
        q = quality(v, lists)
        if not q["measurable"]:
            lines.append(f"| {v.name} | not measurable offline | | | | {q['why']} |")
        else:
            lines.append(f"| {v.name} | {q['expected_match']:.2f} | {q['statuses']} | {q['contract_violations']} | "
                         f"{'yes' if q['lossless'] else 'NO'} | plumbing only: says nothing about a real model |")
    lines += ["", f"Budget (agents/budget.py): daily soft cap ${b.daily_soft_usd:.2f}, monthly alert "
              f"${b.monthly_alert_usd:.2f}, monthly hard cap ${b.monthly_hard_usd:.2f}; "
              f"limits.preopen.max_tokens {cfg.limits['preopen']['max_tokens']}."]
    return "\n".join(lines)


__all__ = ["ASK", "ON_ENV", "PACKS", "SESSIONS", "VARIANTS", "Call", "PackReader", "Variant", "against_budget",
           "budget", "calls_for", "cut_off", "decode_pack", "eval_reviews", "flagged_by_stub",
           "largest_list_that_fits", "lossless", "measure", "quality", "render_pack", "report", "review_cost",
           "scenario", "size_call", "variant"]

if __name__ == "__main__":
    print(report())
