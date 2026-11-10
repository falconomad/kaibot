from kaibot.broker.shadow_costs import QuoteCtx, max_fill_qty, shadow_fill_price

Q = QuoteCtx(bid=99.99, ask=100.01, sigma_1m=0.001, vol_1m=50_000)


def test_buys_pay_more_than_ask_and_sells_get_less_than_bid():
    assert shadow_fill_price("buy", 100, Q) > Q.ask
    assert shadow_fill_price("sell", 100, Q) < Q.bid


def test_impact_grows_with_size():
    assert shadow_fill_price("buy", 5000, Q) > shadow_fill_price("buy", 50, Q)


def test_participation_cap():
    assert max_fill_qty(Q) == 2500
