"""Issue #75: rerun replays 001-003 on SIP history (and, when an IEX-feed download exists, on IEX bars) with the
default and the stricter fills, and write the side-by-side table to docs/m0/replay_004.md.

One command, on the machine that holds the history (it needs no market-data key, only the VIX cache or FRED key):

    PYTHONPATH=src .venv/bin/python -m kaibot.replay.sip_rerun --env ~/Projects/kaibot-secrets/.env \
        --sip-data data [--iex-data data_iex] [--sweep-bps 1,2,5] [--runs 200] [--out docs/m0/replay_004.md]

Same code and parameters as the original runs: variant, window, the seed universe, bars from 2025-01-01, Balanced,
$100k, 200 random runs (docs/m0/reproduce.md). Every run appends one row to <sip-data>/trials.jsonl, labelled with
its feed and fill model. Nothing here is imported by the engine.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from kaibot.replay import runner
from kaibot.replay.compare import render

ROOT = runner.ROOT
DATA_FROM = "2025-01-01"


@dataclass(frozen=True)
class Window:
    replay: str
    variant: str
    label: str
    start: str
    end: str
    recorded: str  # file under docs/m0 holding the run of 10 Nov 2026

    @property
    def key(self) -> str:
        return f"{self.variant}:{self.label}"


# v1 was only ever run on the full period (replay 001); A and B on the design and hold-out windows.
WINDOWS = (
    Window("001", "v1", "full", "2025-02-01", "2026-11-06", "replay_001.json"),
    Window("002", "A", "design", "2025-02-01", "2026-06-30", "replay_002_A_design.json"),
    Window("002", "A", "holdout", "2026-07-01", "2026-11-06", "replay_002_A_holdout.json"),
    Window("003", "B", "design", "2025-02-01", "2026-06-30", "replay_003_B_design.json"),
    Window("003", "B", "holdout", "2026-07-01", "2026-11-06", "replay_003_B_holdout.json"),
)


@dataclass(frozen=True)
class Config:
    feed: str  # sip | iex
    fills: str  # baseline | realistic
    slippage_bps: float = 0.0

    @property
    def key(self) -> str:
        bp = f"_{self.slippage_bps:g}bp" if self.slippage_bps else ""
        return f"{self.feed}_{self.fills}{bp}"

    @property
    def title(self) -> str:
        bp = f" +{self.slippage_bps:g} bp/side" if self.slippage_bps else ""
        return f"{self.feed.upper()}, {self.fills} fills{bp}"


def parse_sweep(text: str) -> tuple[float, ...]:
    return tuple(float(x) for x in text.split(",") if x.strip())


def configs(have_iex: bool, sweep_bps: tuple[float, ...] = ()) -> list[Config]:
    out = [Config("iex", "baseline"), Config("iex", "realistic")] if have_iex else []
    out += [Config("sip", "baseline"), Config("sip", "realistic")]
    return out + [Config("sip", "realistic", b) for b in sweep_bps if b > 0]


def replay_args(w: Window, c: Config, a: argparse.Namespace) -> argparse.Namespace:
    argv = ["--env", a.env, "--data", a.sip_data, "--bars-data", a.iex_data if c.feed == "iex" else a.sip_data,
            "--feed", c.feed, "--fills", c.fills, "--slippage-bps", str(c.slippage_bps),
            "--variant", w.variant, "--start", w.start, "--end", w.end, "--label", f"{w.label}_{c.key}",
            "--data-from", DATA_FROM, "--level", "balanced", "--runs", str(a.runs)]
    return runner.build_parser().parse_args(argv)


def run_all(a: argparse.Namespace, run_fn: Callable[[argparse.Namespace], dict] = runner.run) -> dict:
    """{window key: {config key: report}} for every window and configuration, one trial row per run."""
    if a.iex_data and Path(a.iex_data).resolve() == Path(a.sip_data).resolve():
        raise SystemExit("--iex-data and --sip-data are the same directory: that would compare SIP with itself")
    only = {x.strip() for x in a.only.split(",") if x.strip()}
    unknown = only - {w.key for w in WINDOWS}
    if unknown:
        raise SystemExit(f"unknown window(s) {sorted(unknown)}; choose from {[w.key for w in WINDOWS]}")
    results: dict[str, dict[str, dict]] = {}
    for w in WINDOWS:
        if only and w.key not in only:
            continue
        for c in configs(bool(a.iex_data), parse_sweep(a.sweep_bps)):
            print(f"== replay {w.replay} {w.key} | {c.title}", flush=True)
            results.setdefault(w.key, {})[c.key] = run_fn(replay_args(w, c, a))
    return results


def load_recorded(docs: Path) -> dict[str, dict]:
    return {w.key: json.loads((docs / w.recorded).read_text()) for w in WINDOWS if (docs / w.recorded).exists()}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--env", required=True)
    ap.add_argument("--sip-data", default=str(ROOT / "data"), help="SIP history (scripts/fetch_history.sh); "
                    "news, the VIX cache, reports and the trial log are read and written here")
    ap.add_argument("--iex-data", default="", help="directory with an IEX-feed bars_1m/ download (optional)")
    ap.add_argument("--sweep-bps", default="", help="extra SIP runs, realistic fills + N bp per side, e.g. 1,2,5")
    ap.add_argument("--runs", type=int, default=200, help="random-entry runs per replay (the originals used 200)")
    ap.add_argument("--only", default="", help="subset of windows, e.g. A:design,A:holdout")
    ap.add_argument("--out", default=str(ROOT / "docs" / "m0" / "replay_004.md"))
    return ap


def main(argv: list[str] | None = None) -> None:
    a = build_parser().parse_args(argv)
    results = run_all(a)
    out = Path(a.out)
    cfgs = configs(True, parse_sweep(a.sweep_bps))  # IEX columns always shown: "not run" without an IEX download
    meta = {"run_at": datetime.now(UTC).isoformat(timespec="seconds"), "runs": a.runs}
    out.write_text(render(WINDOWS, results, load_recorded(ROOT / "docs" / "m0"), cfgs, meta))
    out.with_suffix(".json").write_text(json.dumps({"meta": meta, "results": results}, indent=1, default=str))
    print(f"wrote {out} and {out.with_suffix('.json')}")


if __name__ == "__main__":
    main()
