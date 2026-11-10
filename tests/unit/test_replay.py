import numpy as np

from kaibot.replay.data import N_MIN, SymbolDays
from kaibot.replay.features import candidates, five_minute
from kaibot.replay.sim import EXIT_M, first_exit, half_spread


def flat_days(n_days=40, price=100.0, seed=1):
    rng = np.random.default_rng(seed)
    c = price * np.exp(np.cumsum(rng.normal(0, 0.0005, (n_days, N_MIN)), axis=1))
    o = np.c_[c[:, :1], c[:, :-1]]
    h, l = np.maximum(o, c) + 0.01, np.minimum(o, c) - 0.01  # noqa: E741
    v = np.full_like(c, 1e5)
    dates = np.arange(np.datetime64("2025-01-02"), np.datetime64("2025-01-02") + n_days)
    return SymbolDays("X", dates, o, h, l, c, v, c.copy())


def test_first_exit_stop_wins_ties_and_time_exit():
    h = np.full(N_MIN, 101.0)
    l = np.full(N_MIN, 99.0)  # noqa: E741
    o = c = np.full(N_MIN, 100.0)
    m, px, why = first_exit(h, l, o, c, 10, 99.5, 100.5)
    assert why == "stop" and m == 10 and px == 99.5
    m, px, why = first_exit(h, l, o, c, 10, 90.0, 120.0)
    assert why == "time" and m == EXIT_M


def test_target_needs_trade_through():
    h = np.full(N_MIN, 100.5)
    l = np.full(N_MIN, 99.9)  # noqa: E741
    o = c = np.full(N_MIN, 100.0)
    assert first_exit(h, l, o, c, 0, 90.0, 100.5)[2] == "time"
    assert first_exit(h, l, o, c, 0, 90.0, 100.49)[2] == "target"


def test_half_spread_floor():
    assert half_spread(10.0) == 0.005 and abs(half_spread(400.0) - 0.04) < 1e-12


def test_features_have_no_lookahead():
    sd, etf = flat_days(), flat_days(seed=2)
    cand_a = candidates(sd, etf, np.array([], dtype="datetime64[s]"), z_max=10, resid_abs_max=99, turnover_max=99,
                        min_adv=0)
    # change only the FUTURE of the last day after 12:00 -> candidates before 12:00 must be identical
    sd2 = flat_days()
    sd2.c[-1, 150:] *= 0.9
    sd2.l[-1, 150:] *= 0.9
    cand_b = candidates(sd2, etf, np.array([], dtype="datetime64[s]"), z_max=10, resid_abs_max=99, turnover_max=99,
                        min_adv=0)
    last = sd.dates[-1]
    a = cand_a[(cand_a.date == last) & (cand_a.m < 150)].reset_index(drop=True)
    b = cand_b[(cand_b.date == last) & (cand_b.m < 150)].reset_index(drop=True)
    assert len(a) and a.equals(b)


def test_five_minute_shapes():
    sd = flat_days(3)
    atr, hl = five_minute(sd.o, sd.h, sd.l, sd.c)
    assert atr.shape == (3, 78) and hl.shape == (3, 78)


def test_trailing_exit_moves_to_breakeven_without_lookahead():
    from kaibot.replay.sim import first_exit_trail
    c = np.full(N_MIN, 100.0)
    c[10:15] = 101.2  # closes above +1R (R = 1.0)
    c[15:] = 99.5
    o = np.r_[c[:1], c[:-1]]
    h, l = np.maximum(o, c) + 0.05, np.minimum(o, c) - 0.05  # noqa: E741
    m, px, why = first_exit_trail(h, l, o, c, 1, 100.0, 1.0, 0.5)
    assert why == "trail" and px >= 100.0 and m == 15
