#!/usr/bin/env bash
# Run the SRE triage lab with and without Cognition Protocols, on MAS Lab.
#
# Usage:
#   OPENAI_API_KEY=... OPENAI_BASE_URL=... LLM_MODEL_NAME=... ./run.sh [options]
#
# Required env:
#   OPENAI_API_KEY    key for an OpenAI-compatible endpoint
#   OPENAI_BASE_URL   base URL of that endpoint, e.g. https://litellm.example.com/v1
#   LLM_MODEL_NAME    model name as that endpoint knows it
set -euo pipefail

usage() {
  cat <<USAGE
Usage: $0 [options]

  -s SCENARIOS    comma-separated lab scenario ids (default: all four)
  -i INCIDENTS    comma-separated dataset item ids (default: payment-async-timeout)
  -r N            runs per scenario and incident (default: 1)
  -j N            runs executed in parallel (default: 1)
  -p N            scenarios MAS Lab runs in parallel in one run (default: 5)
  -w DIR          work directory (default: ./mas-lab-sre-triage)
  --mas-lab-commit SHA   MAS Lab commit
  --lab-commit SHA       ioc-eval-labs commit
  -h              help

Scenarios: baseline, accord-enabled, concord-enabled, concord-and-accord-enabled
USAGE
}

SCENARIOS="baseline,accord-enabled,concord-enabled,concord-and-accord-enabled"
INCIDENTS="payment-async-timeout"
MAX_RUNS=1
MAX_PARALLEL=1
PARALLEL_SCENARIOS=5
WORKDIR="$PWD/mas-lab-sre-triage"
MAS_LAB_REPO="https://github.com/outshift-open/mas-lab"
MAS_LAB_COMMIT="0aeba4627437d49b9097dafbbf3e89ce9bb303ff"
LAB_REPO="https://huggingface.co/datasets/ioc-project/ioc-eval-labs"
LAB_COMMIT="1208a077c7c682f8e484127f298ea52f8fe8e63a"
# skill=version, from https://github.com/ioc-project/cognition-protocols/releases
SKILLS="accord-initiator=0.1.0 accord-receiver=0.1.0 concord-initiator=0.1.0 concord-receiver=0.1.0"
RELEASES="https://github.com/ioc-project/cognition-protocols/releases/download"

while [ $# -gt 0 ]; do
  case "$1" in
    -s) SCENARIOS="$2"; shift 2 ;;
    -i) INCIDENTS="$2"; shift 2 ;;
    -r) MAX_RUNS="$2"; shift 2 ;;
    -j) MAX_PARALLEL="$2"; shift 2 ;;
    -p) PARALLEL_SCENARIOS="$2"; shift 2 ;;
    -w) WORKDIR="$2"; shift 2 ;;
    --mas-lab-commit) MAS_LAB_COMMIT="$2"; shift 2 ;;
    --lab-commit) LAB_COMMIT="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage >&2; exit 2 ;;
  esac
done

: "${OPENAI_API_KEY:?OPENAI_API_KEY not set}"
: "${OPENAI_BASE_URL:?OPENAI_BASE_URL not set}"
: "${LLM_MODEL_NAME:?LLM_MODEL_NAME not set}"
for cmd in uv git curl unzip; do
  command -v "$cmd" >/dev/null || { echo "$cmd not found" >&2; exit 1; }
done

mkdir -p "$WORKDIR"
WORKDIR="$(cd "$WORKDIR" && pwd)"
cd "$WORKDIR"

# Fetch one commit of a repo into a directory, reusing the directory on later runs.
# Extra arguments are sparse-checkout patterns.
checkout() { # url commit dir [pattern...]
  local url="$1" commit="$2" dir="$3"
  shift 3
  [ -d "$dir/.git" ] || git init --quiet "$dir"
  git -C "$dir" remote add origin "$url" 2>/dev/null || git -C "$dir" remote set-url origin "$url"
  if [ $# -gt 0 ]; then
    git -C "$dir" sparse-checkout set --no-cone "$@"
  fi
  GIT_LFS_SKIP_SMUDGE=1 git -C "$dir" fetch --quiet --depth 1 origin "$commit"
  GIT_LFS_SKIP_SMUDGE=1 git -C "$dir" checkout --quiet --force "$commit"
  echo "$dir @ $(git -C "$dir" rev-parse --short HEAD)"
}

echo "==> 1. Check out MAS Lab and the lab"
checkout "$MAS_LAB_REPO" "$MAS_LAB_COMMIT" mas-lab
# The recorded traces of the lab are large and not needed to run it
checkout "$LAB_REPO" "$LAB_COMMIT" ioc-eval-labs '/*' '!labs/cognitive-protocols/traces'
LIBRARY="$WORKDIR/ioc-eval-labs/library-ioc-eval"
LAB="$WORKDIR/ioc-eval-labs/labs/cognitive-protocols"
sed -i.bak "s/parallel_scenarios: .*/parallel_scenarios: ${PARALLEL_SCENARIOS}/" "$LAB/sre-triage-experiments.yaml"

echo "==> 2. Install the protocol skills in the app"
# The overlays of the lab name the skills; MAS Lab finds them in the app's skills/ directory
SKILLS_DIR="$LIBRARY/apps/sre-triage/v2/skills"
for entry in $SKILLS; do
  skill="${entry%%=*}"; version="${entry#*=}"
  tmp="$(mktemp -d)"
  curl -fsSL "$RELEASES/${skill}-v${version}/${skill}.zip" -o "$tmp/${skill}.zip"
  unzip -q "$tmp/${skill}.zip" -d "$tmp"
  rm -rf "${SKILLS_DIR:?}/$skill"
  cp -R "$tmp"/protocols/*/skills/"$skill" "$SKILLS_DIR/"
  rm -rf "$tmp"
  echo "$skill v$version"
done

echo "==> 3. Install MAS Lab and the app library (uv, python 3.12)"
VENV="$WORKDIR/.venv"
[ -d "$VENV" ] || uv venv --quiet --python 3.12 "$VENV"
export VIRTUAL_ENV="$VENV"
export PATH="$VENV/bin:$PATH"
uv pip install --quiet -e "$WORKDIR/mas-lab"
uv pip install --quiet -e "$LIBRARY"

echo "==> 4. Point MAS Lab at the LLM endpoint"
# Kept in the work directory, so your own MAS Lab configuration is left alone
export XDG_CONFIG_HOME="$WORKDIR/config"
mkdir -p "$XDG_CONFIG_HOME/mas"
cat > "$XDG_CONFIG_HOME/mas/config.yaml" <<YAML
infra_refs:
  - llm-proxy.yaml
YAML
cat > "$XDG_CONFIG_HOME/mas/llm-proxy.yaml" <<YAML
apiVersion: infra/v1
kind: LLMProxy
metadata:
  name: llm-proxy
spec:
  proxy:
    api_base: env:OPENAI_BASE_URL
    api_key_env: OPENAI_API_KEY
    timeout: 240
  models:
    mappings:
      generic-model: ${LLM_MODEL_NAME}
    defaults:
      llm: ${LLM_MODEL_NAME}
YAML

echo "==> Pre-flight check: ${OPENAI_BASE_URL}/models"
curl -s -o /dev/null -w "HTTP %{http_code} (%{time_total}s)\n" --max-time 30 \
  -H "Authorization: Bearer ${OPENAI_API_KEY}" "${OPENAI_BASE_URL}/models" \
  || echo "Pre-flight check failed (continuing)"

export OPENAI_API_KEY OPENAI_BASE_URL
export LLM_BASE_MODEL_URL="$OPENAI_BASE_URL"
export LLM_API_KEY="$OPENAI_API_KEY"
export LLM_MODEL_NAME

RESULTS="$WORKDIR/results"

# The lab couples each scenario to a few incidents. A cartesian design runs every scenario
# on every incident you choose instead.
run_one() { # scenario incident
  local scenario="$1" incident="$2"
  local out="$RESULTS/${scenario}/${incident}"
  mkdir -p "$out"
  echo "==> [scenario=$scenario incident=$incident max-runs=$MAX_RUNS] output: $out"
  if (cd "$LAB" && mas-lab benchmark run sre-triage-experiments.yaml \
        --override 'experiment:experiment.execution.design.mode=cartesian' \
        --output-dir "$out" \
        --force \
        --dataset-item "$incident" \
        --scenario-id "$scenario" \
        --max-runs "$MAX_RUNS") >"$out.log" 2>&1; then
    echo "OK   scenario=$scenario incident=$incident"
  else
    echo "FAIL scenario=$scenario incident=$incident (log: $out.log)"
    touch "$out.failed"
  fi
}

# Accept "a,b,c" or "a b c" -> one item per line
to_lines() { tr ', ' '\n\n' <<<"$1" | sed '/^$/d'; }

echo "==> 5. Run the benchmark (max parallel: $MAX_PARALLEL)"
rm -rf "$RESULTS"
while read -r scenario; do
  while read -r incident; do
    while [ "$(jobs -rp | wc -l)" -ge "$MAX_PARALLEL" ]; do sleep 1; done
    run_one "$scenario" "$incident" &
  done < <(to_lines "$INCIDENTS")
done < <(to_lines "$SCENARIOS")
wait

echo "==> Results in $RESULTS"
[ -z "$(find "$RESULTS" -name '*.failed' 2>/dev/null)" ]
