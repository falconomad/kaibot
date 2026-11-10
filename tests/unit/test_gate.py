import pytest

from kaibot.risk.gate import Proposal, RiskPolicy, Side, Snapshot, evaluate

POL = RiskPolicy(risk_per_trade_pct=0.5, max_position_pct=10, max_open_risk_pct=2, max_daily_loss_pct=1.5,
                 max_positions=8)


def snap(**kw):
    base = dict(equity=100_000, day_start_equity=100_000, open_positions=0, symbol_exposure=0, open_risk=1420,
                quote_age_s=5, kill_switch=False, reconciled=True, market_open=True)
    base.update(kw)
    return Snapshot(**base)


def test_position_cap_binds_worked_example():
    v = evaluate(Proposal("KTOS", Side.BUY, 61.20, 58.40), snap(open_risk=0), POL)
    assert v.approved and v.qty == 163 and v.binding == "position_cap"
    assert v.max_loss == 456.40


def test_kill_switch_blocks():
    assert not evaluate(Proposal("X", Side.BUY, 10, 9), snap(kill_switch=True), POL).approved


def test_stale_quote_blocks():
    assert not evaluate(Proposal("X", Side.BUY, 10, 9), snap(quote_age_s=6), POL).approved


def test_daily_loss_halts_new_entries():
    assert not evaluate(Proposal("X", Side.BUY, 10, 9), snap(equity=98_400), POL).approved


@pytest.mark.parametrize("entry,stop_frac,equity", [(61.2, 0.954, 1e5), (5.0, 0.99, 5000), (900, 0.5, 2e6),
                                                     (1.01, 0.999, 1000), (333.3, 0.97, 25_000)])
def test_never_exceeds_risk_or_position(entry, stop_frac, equity):
    _check(entry, stop_frac, equity)


def test_property_never_exceeds_risk_or_position():
    hyp = pytest.importorskip("hypothesis")
    st = hyp.strategies

    @hyp.given(entry=st.floats(1, 1000), stop_frac=st.floats(0.5, 0.999), equity=st.floats(1000, 1e7))
    def prop(entry, stop_frac, equity):
        _check(entry, stop_frac, equity)

    prop()


def _check(entry, stop_frac, equity):
    p = Proposal("X", Side.BUY, entry, entry * stop_frac)
    v = evaluate(p, snap(equity=equity, day_start_equity=equity, open_risk=0), POL)
    if v.approved:
        assert v.max_loss <= equity * 0.005 + 1e-6
        assert v.qty * entry <= equity * 0.10 + 1e-6


def test_open_risk_limited_by_room_to_daily_stop():
    # down 1.2% on the day with a 1.5% stop: only 0.3% of risk room is left
    v = evaluate(Proposal("X", Side.BUY, 100, 90), snap(equity=98_800, open_risk=0), POL)
    assert v.approved and v.qty == 30 and v.binding == "open_risk_headroom"


def test_existing_open_risk_eats_the_room():
    # 1.42% already at risk against a 1.5% daily stop: only ~$80 of new risk fits
    v = evaluate(Proposal("KTOS", Side.BUY, 61.20, 58.40), snap(), POL)
    assert v.approved and v.qty == 28 and v.max_loss <= 80


def test_entries_cap_and_pump_risk_and_duplicates_block():
    pol = RiskPolicy(0.5, 10, 2, 1.5, 8, max_entries_per_day=2)
    assert not evaluate(Proposal("X", Side.BUY, 10, 9), snap(entries_today=2), pol).approved
    assert not evaluate(Proposal("X", Side.BUY, 10, 9), snap(pump_risk=True), POL).approved
    assert not evaluate(Proposal("X", Side.BUY, 10, 9), snap(has_open_position_in_symbol=True), POL).approved


def test_run_drawdown_ends_run():
    assert not evaluate(Proposal("X", Side.BUY, 10, 9), snap(equity=92_000, day_start_equity=92_000,
                                                             run_peak_equity=100_000), POL).approved


def test_profiles_cannot_loosen_ceilings():
    from kaibot.risk.gate import validate_policy
    with pytest.raises(ValueError):
        validate_policy(RiskPolicy(2.0, 10, 2, 1.5, 5))


@pytest.mark.parametrize("level", ["conservative", "balanced", "aggressive"])
def test_property_level_presets_cap_losses(level):
    """Story S10: under every preset, an approved trade never risks more than that level's risk per trade,
    never exceeds its position cap, and nothing is approved once the day's loss hits its daily stop."""
    hyp = pytest.importorskip("hypothesis")
    from kaibot.risk.profiles import load_profiles

    pol = load_profiles()[level]
    st = hyp.strategies

    @hyp.settings(max_examples=300, deadline=None)
    @hyp.given(entry=st.floats(1, 1000), stop_frac=st.floats(0.5, 0.999), equity=st.floats(1000, 1e7),
               day_loss_frac=st.floats(0, 0.05), open_risk_frac=st.floats(0, 0.03),
               positions=st.integers(0, 10))
    def prop(entry, stop_frac, equity, day_loss_frac, open_risk_frac, positions):
        start = equity / (1 - day_loss_frac) if day_loss_frac < 1 else equity
        s = snap(equity=equity, day_start_equity=start, open_risk=equity * open_risk_frac,
                 open_positions=positions)
        v = evaluate(Proposal("X", Side.BUY, entry, entry * stop_frac), s, pol)
        if not v.approved:
            return
        assert v.max_loss <= equity * pol.risk_per_trade_pct / 100 + 1e-6
        assert v.qty * entry <= equity * pol.max_position_pct / 100 + 1e-6
        assert positions < pol.max_positions
        assert (start - equity) < start * pol.max_daily_loss_pct / 100

    prop()
