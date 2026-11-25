#!/bin/sh
#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
# Choose which version of the ACCORD skills the SRE triage app loads.
#
#   use_skills.sh smc      the skills in this checkout (with the SMC hooks)
#   use_skills.sh orig     the original skills, from before the SMC change
#   use_skills.sh status   show what the app links point to
#
# The lab finds the skills through two links in
# <ioc-eval-labs>/library-ioc-eval/apps/sre-triage/v2/skills/. This script only moves
# those links. Do not run it while a benchmark is running.
#
# The lab path is read from .local/lab_path (written by setup.sh) or from LAB_DIR.
# BASE_REF is the commit "orig" is taken from (default: the commit before the SMC change).
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
CP=$(cd "$HERE/../../.." && pwd)

LAB="${LAB_DIR:-}"
if [ -z "$LAB" ] && [ -f "$HERE/.local/lab_path" ]; then
  LAB=$(cat "$HERE/.local/lab_path")
fi
if [ -z "$LAB" ]; then
  echo "No lab path. Run setup.sh first, or set LAB_DIR=<path to your ioc-eval-labs clone>." >&2
  exit 1
fi

SKILLS_DIR="$LAB/library-ioc-eval/apps/sre-triage/v2/skills"
BASE_REF="${BASE_REF:-37d068c41df6eb717543af8a721b2bfb9c40dc14}"
ORIG="$HERE/.local/orig/protocols/accord/skills"
MINE="$CP/protocols/accord/skills"
SKILLS="accord-initiator accord-receiver"

show() {
  for s in $SKILLS; do
    printf '%s -> %s\n' "$s" "$(readlink "$SKILLS_DIR/$s" 2>/dev/null || echo 'NOT A LINK')"
  done
}

case "${1:-}" in
  status) show; exit 0 ;;
  smc) TARGET="$MINE" ;;
  orig)
    if [ ! -d "$ORIG" ]; then
      mkdir -p "$HERE/.local/orig"
      git -C "$CP" archive "$BASE_REF" protocols/accord/skills | tar -x -C "$HERE/.local/orig"
    fi
    TARGET="$ORIG"
    ;;
  *) echo "usage: $0 smc|orig|status" >&2; exit 1 ;;
esac

for s in $SKILLS; do
  dest="$SKILLS_DIR/$s"
  if [ -e "$dest" ] && [ ! -L "$dest" ]; then
    echo "$dest is a real folder, not a link. Move it away first:" >&2
    echo "  mv \"$dest\" \"$dest.bak\"" >&2
    exit 1
  fi
  ln -sfn "$TARGET/$s" "$dest"
done

echo "accord skills now -> $1"
show
