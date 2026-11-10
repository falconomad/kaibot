from dataclasses import replace
from datetime import time

from kaibot.strategies.dip_reversal import DipFeatures, Regime, rejects, signal

F = DipFeatures("MSFT", 82, 9e9, 1.2, -2.1, 0.4, 1.1, False, True, 400.0, 1.2, 403.0, time(11, 5))
R = Regime(False, False, 0.002, 18, False)


def test_clean_setup_signals():
    s = signal(F, R)
    assert s and s.stop == 398.2 and s.target == 402.7 and s.size_mult == 1.0


def test_company_news_blocks():
    assert "company_news" in rejects(replace(F, company_news_24h=True), R)


def test_high_turnover_blocks():
    assert signal(replace(F, turnover_ratio=2.4), R) is None


def test_bad_tape_turns_strategy_off():
    assert "regime_off" in rejects(F, replace(R, spy_below_vwap=True, spy_first30_ret=-0.012))


def test_high_vix_halves_size():
    assert signal(F, replace(R, vix=28)).size_mult == 0.5
