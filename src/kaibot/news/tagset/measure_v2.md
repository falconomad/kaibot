Sample v2: 860 headlines, 560 in the random stratum (years 2012, 2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026).

| Tag | Labelled positives | Precision (fired stratum) | Precision (random) | Recall (random) | Ships |
|---|---|---|---|---|---|
| `results` | 66 | 76% of 25 | 76% of 25 | 66% | no |
| `guidance_up` | 43 | 82% of 50 | 67% of 3 | 100% | no |
| `guidance_down` | 39 | 74% of 50 | n/a of 0 | 0% | no |
| `analyst_up` | 61 | - | 100% of 54 | 93% | yes |
| `analyst_down` | 70 | 96% of 25 | 100% of 34 | 79% | yes |
| `deal` | 46 | 88% of 25 | 60% of 10 | 33% | no |
| `offering` | 37 | 80% of 25 | 100% of 1 | 17% | no |
| `buyback` | 21 | 76% of 25 | 100% of 1 | 100% | no |
| `management_change` | 30 | 96% of 25 | 100% of 1 | 20% | yes |
| `legal_regulatory` | 48 | 88% of 25 | 90% of 10 | 38% | no |
| `listing` | 30 | 96% of 25 | n/a of 0 | 0% | yes |

| Guidance tag | Day | Precision (fired) | Precision (random) | Recall (random) |
|---|---|---|---|---|
| `guidance_up` | earnings_day | 100% of 25 | 100% of 2 | 100% |
| `guidance_up` | other_day | 64% of 25 | 0% of 1 | n/a |
| `guidance_down` | earnings_day | 92% of 25 | n/a of 0 | 0% |
| `guidance_down` | other_day | 56% of 25 | n/a of 0 | n/a |
