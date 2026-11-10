"""Watchdog (M1, story S16).

Separate process with its own Alpaca client: cancels entries if the engine heartbeat is older than 90 s,
and flattens at 15:55 ET. See docs/decisions/08_tech_design_v2.md section 3.
"""

from __future__ import annotations


def main() -> None:
    raise SystemExit("Watchdog lands in milestone M1 (story S16).")


if __name__ == "__main__":
    main()
