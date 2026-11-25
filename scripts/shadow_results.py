#!/usr/bin/env python3
"""S97 (#371): the shadow rules' results to date, as the helper: no write-capable key (migration 0047).

    PYTHONPATH=src python3 scripts/shadow_results.py      # reads ~/Projects/kaibot-secrets/helper.env

The same four tables as scripts/shadow_results.sh (Q1, Q2, Q3, Q4 of scripts/shadow_results.sql, psql's aligned
format with its "(n rows)" lines), without DATABASE_URL and without the service key: it signs in as the helper of
docs/ops/helper_role.md (publishable key + the helper user, from the helpers' env file only) and calls the
read-only function public.kb_helper_shadow_results(), which runs those four SELECTs byte for byte. The research
runner runs it (ops/runner/allowed.yaml; no flags, no --env, no writes).

It never opens the engine's .env and never reads SUPABASE_SERVICE_KEY: helper_env() refuses a helpers' file that
holds one, and nothing here looks at os.environ. It prints no key, password or token.

Exit 0 with the tables; 2 without the helpers' env file; 3 when the function is missing (migration 0047 not applied
yet); 1 on any other failure (the HTTP status and at most 200 characters of the answer)."""

from __future__ import annotations

import argparse
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kaibot.common.helper_session import HelperError, HelperSession, helper_env  # noqa: E402

DEFAULT_URL = "https://dosdkdlxejqynbgkjhup.supabase.co"  # the project's public URL, not a secret
FUNCTION = "kb_helper_shadow_results"
MIGRATION = "migrations/0047_helper_shadow_results.sql"

# Each query's columns in order, and whether psql right-aligns them (numbers) or not (text, dates). The names are
# the aliases of scripts/shadow_results.sql; tests/test_helper_shadow_results_sql.py checks them against psql.
N, T = True, False
COLUMNS: dict[str, list[tuple[str, bool]]] = {
    "q1": [("rule", T), ("sessions", N), ("first_day", T), ("last_day", T), ("signals", N), ("sized_trades", N),
           ("sessions_with_a_trade", N), ("trades_per_session", N), ("mean_r_per_trade", N), ("up_sessions", N),
           ("down_sessions", N), ("mean_daily_bp_of_pot", N), ("sd_daily_bp_of_pot", N), ("t_daily", N),
           ("sessions_for_900_trades_at_this_rate", N)],
    "q2": [("day", T), ("run_id", N), ("pot_usd", N), ("rule", T), ("signals", N), ("sized", N), ("pnl_usd", N),
           ("avg_r", N), ("symbols_watched", N)],
    "q3": [("rule", T), ("trades", N), ("sessions_with_a_trade", N), ("symbols", N), ("mean_r", N), ("sd_r", N),
           ("t_per_trade", N), ("win_pct", N), ("stopped_out", N), ("held_to_1555", N), ("best_r", N),
           ("worst_r", N), ("total_r", N), ("total_r_without_best_5", N), ("trades_for_t3_if_this_mean_were_true", N)],
    "q4": [("finding", T), ("day", T), ("detail", T)],
}


def _width(s: str) -> int:
    """Display width as psql counts it: wide and full-width characters take two columns, combining marks none."""
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _pad(s: str, w: int, right: bool) -> str:
    gap = " " * (w - _width(s))
    return gap + s if right else s + gap


def table(cols: list[tuple[str, bool]], rows: list[list[str | None]]) -> str:
    """One result as psql prints it (`psql -P pager=off`, aligned, border 1): the centred header, the dashes, one
    line per row (a value with a newline continues on the next line, marked `+`, as psql does), "(n rows)"."""
    cells = [[("" if v is None else str(v)).split("\n") for v in row] for row in rows]
    widths = [max([_width(name)] + [_width(part) for row in cells for part in row[i]])
              for i, (name, _) in enumerate(cols)]
    last = len(cols) - 1
    head = []
    for i, (name, _) in enumerate(cols):
        gap = widths[i] - _width(name)
        head.append(" " + " " * (gap // 2) + name + " " * (gap - gap // 2) + " ")
    out = ["|".join(head), "+".join("-" * (w + 2) for w in widths)]
    for row in cells:
        for k in range(max(len(c) for c in row)):
            line = []
            for i, (_, right) in enumerate(cols):
                part = row[i][k] if k < len(row[i]) else ""
                more = k + 1 < len(row[i])
                if i < last:
                    line.append(" " + _pad(part, widths[i], right) + ("+" if more else " "))
                elif more:  # psql pads the last column only to place its continuation mark
                    line.append(" " + _pad(part, widths[i], right) + "+")
                elif k >= len(row[i]):  # another column's continuation line: nothing at all here
                    line.append(" ")
                else:       # and never leaves trailing spaces after a left-aligned last value
                    line.append(" " + (_pad(part, widths[i], True) if right else part))
            out.append("|".join(line))
    n = len(rows)
    out.append(f"({n} row{'' if n == 1 else 's'})")
    return "\n".join(out) + "\n"


def render(result: dict) -> str:
    """The four tables of the function's answer, in order, each followed by a blank line (as the .sh prints)."""
    if not isinstance(result, dict) or sorted(result) != sorted(COLUMNS):
        got = sorted(result) if isinstance(result, dict) else type(result).__name__
        raise ValueError(f"unexpected answer from {FUNCTION}: {got}")
    parts = []
    for q, cols in COLUMNS.items():
        names = [c for c, _ in cols]
        rows = []
        for r in result[q] or []:
            if not isinstance(r, dict) or list(r) != names:
                raise ValueError(f"{q}: the function's columns {list(r) if isinstance(r, dict) else r!r} are not "
                                 f"this script's {names}: update scripts/shadow_results.py with {MIGRATION}")
            rows.append([r[c] for c in names])
        parts.append(table(cols, rows) + "\n")
    return "".join(parts)


def missing_function(e: HelperError) -> bool:
    """PostgREST's answer for an RPC it does not know (PGRST202, HTTP 404): migration 0047 is not applied."""
    return e.status == 404


def main(argv=None, *, session: HelperSession | None = None) -> int:
    argparse.ArgumentParser(description=__doc__.split("\n\n")[0]).parse_args(argv)
    if session is None:
        env = helper_env()
        if env is None:
            print("no helpers' env file (~/Projects/kaibot-secrets/helper.env): do the one-time setup of "
                  "docs/ops/helper_role.md first; nothing was read")
            return 2
        session = HelperSession.from_env(env, DEFAULT_URL, timeout_s=90.0)
    try:
        result = session.rpc(FUNCTION, {})
    except HelperError as e:
        if missing_function(e):
            print(f"the database has no public.{FUNCTION}(): migration 0047 is not applied yet "
                  f"({MIGRATION}; docs/ops/migrations_pending.md). Nothing was read. Apply it, then queue a new job. "
                  "(Applied already? The API may not have reloaded its schema: run `notify pgrst, 'reload schema';`.)")
            return 3
        if e.status in (401, 403):
            print(f"refused ({e}): is the helpers' sign-in listed in kaibot.helper? (scripts/helper_check.py)")
            return 1
        print(f"the shadow results could not be read: {e}")
        return 1
    try:
        sys.stdout.write(render(result))
    except ValueError as e:
        print(str(e))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
