import pytest
from pydantic import ValidationError

from kaibot.agents.contracts import Confidence, DropCause, JudgeVerdict, admissible


def v(**kw):
    base = dict(candidate_id="c1", verdict="allow", cause=DropCause.MACRO, confidence=Confidence.MEDIUM,
                invalidation="SPY loses VWAP", horizon_minutes=60)
    base.update(kw)
    return JudgeVerdict(**base)


def test_company_news_dip_is_never_admissible():
    assert not admissible(v(cause=DropCause.COMPANY_NEWS), "dip_reversal")
    assert admissible(v(), "dip_reversal")


def test_bad_verdict_rejected():
    with pytest.raises(ValidationError):
        v(verdict="buy_everything")
