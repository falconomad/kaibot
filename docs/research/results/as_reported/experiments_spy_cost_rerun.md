# SPY noise band: cost re-run with the index-fund bucket

Run 2026-11-23T12:57:24+00:00 at code `0743ef0`, 2017-05-01 to 2024-12-31 (1931 sessions), level balanced. The engine with SimBroker, the frozen rule, one run.

**Not a verdict.** One thing differs from the run of issue #202: SPY's fills pay 0.20 bp a side (the measured index-fund bucket, quotes, 260 rows) in place of cost model v2's 1.75 bp. No neighbour, no control and no Gate B evaluation was run.

| Account | Sessions | Round trips | Net bp a trip | t | Cost bp a side | Return | CAGR | Sharpe | Max drawdown |
|---|---|---|---|---|---|---|---|---|---|
| **this run: v2 with the index-fund bucket** | 1931 | 888 | 4.01 | 3.03 | 0.311 | 4.18% | 0.54% | 1.19 | 0.45% |
| earlier run, v2 (same sessions, from its daily file) | 1931 | 1109* | -0.01* | -0.01* | 1.86* | 0.5% | 0.06% | 0.14 | 0.87% |
| earlier run, 0.5 (same sessions, from its daily file) | 1931 | 1110* | 2.7* | 2.31* | 0.5* | 3.72% | 0.48% | 1.06 | 0.5% |
| earlier run, 1 (same sessions, from its daily file) | 1931 | 1110* | 1.7* | 1.46* | 1.0* | 2.53% | 0.33% | 0.72 | 0.62% |

\* Round-trip figures of the earlier run are over its own window, as `spy_engine_balanced_20261121T172654.json` reports them; they cannot be cut to a window from a daily file.

- Gross edge per round trip and the break-even cost: {"per_trip_bp": 2.31, "account_bp": 2.28}.
- Engine errors 0, halts {}, orders refused by the risk gate 0. Mean position 13.16% of equity (the single-name cap applies to SPY as to any stock).
- Trial: one row, n_trials 1 (a parameter change of the cost model), `spy_cost_rerun_balanced_20261123T125724_spy_noise_band_v2idx`.
- Bucket file sha256 `362c038fa07b703e1dd70abe8ec556bc110a2b33ca8b0fbbdc9eccb7692e32a5`; pre-registration sha256 `054b6baf502bac3a9cf51c15aaab036b3a1414a61cdce1cd7b5571eaa3f8e1ed`.
- The 2022-23 window and the yearly returns are in the JSON report.
