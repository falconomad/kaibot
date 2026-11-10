from kaibot.agents.budget import BudgetGateway, BudgetState, ModelPrice

PRICES = {"sonnet": ModelPrice(2.0, 10.0), "haiku": ModelPrice(0.10, 0.50)}


def test_reserve_settle_and_daily_soft_cap():
    g = BudgetGateway(PRICES, daily_soft_usd=0.10)
    r = g.reserve("sonnet", 6000, 600)  # worst case 0.012 + 0.006 = 0.018
    assert abs(r - 0.018) < 1e-12 and g.state() is BudgetState.OK
    g.settle(r, 0.01)
    assert g.reserved == 0 and abs(g.spent_today - 0.01) < 1e-12
    while (x := g.reserve("sonnet", 6000, 600)) is not None:
        g.settle(x, x)
    assert g.state() is BudgetState.RULES_ONLY


def test_monthly_hard_stop_means_no_entries():
    g = BudgetGateway(PRICES, spent_month=190.0)
    assert g.reserve("haiku", 1000, 100) is None and g.state() is BudgetState.NO_ENTRIES
