#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="${HELM_WORKSPACE:-$HOME/.helm/workspace}"
PROJECT="${1:-$PWD}"

helm checkpoint create --path "$WORKSPACE" --label before-risky-refactor --include "$PROJECT"
helm profile --path "$WORKSPACE" run risky_edit \
  --task-name "risky refactor verification" \
  -- python3 -m pytest "$PROJECT/tests" -q
helm status --path "$WORKSPACE" --brief
