"""M12 3.9 (#471): the pre-open shadow review in logging mode for the pre-registered negative-news veto.

Pinned model, every call recorded (model, served model, prompt hash, fingerprint, input), a masked arm beside the
raw one, the 100-item golden set with its re-run guard, and the forward-only judgement with its stopping rule.
Offline: FakeStore, a scripted model, no network, no key, no spend.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

import pytest
import yaml
from fakes import FakeLLM, FakeStore

from kaibot.agents import preopen
from kaibot.agents import preopen_shadow as ps
from kaibot.agents import veto_prereg as vp
from kaibot.agents.evalset import golden
from kaibot.agents.gateway import Gateway, LLMConfig
from kaibot.agents.preopen import PREOPEN_TOOL, GatewayReviewer, ReviewOutcome
from kaibot.agents.preopen_mask import Masker, alias_for, mask_text
from kaibot.engine import m3glue
from kaibot.strategies.base import TargetList

ROOT = Path(__file__).resolve().parents[1]
CFG = LLMConfig.load()
D = date(2026, 11, 18)
R = "trend_momentum"
PLANNED = {"run_id": 1, "strategy": R, "session": str(D), "decided": "2026-11-17",
           "targets": [["AAPL", 10, "buy", None], ["MSFT", 6, "buy", None], ["Z", 0, "sell", "x"]]}
HELD = {"MSFT": (3.0, 10.0), "Z": (4.0, 30.0)}
TL = TargetList(R, date(2026, 11, 17), {"AAPL": 0.5, "MSFT": 0.3}, reasons=("12-1 momentum",))
NOW = datetime(2026, 11, 18, 20, 51)
NEWS = {"AAPL": [{"id": "n1", "scope": "ticker", "source": "x", "published_at": "2026-11-18T12:00:00Z",
                  "headline": "Apple cuts outlook; AAPL falls as Apple's iPhone sales slow"}],
        "MSFT": [{"id": "n2", "scope": "ticker", "source": "x", "published_at": "2026-11-18T12:00:00Z",
                  "headline": "Microsoft (MSFT) wins cloud contract"}]}


class PinnedLLM(FakeLLM):
    """The scripted model, with the response's `model` and `id` as the API returns them."""

    def __init__(self, *a, served="claude-sonnet-5-5", **k):
        super().__init__(*a, **k)
        self.served = served

    def create(self, **kw):
        r = super().create(**kw)
        r.model, r.id = self.served, f"msg_{len(self.calls)}"
        return r


def answer(veto_word: str = "outlook"):
    """Veto every candidate whose shown headline contains `veto_word`, allow the rest."""
    def ans(kw):
        data = json.loads(kw["messages"][0]["content"].split("<data>\n", 1)[1].rsplit("\n</data>", 1)[0])
        out = []
        for c in data["candidates"]:
            bad = any(veto_word in (e.get("headline") or "") for e in c.get("evidence") or [])
            out.append({"candidate_id": c["id"], "verdict": "veto" if bad else "allow",
                        "flag": "company_news" if bad else "none", "confidence": "medium",
                        "reason": f"seen {c['symbol']}", "evidence_ids": [e["id"] for e in c.get("evidence") or []]})
        return {"session": data["session"], "verdicts": out}
    return ans


def desk(masked=True, served="claude-sonnet-5-5"):
    llm = PinnedLLM({PREOPEN_TOOL["name"]: answer()}, served=served)
    gw = Gateway(FakeStore(), llm, CFG)
    return llm, ps.ShadowDesk(GatewayReviewer(gw), gw, inline=True, masked=masked,
                              evidence_fn=lambda syms, as_of: {s: NEWS[s] for s in syms if s in NEWS})


def run(d):
    d.observe(R, D, PLANNED, TL, HELD, NOW, 1)
    return d.collect(R, D)


# ------------------------------------------------------------------------------------------------ pinned and logged


def test_the_review_model_is_pinned_in_config():
    raw = yaml.safe_load((ROOT / "config" / "llm.yaml").read_text())
    assert raw["models"]["preopen"] == vp.PINNED_MODEL == preopen.review_model(CFG)
    assert vp.PINNED_MODEL in CFG.prices
    assert vp.knowledge_cutoff() == date(2026, 6, 30)
    with pytest.raises(ValueError, match="models.preopen"):
        preopen.review_model(LLMConfig(1, 20, 25, 300, {"room": "x"}, CFG.prices, CFG.limits))


def test_every_call_is_recorded_with_model_hash_and_input():
    llm, d = desk(masked=False)
    rec = run(d)
    assert len(llm.calls) == 1 and llm.calls[0]["model"] == vp.PINNED_MODEL
    call = rec["call"]
    sent = llm.calls[0]["messages"][0]["content"]
    assert call["input"] == sent and call["served_model"] == vp.PINNED_MODEL and call["response_id"] == "msg_1"
    assert call["fingerprint"] == preopen.fingerprint(vp.PINNED_MODEL) == vp.FROZEN_FINGERPRINT
    assert call["prompt_sha256"] == preopen.sha256(preopen.SYSTEM + "\n" + sent + "\n"
                                                   + json.dumps(PREOPEN_TOOL, sort_keys=True))
    assert call["prompt_version"] == preopen.PROMPT_VERSION
    assert rec["would_have"]["vetoed"] == ["AAPL"] and rec["acted"] is False
    assert "masked" not in rec


def test_a_refused_call_is_recorded_too():
    gw = Gateway(FakeStore(), None, CFG)  # no client
    _r, out, _c = preopen.review(D, [TL], {(R, "AAPL"): 10}, {}, GatewayReviewer(gw), env=ps._REVIEW_ON, gateway=gw)
    assert out.status == "no_key" and out.call["served_model"] is None and out.call["model"] == vp.PINNED_MODEL


def test_the_journalled_row_keeps_the_call_only_when_there_is_one():
    plan, cur = {(R, "AAPL"): 10}, {}
    assert "call" not in ReviewOutcome("done", D).row(plan, plan, cur)
    assert ReviewOutcome("done", D, call={"model": "m"}).row(plan, plan, cur)["call"] == {"model": "m"}


# ------------------------------------------------------------------------------------------------ the masked arm


def test_mask_text_hides_ticker_and_name():
    t = mask_text("Apple cuts outlook; AAPL falls as Apple's sales slow ($AAPL)", "AAPL", "STOCK_A", ["Apple Inc."])
    assert "Apple" not in t and "AAPL" not in t and t.count("STOCK_A") == 4
    gm = mask_text("General Motors recalls trucks; general market flat", "GM", "STOCK_B", ["General Motors Co"])
    assert gm == "STOCK_B recalls trucks; general market flat"
    assert mask_text("A new day (A) for NYSE: A", "A", "STOCK_C") == "A new day STOCK_C for STOCK_C"
    assert alias_for(0) == "STOCK_A" and alias_for(27) == "STOCK_AB"


@pytest.mark.parametrize("symbol, names, headline, gone", [  # QA on #581: leaks found in review
    ("GE", ["GE Aerospace", "General Electric Co"], "GE beats estimates; GE Aerospace raises guidance",
     ["GE", "Aerospace"]),
    ("T", ["AT&T Inc"], "AT&T outage hits millions; T shares slip", ["AT&T", "T shares"]),
    ("KO", ["Coca-Cola Co. (The)"], "Coca-Cola misses; KO falls", ["Coca", "Cola", "KO"]),
    ("BF.B", ["Brown-Forman Corp"], "Brown-Forman sales fall; BF-B and BF.B lower", ["Brown", "Forman", "BF"]),
])
def test_mask_text_hides_short_tickers_and_joined_names(symbol, names, headline, gone):
    t = mask_text(headline, symbol, "STOCK_A", names)
    for word in gone:
        assert word not in t, t


def test_the_masked_arm_sends_no_ticker_or_name_and_maps_verdicts_back():
    llm, d = desk()
    rec = run(d)
    assert len(llm.calls) == 2
    masked_sent = llm.calls[1]["messages"][0]["content"]
    for word in ("AAPL", "MSFT", "Apple", "Microsoft"):
        assert word not in masked_sent, word
    m = rec["masked"]
    assert m["status"] == "done" and m["mask_version"] == "mask-v1"
    assert m["verdicts"] == {f"{R}:AAPL": "veto", f"{R}:MSFT": "allow"}
    assert {v["candidate_id"] for v in m["answer"]["verdicts"]} == {f"{R}:AAPL", f"{R}:MSFT"}
    assert m["would_have"]["vetoed"] == ["AAPL"] and m["name_masked"] == {"AAPL": True, "MSFT": True}
    assert m["call"]["input"] == masked_sent and m["call"]["fingerprint"] == rec["call"]["fingerprint"]
    assert sorted(m["aliases"].values()) == ["AAPL", "MSFT"]
    # the raw arm, the event and the heartbeat are what they were
    assert rec["verdicts"] == m["verdicts"] and set(ps.event_body(rec)) >= {"would_veto", "reasons"}
    assert "masked" not in ps.event_body(rec)


def test_the_masked_arm_waits_for_a_raw_answer():
    llm = PinnedLLM({PREOPEN_TOOL["name"]: answer()}, fail={PREOPEN_TOOL["name"]})
    gw = Gateway(FakeStore(), llm, CFG)
    rec = run(ps.ShadowDesk(GatewayReviewer(gw), gw, inline=True, masked=True))
    assert rec["status"] == "failed" and "masked" not in rec and len(llm.calls) == 1


def test_the_masked_switch(monkeypatch):
    assert ps.masked_enabled({}) and ps.masked_enabled({"KAIBOT_PREOPEN_MASKED": "on"})
    assert not ps.masked_enabled({"KAIBOT_PREOPEN_MASKED": "off"})
    env = {"KAIBOT_PREOPEN": "shadow", "KAIBOT_PREOPEN_MASKED": "off"}
    assert m3glue.make_preopen_shadow(FakeStore(), None, env=env).masked is False
    assert m3glue.make_preopen_shadow(FakeStore(), None, env={"KAIBOT_PREOPEN": "shadow"}).masked is True
    assert ps.STALE_S > 2 * preopen.TOTAL_S


def test_masker_rewrites_only_what_is_shown():
    c = preopen.candidates(D, [TL], {(R, "AAPL"): 10}, {}, NEWS)
    m = Masker()
    (mc,) = m(c)
    assert mc.symbol == "STOCK_A" and mc.candidate_id == f"{R}:STOCK_A" and mc.features == c[0].features
    assert m.real_id(f"{R}:STOCK_A") == f"{R}:AAPL"


# ------------------------------------------------------------------------------------------------ the golden set


def test_the_golden_set_is_100_frozen_items():
    items = golden.load()
    assert items == golden.build()  # the committed file is the generator's output
    assert len(items) == 100 and len({i["case_id"] for i in items}) == 100
    assert len({i["symbol"] for i in items}) == 100
    assert sum(i["negative_news"] for i in items) == 43
    assert {i["expected"] for i in items} == {"allow", "hold_back"}
    cutoff = vp.knowledge_cutoff()
    for it in items:
        assert date.fromisoformat(it["session"]) > cutoff
        for e in it["evidence"]:
            assert e["published_at"][:10] > cutoff.isoformat()


def test_the_golden_set_is_rerun_on_any_change():
    """The latest record of golden_runs.jsonl must be for today's key: a change of model, system prompt, tool,
    prompt version, set or masking fails here until `python -m kaibot.agents.evalset.golden --record` (and, for
    the veto to count, a --live run) is committed."""
    rows = golden.runs()
    assert rows, "golden_runs.jsonl is empty"
    last = rows[-1]
    assert last["key"] == golden.key(preopen.review_model(CFG)), "the review changed: re-run the golden set"
    assert last["fingerprint"] == preopen.fingerprint(preopen.review_model(CFG))


def test_a_stub_run_covers_every_item_on_both_arms():
    from kaibot.agents.evalset.preopen import offline_gateway
    from kaibot.agents.preopen_daily import DeterministicReviewer

    res = golden.run(DeterministicReviewer(), offline_gateway())
    m = res["metrics"]
    assert m["raw"]["answered"] == m["masked"]["answered"] == 100
    assert res["invalid_sessions"] == {"raw": 0, "masked": 0}
    assert m["raw"]["negative_news_veto_rate"] == 1.0  # the stub vetoes every company story
    row = golden.record_row(res, "stub", vp.PINNED_MODEL, date(2026, 11, 22))
    assert row["ok"] is False  # only a live run can pass
    assert golden.passes({"raw": {"answered": 100, "n": 100, "agreement": 0.9, "negative_news_veto_rate": 0.85,
                                  "false_hold_back_rate": 0.1}})
    assert not golden.passes({"raw": {"answered": 99, "n": 100, "agreement": 0.9, "negative_news_veto_rate": 0.85,
                                      "false_hold_back_rate": 0.1}})


def test_masking_the_golden_set_removes_every_name():
    for d_, group in golden.sessions(golden.load()):
        _, tl, plan, evidence, names = golden._inputs(group)
        m = Masker(extra_names=names)
        for c in m(preopen.candidates(d_, [tl], plan, {}, evidence)):
            text = json.dumps(c.shown())
            for it in group:
                assert it["symbol"] not in text and it["name"] not in text


# ------------------------------------------------------------------------------------------------ the pre-registration


def test_the_code_repeats_the_pre_registration():
    text = (ROOT / "docs" / "prereg" / "preopen_negative_news_veto.md").read_text()
    assert vp.FROZEN_FINGERPRINT in text and vp.PINNED_MODEL in text
    assert preopen.fingerprint(vp.PINNED_MODEL) == vp.FROZEN_FINGERPRINT
    for phrase in ("25 and 50 vetoed buys", "100 vetoed buys", "24 months", "<= -0.5%", "< 0.05", "10 000 shuffles",
                   "seed 471", "5 sessions", "2026-11-22", "mask-v1", "agreement >= 0.85",
                   "negative-news veto rate >= 0.80", "false hold-back rate <= 0.15"):
        assert phrase in text, phrase
    assert (vp.LOOKS, vp.FINAL_N, vp.MAX_MONTHS, vp.MIN_EFFECT, vp.ALPHA, vp.HORIZON) == ((25, 50), 100, 24, -0.005,
                                                                                         0.05, 5)
    assert golden.MARKS == {"agreement": 0.85, "negative_news_veto_rate": 0.80, "false_hold_back_rate": 0.15}
    assert vp.FREEZE_DATE.isoformat() in text


LIVE = {vp.FROZEN_FINGERPRINT: "2026-11-24"}


def _rec(session, verdicts, masked=None, model=vp.PINNED_MODEL, served=vp.PINNED_MODEL, fp=vp.FROZEN_FINGERPRINT):
    ans = {"verdicts": [{"candidate_id": f"{R}:{s}", "verdict": v, "flag": "company_news" if v == "veto" else "none"}
                        for s, v in verdicts.items()]}
    rec = {"status": "done", "strategy": R, "session": session,
           "verdicts": {f"{R}:{s}": v for s, v in verdicts.items()}, "answer": ans,
           "call": {"model": model, "served_model": served, "fingerprint": fp}}
    if masked is not None:
        mans = {"verdicts": [{"candidate_id": f"{R}:{s}", "verdict": v,
                              "flag": "company_news" if v == "veto" else "none"} for s, v in masked.items()]}
        rec["masked"] = {"status": "done", "verdicts": {f"{R}:{s}": v for s, v in masked.items()}, "answer": mans}
    return rec


def test_eligibility_is_forward_only_pinned_and_golden_checked():
    start = vp.start_date(LIVE)
    assert start == date(2026, 11, 24)
    assert vp.start_date({}) is None
    assert vp.start_date({vp.FROZEN_FINGERPRINT: "2026-01-05"}, cutoff=date(2026, 6, 30)) == date(2026, 11, 23)
    assert vp.eligible(_rec("2026-11-30", {"A": "veto"}), start) is None
    assert "before the start" in vp.eligible(_rec("2026-11-23", {"A": "veto"}), start)
    assert "answered by" in vp.eligible(_rec("2026-11-30", {"A": "veto"}, served="claude-other"), start)
    assert "fingerprint" in vp.eligible(_rec("2026-11-30", {"A": "veto"}, fp="x"), start)
    assert "golden" in vp.eligible(_rec("2026-11-30", {"A": "veto"}), None)
    assert "status" in vp.eligible({**_rec("2026-11-30", {"A": "veto"}), "status": "failed"}, start)


def test_the_stopping_rule():
    s, today = date(2026, 12, 1), date(2027, 3, 1)
    worse, better = [-0.02] * 30, [0.01] * 200
    assert vp.judge(([], []), ([], []), None, today)["status"] == "not_started"
    assert vp.judge((worse[:10], better), ([], []), s, today)["status"] == "running"
    assert vp.judge(([0.03] * 25, better), ([], []), s, today)["status"] == "stopped_futility"
    late = date(2029, 1, 1)
    assert vp.judge((worse[:10], better), ([], []), s, late)["status"] == "inconclusive"
    rng = __import__("random").Random(1)
    v = [-0.02 + rng.gauss(0, 0.01) for _ in range(100)]
    a = [0.01 + rng.gauss(0, 0.01) for _ in range(300)]
    ok = vp.judge((v, a), (v, a), s, today)
    assert ok["status"] == "passed" and ok["p_value"] < 0.05 and ok["diff"] <= -0.005
    assert vp.judge((v, a), (a[:100], a), s, today)["status"] == "identity_driven"
    small = [x + 0.027 for x in v]  # negative but smaller than the mark
    assert vp.judge((small, a), (v, a), s, today)["status"] == "failed"


def test_evaluate_end_to_end_from_journal_bodies():
    recs, fills, marks = [], [], []
    sessions = [f"2026-12-{d:02d}" for d in range(1, 29)]
    for k, sess in enumerate(sessions[:20]):
        recs.append(_rec(sess, {f"V{k}": "veto", f"A{k}": "allow"}, masked={f"V{k}": "veto", f"A{k}": "allow"}))
        fills += [{"strategy": R, "symbol": f"V{k}", "side": "buy", "px": 100.0, "session": sess},
                  {"strategy": R, "symbol": f"A{k}", "side": "buy", "px": 100.0, "session": sess}]
    for sess in sessions:
        marks.append({"session": sess, "prices": {**{f"{R}|V{k}": 97.0 for k in range(20)},
                                                  **{f"{R}|A{k}": 101.0 for k in range(20)}}})
    recs.append(_rec("2026-11-20", {"OLD": "veto"}))  # before the start: left out
    res = vp.evaluate(recs, fills, marks, LIVE, date(2027, 1, 15))
    assert res["records"] == 20 and res["excluded"] == {"session before the start 2026-11-24": 1}
    assert res["vetoed"] > 0 and res["diff"] == pytest.approx(-0.04) and res["status"] == "running"


def test_the_veto_test_is_registered_in_a_family_and_never_imported_by_the_engine():
    gate = yaml.safe_load((ROOT / "config" / "gate_v3.yaml").read_text())
    assert "preopen_negative_news_veto" in gate["families"]["news_drift"]["rules"]
    for p in (ROOT / "src" / "kaibot" / "engine").rglob("*.py"):
        t = p.read_text()
        assert not re.search(r"veto_prereg|evalset\.golden", t), p
