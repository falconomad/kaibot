"""Run one fuzz target of tests/fuzz/targets.py under Atheris (M12 ticket 3.5, #467). Linux; the `fuzz` workflow.

    PYTHONPATH=src python tests/fuzz/run_fuzz.py TARGET -t SECONDS [--corpus DIR] [--crashes DIR]
    PYTHONPATH=src python tests/fuzz/run_fuzz.py TARGET --replay FILE     # one saved input again, with its traceback

A finding (a `Violation`, or any exception escaping the target) is saved by libFuzzer as `crash-<sha1>` in the crash
directory, and the run exits non-zero. Replay it, fix the decoder (an engine issue) or the target (its property),
and add the input to the target's corpus so the ordinary test run keeps it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1] / "src")]

from targets import TARGETS  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("target", choices=sorted(TARGETS))
    ap.add_argument("-t", "--seconds", type=int, default=60)
    ap.add_argument("--corpus", help="the corpus directory (default tests/fuzz/corpus/TARGET; new inputs are added)")
    ap.add_argument("--crashes", default=".", help="where libFuzzer writes crash-* inputs")
    ap.add_argument("--replay", help="run one input file and print what happens")
    a = ap.parse_args(argv)
    fn = TARGETS[a.target]
    if a.replay:
        fn(Path(a.replay).read_bytes())
        print(f"{a.replay}: the property holds")
        return 0
    import atheris  # Linux, the `fuzz` dependency group

    with atheris.instrument_imports(include=["kaibot"]):
        import kaibot.broker.protocol  # noqa: F401
        import kaibot.engine.journal  # noqa: F401
        import kaibot.engine.ledger  # noqa: F401
        import kaibot.engine.orders  # noqa: F401
        import kaibot.news.desk  # noqa: F401
        import kaibot.news.text  # noqa: F401

    corpus = Path(a.corpus) if a.corpus else HERE / "corpus" / a.target
    corpus.mkdir(parents=True, exist_ok=True)
    Path(a.crashes).mkdir(parents=True, exist_ok=True)
    args = [sys.argv[0], str(corpus), f"-max_total_time={a.seconds}", f"-artifact_prefix={a.crashes.rstrip('/')}/",
            "-timeout=20", "-rss_limit_mb=2048", "-print_final_stats=1"]
    atheris.Setup(args, fn)
    atheris.Fuzz()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
