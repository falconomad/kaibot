# 8-K filing flags: the pre-registered study (2026-11-21)

- Pre-registration `docs/prereg/filing_flags_study.md`, sha256 `aac01b5f853f14ba8b2194ba80c13d00dbfccfaf429c21774e2567dbaa8a3a66`; code `8b7afd083250b301e64549604ba5c7e3342bdb63`.
- Day 0 from 2017-05-01 to 2026-10-23; filings file newest acceptance 2026-11-20 22:10:25+00:00.
- Left out: {"amendments": 1906, "with_2_02": 24616, "g402_non_reliance_not_member_or_repeat": 2, "g401_auditor_not_member_or_repeat": 16, "g205_206_restructuring_not_member_or_repeat": 86, "g502_officers_not_member_or_repeat": 3832, "g101_agreement_not_member_or_repeat": 2685, "g301_103_delisting_bankruptcy_not_member_or_repeat": 125, "observations_dropped_no_close_or_no_store_key_per_window": 10428}.

| Group | Window | Obs | Mean abnormal return | t (obs) | t (months) | t used | Same sign (of 10 years) | Counts | Trial row |
|---|---|---|---|---|---|---|---|---|---|
| `g402_non_reliance` | w5 | 6 | 0.0111 | 0.51 | 0.51 | 0.51 | 3 | no | logged |
| `g402_non_reliance` | w20 | 6 | -0.0042 | -0.22 | -0.22 | -0.22 | 3 | no | logged |
| `g401_auditor` | w5 | 20 | 0.0023 | 0.22 | 0.13 | 0.13 | 6 | no | logged |
| `g401_auditor` | w20 | 20 | 0.0066 | 0.36 | 0.31 | 0.31 | 5 | no | logged |
| `g205_206_restructuring` | w5 | 187 | 0.0034 | 0.96 | 0.81 | 0.81 | 5 | no | logged |
| `g205_206_restructuring` | w20 | 187 | 0.0009 | 0.12 | -0.22 | 0.12 | 4 | no | logged |
| `g502_officers` | w5 | 7135 | -0.0014 | -2.75 | -1.91 | -1.91 | 9 | no | logged |
| `g502_officers` | w20 | 7135 | -0.0035 | -3.55 | -1.89 | -1.89 | 7 | no | logged |
| `g101_agreement` | w5 | 3565 | -0.0024 | -3.23 | -2.57 | -2.57 | 9 | no | logged |
| `g101_agreement` | w20 | 3565 | -0.0045 | -3.25 | -2.42 | -2.42 | 7 | no | logged |
| `g301_103_delisting_bankruptcy` | w5 | 35 | -0.0072 | -0.88 | -0.74 | -0.74 | 6 | no | logged |
| `g301_103_delisting_bankruptcy` | w20 | 35 | 0.0156 | 0.64 | 0.70 | 0.64 | 4 | no | logged |

Groups that count (negative only; the flag's sessions): **none: the idea is dropped**.

Bar: at least 30 observations, |t| >= 3.5 on the smaller of the two t, the same sign in at least 6 of the years 2017-2026. Nothing here changes an order.
