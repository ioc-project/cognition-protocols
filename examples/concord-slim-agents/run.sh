#!/usr/bin/env bash
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
# Run the evacuation scenario: a SLIM node and three agents. participant-a starts a CONCORD run with the other two.
#
#   ./run.sh [--transport star] [--model M] [--threshold T] [--temperature X] [--timeout S] [--scenario FILE]
#
# Needs LLM_API_KEY and LLM_MODEL (LLM_BASE_URL for a proxy such as LiteLLM), unless --fake-llm (keyword answers, to check the plumbing). Logs and the record of each agent go to runs/<time>/.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

case " $* " in *" --fake-llm "*) ;; *) : "${LLM_API_KEY:-${ANTHROPIC_API_KEY:?set LLM_API_KEY (or pass --fake-llm)}}"
    case " $* " in *" --model "*) ;; *) : "${LLM_MODEL:?set LLM_MODEL or pass --model}" ;; esac ;;
esac
OUT="${RUN_DIR:-runs/$(date +%Y%m%d-%H%M%S)}"      # RUN_DIR: where to write (batch.py sets it)
mkdir -p "$OUT"
PIDS=()
AGENT_PIDS=()
trap 'kill "${PIDS[@]}" 2>/dev/null || true' EXIT INT TERM

uv sync --quiet
case " $* " in *" --transport star "*) ;; *)       # a SLIM node is needed over SLIM only; a star is direct connections
    uv run python -m concord_slim_agents server > "$OUT/server.log" 2>&1 &
    PIDS+=($!)
    sleep 2 ;;
esac
for a in participant-b participant-c; do
    uv run python -m concord_slim_agents agent "$a" --out "$OUT" "$@" > "$OUT/$a.log" 2>&1 &
    PIDS+=($!)
    AGENT_PIDS+=($!)
done
sleep 2
status=0
uv run python -m concord_slim_agents agent participant-a --out "$OUT" "$@" 2>&1 | tee "$OUT/participant-a.log" || status=$?
wait "${AGENT_PIDS[@]}" 2>/dev/null || true
echo "==> logs and records in $OUT"
tail -n 1 "$OUT"/participant-*.log
exit $status
