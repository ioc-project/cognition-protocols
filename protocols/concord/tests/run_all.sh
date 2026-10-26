#!/usr/bin/env bash
# Run every CONCORD script test against this checkout. Usage: protocols/concord/tests/run_all.sh [python]
set -u
PY="${1:-python3}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fail=0
for t in "$ROOT"/tests/test_*.py; do
  case "$t" in
    *transport*) arg="$ROOT/skills/concord-initiator/scripts/concord_initiator.py" ;;
    *) arg="$ROOT" ;;
  esac
  if out="$("$PY" "$t" "$arg" 2>&1)"; then
    echo "ok   $(basename "$t")"
  else
    echo "FAIL $(basename "$t")"; echo "$out" | tail -8; fail=1
  fi
done
exit "$fail"
