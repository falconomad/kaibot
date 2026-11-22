"""Tests proposed by the test-writing agent (M12 3.8, #470; `kaibot.agents.testwriter`).

`proposed/` holds model-written tests no human has read yet: never collected by the normal suite (many fail by
design: a failing proposal is a bug report). `accepted/` holds the ones a human accepted with
`python -m kaibot.agents.testwriter decide <id> accept --by <name>`: collected like any other test.
"""

collect_ignore_glob = ["proposed/*"]
