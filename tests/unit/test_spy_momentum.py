from kaibot.strategies.spy_momentum import MomentumInputs, should_buy


def test_buys_only_on_strong_morning_and_once():
    assert should_buy(MomentumInputs(0.006, 0.008, False, False))
    assert not should_buy(MomentumInputs(0.002, 0.008, False, False))
    assert not should_buy(MomentumInputs(0.006, 0.008, False, True))
    assert not should_buy(MomentumInputs(0.006, 0.008, True, False))
