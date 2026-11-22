"""The test-writing agent (M12 ticket 3.8, #470): a model proposes failing property tests and tests that kill
surviving mutants; a human accepts or rejects each one. It never patches code, and nothing here is imported by the
engine.

Why: an agent writing Hypothesis tests produced bug reports that were about half valid on manual review (a
vendor's own report, not replicated; docs/research/engine_audit/report.md). That supports proposing tests for a
human to read, never automatic patches to money-handling code.

Three steps, three different powers:

1. `propose` (holds the API key, runs no generated code). Builds a prompt from ONE money-critical module listed in
   tests/mutation/targets.toml (its source and, optionally, the surviving mutants of the weekly `mutation` run),
   refuses to send it if anything in it looks like a credential (`guard.secret_findings`, also against the values
   of the process's own key variables), and makes one call through the LLM budget gateway (purpose `testwriter`,
   `models.testwriter` and `limits.testwriter` in config/llm.yaml). The model gets no tools but the forced answer
   tool, whose contract (`TestProposals`) has no field for a patch. Each proposal is read as a syntax tree
   (`guard.check_code`: allow-listed imports, no files, network, environment, fixtures or monkeypatching) and
   written to tests/candidates/proposed/; every step is appended to tests/candidates/ledger.jsonl.
2. `triage` (holds nothing, runs the code). Each proposal runs in `sandbox.run_test`: a temporary copy of
   src/kaibot, an empty environment, no network and no filesystem but the copy (Linux: user, mount and network
   namespaces and a chroot into a tmpfs with read-only /usr, Python and the copy; macOS: a sandbox-exec profile;
   refused elsewhere), CPU, memory and time limits. Before the test runs, every name it imports and every
   attribute chain it reads off one is resolved to the module that defines it and checked against the allow-list.
   Results: fails_on_main (a candidate bug report), holds (a candidate regression test), kills_mutant /
   misses_mutant (against the mutant's diff applied to the copy), refused, error, timeout. The ledger keeps only
   the result and the output's sha256; the scrubbed output is in tests/candidates/runs/ (ignored by git).
3. `decide` (a human). Accept moves the file to tests/candidates/accepted/, which the normal suite collects; a
   test that fails on today's code is accepted only with `--bug <issue>` and marked xfail(strict=True). Reject
   deletes it. Either way the ledger records who decided. The human commits accepted tests and the ledger in an
   ordinary pull request; the agent never commits, pushes or opens anything.

Spend: inside the gateway's caps ($1 a day, $25 a month, $150 in all) the agent has its own: `limits.testwriter`
(daily_usd, checked by the gateway against the whole day's spend, so run it at night before anything else spends;
monthly_usd, checked here against `kaibot.llm_call` rows of purpose testwriter before the call). CEILINGS in
`core` are the most config may set. `report` prints the month's counts and spend against the cap (exit 1 when over).

    python -m kaibot.agents.testwriter propose ledger --env <env file> [--survivors results/ledger.json]
    python -m kaibot.agents.testwriter triage [--survivors results/ledger.json]
    python -m kaibot.agents.testwriter list
    python -m kaibot.agents.testwriter decide <id> accept|reject --by <name> [--note ...] [--bug #123]
    python -m kaibot.agents.testwriter report [--month 2026-11]

Off by default: `propose` refuses to run without KAIBOT_TESTWRITER=on. Scheduling it nightly is the stakeholder's
decision (it spends API credit); the other commands spend nothing.
"""
