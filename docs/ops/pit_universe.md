# Point-in-time universe: S&P 500 membership by date (story S83, #123)

Research data only. The live engine does not read any of it. Audit finding E3: the research universe
(`config/universe_history.yaml`) is today's 288 large caps applied back to 2016. Today's large caps are the decade's
winners by construction, so that universe flatters every rule that buys strength. Holding all 288 equal-weight made
+246% against SPY's +209%. This story builds the universe as it was on each day: S&P 500 members on day T, including
the companies that later left the index, were taken over or failed.

## What the stakeholder runs on the Mac

```bash
cd ~/Projects/kaibot && git pull
.venv/bin/python scripts/fetch_sp500_history.py            # 2 seconds, offline: writes data/universe/former_members.csv
./scripts/fetch_daily.sh                                   # #124's fetcher; reads that file by itself
.venv/bin/python scripts/fetch_sp500_history.py --holes    # 1 minute, offline: data/universe/holes.csv
```

`./scripts/fetch_daily.sh` comes from #124 (daily bar store, PR open). Once #124 is merged it picks up
`data/universe/former_members.csv` with no extra option, which is the same as
`fetch_daily.py --symbols-file data/universe/former_members.csv`. If the store already holds today's 300 symbols, only
the 442 new keys are downloaded.

| | Today's 300 only (#124) | Plus the 442 lines of this story |
|---|---|---|
| Requests | about 620 | about 1,550 |
| Time | about 7 min (17 min while the engine runs) | about 17 min (about 45 min while the engine runs) |
| Transfer | about 180 MB | about 450 MB |
| On disk | about 50 MB | about 120 MB |

These are estimates scaled from #124's numbers (two requests per key, at 120 a minute or 40 while the engine runs).
Companies that left the index early have shorter histories, so the real numbers should be lower. The fetch is
resumable. All of it is free: Alpaca's own budget in `config/sources.yaml`, SIP bars older than 15 minutes.

## The numbers (committed change log, as of 12 Nov 2026)

**714 distinct companies** were in the S&P 500 between 1 Jan 2016 and 12 Nov 2026. 500 are members today and
**214 have left** the index. `former_members.csv` has **442 lines**:

- 214 companies that left the index;
- 225 current members that are outside the 288;
- 3 of the 288 under their new ticker (BK is now BNY, MMC is now MRSH, EQR is now VMRK), as a second chance in case
  the old ticker returns no bars.

Every line is a ticker requested as of a date inside the company's last membership spell. That date is 7 days before
the last membership day, so it falls before any delisting.

Companies are counted one per company: GOOG, FOX, NWS, UA and DISCK are dropped as second share classes. The index
itself has 503-506 lines.

| Year | Companies on 1 Jan | On 31 Dec | Distinct in the year | Added | Removed |
|---|---|---|---|---|---|
| 2016 | 498 | 500 | 526 | 28 | 26 |
| 2017 | 500 | 499 | 527 | 27 | 28 |
| 2018 | 499 | 500 | 523 | 24 | 23 |
| 2019 | 500 | 500 | 520 | 20 | 20 |
| 2020 | 500 | 500 | 517 | 17 | 17 |
| 2021 | 500 | 500 | 519 | 19 | 19 |
| 2022 | 500 | 500 | 515 | 15 | 15 |
| 2023 | 500 | 500 | 515 | 15 | 15 |
| 2024 | 500 | 500 | 517 | 17 | 17 |
| 2025 | 500 | 500 | 520 | 20 | 20 |
| 2026 (to 12 Nov) | 500 | 500 | 519 | 19 | 19 |

Added and removed count companies entering and leaving the index. A ticker change (FB to META) is neither. Over the
ten years, 221 company additions and 219 removals replaced about 44% of the index.

**Today's 288 research names against the index:** 53 of the 288 were not members on 1 Jan 2016.

- 49 joined later: ABNB, ALB, AMD, ANET, BX, CARR, CDNS, CEG, CHTR, COIN, CTVA, DD, DELL, DLR, DOW, DPZ, DXCM, ETSY,
  EXR, FANG, FTNT, HLT, HOOD, IDXX, IQV, KKR, LIN, LVS, LYV, MRNA, MRVL, MTCH, NOW, NXPI, ODFL, ON, OTIS, PANW, PLTR,
  RMD, SBAC, SNPS, TMUS, TSLA, TTWO, UBER, ULTA, VICI, WDAY. Many joined *because* they had risen: TSLA in Dec 2020,
  AMD in 2017, PLTR in 2024, COIN in 2025.
- 4 were never members: ALLY, PINS, SNAP, SOFI.
- 9 of the 288 have since left the index: AAL, ETSY, IPG, MTCH, CTRA, EA, AVB, CTVA, WBD.

Reproduce the table with `.venv/bin/python scripts/fetch_sp500_history.py --report`.

## Hand-off to Backend-quant (research harness `src/kaibot/replay/multiday*.py`)

The rule: **on day T, decide only from companies that were members on T and pass the liquidity filter on T.**
Data provides the membership half. The liquidity half already exists (`ind.adv >= MIN_ADV` on T).

```python
from kaibot.ingest.sp500_pit import Membership
m = Membership.load()                                  # committed log; no network
member = m.mask(bars.dates, bars.symbols)              # (sessions x symbols) bool, False for ETFs / SPY / non-members
# multiday_core.Signals and multiday_rules: replace the column-only mask
#   ok = isfinite(c) & (c >= MIN_PRICE) & (adv >= MIN_ADV) & mask[None, :]
# with
#   ok = isfinite(c) & (c >= MIN_PRICE) & (adv >= MIN_ADV) & member
```

- **Columns.** Load the 288 plus the keys of `former_members.csv` (store keys `SYMBOL@ASOF`) into one `DailyBars`.
  `m.keys(company)` gives a company's keys, preferred first, and `mask` uses the first one present. A 288 name keeps
  its plain ticker, so today's-list runs are unchanged.
- **Exits.** A held name that leaves the index stops being `ok`. Each pre-registration should state whether the
  rule's normal exit sells it, or whether the rule holds it until its own exit triggers. Ranking only `ok` names
  sells it on the next decision day.
- **Holes.** Bars can stop while a position is held (see the hole policy below). Close the position at its last bar,
  with the return `kaibot.ingest.sp500_pit.terminal_return(company)` applied to that bar's close. `data/universe/holes.csv`
  lists every such case with the company id; `m.spells[c]` gives the spells.
- **Gate B mark 5** ("holds on the point-in-time universe") can then be computed. Run each candidate twice, on
  today's 288 and on the member mask, and report the gap. `gate_b` in `multiday_core.py` still marks it `CANNOT`
  until then.
- **Sectors.** The member mask needs no sector map. Rules that use sector ETFs (the variant A trades) would need a
  sector for former members. That is not part of this story.
- **`kaibot.universe_daily`:** drop it from the plan. Research membership is a committed file read offline, and the
  live engine trades its own list. Nothing needs a database table. Dropping the table is Backend-quant's call
  (migrations).

## Hole policy

A **hole** is a membership spell for which the store has no usable bars: Alpaca's `holes.csv` from #124, or bars
that stop early. `--holes` writes one row per affected spell to `data/universe/holes.csv`. The columns are company,
key, spell dates, first and last bar, kind, member-days lost and terminal return. There are three kinds:

- `no_bars`: no key of the company has bars in the spell. The company cannot be picked on those days. The universe is
  short by that name, and the row counts the days.
- `starts_late`: bars begin more than 10 days after the spell starts. The company cannot be picked before its first
  bar.
- `ends_early`: bars stop more than 10 days before the spell ends. A position held then is **closed at the last bar
  at a terminal return**:
  - **-100%** for the three bank failures (SIVB, SBNY, FRC);
  - **0%** (the last price) for takeovers with known terms (EA, AVB, WBD, KVUE);
  - **-30%** for everything else (`DEFAULT_TERMINAL_RETURN`). This default is deliberately harsh. A taken-over
    company usually trades to its deal price, and Alpaca staff say delisted symbols stay in its database, so most
    takeovers will not be holes at all.

Known outcomes are listed in `src/kaibot/ingest/sp500/outcomes.csv`. Add a row when a hole turns out to be a takeover
with a known price.

The hole log needs the Mac's store, so it is not in this PR. Run the third command above and send
`data/universe/holes.csv` (a few kB) to the Data team. We then add outcomes for the largest holes or fill them from a
second free source.

## Sources and their limits

| Source | Used for | Limits |
|---|---|---|
| [fja05680/sp500](https://github.com/fja05680/sp500), file "S&P 500 Historical Components & Changes (Updated).csv" (fetched 12 Nov 2026, last row 18 Aug 2026) | the full list on every date it changed | 1996-2019 is Andreas Clenow's list (from *Trading Evolved*). From 2019 the maintainer updates it from Wikipedia's change table and checks it against news. Some old tickers are relabelled with later names (BKNG for Priceline in 2016, KDP for Dr Pepper Snapple, TT/IR); these stay inside one company, so the set is right, but an as-of request under a later ticker may return nothing (a hole). Two duplicates were found and fixed: CPRI listed beside KORS in 2016-2018, and CCEP beside CCE in 2016 |
| S&P Dow Jones Indices press releases (press.spglobal.com) | changes 21 Sep to 4 Nov 2026 (`supplement.csv`) | entered by hand from four releases; checked against a second listing (tickericons.com/changes) |
| Wikipedia "List of S&P 500 companies" | not fetched directly (blocked from our network); it is fja's source from 2019 | its table is "selected changes"; renames are not changes |
| IVV holdings (iShares Core S&P 500 ETF) from SEC EDGAR: N-Q for 31 Dec 2016, N-PORT-P for 30 Sep 2019, 31 Dec 2021, 31 Dec 2023, 30 Jun 2026 | cross-check | ishares.com is blocked from our network; the SEC filings carry names and CUSIPs, no tickers, so matching is by issuer name (`ivv_names.csv`). Quarter-end dates only |
| Renames and reused tickers (`ticker_map.csv`, 39 rows) | linking FB to META, telling Dow Chemical (DOW to 2017) apart from Dow Inc. (DOW from 2019), and so on | hand-made from the change table; renames before 2019 inside Clenow's list are not visible there |

**Cross-check against IVV (five dates).** The result is an exact match on all five dates, after the two duplicate
fixes:

| Date | List | IVV stock lines | Only in the list | Only in IVV |
|---|---|---|---|---|
| 2016-12-30 / IVV 2016-12-31 | 505 | 505 | none (CPRI before the fix) | none |
| 2019-09-30 | 505 | 505 | none | none |
| 2021-12-31 | 505 | 505 | none | none |
| 2023-12-31 | 503 | 503 | none | none |
| 2026-06-30 | 503 | 503 | none | none |

Repeat it with `.venv/bin/python scripts/fetch_sp500_history.py --crosscheck <N-PORT primary_doc.xml> ...` (files from
`https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=S000004310&type=NPORT-P`).

What the cross-check does not prove:

- Exact change dates between the check dates. A change recorded a few days early or late would not show up.
- 2016 before December: CCE/CCEP was fixed from the company history, not from a holdings file.

**Remaining risks.** Alpaca's as-of lookup for retired tickers is not verified until the Mac run, and the hole log
will show how good it is. The 288 config still lists BK, MMC and EQR, which now trade as BNY, MRSH and VMRK. If #124
stores no bars under the old names, the second-chance lines cover them. Fix the config in a later universe story.

## Files

| File | What |
|---|---|
| `src/kaibot/ingest/sp500/changes.csv` | the compact dated change log: the full list on 1 Jan 2016, then 211 dated changes to 4 Nov 2026 (11 kB) |
| `src/kaibot/ingest/sp500/ticker_map.csv`, `supplement.csv`, `outcomes.csv`, `ivv_names.csv` | see above |
| `src/kaibot/ingest/sp500_pit.py` | reader: `Membership.members_on(d)` (tickers as listed), `companies_on(d)`, `spells`, `keys`, `mask`, `former_members`, `hole_log`, `crosscheck` |
| `scripts/fetch_sp500_history.py` | builder: default build, `--holes`, `--report`, `--crosscheck`, `--update` (Data team: re-download fja's file and rebuild the log) |
| `data/universe/former_members.csv`, `spells.csv`, `holes.csv` | generated on the Mac (`data/` is not in git) |

To refresh after new index changes, add a row to `supplement.csv` from the S&P DJI release (or wait for fja's next
update), then run `--update` and commit `changes.csv`.
