# FINRA Reg SHO daily short volume (`finra_regsho`): switched off, do not use

**Status: off.** Stakeholder decision on #623, 24 Nov 2026, option (b): switch the collector off and remove its
data from research inputs. Short interest stays parked.

## Why
The collector (`src/kaibot/ingest/alt/finra.py`) downloaded `cdn.finra.org/equity/regsho/daily/CNMSshvol<date>.txt`.
That falls under the finra.org Terms of Use (checked by the data team, 24 Nov 2026), which:
- limit use to "your own non-commercial personal or professional use";
- forbid creating "a database of data using the FINRA Website" or copying it in bulk with robots;
- forbid use "in conjunction with any … predictive analytics".

Kaibot's alt store is a database built from those files for predictive research, so it is not allowed.

## What is switched off (PR "Fixes #623")
- `kaibot.ingest.alt.finra.collect` always raises `SourceDisabled` and makes no request.
- `kaibot.ingest.alt.store.DISABLED` refuses every read, write and lock of `data/alt/finra_regsho`, so old files
  on disk cannot be read through the store either.
- `scripts/collect_alt.py`: `--source all` (the nightly launchd job) no longer runs it; `--source finra` exits 2
  with the reason. The launchd plist needs no change.
- `config/sources.yaml`: the `finra_regsho` budget is gone.
- `kaibot.features.alt`: the `short_volume` feature is removed; `alt_features()` no longer returns it.
- `scripts/run_ablation.py` refuses a config with rules on `short_volume.*`. The pre-registered
  `config/ablation.yaml` still has two such rules (`sv_rel_no_spike`, `sv_level_not_extreme`). It is frozen (its
  sha256 is in every ablation trial), so the data team did not edit it: **Product decides and amends it**.
  Until then the ablation is refused instead of quietly reporting those rules as `insufficient`.
- The CNMS test fixture is deleted. `tests/test_finra_off.py` checks all of the above, and that no code, config
  or dashboard file references the source.

The live engine never imported any of this (`tests/unit/test_alt_isolation.py`).

## Old data on the Mac: the owner deletes it
The repo held only a 10-line CNMS test fixture, deleted in this PR. The collector wrote under the data folder, by default `~/Projects/kaibot/data`
(the launchd job's `--data`, or `KAIBOT_DATA_DIR` if you set it):

```
rm -rf ~/Projects/kaibot/data/alt/finra_regsho
```

If you ran it with another `--data` / `KAIBOT_DATA_DIR`, delete `<that folder>/alt/finra_regsho` too. Check with
`ls ~/Projects/kaibot/data/alt/`. It must not be copied, backed up into research stores or used in any analysis.
Older ablation results (`data/ablation/*.json`, `kind: "ablation"` rows in `data/trials.jsonl`) may hold verdicts for
the two `sv_*` rules. Leave the trial ledger as it is, and do not use those verdicts.

## Turning it back on
Only with a new stakeholder decision. For example, FINRA Query API credentials come with their own non-commercial
terms, so they would still not cover multi-user use. Remove the entry from `store.DISABLED` only in that PR.
