#!/bin/sh
#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
# One-time setup of the ACCORD + SMC experiment in an ioc-eval-labs clone.
#
#   ./setup.sh <path to your ioc-eval-labs clone>
#
# It writes only into your local clone and into this folder's .local/ directory:
#   1. copies the experiment file into <clone>/labs/cognitive-protocols/
#   2. links the app's ACCORD skills to this checkout (use_skills.sh smc)
#   3. creates smc.json from smc.example.json if there is none (you edit it)
#   4. renders the model config into .local/config/mas/
#
# Set LLM_MODEL_NAME (and optionally MAX_TOKENS) before running to choose the model.
set -eu

LAB="${1:-}"
if [ -z "$LAB" ]; then
  echo "usage: $0 <path to your ioc-eval-labs clone>" >&2
  exit 1
fi
LAB=$(cd "$LAB" && pwd)

HERE=$(cd "$(dirname "$0")" && pwd)
CP=$(cd "$HERE/../../.." && pwd)
SKILL="$CP/protocols/accord/skills/accord-initiator"
EXP_DIR="$LAB/labs/cognitive-protocols"
SKILLS_DIR="$LAB/library-ioc-eval/apps/sre-triage/v2/skills"
MODEL="${LLM_MODEL_NAME:-bedrock/global.anthropic.claude-haiku-4-5-20251001-v1:0}"
MAX_TOKENS="${MAX_TOKENS:-32768}"
CONF="$HERE/.local/config"

if [ ! -d "$EXP_DIR" ] || [ ! -d "$SKILLS_DIR" ]; then
  echo "$LAB does not look like an ioc-eval-labs clone:" >&2
  echo "  missing $EXP_DIR or $SKILLS_DIR" >&2
  exit 1
fi

mkdir -p "$HERE/.local"
printf '%s\n' "$LAB" > "$HERE/.local/lab_path"

# 1. experiment file
cp "$HERE/sre-triage-accord-smc-smoke.yaml" "$EXP_DIR/"
echo "copied the experiment to $EXP_DIR/sre-triage-accord-smc-smoke.yaml"

# 2. skill links
"$HERE/use_skills.sh" smc

# 3. SMC config
if [ ! -f "$SKILL/smc.json" ]; then
  cp "$SKILL/smc.example.json" "$SKILL/smc.json"
  echo "created $SKILL/smc.json from the example: EDIT IT (base_url, workspace_id, mas_id)"
else
  echo "kept your existing $SKILL/smc.json"
fi

# 4. model config
mkdir -p "$CONF/mas"
cp "$HERE/config/config.yaml" "$CONF/mas/config.yaml"
sed -e "s|@LLM_MODEL_NAME@|$MODEL|g" -e "s|@MAX_TOKENS@|$MAX_TOKENS|g" \
  "$HERE/config/llm-proxy.yaml.template" > "$CONF/mas/llm-proxy.yaml"
echo "wrote the model config for $MODEL to $CONF/mas/"

cat <<EOF

Next:
  1. edit $SKILL/smc.json
  2. in the shell you will run from:
       export XDG_CONFIG_HOME=$CONF
     (plus OPENAI_API_KEY, OPENAI_BASE_URL, LLM_API_KEY, LLM_BASE_MODEL_URL, as in the tutorial)
  3. mas-lab control stop        # so the daemon restarts with this environment
  4. follow the "Run it" section of the README
EOF
