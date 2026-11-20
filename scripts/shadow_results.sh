#!/usr/bin/env bash
# S97 (#371): the shadow rules' results to date, from Supabase, read-only. Runs the four SELECTs of
# scripts/shadow_results.sql (docs/research/event_driven/design.md, section 5) in a session the SERVER keeps read-only
# (default_transaction_read_only=on, as a startup option and again as the first statement, for a pooler that drops
# startup options): a write would be refused, and the file has none.
#   DATABASE_URL='postgresql://...' ./scripts/shadow_results.sh      # Supabase: Project settings > Database > URI
# Prints the four outputs in order (Q1, Q2, Q3, Q4), as psql aligned tables, ready to paste into #371 unchanged.
# Needs psql (brew install libpq). Exit 2 without DATABASE_URL; psql's own code otherwise.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -n "${DATABASE_URL:-}" ] || { echo "Set DATABASE_URL to the Supabase Postgres connection string"; exit 2; }
command -v psql >/dev/null || { echo "Needs psql: brew install libpq (then add its bin to PATH)"; exit 2; }
PGOPTIONS="-c default_transaction_read_only=on -c statement_timeout=60s" \
  exec psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -P pager=off \
  -c "set default_transaction_read_only = on" -f scripts/shadow_results.sql
