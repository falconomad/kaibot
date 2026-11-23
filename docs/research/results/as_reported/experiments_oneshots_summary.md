# One-shot ten-year tests (issue #112, story S72)

Six low-prior rules, one frozen pre-registered run each (`docs/prereg/<rule>.md`); nothing tuned. **PROVISIONAL (decision 15):** the research harness of #113 with cost model v1. A rule can FAIL here; none can pass here (the go/no-go run is the engine with `SimBroker`). The ORB verdict is keep or propose deletion (issue #112).

SPY total return over the daily rules' window: CAGR 14.37%, Sharpe 0.83, max drawdown 33.78%.

| Rule / version | Universe and data | Window | Result (base version) | Pass / fail |
|---|---|---|---|---|
| `orb_stocks_in_play` | today's list, minute bars (survivorship bias) | 2017-05-01 to 2026-11-06 | 17591 trades, mean net R -0.7628, t -55.73, total R without the 10 best symbols -13423.48; account CAGR -16.71%, Sharpe -12.11 | **FAIL: propose deletion** |
| `news_continuation` | today's list, minute bars (survivorship bias); complete news months: 2017-06 to 2017-07, 2017-10, 2018-01 to 2018-02, 2018-04 to 2018-05, 2018-07 to 2018-11, 2019-01 to 2019-02, 2019-04 to 2026-10 | 2017-05-01 to 2026-11-06 | 3271 trades, mean net R -0.0681, t -2.96, total R without the 10 best symbols -338.01; account CAGR -1.48%, Sharpe -0.41; random-entry pctile 0.0 | **FAIL** |
| `pullback_uptrend` | point-in-time S&P 500, daily store | 2017-05-01 to 2026-11-06 | CAGR 4.82%, Sharpe 0.44, max DD 27.14%, 2113 trades, avg exposure 53.4%; Sharpe pctile vs exposure-matched random 46.0 | **FAIL** (mark 2, 3, 4, 5) |
| `ath_breakout` | point-in-time S&P 500, daily store | 2017-05-01 to 2026-11-06 | median seed 171 of 200: CAGR 5.36%, Sharpe 0.4, max DD 26.36%, 120 trades, avg exposure 95.3%; CAGR p5/p50/p95 2.32/5.58/9.82%, 84.5% of seeds pass mark 1; Sharpe pctile vs exposure-matched random 9.4 | **FAIL** (mark 2, 4, 5, 6) |
| `ath_breakout_k5` | point-in-time S&P 500, daily store | 2017-05-01 to 2026-11-06 | median seed 11 of 200: CAGR 4.95%, Sharpe 0.39, max DD 31.7%, 58 trades, avg exposure 80.8%; CAGR p5/p50/p95 0.09/5.23/9.74%, 75.5% of seeds pass mark 1 | reported (distribution only) |
| `ath_breakout_k20` | point-in-time S&P 500, daily store | 2017-05-01 to 2026-11-06 | median seed 60 of 200: CAGR 7.1%, Sharpe 0.52, max DD 21.54%, 227 trades, avg exposure 95.1%; CAGR p5/p50/p95 5.27/7.1/9.17%, 94.5% of seeds pass mark 1 | reported (distribution only) |
| `post_earnings_week` | point-in-time S&P 500, daily store | 2017-05-01 to 2026-11-06 | CAGR 6.01%, Sharpe 0.58, max DD 24.41%, 1772 trades, avg exposure 36.1%; Sharpe pctile vs exposure-matched random 77.2 | **FAIL** (mark 2, 3, 4, 5) |
| `weekly_fall_rebound` | point-in-time S&P 500, daily store | 2017-05-01 to 2026-11-06 | CAGR 9.7%, Sharpe 0.52, max DD 47.63%, 1072 trades, avg exposure 21.9%; Sharpe pctile vs exposure-matched random 94.6 | **FAIL** (mark 2, 4, 5) |
| `weekly_fall_rebound_20` | point-in-time S&P 500, daily store | 2017-05-01 to 2026-11-06 | CAGR 6.37%, Sharpe 0.36, max DD 55.12%, 696 trades, avg exposure 56.3%; Sharpe pctile vs exposure-matched random 12.8 | **FAIL** (mark 2, 3, 4, 5) |
| `news_gap_week` | today's list inside the point-in-time S&P 500, complete news months, daily store | 2017-05-01 to 2026-11-06 | CAGR 2.76%, Sharpe 0.54, max DD 13.94%, 532 trades, avg exposure 11.0%; Sharpe pctile vs exposure-matched random 88.4 | **FAIL** (mark 2, 3, 4, 6) |
| `news_gap_week_nonews` | today's list inside the point-in-time S&P 500, complete news months, daily store | 2017-05-01 to 2026-11-06 | CAGR 0.07%, Sharpe 0.04, max DD 9.93%, 273 trades, avg exposure 5.6% | control of `news_gap_week` (not judged on its own) |
| `news_gap_week` reading |  |  | dropped (mark 3, 4 failed) |  |

Daily rules: Gate B marks 1-6 of decision 15 section 7 on the base version (base fill, 1x costs, 100% cap), SPY total return, the exposure-matched random control (section 10); `ath_breakout` is a distribution over random signal subsets, judged on its median-Sharpe seed at 10 positions. ORB: kept only if mean net R >= +0.05 with t >= 3 and still positive without its ten best symbols. News continuation: the intraday bar of config/gate.yaml over the complete news months only.

## Notes

- `orb_stocks_in_play`: pre-registration `docs/prereg/orb_stocks_in_play.md` (sha256 `6c47db75ad3c`), report `oneshots_orb_stocks_in_play_20261113T085035.json`, code `7c0123e`, runtime 65.4 s.
- `news_continuation`: pre-registration `docs/prereg/news_continuation.md` (sha256 `533831f810a6`), report `oneshots_news_continuation_20261113T085255.json`, code `7c0123e`, runtime 141.0 s.
- `pullback_uptrend`: pre-registration `docs/prereg/pullback_uptrend.md` (sha256 `57693a0de82d`), report `oneshots_pullback_uptrend_20261113T085258.json`, code `7c0123e`, runtime 19.8 s.
- `ath_breakout`: pre-registration `docs/prereg/ath_breakout.md` (sha256 `12fe0766f5dd`), report `oneshots_ath_breakout_20261113T085319.json`, code `7c0123e`, runtime 190.4 s.
- `post_earnings_week`: pre-registration `docs/prereg/post_earnings_week.md` (sha256 `30eebcfdf162`), report `oneshots_post_earnings_week_20261113T085630.json`, code `7c0123e`, runtime 17.9 s.
- `weekly_fall_rebound`: pre-registration `docs/prereg/weekly_fall_rebound.md` (sha256 `77f35d6ed77a`), report `oneshots_weekly_fall_rebound_20261113T085648.json`, code `7c0123e`, runtime 28.6 s.
- `news_gap_week`: pre-registration `docs/prereg/news_gap_week.md` (sha256 `be0e75a6ac81`), report `oneshots_news_gap_week_20261121T171857.json`, code `8b7afd0`, runtime 9.6 s.
