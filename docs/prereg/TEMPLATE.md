# Pre-registration: <<rule_name>>

<!--
How to use this template (decision 15, sections 5 and 7, Gate A):
1. Copy it to docs/prereg/<rule_name>.md (lower-case letters, digits, underscores: the name the code uses).
2. Replace EVERY placeholder in double angle brackets, then delete this comment. Keep every heading.
3. Commit the file BEFORE the rule's first run. The walk-forward calls kaibot.evaluation.prereg.require(rule),
   which refuses to run while the file is missing, unfinished or uncommitted, and logs the file's sha256.
4. Do not edit the file after the first run. A change is a new rule under a new name, with its own file.
   An amendment written before any run on real data goes at the end ("## Amendment 1") and changes the hash.
5. The long numbered layout of docs/prereg/trend_momentum.md is accepted too: the rule in backticks in the
   title, a date like "12 November 2026", combined headings ("## 1. Hypothesis, and who is on the other side",
   "## 2. Frozen construction"), neighbours as a table, and a "Sources:" paragraph pointing to docs/research/.
   Without a Status line the file counts as pre-registered.
-->

- **Rule:** `<<rule_name>>`
- **Status:** pre-registered
- **Date:** <<YYYY-MM-DD, the day this file is committed>>
- **Author:** <<team>>
- **Issue:** <<#number of the story>>

## Hypothesis

<<One or two sentences: what the rule expects to happen and why. State the expected result net of costs,
relative to SPY total return, for example "SPY -2 to +1 points a year with a clearly shallower drawdown".>>

## Who is on the other side of the trade

<<Who sells to us when we buy and buys from us when we sell, and why they keep doing it. If nobody can be
named, the rule has no reason and should not be tested.>>

## Published evidence

<<The published evidence for this family of rules, with sources, and what replications found after costs and
after publication. Point to docs/research/engine_v3/ where it is already written up.>>

## Frozen settings

<<Every setting, with its value and where the value comes from (the published one, not one we tuned): universe
and liquidity filter, signal, lookbacks, entry, exit, sizing, exposure cap, index filter, rebalancing, earnings
version ("hold through" or "exit before"), level. Nothing may be left to choose after the first run.>>

## Neighbours (at most five)

<<Up to five neighbouring settings for the plateau check, one per list item, each as `name`: what differs from
the frozen setting. Every one of them must pass mark 1 of Gate B. Write "None" only if the rule has no setting
that could be varied; a rule with no neighbour has had no plateau check and does not meet mark 6.>>

## Fill model

<<How orders are filled in the test. The base model of decision 15: decide after the close of T, fill at the
close of T+1 at half a spread per side. The stress model: fill at the open of T+1 at twice the midday
half-spread; a stop gapped through fills at the open. Name the cost model version.>>

## Pass marks

<<"The Gate B and Gate C marks of config/gate_v2.yaml as committed together with this file." Add anything
stricter that this rule promises, and the retirement trigger for paper incubation if it differs from the
config. Marks cannot be loosened here.>>
