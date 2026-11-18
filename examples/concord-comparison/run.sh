#!/usr/bin/env bash
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
# Run the evacuation scenario: three participants and a facilitator (a SLIM node too, over SLIM). The facilitator starts the run and takes no part in it.
#
#   ./run.sh [--mode concord|free] [--transport slim|star] [--model M] [--threshold T] [--temperature X] [--timeout S] [--scenario FILE] [--slim HOST:PORT]
#
# Needs LLM_API_KEY and LLM_MODEL (LLM_BASE_URL for a proxy such as LiteLLM), unless --fake-llm (keyword answers, to check the plumbing). Logs and the record of each agent go to runs/<time>/.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

# the flags that decide what to start, whether written `--flag value` or `--flag=value`
transport=slim mode=concord slim=127.0.0.1:12345 fake=0 have_model=0
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
    case "${args[i]}" in
        --transport) transport="${args[i+1]:-}" ;;  --transport=*) transport="${args[i]#*=}" ;;
        --mode) mode="${args[i+1]:-}" ;;            --mode=*) mode="${args[i]#*=}" ;;
        --slim) slim="${args[i+1]:-}" ;;            --slim=*) slim="${args[i]#*=}" ;;
        --model | --model=*) have_model=1 ;;
        --fake-llm) fake=1 ;;
    esac
done
if [[ $fake == 0 ]]; then
    : "${LLM_API_KEY:-${ANTHROPIC_API_KEY:?set LLM_API_KEY (or pass --fake-llm)}}"
    [[ $have_model == 1 ]] || : "${LLM_MODEL:?set LLM_MODEL or pass --model}"
fi
OUT="${RUN_DIR:-runs/$(date +%Y%m%d-%H%M%S)-$mode-$transport}"      # RUN_DIR: where to write (batch.py sets it)
mkdir -p "$OUT"
PIDS=()
AGENT_PIDS=()
AGENT_NAMES=()
stop() {             # a process and what it started (uv run starts python)
    for pid in "$@"; do pkill -TERM -P "$pid" 2>/dev/null || true; kill "$pid" 2>/dev/null || true; done
}
trap 'stop "${PIDS[@]}"' EXIT INT TERM

[[ -n "${SKIP_SYNC:-}" ]] || uv sync --quiet      # SKIP_SYNC: batch.py has synced already, and runs in parallel must not sync together
if [[ "$transport" != star ]]; then      # a SLIM node is needed over SLIM only; a star is direct connections
    uv run python -m concord_comparison server --slim "$slim" > "$OUT/server.log" 2>&1 &
    PIDS+=($!)
    sleep 2
fi
for a in participant-a participant-b participant-c; do
    uv run python -m concord_comparison agent "$a" --out "$OUT" "$@" > "$OUT/$a.log" 2>&1 &
    PIDS+=($!)
    AGENT_PIDS+=($!)
    AGENT_NAMES+=("$a")
done
sleep 2
uv run python -m concord_comparison agent facilitator --out "$OUT" "$@" > >(tee "$OUT/facilitator.log") 2>&1 &
FACILITATOR_PID=$!
PIDS+=($FACILITATOR_PID)

# A participant that exits with an error (a crash: a wrong answer is not an error) leaves the others waiting for it until the
# protocol's own timeouts, many minutes. So watch the participants while the facilitator runs, and stop the run at the first crash.
crashed=""
reaped=()
while kill -0 "$FACILITATOR_PID" 2>/dev/null && [[ -z "$crashed" ]]; do
    for i in "${!AGENT_PIDS[@]}"; do
        if [[ -z "${reaped[$i]:-}" ]] && ! kill -0 "${AGENT_PIDS[$i]}" 2>/dev/null; then
            reaped[$i]=1
            code=0
            wait "${AGENT_PIDS[$i]}" || code=$?
            [[ $code == 0 ]] || crashed="${AGENT_NAMES[$i]} exited with status $code"
        fi
    done
    sleep 1
done
status=0
if [[ -n "$crashed" ]]; then
    echo "==> $crashed: stopping the run (see $OUT/${crashed%% *}.log)" >&2
    stop "${PIDS[@]}"
    status=1
else
    wait "$FACILITATOR_PID" || status=$?
fi
wait "${AGENT_PIDS[@]}" 2>/dev/null || true
echo "==> logs and records in $OUT"
tail -n 1 "$OUT"/participant-*.log
exit $status
